"""Game lobby, player management, state serialization, and diff calculation."""
import random
import uuid
import json
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List, Tuple
import sqlite3

from app.database import db_transaction, query_one, query_db
from app.engine.ledger import append_event_to_ledger
from app.engine.win_conditions import evaluate_win_conditions, evaluate_diplomatic_conference
from app.config import get_server_now, get_server_timezone
from app.models.game_types import LobbyConfig, GameStatus
from app.services.notification_service import create_in_app_notification


def create_game_lobby(name: str, creator_user_id: str, config: Optional[LobbyConfig] = None, is_playtest: bool = False) -> str:
    """Create a new game lobby with parameters stored in game_parameters table."""
    game_id = f"game_{uuid.uuid4().hex[:12]}"
    now_iso = datetime.now(timezone.utc).isoformat()
    cfg = config or LobbyConfig()

    single_outcomes_str = json.dumps(cfg.conference_single_disagree_outcomes) if isinstance(cfg.conference_single_disagree_outcomes, list) else str(cfg.conference_single_disagree_outcomes)
    multi_outcomes_str = json.dumps(cfg.conference_multi_disagree_outcomes) if isinstance(cfg.conference_multi_disagree_outcomes, list) else str(cfg.conference_multi_disagree_outcomes)

    with db_transaction() as conn:
        conn.execute(
            """
            INSERT INTO games (id, name, status, created_by, created_at, is_playtest)
            VALUES (?, ?, 'WAITING', ?, ?, ?)
            """,
            (game_id, name, creator_user_id, now_iso, 1 if is_playtest else 0)
        )

        conn.execute(
            """
            INSERT INTO game_parameters (
                game_id, board_width, board_height, starting_hp, starting_ap,
                starting_range_level, ap_drop_cron, ap_drop_timezone, ap_drop_amount,
                ap_drop_server_time, burst_cap_ap, move_cost_ap, attack_cost_ap, give_ap_cost_ap,
                range_upgrade_cost_base, range_upgrade_cost_scale,
                win_cond_last_man, win_cond_armistice, armistice_threshold,
                armistice_trigger_threshold, armistice_hide_vote_count, diplomatic_conference_duration_sec,
                conference_transparency, conference_end_condition, conference_single_disagree_outcomes, conference_multi_disagree_outcomes,
                armistice_outcome_multi_disagree,
                win_cond_rogue, rogue_resolution_mode, rogue_bonus_ap, multi_disagree_bonus_ap,
                win_cond_stagnation, stagnation_days_limit, stagnation_mode,
                reveal_killer_on_death, allow_dead_chat, fog_of_war_stats,
                first_ap_drop_days, require_email_verify, audit_log_public,
                allow_late_join, map_voting_enabled, map_voting_duration_minutes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                game_id, cfg.board_width, cfg.board_height, cfg.starting_hp, cfg.starting_ap,
                cfg.starting_range_level, cfg.ap_drop_cron, cfg.ap_drop_timezone, cfg.ap_drop_amount,
                cfg.ap_drop_server_time, cfg.burst_cap_ap, cfg.move_cost_ap, cfg.attack_cost_ap, cfg.give_ap_cost_ap,
                cfg.range_upgrade_cost_base, cfg.range_upgrade_cost_scale,
                int(cfg.win_cond_last_man), int(cfg.win_cond_armistice), cfg.armistice_threshold,
                cfg.armistice_trigger_threshold, int(cfg.armistice_hide_vote_count), cfg.diplomatic_conference_duration_sec,
                cfg.conference_transparency, cfg.conference_end_condition, single_outcomes_str, multi_outcomes_str,
                cfg.armistice_outcome_multi_disagree,
                int(cfg.win_cond_rogue), cfg.rogue_resolution_mode.value, cfg.rogue_bonus_ap, cfg.multi_disagree_bonus_ap,
                int(cfg.win_cond_stagnation), cfg.stagnation_days_limit, cfg.stagnation_mode.value,
                int(cfg.reveal_killer_on_death), int(cfg.allow_dead_chat), int(cfg.fog_of_war_stats),
                cfg.first_ap_drop_days, int(cfg.require_email_verify), int(cfg.audit_log_public),
                int(getattr(cfg, "allow_late_join", False)), int(getattr(cfg, "map_voting_enabled", False)), getattr(cfg, "map_voting_duration_minutes", 10)
            )
        )

    # Automatically join creator to their lobby
    join_game_lobby(game_id, creator_user_id)
    return game_id


def find_unique_spawn_coordinate(conn: sqlite3.Connection, game_id: str, width: int, height: int) -> Tuple[int, int]:
    """Find a random unoccupied grid coordinate for player spawn."""
    cur = conn.cursor()
    cur.execute("SELECT x, y FROM players WHERE game_id = ?", (game_id,))
    occupied = {(r["x"], r["y"]) for r in cur.fetchall()}

    all_tiles = [(x, y) for x in range(1, width + 1) for y in range(1, height + 1)]
    available = [t for t in all_tiles if t not in occupied]
    if not available:
        raise ValueError("Grid is completely full, cannot spawn new player")
    return random.choice(available)


def join_game_lobby(game_id: str, user_id: str) -> Dict[str, Any]:
    """Register player into the lobby and assign unique starting coordinate."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with db_transaction() as conn:
        cur = conn.cursor()
        cur.execute("SELECT status, created_by, is_playtest FROM games WHERE id = ?", (game_id,))
        game = cur.fetchone()
        if not game or game["status"] != "WAITING":
            raise ValueError("Lobby is not open for registration")

        if game["is_playtest"] and user_id != game["created_by"]:
            cur.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,))
            u_adm = cur.fetchone()
            if not (u_adm and u_adm["is_admin"]):
                raise ValueError("This match is in Playtest mode. Only bots are permitted to join.")

        # Check existing registration
        cur.execute("SELECT * FROM players WHERE game_id = ? AND user_id = ?", (game_id, user_id))
        existing = cur.fetchone()
        if existing:
            return dict(existing)

        # Get user info and game parameters
        cur.execute("SELECT username FROM users WHERE id = ?", (user_id,))
        user = cur.fetchone()
        if not user:
            raise ValueError("User not found")

        cur.execute("SELECT * FROM game_parameters WHERE game_id = ?", (game_id,))
        cfg = cur.fetchone()
        spawn_x, spawn_y = find_unique_spawn_coordinate(conn, game_id, cfg["board_width"], cfg["board_height"])

        player_id = f"p_{uuid.uuid4().hex[:10]}"
        cur.execute(
            """
            INSERT INTO players (
                id, game_id, user_id, username, x, y, hp, ap, range_level,
                is_alive, armistice_vote, last_active_at, spawn_x, spawn_y
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?, ?)
            """,
            (
                player_id, game_id, user_id, user["username"], spawn_x, spawn_y,
                cfg["starting_hp"], cfg["starting_ap"], cfg["starting_range_level"],
                now_iso, spawn_x, spawn_y
            )
        )

        return {
            "player_id": player_id,
            "game_id": game_id,
            "x": spawn_x,
            "y": spawn_y,
            "hp": cfg["starting_hp"],
            "ap": cfg["starting_ap"]
        }


def remove_player_from_lobby(game_id: str, admin_user_id: str, player_id: str) -> Dict[str, Any]:
    """Remove a joined player or bot from the lobby in the waiting area."""
    game = query_one("SELECT status, created_by FROM games WHERE id = ?", (game_id,))
    if not game:
        raise ValueError("Game does not exist")
    if game["status"] != "WAITING":
        raise ValueError("Players can only be removed while the match is in WAITING status")

    caller = query_one("SELECT is_admin FROM users WHERE id = ?", (admin_user_id,))
    is_admin = bool(caller and caller["is_admin"])
    is_creator = (game["created_by"] == admin_user_id)
    if not (is_admin or is_creator):
        raise PermissionError("Only the lobby creator or an administrator can remove players")

    target = query_one("SELECT id, user_id, username, is_bot FROM players WHERE id = ? AND game_id = ?", (player_id, game_id))
    if not target:
        raise ValueError("Player not found in this lobby")

    if target["user_id"] == game["created_by"] and not is_admin:
        raise ValueError("Lobby host cannot be removed from their own lobby")

    with db_transaction() as conn:
        conn.execute("DELETE FROM players WHERE id = ? AND game_id = ?", (player_id, game_id))

    return {"status": "REMOVED", "player_id": player_id, "username": target["username"]}


def start_game(game_id: str, admin_user_id: str) -> Dict[str, Any]:
    """Commence match: transitions status to MAP_VOTING (if enabled) or ACTIVE and seeds ledger."""
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    now_ns = int(now.timestamp() * 1e9)

    with db_transaction() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM games WHERE id = ?", (game_id,))
        game = cur.fetchone()
        if not game or game["status"] != "WAITING":
            raise ValueError("Game cannot be started from current status")

        # Verify player count
        cur.execute("SELECT COUNT(*) as cnt FROM players WHERE game_id = ?", (game_id,))
        count = cur.fetchone()["cnt"]
        if count < 1:
            raise ValueError("At least 1 player is required to start a game")

        cur.execute("SELECT * FROM game_parameters WHERE game_id = ?", (game_id,))
        cfg = cur.fetchone()
        width = cfg["board_width"] if cfg else 15
        height = cfg["board_height"] if cfg else 15
        map_voting_enabled = bool(cfg["map_voting_enabled"]) if (cfg and "map_voting_enabled" in cfg.keys()) else False

        cur.execute("SELECT id, user_id, username FROM players WHERE game_id = ?", (game_id,))
        players_list = cur.fetchall()

        if map_voting_enabled:
            # Generate 4 random placement options
            cur.execute("DELETE FROM game_map_options WHERE game_id = ?", (game_id,))
            cur.execute("DELETE FROM game_map_votes WHERE game_id = ?", (game_id,))

            labels = ["Alpha", "Bravo", "Charlie", "Delta"]
            all_possible_coords = [(x, y) for x in range(1, width + 1) for y in range(1, height + 1)]

            for opt_idx in range(1, 5):
                coords_copy = list(all_possible_coords)
                random.shuffle(coords_copy)
                opt_placements = {}
                for idx, p in enumerate(players_list):
                    cx, cy = coords_copy[idx]
                    opt_placements[p["user_id"]] = {
                        "x": cx,
                        "y": cy,
                        "player_id": p["id"],
                        "username": p["username"]
                    }
                opt_id = f"opt_{uuid.uuid4().hex[:10]}"
                cur.execute(
                    """
                    INSERT INTO game_map_options (id, game_id, option_idx, option_label, placements_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (opt_id, game_id, opt_idx, labels[opt_idx - 1], json.dumps(opt_placements), now_iso)
                )

            duration_min = cfg["map_voting_duration_minutes"] if (cfg and "map_voting_duration_minutes" in cfg.keys()) else 10
            ends_at_iso = (now + timedelta(minutes=duration_min)).isoformat()

            cur.execute(
                "UPDATE games SET status = 'MAP_VOTING', map_voting_ends_at = ? WHERE id = ?",
                (ends_at_iso, game_id)
            )

            append_event_to_ledger(
                conn=conn,
                game_id=game_id,
                event_type="MAP_VOTING_STARTED",
                payload={"player_count": count, "duration_minutes": duration_min, "ends_at": ends_at_iso},
                timestamp_ns=now_ns,
                created_at_iso=now_iso,
                initiator_user_id=admin_user_id,
                visibility="PUBLIC"
            )

            for p in players_list:
                cur.execute(
                    """
                    INSERT INTO notifications (id, user_id, game_id, event_type, title_en, title_ru, message_en, message_ru, created_at)
                    VALUES (?, ?, ?, 'MAP_VOTING_STARTED', 'Map Placement Voting Open', 'Голосование за расстановку открыто',
                            'Rank the 4 deployment map options in order of preference to decide starting positions.',
                            'Расставьте 4 варианта карты в порядке предпочтения, чтобы определить начальные позиции.', ?)
                    """,
                    (uuid.uuid4().hex, p["user_id"], game_id, now_iso)
                )

            return {"status": "MAP_VOTING", "map_voting_ends_at": ends_at_iso}

        # Standard direct start
        all_coords = [(x, y) for x in range(1, width + 1) for y in range(1, height + 1)]
        random.shuffle(all_coords)

        for idx, p in enumerate(players_list):
            sx, sy = all_coords[idx]
            cur.execute(
                "UPDATE players SET x = ?, y = ?, spawn_x = ?, spawn_y = ? WHERE id = ?",
                (sx, sy, sx, sy, p["id"])
            )

        cur.execute(
            "UPDATE games SET status = 'ACTIVE', started_at = ?, last_combat_at = ? WHERE id = ?",
            (now_iso, now_iso, game_id)
        )

        append_event_to_ledger(
            conn=conn,
            game_id=game_id,
            event_type="GAME_STARTED",
            payload={"player_count": count},
            timestamp_ns=now_ns,
            created_at_iso=now_iso,
            initiator_user_id=admin_user_id,
            visibility="PUBLIC"
        )
        return {"status": "ACTIVE"}


def add_dummy_player(game_id: str, username: Optional[str] = None) -> Dict[str, Any]:
    """Add a simulated dummy bot player for tactical simulation and playtesting."""
    now_iso = datetime.now(timezone.utc).isoformat()
    flags = ["🇺🇳", "🇫🇮", "🏳️‍🌈", "🏁", "🏴‍☠️", "🇩🇪", "🇫🇷", "🇯🇵", "🇬🇧", "🇨🇳", "🇺🇸"]
    chosen_flag = random.choice(flags)
    dummy_name = username or f"Bot_{random.randint(100, 999)}"

    with db_transaction() as conn:
        cur = conn.cursor()
        # Ensure dummy user exists in users table
        cur.execute("SELECT id FROM users WHERE username = ?", (dummy_name,))
        existing_u = cur.fetchone()
        if existing_u:
            dummy_user_id = existing_u["id"]
        else:
            dummy_user_id = f"usr_{uuid.uuid4().hex[:10]}"
            cur.execute(
                """
                INSERT INTO users (id, username, password_hash, national_flag, preferred_lang, created_at, is_bot)
                VALUES (?, ?, 'dummy_hash', ?, 'en', ?, 1)
                """,
                (dummy_user_id, dummy_name, chosen_flag, now_iso)
            )

        cur.execute("SELECT * FROM game_parameters WHERE game_id = ?", (game_id,))
        cfg = cur.fetchone()
        spawn_x, spawn_y = find_unique_spawn_coordinate(conn, game_id, cfg["board_width"], cfg["board_height"])

        player_id = f"p_{uuid.uuid4().hex[:10]}"
        cur.execute(
            """
            INSERT INTO players (
                id, game_id, user_id, username, x, y, hp, ap, range_level,
                is_alive, armistice_vote, is_bot, last_active_at, spawn_x, spawn_y
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, 1, ?, ?, ?)
            """,
            (
                player_id, game_id, dummy_user_id, dummy_name, spawn_x, spawn_y,
                cfg["starting_hp"], cfg["starting_ap"], cfg["starting_range_level"],
                now_iso, spawn_x, spawn_y
            )
        )

        return {
            "player_id": player_id,
            "username": dummy_name,
            "x": spawn_x,
            "y": spawn_y,
            "national_flag": chosen_flag,
            "is_bot": True
        }


def toggle_armistice_vote(game_id: str, user_id: str) -> Dict[str, Any]:
    """Toggle a player's peace vote and check if Armistice Conference triggers."""
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    now_ns = int(now.timestamp() * 1e9)

    with db_transaction() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM game_parameters WHERE game_id = ?", (game_id,))
        cfg = cur.fetchone()
        if not cfg or cfg["win_cond_armistice"] == 0:
            raise ValueError("Armistice win condition is disabled in this lobby")

        cur.execute("SELECT * FROM games WHERE id = ?", (game_id,))
        game = cur.fetchone()
        if not game or game["status"] != "ACTIVE":
            raise ValueError("Game is not active")

        cur.execute("SELECT * FROM players WHERE game_id = ? AND user_id = ?", (game_id, user_id))
        player = cur.fetchone()
        if not player or player["is_alive"] != 1:
            raise ValueError("Living player required to vote")

        new_vote = 0 if player["armistice_vote"] == 1 else 1
        cur.execute("UPDATE players SET armistice_vote = ?, last_active_at = ? WHERE id = ?", (new_vote, now_iso, player["id"]))

        append_event_to_ledger(
            conn=conn,
            game_id=game_id,
            event_type="ARMISTICE_VOTE",
            payload={"player_id": player["id"], "username": player["username"], "voted": bool(new_vote)},
            timestamp_ns=now_ns,
            created_at_iso=now_iso,
            initiator_user_id=user_id,
            visibility="PUBLIC"
        )

        # Check living players
        cur.execute("SELECT * FROM players WHERE game_id = ? AND is_alive = 1", (game_id,))
        living = cur.fetchall()
        living_count = len(living)
        voters_count = len([p for p in living if (p["id"] == player["id"] and new_vote == 1) or (p["id"] != player["id"] and p["armistice_vote"] == 1)])
        trigger_threshold = cfg["armistice_trigger_threshold"] if (cfg and "armistice_trigger_threshold" in cfg.keys()) else 1.0

        conference_triggered = False
        if living_count >= 2 and (voters_count / living_count) >= trigger_threshold:
            if game["conference_status"] != "CONVENED":
                duration_sec = cfg["diplomatic_conference_duration_sec"] if (cfg and "diplomatic_conference_duration_sec" in cfg.keys()) else 120
                ends_at = (now + timedelta(seconds=duration_sec)).isoformat()
                cur.execute(
                    """
                    UPDATE games
                    SET conference_status = 'CONVENED', conference_triggered_at = ?, conference_ends_at = ?
                    WHERE id = ?
                    """,
                    (now_iso, ends_at, game_id)
                )
                cur.execute("UPDATE players SET armistice_vote = 0, conference_vote = NULL WHERE game_id = ? AND is_alive = 1", (game_id,))
                append_event_to_ledger(
                    conn=conn,
                    game_id=game_id,
                    event_type="CONFERENCE_CONVENED",
                    payload={"duration_sec": duration_sec, "ends_at": ends_at, "trigger_voters": voters_count},
                    timestamp_ns=now_ns,
                    created_at_iso=now_iso,
                    visibility="PUBLIC"
                )
                cur.execute("SELECT DISTINCT user_id FROM players WHERE game_id = ? AND user_id IS NOT NULL", (game_id,))
                for p_row in cur.fetchall():
                    cur.execute(
                        """
                        INSERT INTO notifications (id, user_id, game_id, event_type, title_en, title_ru, message_en, message_ru, created_at)
                        VALUES (?, ?, ?, 'CONFERENCE_CONVENED', 'Diplomatic Conference Convened', 'Созвана дипломатическая конференция',
                                'All active players have convened a diplomatic conference. Combat is blocked. Review the accord and cast your ballot.',
                                'Все активные игроки созвали дипломатическую конференцию. Действия заблокированы. Ознакомьтесь с условиями и отдайте голос.', ?)
                        """,
                        (uuid.uuid4().hex, p_row["user_id"], game_id, now_iso)
                    )
                conference_triggered = True

        return {
            "armistice_vote": bool(new_vote),
            "conference_triggered": conference_triggered,
            "voters_count": voters_count,
            "living_count": living_count
        }


def cast_conference_vote(game_id: str, user_id: str, vote: bool) -> Dict[str, Any]:
    """Cast an explicit AGREE (vote=True) or REJECT (vote=False) ballot in an active diplomatic conference."""
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    now_ns = int(now.timestamp() * 1e9)

    with db_transaction() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM games WHERE id = ?", (game_id,))
        game = cur.fetchone()
        if not game or game["status"] != "ACTIVE":
            raise ValueError("Game is not active")
        if game["conference_status"] != "CONVENED":
            raise ValueError("No diplomatic conference is currently convened")

        cur.execute("SELECT * FROM players WHERE game_id = ? AND user_id = ?", (game_id, user_id))
        player = cur.fetchone()
        if not player or player["is_alive"] != 1:
            raise ValueError("Only living players can participate in conference voting")

        vote_val = 1 if vote else 0
        cur.execute("UPDATE players SET conference_vote = ?, last_active_at = ? WHERE id = ?", (vote_val, now_iso, player["id"]))

        append_event_to_ledger(
            conn=conn,
            game_id=game_id,
            event_type="CONFERENCE_VOTE_CAST",
            payload={"player_id": player["id"], "username": player["username"], "vote": vote_val},
            timestamp_ns=now_ns,
            created_at_iso=now_iso,
            initiator_user_id=user_id,
            visibility="PUBLIC"
        )

        # Check if all living players have voted
        cur.execute("SELECT COUNT(*) as unvoted FROM players WHERE game_id = ? AND is_alive = 1 AND conference_vote IS NULL", (game_id,))
        unvoted_count = cur.fetchone()["unvoted"]

        cur.execute("SELECT conference_end_condition FROM game_parameters WHERE game_id = ?", (game_id,))
        cfg_row = cur.fetchone()
        end_condition = cfg_row["conference_end_condition"] if (cfg_row and "conference_end_condition" in cfg_row.keys()) else "ON_ALL_VOTED"

        outcome = None
        if unvoted_count == 0 and end_condition != "TIMER_ONLY":
            outcome = evaluate_diplomatic_conference(conn, game_id, now_ns, now_iso)

        return {
            "vote": bool(vote_val),
            "all_voted": (unvoted_count == 0),
            "outcome": outcome
        }


def get_game_state(game_id: str, current_user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Retrieve game state applying strict Fog of War separation (Section 6).
    Other players' AP, HP, and Range tiers are omitted from public responses.
    """
    game = query_one("SELECT * FROM games WHERE id = ?", (game_id,))
    if not game:
        raise ValueError("Game not found")

    # Check if active conference has expired
    if game["status"] == "ACTIVE" and game["conference_status"] == "CONVENED" and game["conference_ends_at"]:
        try:
            ends_dt = datetime.fromisoformat(game["conference_ends_at"].replace("Z", "+00:00"))
            if datetime.now(timezone.utc) >= ends_dt:
                now_iso = datetime.now(timezone.utc).isoformat()
                now_ns = int(datetime.now(timezone.utc).timestamp() * 1e9)
                with db_transaction() as conn:
                    evaluate_diplomatic_conference(conn, game_id, now_ns, now_iso)
                game = query_one("SELECT * FROM games WHERE id = ?", (game_id,))
        except Exception:
            pass

    # Check if conference resolution cooldown has expired
    if game["status"] == "ACTIVE" and game["conference_status"] == "COOLDOWN" and game["conference_ends_at"]:
        try:
            ends_dt = datetime.fromisoformat(game["conference_ends_at"].replace("Z", "+00:00"))
            if datetime.now(timezone.utc) >= ends_dt:
                with db_transaction() as conn:
                    conn.execute("UPDATE games SET conference_status = 'IDLE', conference_ends_at = NULL, conference_outcome = NULL, conference_resolution_data = NULL WHERE id = ?", (game_id,))
                    conn.execute("UPDATE players SET conference_vote = NULL WHERE game_id = ?", (game_id,))
                game = query_one("SELECT * FROM games WHERE id = ?", (game_id,))
        except Exception:
            pass

    if game["status"] == "MAP_VOTING" and "map_voting_ends_at" in game.keys() and game["map_voting_ends_at"]:
        try:
            m_ends_dt = datetime.fromisoformat(game["map_voting_ends_at"].replace("Z", "+00:00"))
            if datetime.now(timezone.utc) >= m_ends_dt:
                resolve_map_rcv_vote(game_id)
                game = query_one("SELECT * FROM games WHERE id = ?", (game_id,))
        except Exception:
            pass

    cfg_row = query_one("SELECT * FROM game_parameters WHERE game_id = ?", (game_id,))
    cfg = dict(cfg_row) if cfg_row else {}
    if "board_width" in cfg:
        cfg["grid_width"] = cfg["board_width"]
    if "board_height" in cfg:
        cfg["grid_height"] = cfg["board_height"]

    players_rows = query_db(
        """
        SELECT p.*, u.national_flag, u.avatar_path
        FROM players p
        JOIN users u ON p.user_id = u.id
        WHERE p.game_id = ?
        ORDER BY p.username ASC
        """,
        (game_id,)
    )

    fow_active = bool(cfg.get("fog_of_war_stats", 1)) and (game["status"] != "ENDED")
    hide_armistice_votes = bool(cfg.get("armistice_hide_vote_count", 0))
    conf_transparency = cfg.get("conference_transparency", "REVEAL_COUNTS_AT_END")

    total_agreed_count = sum(1 for p in players_rows if p["conference_vote"] == 1)

    serialized_players = []
    self_player_id = None

    for p in players_rows:
        is_self = (current_user_id is not None and p["user_id"] == current_user_id)
        if is_self:
            self_player_id = p["id"]

        armistice_voted = bool(p["armistice_vote"])
        if hide_armistice_votes and not is_self:
            armistice_voted = False

        conf_vote = p["conference_vote"]
        if not is_self:
            if conf_transparency == "SHOW_TOTAL_AGREE":
                conf_vote = None
            elif conf_transparency == "REVEAL_COUNTS_AT_END":
                if game["conference_status"] == "CONVENED":
                    conf_vote = None
            elif conf_transparency == "SECRET_RESOLVE":
                conf_vote = None

        player_data = {
            "id": p["id"],
            "user_id": p["user_id"],
            "username": p["username"],
            "national_flag": p["national_flag"],
            "avatar_path": p["avatar_path"],
            "x": p["x"],
            "y": p["y"],
            "is_alive": bool(p["is_alive"]),
            "is_bot": bool(p["is_bot"] if "is_bot" in p.keys() else 0),
            "armistice_vote": armistice_voted,
            "conference_vote": conf_vote,
            "is_self": is_self
        }

        if is_self or not fow_active:
            # Authorized private statistics
            player_data["hp"] = p["hp"]
            player_data["ap"] = p["ap"]
            player_data["range_level"] = p["range_level"]
        else:
            # Confidential stats hidden by Fog of War
            player_data["hp"] = None
            player_data["ap"] = None
            player_data["range_level"] = None

        serialized_players.append(player_data)

    # Get latest event seq scoped to events visible to requesting user
    if current_user_id:
        last_ev = query_one(
            """
            SELECT MAX(event_seq) as max_seq FROM events
            WHERE game_id = ? AND (visibility = 'PUBLIC' OR initiator_user_id = ? OR target_user_id = ?)
            """,
            (game_id, current_user_id, current_user_id)
        )
    else:
        last_ev = query_one(
            "SELECT MAX(event_seq) as max_seq FROM events WHERE game_id = ? AND visibility = 'PUBLIC'",
            (game_id,)
        )
    last_seq = last_ev["max_seq"] if last_ev and last_ev["max_seq"] else 0

    return {
        "game_id": game["id"],
        "name": game["name"],
        "status": game["status"],
        "created_by": game["created_by"],
        "is_playtest": bool(game["is_playtest"] if "is_playtest" in game.keys() else 0),
        "board_width": cfg.get("board_width", 15),
        "board_height": cfg.get("board_height", 15),
        "current_shrink_level": game["current_shrink_level"],
        "players": serialized_players,
        "self_player_id": self_player_id,
        "winner_id": game["winner_id"],
        "winner_type": game["winner_type"],
        "winner_details": game["winner_details"],
        "conference_status": game["conference_status"],
        "conference_ends_at": game["conference_ends_at"],
        "conference_triggered_at": game["conference_triggered_at"],
        "conference_resolution_data": (
            json.loads(game["conference_resolution_data"])
            if ("conference_resolution_data" in game.keys() and game["conference_resolution_data"] and isinstance(game["conference_resolution_data"], str))
            else (game["conference_resolution_data"] if "conference_resolution_data" in game.keys() else None)
        ),
        "conference_total_agree": total_agreed_count if (conf_transparency in ("SHOW_ALL_VOTES", "SHOW_TOTAL_AGREE") or (conf_transparency == "REVEAL_COUNTS_AT_END" and game["conference_status"] != "CONVENED")) else None,
        "map_voting_ends_at": game["map_voting_ends_at"] if "map_voting_ends_at" in game.keys() else None,
        "last_event_seq": last_seq,
        "config": cfg
    }


def delete_game_lobby(game_id: str, user_id: str) -> None:
    """Permanently erase and delete a game lobby and all its records (by creator host or admin)."""
    with db_transaction() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM games WHERE id = ?", (game_id,))
        game = cur.fetchone()
        if not game:
            raise ValueError("Game lobby not found")

        cur.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,))
        u = cur.fetchone()
        is_admin = bool(u["is_admin"]) if u else False

        # Only administrators can delete match lobbies
        if not is_admin:
            raise ValueError("Only an administrator can delete game lobbies")

        # Collect bot user IDs associated with this lobby before deleting player records
        cur.execute("SELECT user_id FROM players WHERE game_id = ? AND is_bot = 1", (game_id,))
        bot_user_ids = [row["user_id"] for row in cur.fetchall()]

        # Explicitly delete all child records to guarantee complete erasure regardless of FK settings
        conn.execute("DELETE FROM events WHERE game_id = ?", (game_id,))
        conn.execute("DELETE FROM players WHERE game_id = ?", (game_id,))
        conn.execute("DELETE FROM game_parameters WHERE game_id = ?", (game_id,))
        conn.execute("DELETE FROM game_parameter_audit_logs WHERE game_id = ?", (game_id,))
        conn.execute("DELETE FROM notifications WHERE game_id = ?", (game_id,))
        conn.execute("DELETE FROM games WHERE id = ?", (game_id,))

        # Permanently destroy bot user accounts belonging to this lobby
        for b_uid in bot_user_ids:
            conn.execute("DELETE FROM users WHERE id = ? AND (is_bot = 1 OR password_hash = 'dummy_hash')", (b_uid,))
        # Clean up any orphaned bot users that no longer participate in any game
        conn.execute("DELETE FROM users WHERE (is_bot = 1 OR password_hash = 'dummy_hash') AND id NOT IN (SELECT user_id FROM players)")


def terminate_game_prematurely(game_id: str, admin_user_id: str, reason: str = "Terminated by Administrator") -> None:
    """Force terminate an active game beforehand (admin only)."""
    now_iso = datetime.now(timezone.utc).isoformat()
    now_ns = int(datetime.now(timezone.utc).timestamp() * 1e9)
    with db_transaction() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM games WHERE id = ?", (game_id,))
        game = cur.fetchone()
        if not game:
            raise ValueError("Game not found")
        if game["status"] == "ENDED":
            raise ValueError("Game is already concluded")
        conn.execute(
            "UPDATE games SET status = 'ENDED', winner_id = NULL, winner_type = 'ADMIN_TERMINATED', winner_details = ?, ended_at = ? WHERE id = ?",
            (reason, now_iso, game_id)
        )
        append_event_to_ledger(
            conn=conn,
            game_id=game_id,
            event_type="GAME_TERMINATED",
            payload={"reason": reason, "terminated_by": admin_user_id},
            timestamp_ns=now_ns,
            created_at_iso=now_iso,
            initiator_user_id=admin_user_id,
            visibility="PUBLIC"
        )


def award_playtest_ap(game_id: str, admin_user_id: str, amount: int = 1) -> None:
    """Award AP to all living players in a playtest match."""
    now_iso = datetime.now(timezone.utc).isoformat()
    now_ns = int(datetime.now(timezone.utc).timestamp() * 1e9)
    with db_transaction() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM games WHERE id = ?", (game_id,))
        game = cur.fetchone()
        if not game or game["status"] != "ACTIVE":
            raise ValueError("Game is not active")
        if not game["is_playtest"]:
            raise ValueError("AP award is only available in Playtest mode")
        conn.execute("UPDATE players SET ap = ap + ? WHERE game_id = ? AND is_alive = 1", (amount, game_id))
        append_event_to_ledger(
            conn=conn,
            game_id=game_id,
            event_type="PLAYTEST_AP_AWARDED",
            payload={"amount": amount, "admin_user_id": admin_user_id},
            timestamp_ns=now_ns,
            created_at_iso=now_iso,
            initiator_user_id=admin_user_id,
            visibility="PUBLIC"
        )


async def control_playtest_bot(
    game_id: str,
    admin_user_id: str,
    bot_player_id: str,
    actions: Optional[List[Dict[str, Any]]] = None,
    action_batch: Optional[Any] = None,
    expected_seq: Optional[int] = None
) -> Dict[str, Any]:
    """Admin executes actions on behalf of a bot in a playtest game."""
    if actions is None and action_batch is not None:
        if isinstance(action_batch, dict):
            actions = action_batch.get("actions", [])
        elif isinstance(action_batch, list):
            actions = action_batch
    if actions is None:
        actions = []

    from app.engine.queue import submit_action_to_queue
    game = query_one("SELECT * FROM games WHERE id = ?", (game_id,))
    if not game or not game["is_playtest"]:
        raise ValueError("Bot control is only permitted in Playtest matches")
    bot = query_one("SELECT * FROM players WHERE id = ? AND game_id = ?", (bot_player_id, game_id))
    if not bot:
        raise ValueError("Target bot player not found")

    # Support conference petition voting and active conference ballots for bots
    if actions and len(actions) == 1:
        first_act = actions[0]
        act_type = first_act.get("action_type") if isinstance(first_act, dict) else getattr(first_act, "action_type", None)
        if act_type in ("VOTE_CONFERENCE", "VOTE_ARMISTICE"):
            vote_res = toggle_armistice_vote(game_id, bot["user_id"])
            return {"success": True, "message": f"Bot @{bot['username']} conference vote toggled", "result": vote_res}
        elif act_type == "CONFERENCE_AGREE":
            vote_res = cast_conference_vote(game_id, bot["user_id"], True)
            return {"success": True, "message": f"Bot @{bot['username']} voted AGREE in conference", "result": vote_res}
        elif act_type == "CONFERENCE_REJECT":
            vote_res = cast_conference_vote(game_id, bot["user_id"], False)
            return {"success": True, "message": f"Bot @{bot['username']} voted REJECT in conference", "result": vote_res}

    return await submit_action_to_queue(
        game_id=game_id,
        initiator_user_id=bot["user_id"],
        actions=actions,
        expected_seq=expected_seq,
        as_player_id=bot_player_id
    )


def get_board_diff_since_last_visit(game_id: str, user_id: str) -> Dict[str, Any]:
    """Calculate 'What Changed Since Last Visit' diff, ghost trails, and offline events."""
    player = query_one("SELECT * FROM players WHERE game_id = ? AND user_id = ?", (game_id, user_id))
    last_active = player["last_active_at"] if player else None

    if not last_active:
        return {"last_active_timestamp": None, "ghost_trails": [], "offline_events": [], "casualty_sites": []}

    # Fetch events that occurred since last active timestamp
    events = query_db(
        "SELECT * FROM events WHERE game_id = ? AND created_at > ? ORDER BY event_seq ASC",
        (game_id, last_active)
    )

    ghost_trails = []
    offline_events = []
    casualty_sites = []

    for ev in events:
        payload = json.loads(ev["payload_json"])
        etype = ev["event_type"]

        if etype == "MOVE":
            ghost_trails.append({
                "player_id": payload["player_id"],
                "username": payload["username"],
                "from_x": payload["from_x"],
                "from_y": payload["from_y"],
                "to_x": payload["to_x"],
                "to_y": payload["to_y"],
                "timestamp": ev["created_at"]
            })
        elif etype == "ELIMINATION":
            casualty_sites.append({
                "victim_username": payload.get("victim_username"),
                "x": payload.get("x"),
                "y": payload.get("y"),
                "timestamp": ev["created_at"]
            })
            offline_events.append({
                "type": "ELIMINATION",
                "message": f"Player {payload.get('victim_username')} was eliminated.",
                "timestamp": ev["created_at"]
            })
        elif etype in ("COMBAT_HIT", "GIVE_AP") and ev["target_user_id"] == user_id:
            offline_events.append({
                "type": etype,
                "message": "You were targeted while offline.",
                "timestamp": ev["created_at"]
            })

    # Update player's last active timestamp to now
    now_iso = datetime.now(timezone.utc).isoformat()
    if player:
        with db_transaction() as conn:
            conn.execute("UPDATE players SET last_active_at = ? WHERE id = ?", (now_iso, player["id"]))

    return {
        "last_active_timestamp": last_active,
        "ghost_trails": ghost_trails,
        "offline_events": offline_events,
        "casualty_sites": casualty_sites
    }


def reconstruct_board_at_sequence(game_id: str, max_event_seq: int) -> Dict[str, Any]:
    """Reconstruct exact board layout at a historical event_seq for the replay scrubber."""
    game = query_one("SELECT * FROM games WHERE id = ?", (game_id,))
    cfg_row = query_one("SELECT * FROM game_parameters WHERE game_id = ?", (game_id,))
    cfg = dict(cfg_row) if cfg_row else {}
    board_width = cfg.get("board_width", 15)
    board_height = cfg.get("board_height", 15)

    events = query_db(
        "SELECT * FROM events WHERE game_id = ? AND event_seq <= ? ORDER BY event_seq ASC",
        (game_id, max_event_seq)
    )

    # Initial positions and attributes joining players and users
    players = query_db(
        """
        SELECT p.id, p.user_id, p.username, p.spawn_x, p.spawn_y, p.range_level, p.is_bot,
               u.national_flag, u.avatar_path
        FROM players p
        JOIN users u ON p.user_id = u.id
        WHERE p.game_id = ?
        """,
        (game_id,)
    )
    board_state: Dict[str, Dict[str, Any]] = {}
    for p in players:
        board_state[p["id"]] = {
            "id": p["id"],
            "user_id": p["user_id"],
            "username": p["username"],
            "national_flag": p["national_flag"] or "UN",
            "avatar_path": p["avatar_path"],
            "x": p["spawn_x"],
            "y": p["spawn_y"],
            "range_level": p["range_level"],
            "is_alive": True,
            "is_bot": bool(p["is_bot"])
        }

    current_shrink = 0
    for ev in events:
        etype = ev["event_type"]
        payload = json.loads(ev["payload_json"])

        if etype == "MOVE":
            pid = payload.get("player_id")
            if pid in board_state:
                board_state[pid]["x"] = payload["to_x"]
                board_state[pid]["y"] = payload["to_y"]
        elif etype == "ELIMINATION":
            vid = payload.get("victim_id")
            if vid in board_state:
                board_state[vid]["is_alive"] = False
        elif etype == "UPGRADE_RANGE":
            pid = payload.get("player_id")
            if not pid and ev["initiator_user_id"]:
                for p_id, p_data in board_state.items():
                    if p_data["user_id"] == ev["initiator_user_id"]:
                        pid = p_id
                        break
            if pid in board_state and "new_range_level" in payload:
                board_state[pid]["range_level"] = payload["new_range_level"]
        elif etype == "STAGNATION_SUDDEN_DEATH":
            current_shrink = payload.get("new_shrink_level", current_shrink)

    return {
        "event_seq": max_event_seq,
        "board_width": board_width,
        "board_height": board_height,
        "current_shrink_level": current_shrink,
        "status": game["status"] if game else "ACTIVE",
        "players": list(board_state.values())
    }


# =============================================================================
# V9: Recreate Lobby with Same Players & Custom Settings
# =============================================================================

def recreate_game_lobby(
    source_game_id: str,
    user_id: str,
    name: Optional[str] = None,
    config: Optional[LobbyConfig] = None,
    is_playtest: Optional[bool] = None
) -> Dict[str, Any]:
    """Recreate a match lobby with custom settings while automatically re-enlisting players from the source lobby."""
    source_game = query_one("SELECT * FROM games WHERE id = ?", (source_game_id,))
    if not source_game:
        raise ValueError("Source lobby not found")

    caller = query_one("SELECT is_admin FROM users WHERE id = ?", (user_id,))
    is_admin = bool(caller and caller["is_admin"])
    if source_game["created_by"] != user_id and not is_admin:
        raise PermissionError("Only the lobby creator or an administrator can recreate this lobby")

    if config is None:
        src_params = query_one("SELECT * FROM game_parameters WHERE game_id = ?", (source_game_id,))
        if src_params:
            config = LobbyConfig(**{k: src_params[k] for k in src_params.keys() if k in LobbyConfig.model_fields})
        else:
            config = LobbyConfig()

    lobby_name = name or f"{source_game['name']} (Rematch)"
    playtest = is_playtest if is_playtest is not None else bool(source_game["is_playtest"])

    # Create new lobby with updated settings
    new_game_id = create_game_lobby(
        creator_user_id=user_id,
        name=lobby_name,
        config=config,
        is_playtest=playtest
    )

    # Fetch all players from source lobby
    source_players = query_db(
        "SELECT user_id, username, is_bot FROM players WHERE game_id = ? ORDER BY id ASC",
        (source_game_id,)
    )

    re_enlisted_count = 0
    for sp in source_players:
        p_uid = sp["user_id"]
        if p_uid == user_id:
            # Creator is already auto-joined by create_game_lobby
            re_enlisted_count += 1
            continue

        if sp["is_bot"]:
            add_dummy_player(new_game_id, username=sp["username"])
            re_enlisted_count += 1
        else:
            try:
                join_game_lobby(new_game_id, p_uid)
                re_enlisted_count += 1
                create_in_app_notification(
                    user_id=p_uid,
                    event_type="LOBBY_RECREATED",
                    title_en=f"Lobby Recreated: {lobby_name}",
                    title_ru=f"Лобби пересоздано: {lobby_name}",
                    message_en=f"The match host has recreated {source_game['name']}. You have been automatically enlisted in {lobby_name}.",
                    message_ru=f"Хост матча пересоздал {source_game['name']}. Вы были автоматически добавлены в {lobby_name}.",
                    game_id=new_game_id
                )
            except Exception:
                pass

    return {
        "game_id": new_game_id,
        "new_game_id": new_game_id,
        "name": lobby_name,
        "players_count": re_enlisted_count,
        "players_transferred_count": re_enlisted_count,
        "status": "WAITING"
    }


# =============================================================================
# V9: Late Join System (Up to 3 Hours Before First AP Drop + Host Approval)
# =============================================================================

def get_first_ap_drop_cutoff(game_id: str) -> Tuple[bool, str, Optional[str]]:
    """Determine if late join is currently available (up to 3 hours before first AP drop)."""
    game = query_one("SELECT * FROM games WHERE id = ?", (game_id,))
    if not game:
        return False, "Match not found", None
    if game["status"] != "ACTIVE":
        return False, "Match is not currently in ACTIVE state", None

    cfg = query_one("SELECT * FROM game_parameters WHERE game_id = ?", (game_id,))
    if not cfg or not cfg["allow_late_join"]:
        return False, "Late join is not permitted in this match's settings", None

    # Check if any AP drop has already occurred
    prior_drop = query_one(
        "SELECT id FROM events WHERE game_id = ? AND event_type = 'DROP_AP' LIMIT 1",
        (game_id,)
    )
    if prior_drop:
        return False, "First AP drop has already concluded. Late join window is closed.", None

    if not game["started_at"]:
        return False, "Match start time not established", None

    try:
        started_dt = datetime.fromisoformat(game["started_at"].replace("Z", "+00:00")).astimezone(get_server_timezone())
        first_drop_days = cfg["first_ap_drop_days"] if ("first_ap_drop_days" in cfg.keys() and cfg["first_ap_drop_days"] is not None) else 1
        server_time_str = cfg["ap_drop_server_time"] or "22:00"
        parts = server_time_str.split(":")
        hour = int(parts[0]) if len(parts) > 0 else 22
        minute = int(parts[1]) if len(parts) > 1 else 0

        target_date = (started_dt + timedelta(days=first_drop_days)).date()
        first_drop_dt = datetime(target_date.year, target_date.month, target_date.day, hour, minute, tzinfo=get_server_timezone())
        if first_drop_dt <= started_dt:
            first_drop_dt += timedelta(days=1)

        cutoff_dt = first_drop_dt - timedelta(hours=3)
        now_server = get_server_now()

        if now_server >= cutoff_dt:
            return False, "Late join cutoff reached (3 hours before first AP drop)", cutoff_dt.isoformat()
        return True, "Late join is open", cutoff_dt.isoformat()
    except Exception as e:
        return False, f"Error calculating late join cutoff: {str(e)}", None


def create_join_request(game_id: str, user_id: str) -> Dict[str, Any]:
    """Submit a late join request for host approval."""
    allowed, reason, cutoff_iso = get_first_ap_drop_cutoff(game_id)
    if not allowed:
        raise ValueError(reason)

    existing_player = query_one("SELECT id FROM players WHERE game_id = ? AND user_id = ?", (game_id, user_id))
    if existing_player:
        raise ValueError("You are already an enlisted player in this match")

    existing_req = query_one(
        "SELECT * FROM game_join_requests WHERE game_id = ? AND user_id = ? AND status = 'PENDING'",
        (game_id, user_id)
    )
    if existing_req:
        raise ValueError("You already have a pending join request for this match")

    user = query_one("SELECT username FROM users WHERE id = ?", (user_id,))
    if not user:
        raise ValueError("User account not found")

    game = query_one("SELECT name, created_by FROM games WHERE id = ?", (game_id,))
    req_id = f"req_{uuid.uuid4().hex[:10]}"
    now_iso = datetime.now(timezone.utc).isoformat()

    with db_transaction() as conn:
        conn.execute(
            """
            INSERT INTO game_join_requests (id, game_id, user_id, username, status, created_at)
            VALUES (?, ?, ?, ?, 'PENDING', ?)
            """,
            (req_id, game_id, user_id, user["username"], now_iso)
        )

    # Notify lobby host outside transaction
    try:
        create_in_app_notification(
            user_id=game["created_by"],
            event_type="JOIN_REQUEST_RECEIVED",
            title_en="Late Join Request Received",
            title_ru="Получен запрос на присоединение",
            message_en=f"@{user['username']} has requested late deployment to {game['name']}. Review and approve in match settings.",
            message_ru=f"@{user['username']} запросил позднее присоединение к {game['name']}. Рассмотрите заявку в настройках матча.",
            game_id=game_id
        )
    except Exception:
        pass

    return {
        "id": req_id,
        "game_id": game_id,
        "user_id": user_id,
        "username": user["username"],
        "status": "PENDING",
        "created_at": now_iso,
        "cutoff_at": cutoff_iso
    }


def get_game_join_requests(game_id: str) -> List[Dict[str, Any]]:
    """Retrieve all pending and processed join requests for a game."""
    rows = query_db(
        """
        SELECT r.*, u.national_flag, u.avatar_path
        FROM game_join_requests r
        JOIN users u ON r.user_id = u.id
        WHERE r.game_id = ?
        ORDER BY r.created_at DESC
        """,
        (game_id,)
    )
    return [dict(r) for r in rows]


def approve_join_request(game_id: str, request_id: str, host_user_id: str) -> Dict[str, Any]:
    """Approve a late join request and spawn the new player on the active grid."""
    game = query_one("SELECT * FROM games WHERE id = ?", (game_id,))
    if not game:
        raise ValueError("Match not found")

    caller = query_one("SELECT is_admin FROM users WHERE id = ?", (host_user_id,))
    is_admin = bool(caller and caller["is_admin"])
    if game["created_by"] != host_user_id and not is_admin:
        raise PermissionError("Only the lobby host or an administrator can approve join requests")

    allowed, reason, _ = get_first_ap_drop_cutoff(game_id)
    if not allowed:
        raise ValueError(reason)

    req = query_one("SELECT * FROM game_join_requests WHERE id = ? AND game_id = ?", (request_id, game_id))
    if not req or req["status"] != "PENDING":
        raise ValueError("Join request not found or already resolved")

    user_id = req["user_id"]
    cfg = query_one("SELECT * FROM game_parameters WHERE game_id = ?", (game_id,))
    starting_hp = cfg["starting_hp"] if cfg else 3
    starting_ap = cfg["starting_ap"] if cfg else 0
    now_iso = datetime.now(timezone.utc).isoformat()
    now_ns = int(datetime.now(timezone.utc).timestamp() * 1e9)

    with db_transaction() as conn:
        sx, sy = find_unique_spawn_coordinate(conn, game_id, cfg["board_width"], cfg["board_height"])
        player_id = f"p_{uuid.uuid4().hex[:10]}"

        conn.execute(
            """
            INSERT INTO players (
                id, game_id, user_id, username, x, y, hp, ap, range_level,
                is_alive, armistice_vote, last_active_at, spawn_x, spawn_y
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 1, 0, ?, ?, ?)
            """,
            (player_id, game_id, user_id, req["username"], sx, sy, starting_hp, starting_ap, now_iso, sx, sy)
        )

        conn.execute(
            "UPDATE game_join_requests SET status = 'APPROVED', resolved_at = ? WHERE id = ?",
            (now_iso, request_id)
        )

        append_event_to_ledger(
            conn=conn,
            game_id=game_id,
            event_type="PLAYER_JOINED",
            payload={"player_id": player_id, "username": req["username"], "spawn_x": sx, "spawn_y": sy, "late_join": True},
            timestamp_ns=now_ns,
            created_at_iso=now_iso,
            initiator_user_id=user_id,
            visibility="PUBLIC"
        )

    try:
        create_in_app_notification(
            user_id=user_id,
            event_type="JOIN_REQUEST_APPROVED",
            title_en="Join Request Approved!",
            title_ru="Запрос на присоединение одобрен!",
            message_en=f"Your request to join {game['name']} was approved. You have deployed at sector ({sx}, {sy}).",
            message_ru=f"Ваш запрос на присоединение к {game['name']} одобрен. Вы высадились в секторе ({sx}, {sy}).",
            game_id=game_id
        )
    except Exception:
        pass

    return {
        "status": "APPROVED",
        "player_id": player_id,
        "user_id": user_id,
        "username": req["username"],
        "spawn_x": sx,
        "spawn_y": sy
    }


def deny_join_request(game_id: str, request_id: str, host_user_id: str) -> Dict[str, Any]:
    """Decline a late join request."""
    game = query_one("SELECT * FROM games WHERE id = ?", (game_id,))
    if not game:
        raise ValueError("Match not found")

    caller = query_one("SELECT is_admin FROM users WHERE id = ?", (host_user_id,))
    is_admin = bool(caller and caller["is_admin"])
    if game["created_by"] != host_user_id and not is_admin:
        raise PermissionError("Only the lobby host or an administrator can decline join requests")

    req = query_one("SELECT * FROM game_join_requests WHERE id = ? AND game_id = ?", (request_id, game_id))
    if not req or req["status"] != "PENDING":
        raise ValueError("Join request not found or already resolved")

    now_iso = datetime.now(timezone.utc).isoformat()
    with db_transaction() as conn:
        conn.execute(
            "UPDATE game_join_requests SET status = 'DENIED', resolved_at = ? WHERE id = ?",
            (now_iso, request_id)
        )

    try:
        create_in_app_notification(
            user_id=req["user_id"],
            event_type="JOIN_REQUEST_DENIED",
            title_en="Join Request Declined",
            title_ru="Запрос на присоединение отклонен",
            message_en=f"Your request to join {game['name']} was declined by the lobby host.",
            message_ru=f"Ваш запрос на присоединение к {game['name']} был отклонен хостом лобби.",
            game_id=game_id
        )
    except Exception:
        pass

    return {"status": "DENIED", "request_id": request_id}


# =============================================================================
# V9: Ranked Choice Voting (RCV) for 4 Map Placement Options
# =============================================================================

def get_map_voting_state(game_id: str, user_id: Optional[str] = None) -> Dict[str, Any]:
    """Retrieve candidate map placement options, current voter rankings, and voting timer."""
    game = query_one("SELECT * FROM games WHERE id = ?", (game_id,))
    if not game:
        raise ValueError("Match not found")

    options_rows = query_db(
        "SELECT * FROM game_map_options WHERE game_id = ? ORDER BY option_idx ASC",
        (game_id,)
    )

    options = []
    for r in options_rows:
        p_data = json.loads(r["placements_json"])
        if isinstance(p_data, dict):
            p_list = list(p_data.values())
        else:
            p_list = p_data
        options.append({
            "option_idx": r["option_idx"],
            "label": r["option_label"],
            "placements": p_list
        })

    user_vote = None
    if user_id:
        v_row = query_one("SELECT ranks_json FROM game_map_votes WHERE game_id = ? AND user_id = ?", (game_id, user_id))
        if v_row:
            user_vote = json.loads(v_row["ranks_json"])

    total_players = query_one("SELECT COUNT(*) as cnt FROM players WHERE game_id = ?", (game_id,))["cnt"]
    voted_count = query_one("SELECT COUNT(DISTINCT user_id) as cnt FROM game_map_votes WHERE game_id = ?", (game_id,))["cnt"]

    return {
        "game_id": game_id,
        "status": game["status"],
        "map_voting_ends_at": game["map_voting_ends_at"] if "map_voting_ends_at" in game.keys() else None,
        "options": options,
        "user_vote": user_vote,
        "total_players": total_players,
        "voted_count": voted_count
    }


def cast_map_vote(game_id: str, user_id: str, ranks: List[int]) -> Dict[str, Any]:
    """Cast or update a player's ranked-choice ballot for deployment map placements."""
    game = query_one("SELECT status FROM games WHERE id = ?", (game_id,))
    if not game or game["status"] != "MAP_VOTING":
        raise ValueError("Match is not in MAP_VOTING phase")

    player = query_one("SELECT id FROM players WHERE game_id = ? AND user_id = ?", (game_id, user_id))
    if not player:
        raise PermissionError("Only enlisted players in this lobby can vote on map placements")

    # Validate ranks contain integers 1..4 without duplicates
    cleaned_ranks = []
    for r in ranks:
        try:
            ri = int(r)
            if 1 <= ri <= 4 and ri not in cleaned_ranks:
                cleaned_ranks.append(ri)
        except Exception:
            pass

    if len(cleaned_ranks) == 0:
        raise ValueError("Ballot must contain at least one valid candidate ranking")

    now_iso = datetime.now(timezone.utc).isoformat()
    should_resolve = False
    with db_transaction() as conn:
        conn.execute(
            """
            INSERT INTO game_map_votes (game_id, user_id, ranks_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(game_id, user_id) DO UPDATE SET ranks_json=excluded.ranks_json, updated_at=excluded.updated_at
            """,
            (game_id, user_id, json.dumps(cleaned_ranks), now_iso)
        )

        # Check if all players have voted
        total = conn.execute("SELECT COUNT(*) as c FROM players WHERE game_id = ?", (game_id,)).fetchone()[0]
        voted = conn.execute("SELECT COUNT(DISTINCT user_id) as c FROM game_map_votes WHERE game_id = ?", (game_id,)).fetchone()[0]
        if total > 0 and voted >= total:
            should_resolve = True

    if should_resolve:
        resolve_map_rcv_vote(game_id)

    return get_map_voting_state(game_id, user_id)


def resolve_map_rcv_vote(game_id: str, initiator_user_id: Optional[str] = None, forced: bool = False) -> Dict[str, Any]:
    """Resolve Ranked Choice Voting (RCV / Instant Runoff) across candidate maps and transition match to ACTIVE."""
    game = query_one("SELECT * FROM games WHERE id = ?", (game_id,))
    if not game:
        raise ValueError("Match not found")
    if game["status"] != "MAP_VOTING":
        ev = query_one("SELECT payload_json FROM events WHERE game_id = ? AND event_type = 'GAME_STARTED' ORDER BY id DESC LIMIT 1", (game_id,))
        win_idx = 1
        if ev and ev["payload_json"]:
            try:
                p = json.loads(ev["payload_json"])
                win_idx = p.get("map_rcv_winner", 1)
            except Exception:
                pass
        return {
            "status": game["status"],
            "already_resolved": True,
            "winning_option_idx": win_idx,
            "winner_option_idx": win_idx
        }

    if initiator_user_id:
        caller = query_one("SELECT is_admin FROM users WHERE id = ?", (initiator_user_id,))
        is_admin = bool(caller and caller["is_admin"])
        if game["created_by"] != initiator_user_id and not is_admin:
            raise PermissionError("Only the lobby creator or an administrator can resolve the vote early")

    opt_rows = query_db("SELECT option_idx, placements_json FROM game_map_options WHERE game_id = ? ORDER BY option_idx ASC", (game_id,))
    if not opt_rows:
        raise ValueError("No map placement options found for this match")

    ballot_rows = query_db("SELECT ranks_json FROM game_map_votes WHERE game_id = ?", (game_id,))
    ballots: List[List[int]] = []
    for br in ballot_rows:
        try:
            b = json.loads(br["ranks_json"])
            if isinstance(b, list) and b:
                ballots.append([int(x) for x in b if isinstance(x, (int, str))])
        except Exception:
            pass

    # Instant Runoff Voting (RCV) Tally
    candidates = {r["option_idx"] for r in opt_rows}
    rounds: List[Dict[str, Any]] = []
    winner = None

    if not ballots:
        # Fallback if no ballots cast
        winner = 1
        rounds.append({"round": 1, "counts": {1: 0}, "active_ballots": 0, "winner": 1})
    else:
        while len(candidates) > 1:
            counts = {c: 0 for c in candidates}
            active_ballots = 0
            for ballot in ballots:
                for choice in ballot:
                    if choice in candidates:
                        counts[choice] += 1
                        active_ballots += 1
                        break

            rounds.append({"round": len(rounds) + 1, "counts": dict(counts), "active_ballots": active_ballots})

            if active_ballots == 0:
                winner = sorted(candidates)[0]
                rounds[-1]["winner"] = winner
                break

            # Check majority (> 50%)
            for c, tally in counts.items():
                if tally > active_ballots / 2.0:
                    winner = c
                    rounds[-1]["winner"] = winner
                    break

            if winner is not None:
                break

            # Eliminate candidate with fewest votes
            min_count = min(counts.values())
            tied_lowest = [c for c, count in counts.items() if count == min_count]
            if len(tied_lowest) == len(candidates):
                # Complete tie among all surviving candidates
                winner = sorted(candidates)[0]
                rounds[-1]["winner"] = winner
                break

            eliminated = sorted(tied_lowest)[0]
            candidates.remove(eliminated)
            rounds[-1]["eliminated"] = eliminated

        if winner is None and len(candidates) == 1:
            winner = list(candidates)[0]

    if winner is None:
        winner = 1

    # Apply winning layout
    winner_row = query_one(
        "SELECT placements_json FROM game_map_options WHERE game_id = ? AND option_idx = ?",
        (game_id, winner)
    )
    if not winner_row:
        winner_row = opt_rows[0]
        winner = winner_row["option_idx"]

    placements = json.loads(winner_row["placements_json"])
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    now_ns = int(now.timestamp() * 1e9)

    cfg = query_one("SELECT * FROM game_parameters WHERE game_id = ?", (game_id,))
    starting_hp = cfg["starting_hp"] if cfg else 3
    starting_ap = cfg["starting_ap"] if cfg else 0

    with db_transaction() as conn:
        for user_id, coords in placements.items():
            conn.execute(
                """
                UPDATE players
                SET x = ?, y = ?, spawn_x = ?, spawn_y = ?, hp = ?, ap = ?, is_alive = 1
                WHERE game_id = ? AND user_id = ?
                """,
                (coords["x"], coords["y"], coords["x"], coords["y"], starting_hp, starting_ap, game_id, user_id)
            )

        conn.execute(
            """
            UPDATE games
            SET status = 'ACTIVE', started_at = ?, last_combat_at = ?, map_voting_ends_at = NULL
            WHERE id = ?
            """,
            (now_iso, now_iso, game_id)
        )

        append_event_to_ledger(
            conn=conn,
            game_id=game_id,
            event_type="GAME_STARTED",
            payload={
                "map_rcv_winner": winner,
                "rcv_rounds": rounds,
                "player_count": len(placements)
            },
            timestamp_ns=now_ns,
            created_at_iso=now_iso,
            initiator_user_id=initiator_user_id,
            visibility="PUBLIC"
        )

        conn.execute(
            """
            INSERT INTO notifications (id, user_id, game_id, event_type, title_en, title_ru, message_en, message_ru, created_at)
            SELECT ('notif_' || lower(hex(randomblob(10)))), user_id, ?, 'MAP_VOTING_CONCLUDED', 'Map Placements Decided', 'Расстановка на карте определена',
                   ?, ?, ?
            FROM players WHERE game_id = ?
            """,
            (
                game_id,
                f"Ranked Choice Voting concluded! Winning placement: Option {winner}. The match is now ACTIVE.",
                f"Рейтинговое голосование завершено! Выбран вариант расстановки {winner}. Матч начался.",
                now_iso,
                game_id
            )
        )

    return {
        "status": "ACTIVE",
        "winning_option_idx": winner,
        "winner_option_idx": winner,
        "rcv_rounds": rounds,
        "started_at": now_iso
    }

