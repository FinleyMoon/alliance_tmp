"""Multi-Win Condition Engine: Last Man, Armistice, Rogue, and Stagnation."""
import sqlite3
import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List, Tuple
from app.engine.ledger import append_event_to_ledger
from app.models.game_types import RogueResolutionMode, StagnationMode


def evaluate_win_conditions(
    conn: sqlite3.Connection,
    game_id: str,
    timestamp_ns: int,
    now_iso: str
) -> Optional[Dict[str, Any]]:
    """
    Evaluate all simultaneous win condition modules.
    Returns result dict if game ended or special rogue effect resolved, else None.
    """
    cur = conn.cursor()

    # Load game status and config
    cur.execute("SELECT * FROM games WHERE id = ?", (game_id,))
    game_row = cur.fetchone()
    if not game_row or game_row["status"] != "ACTIVE":
        return None

    # If a diplomatic conference is currently convened, win condition evaluations are suspended
    if "conference_status" in game_row.keys() and game_row["conference_status"] == "CONVENED":
        return None

    cur.execute("SELECT * FROM game_parameters WHERE game_id = ?", (game_id,))
    cfg = cur.fetchone()
    if not cfg:
        return None

    # Load all players
    cur.execute("SELECT * FROM players WHERE game_id = ?", (game_id,))
    players = cur.fetchall()

    living_players = [p for p in players if p["is_alive"] == 1]
    living_count = len(living_players)

    # 1. Check Last Man Standing
    if cfg["win_cond_last_man"] and len(players) > 1 and living_count <= 1:
        winner_id = living_players[0]["id"] if living_count == 1 else None
        winner_user = living_players[0]["username"] if living_count == 1 else "No Survivors"
        details = f"Last surviving player: {winner_user}"

        cur.execute(
            """
            UPDATE games
            SET status = 'ENDED', winner_id = ?, winner_type = 'LAST_MAN',
                winner_details = ?, ended_at = ?
            WHERE id = ?
            """,
            (winner_id, details, now_iso, game_id)
        )

        append_event_to_ledger(
            conn=conn,
            game_id=game_id,
            event_type="GAME_ENDED",
            payload={
                "winner_id": winner_id,
                "winner_username": winner_user,
                "condition": "LAST_MAN",
                "details": details
            },
            timestamp_ns=timestamp_ns,
            created_at_iso=now_iso,
            visibility="PUBLIC"
        )
        return {"ended": True, "condition": "LAST_MAN", "winner_id": winner_id, "winner_username": winner_user}

    # 2. Check Armistice & Rogue Rule
    if living_count >= 2:
        peace_voters = [p for p in living_players if p["armistice_vote"] == 1]
        voters_count = len(peace_voters)
        armistice_ratio = voters_count / living_count

        # Check Unanimous Armistice
        if cfg["win_cond_armistice"] and armistice_ratio >= cfg["armistice_threshold"]:
            details = f"Unanimous peace treaty passed by all {living_count} surviving players"
            cur.execute(
                """
                UPDATE games
                SET status = 'ENDED', winner_id = NULL, winner_type = 'ARMISTICE',
                    winner_details = ?, ended_at = ?
                WHERE id = ?
                """,
                (details, now_iso, game_id)
            )

            living_names = [p["username"] for p in living_players]
            append_event_to_ledger(
                conn=conn,
                game_id=game_id,
                event_type="GAME_ENDED",
                payload={
                    "winner_type": "ARMISTICE",
                    "survivors": living_names,
                    "condition": "ARMISTICE",
                    "details": details
                },
                timestamp_ns=timestamp_ns,
                created_at_iso=now_iso,
                visibility="PUBLIC"
            )
            return {"ended": True, "condition": "ARMISTICE", "survivors": living_names}

        # Check Rogue / Traitor Rule: living >= 3, exactly N - 1 voted peace, 1 holdout
        if cfg["win_cond_rogue"] and living_count >= 3 and voters_count == (living_count - 1):
            rogue = [p for p in living_players if p["armistice_vote"] == 0][0]
            mode = cfg["rogue_resolution_mode"]

            if mode == "INSTANT_WIN":
                details = f"Rogue sabotage victory: {rogue['username']} betrayed the peace alliance!"
                cur.execute(
                    """
                    UPDATE games
                    SET status = 'ENDED', winner_id = ?, winner_type = 'ROGUE',
                        winner_details = ?, ended_at = ?
                    WHERE id = ?
                    """,
                    (rogue["id"], details, now_iso, game_id)
                )
                append_event_to_ledger(
                    conn=conn,
                    game_id=game_id,
                    event_type="GAME_ENDED",
                    payload={
                        "winner_id": rogue["id"],
                        "winner_username": rogue["username"],
                        "condition": "ROGUE_BETRAYAL",
                        "details": details
                    },
                    timestamp_ns=timestamp_ns,
                    created_at_iso=now_iso,
                    visibility="PUBLIC"
                )
                return {"ended": True, "condition": "ROGUE", "winner_id": rogue["id"], "winner_username": rogue["username"]}

            elif mode == "GRANT_AP":
                bonus = cfg["rogue_bonus_ap"]
                cur.execute(
                    "UPDATE players SET ap = ap + ? WHERE id = ?",
                    (bonus, rogue["id"])
                )
                append_event_to_ledger(
                    conn=conn,
                    game_id=game_id,
                    event_type="ROGUE_AP_GRANT",
                    payload={
                        "rogue_player_id": rogue["id"],
                        "rogue_username": rogue["username"],
                        "bonus_ap": bonus
                    },
                    timestamp_ns=timestamp_ns,
                    created_at_iso=now_iso,
                    initiator_user_id=rogue["user_id"],
                    visibility="PUBLIC"
                )
                return {"ended": False, "condition": "ROGUE_GRANT_AP", "rogue_username": rogue["username"], "bonus_ap": bonus}

            elif mode == "REVEAL_IDENTITY":
                append_event_to_ledger(
                    conn=conn,
                    game_id=game_id,
                    event_type="ROGUE_IDENTITY_REVEAL",
                    payload={
                        "rogue_player_id": rogue["id"],
                        "rogue_username": rogue["username"],
                        "message": f"Diplomatic impasse: {rogue['username']} is the lone holdout refusing peace."
                    },
                    timestamp_ns=timestamp_ns,
                    created_at_iso=now_iso,
                    visibility="PUBLIC"
                )
                return {"ended": False, "condition": "ROGUE_REVEAL", "rogue_username": rogue["username"]}

    return None


def evaluate_stagnation(
    conn: sqlite3.Connection,
    game_id: str,
    timestamp_ns: int,
    now_iso: str
) -> Optional[Dict[str, Any]]:
    """
    Check if game has stalled with no combat for stagnation_days_limit.
    """
    cur = conn.cursor()
    cur.execute("SELECT last_combat_at, started_at, current_shrink_level, status FROM games WHERE id = ?", (game_id,))
    game = cur.fetchone()
    if not game or game["status"] != "ACTIVE":
        return None

    cur.execute("SELECT win_cond_stagnation, stagnation_days_limit, stagnation_mode, board_width, board_height FROM game_parameters WHERE game_id = ?", (game_id,))
    cfg = cur.fetchone()
    if not cfg or not cfg["win_cond_stagnation"]:
        return None

    reference_time_str = game["last_combat_at"] or game["started_at"]
    if not reference_time_str:
        return None

    ref_time = datetime.fromisoformat(reference_time_str)
    now_time = datetime.fromisoformat(now_iso)
    stagnation_limit_delta = timedelta(days=cfg["stagnation_days_limit"])

    if now_time - ref_time >= stagnation_limit_delta:
        if cfg["stagnation_mode"] == "DRAW":
            details = f"Game terminated as an Automatic Draw due to {cfg['stagnation_days_limit']} days without combat."
            cur.execute(
                "UPDATE games SET status = 'ENDED', winner_id = NULL, winner_type = 'STAGNATION_DRAW', winner_details = ?, ended_at = ? WHERE id = ?",
                (details, now_iso, game_id)
            )
            append_event_to_ledger(
                conn=conn,
                game_id=game_id,
                event_type="GAME_ENDED",
                payload={"condition": "STAGNATION_DRAW", "details": details},
                timestamp_ns=timestamp_ns,
                created_at_iso=now_iso,
                visibility="PUBLIC"
            )
            return {"ended": True, "condition": "STAGNATION_DRAW"}

        elif cfg["stagnation_mode"] == "SUDDEN_DEATH":
            new_shrink = game["current_shrink_level"] + 1
            cur.execute(
                "UPDATE games SET current_shrink_level = ?, last_combat_at = ? WHERE id = ?",
                (new_shrink, now_iso, game_id)
            )

            # Eliminate any players caught outside the new shrunk boundary
            min_bound = 1 + new_shrink
            max_bound_x = cfg["board_width"] - new_shrink
            max_bound_y = cfg["board_height"] - new_shrink

            cur.execute(
                """
                UPDATE players
                SET is_alive = 0, eliminated_at = ?, eliminated_by = 'SUDDEN_DEATH_GRID_COLLAPSE'
                WHERE game_id = ? AND is_alive = 1 AND (x < ? OR x > ? OR y < ? OR y > ?)
                """,
                (now_iso, game_id, min_bound, max_bound_x, min_bound, max_bound_y)
            )

            append_event_to_ledger(
                conn=conn,
                game_id=game_id,
                event_type="SUDDEN_DEATH_SHRINK",
                payload={
                    "new_shrink_level": new_shrink,
                    "min_x": min_bound,
                    "max_x": max_bound_x,
                    "min_y": min_bound,
                    "max_y": max_bound_y
                },
                timestamp_ns=timestamp_ns,
                created_at_iso=now_iso,
                visibility="PUBLIC"
            )
            # Re-evaluate win conditions if collapse eliminated players
            return evaluate_win_conditions(conn, game_id, timestamp_ns, now_iso)

    return None


def evaluate_diplomatic_conference(
    conn: sqlite3.Connection,
    game_id: str,
    timestamp_ns: int,
    now_iso: str
) -> Dict[str, Any]:
    """
    Evaluate outcome of an active or concluded Diplomatic Conference:
    1. Everyone agrees -> ALL_SURVIVE_WIN (Armistice collective victory).
    2. Exactly 1 disagrees -> Rogue outcome (GRANT_AP, INSTANT_WIN, REVEAL_IDENTITY).
    3. >1 disagree -> Third outcome (RESUME_COMBAT, PUNISH_DISAGREERS, DRAW).
    """
    cur = conn.cursor()
    cur.execute("SELECT * FROM games WHERE id = ?", (game_id,))
    game = cur.fetchone()
    if not game or game["status"] != "ACTIVE":
        return {"status": "INACTIVE"}

    cur.execute("SELECT * FROM game_parameters WHERE game_id = ?", (game_id,))
    cfg = cur.fetchone()

    cur.execute("SELECT * FROM players WHERE game_id = ? AND is_alive = 1", (game_id,))
    living = cur.fetchall()
    living_count = len(living)

    agreed = [p for p in living if p["conference_vote"] == 1]
    disagreed = [p for p in living if p["conference_vote"] != 1]

    agree_count = len(agreed)
    disagree_count = len(disagreed)

    outcome_data = {
        "game_id": game_id,
        "living_count": living_count,
        "agree_count": agree_count,
        "disagree_count": disagree_count,
        "ended": False
    }

    # Case 1: Everyone agrees
    if disagree_count == 0:
        details = f"Unanimous diplomatic consensus! All {living_count} surviving players share collective victory."
        cur.execute(
            """
            UPDATE games
            SET status = 'ENDED', winner_id = NULL, winner_type = 'ARMISTICE',
                winner_details = ?, ended_at = ?, conference_status = 'RESOLVED', conference_outcome = 'ALL_AGREE'
            WHERE id = ?
            """,
            (details, now_iso, game_id)
        )
        living_names = [p["username"] for p in living]
        append_event_to_ledger(
            conn=conn,
            game_id=game_id,
            event_type="GAME_ENDED",
            payload={
                "winner_type": "ARMISTICE",
                "survivors": living_names,
                "condition": "ARMISTICE",
                "details": details
            },
            timestamp_ns=timestamp_ns,
            created_at_iso=now_iso,
            visibility="PUBLIC"
        )
        cur.execute("SELECT DISTINCT user_id FROM players WHERE game_id = ? AND user_id IS NOT NULL", (game_id,))
        for p_row in cur.fetchall():
            cur.execute(
                """
                INSERT INTO notifications (id, user_id, game_id, event_type, title_en, title_ru, message_en, message_ru, created_at)
                VALUES (?, ?, ?, 'CONFERENCE_RESOLVED', 'Conference Concluded: Peace Accord', 'Итоги конференции: Мирный договор', ?, ?, ?)
                """,
                (uuid.uuid4().hex, p_row["user_id"], game_id, details, details, now_iso)
            )
        outcome_data.update({
            "ended": True,
            "outcome": "ALL_AGREE",
            "condition": "ARMISTICE",
            "survivors": living_names,
            "message": details
        })
        return outcome_data

def _parse_outcomes_list(val: Any, default_single: str) -> List[str]:
    """Parse JSON array or comma-separated list of selected outcomes."""
    if not val:
        return [default_single]
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str):
        val_str = val.strip()
        if val_str.startswith("[") and val_str.endswith("]"):
            try:
                parsed = json.loads(val_str)
                if isinstance(parsed, list):
                    return [str(x).strip() for x in parsed if str(x).strip()]
            except Exception:
                pass
        parts = [p.strip() for p in val_str.split(",") if p.strip()]
        if parts:
            return parts
    return [default_single]


def _apply_conference_dissent_consequences(
    conn: sqlite3.Connection,
    game_id: str,
    dissenters: List[sqlite3.Row],
    outcomes: List[str],
    bonus_ap: int,
    is_single: bool,
    timestamp_ns: int,
    now_iso: str,
    outcome_data: Dict[str, Any]
) -> Dict[str, Any]:
    cur = conn.cursor()
    applied_effects: List[str] = []
    dissenter_names = [d["username"] for d in dissenters]
    dissenter_ids = [d["id"] for d in dissenters]

    # 1. REVEAL_IDENTITY
    if "REVEAL_IDENTITY" in outcomes:
        applied_effects.append("REVEAL_IDENTITY")
        append_event_to_ledger(
            conn=conn,
            game_id=game_id,
            event_type="ROGUE_IDENTITY_REVEAL" if is_single else "CONFERENCE_DISSENTERS_REVEALED",
            payload={
                "player_ids": dissenter_ids,
                "usernames": dissenter_names,
                "message": f"Dissenting holdout: {dissenter_names[0]} rejected peace." if is_single else f"Dissenting players: {', '.join(dissenter_names)} rejected peace."
            },
            timestamp_ns=timestamp_ns,
            created_at_iso=now_iso,
            visibility="PUBLIC"
        )

    # 2. GRANT_AP
    if "GRANT_AP" in outcomes:
        applied_effects.append("GRANT_AP")
        for d in dissenters:
            cur.execute("UPDATE players SET ap = ap + ? WHERE id = ?", (bonus_ap, d["id"]))
        append_event_to_ledger(
            conn=conn,
            game_id=game_id,
            event_type="ROGUE_AP_GRANT",
            payload={
                "player_ids": dissenter_ids,
                "usernames": dissenter_names,
                "bonus_ap": bonus_ap
            },
            timestamp_ns=timestamp_ns,
            created_at_iso=now_iso,
            visibility="PUBLIC"
        )

    # 3. PUNISH_DISAGREERS
    if "PUNISH_DISAGREERS" in outcomes:
        applied_effects.append("PUNISH_DISAGREERS")
        for d in dissenters:
            new_hp = max(0, d["hp"] - 1)
            is_alive = 1 if new_hp > 0 else 0
            cur.execute(
                "UPDATE players SET hp = ?, is_alive = ?, eliminated_at = ? WHERE id = ?",
                (new_hp, is_alive, (now_iso if is_alive == 0 else None), d["id"])
            )
        append_event_to_ledger(
            conn=conn,
            game_id=game_id,
            event_type="CONFERENCE_COLLAPSED",
            payload={"disagree_count": len(dissenters), "penalty": "1_HP"},
            timestamp_ns=timestamp_ns,
            created_at_iso=now_iso,
            visibility="PUBLIC"
        )

    # 4. INSTANT_WIN
    if "INSTANT_WIN" in outcomes:
        applied_effects.append("INSTANT_WIN")
        winner_id = dissenters[0]["id"] if is_single else None
        details = (
            f"Rogue betrayal victory! {dissenter_names[0]} claimed sole victory by sabotaging peace!"
            if is_single else
            f"Dissenters ({', '.join(dissenter_names)}) claimed collective betrayal victory!"
        )
        cur.execute(
            """
            UPDATE games
            SET status = 'ENDED', winner_id = ?, winner_type = 'ROGUE',
                winner_details = ?, ended_at = ?, conference_status = 'RESOLVED', conference_outcome = 'INSTANT_WIN'
            WHERE id = ?
            """,
            (winner_id, details, now_iso, game_id)
        )
        append_event_to_ledger(
            conn=conn,
            game_id=game_id,
            event_type="GAME_ENDED",
            payload={
                "winner_id": winner_id,
                "winner_type": "ROGUE",
                "condition": "ROGUE_BETRAYAL",
                "dissenters": dissenter_names,
                "details": details
            },
            timestamp_ns=timestamp_ns,
            created_at_iso=now_iso,
            visibility="PUBLIC"
        )
        cur.execute("SELECT DISTINCT user_id FROM players WHERE game_id = ? AND user_id IS NOT NULL", (game_id,))
        for p_row in cur.fetchall():
            cur.execute(
                """
                INSERT INTO notifications (id, user_id, game_id, event_type, title_en, title_ru, message_en, message_ru, created_at)
                VALUES (?, ?, ?, 'CONFERENCE_RESOLVED', 'Conference Concluded: Betrayal', 'Итоги конференции: Предательство', ?, ?, ?)
                """,
                (uuid.uuid4().hex, p_row["user_id"], game_id, details, details, now_iso)
            )
        outcome_data.update({
            "ended": True,
            "outcome": "ONE_DISAGREES" if is_single else "MULTI_DISAGREE",
            "condition": "ROGUE_BETRAYAL" if is_single else "INSTANT_WIN",
            "dissenters": dissenter_names,
            "winner_id": winner_id,
            "applied_effects": applied_effects,
            "message": details
        })
        return outcome_data

    # 5. DRAW
    if "DRAW" in outcomes:
        applied_effects.append("DRAW")
        details = f"Diplomatic conference collapsed. Match terminated in an Automatic Draw."
        cur.execute(
            """
            UPDATE games
            SET status = 'ENDED', winner_id = NULL, winner_type = 'DRAW',
                winner_details = ?, ended_at = ?, conference_status = 'RESOLVED', conference_outcome = 'DRAW'
            WHERE id = ?
            """,
            (details, now_iso, game_id)
        )
        append_event_to_ledger(
            conn=conn,
            game_id=game_id,
            event_type="GAME_ENDED",
            payload={"winner_type": "DRAW", "condition": "DRAW", "details": details},
            timestamp_ns=timestamp_ns,
            created_at_iso=now_iso,
            visibility="PUBLIC"
        )
        cur.execute("SELECT DISTINCT user_id FROM players WHERE game_id = ? AND user_id IS NOT NULL", (game_id,))
        for p_row in cur.fetchall():
            cur.execute(
                """
                INSERT INTO notifications (id, user_id, game_id, event_type, title_en, title_ru, message_en, message_ru, created_at)
                VALUES (?, ?, ?, 'CONFERENCE_RESOLVED', 'Conference Concluded: Draw', 'Итоги конференции: Ничья', ?, ?, ?)
                """,
                (uuid.uuid4().hex, p_row["user_id"], game_id, details, details, now_iso)
            )
        outcome_data.update({
            "ended": True,
            "outcome": "ONE_DISAGREES" if is_single else "MULTI_DISAGREE",
            "condition": "DRAW",
            "applied_effects": applied_effects,
            "message": details
        })
        return outcome_data

    # 6. Non-terminal: Resume combat with 10-second resolution cooldown
    if "RESUME_COMBAT" not in applied_effects:
        applied_effects.append("RESUME_COMBAT")

    try:
        base_dt = datetime.fromisoformat(now_iso) if isinstance(now_iso, str) else datetime.now(timezone.utc)
    except Exception:
        base_dt = datetime.now(timezone.utc)
    cooldown_ends = (base_dt + timedelta(seconds=10)).isoformat()

    res_data = {
        "is_single": is_single,
        "dissenters": dissenter_names,
        "applied_effects": applied_effects,
        "bonus_ap": bonus_ap if "GRANT_AP" in applied_effects else 0,
        "awarded_user_ids": [d["user_id"] for d in dissenters if d["user_id"]] if "GRANT_AP" in applied_effects else [],
        "agree_count": outcome_data.get("agree_count", 0),
        "disagree_count": len(dissenters),
        "living_count": outcome_data.get("living_count", 0)
    }

    cur.execute(
        "UPDATE games SET conference_status = 'COOLDOWN', conference_ends_at = ?, conference_outcome = ?, conference_resolution_data = ? WHERE id = ?",
        (cooldown_ends, "+".join(applied_effects), json.dumps(res_data), game_id)
    )
    # Clear armistice petition but preserve conference_vote so SHOW_ALL_VOTES can display breakdown during COOLDOWN
    cur.execute("UPDATE players SET armistice_vote = 0 WHERE game_id = ?", (game_id,))

    details = (
        f"Conference concluded with 1 dissenter ({dissenter_names[0]}). Consequences applied: {', '.join(applied_effects)}. Resuming in 10s cooldown."
        if is_single else
        f"Conference collapsed with {len(dissenters)} dissenters. Consequences applied: {', '.join(applied_effects)}. Resuming in 10s cooldown."
    )
    append_event_to_ledger(
        conn=conn,
        game_id=game_id,
        event_type="CONFERENCE_COLLAPSED",
        payload={
            "disagree_count": len(dissenters),
            "applied_effects": applied_effects,
            "action": "RESUME_COMBAT",
            "cooldown_ends_at": cooldown_ends
        },
        timestamp_ns=timestamp_ns,
        created_at_iso=now_iso,
        visibility="PUBLIC"
    )

    cur.execute("SELECT DISTINCT user_id FROM players WHERE game_id = ? AND user_id IS NOT NULL", (game_id,))
    for p_row in cur.fetchall():
        cur.execute(
            """
            INSERT INTO notifications (id, user_id, game_id, event_type, title_en, title_ru, message_en, message_ru, created_at)
            VALUES (?, ?, ?, 'CONFERENCE_RESOLVED', 'Conference Results', 'Итоги конференции', ?, ?, ?)
            """,
            (uuid.uuid4().hex, p_row["user_id"], game_id, details, details, now_iso)
        )

    primary_cond = applied_effects[0]
    if is_single and primary_cond == "GRANT_AP":
        primary_cond = "ROGUE_GRANT_AP"
    elif is_single and primary_cond == "REVEAL_IDENTITY":
        primary_cond = "ROGUE_REVEAL"

    outcome_data.update({
        "ended": False,
        "outcome": "ONE_DISAGREES" if is_single else "MULTI_DISAGREE",
        "condition": primary_cond,
        "bonus_ap": bonus_ap if "GRANT_AP" in applied_effects else 0,
        "applied_effects": applied_effects,
        "dissenters": dissenter_names,
        "rogue_username": dissenter_names[0] if is_single else None,
        "message": details
    })
    evaluate_win_conditions(conn, game_id, timestamp_ns, now_iso)
    return outcome_data


def evaluate_diplomatic_conference(
    conn: sqlite3.Connection,
    game_id: str,
    timestamp_ns: int,
    now_iso: str
) -> Dict[str, Any]:
    """
    Evaluate outcome of an active or concluded Diplomatic Conference:
    1. Everyone agrees -> ALL_SURVIVE_WIN (Armistice collective victory).
    2. Exactly 1 disagrees -> Configured consequences for 1 dissenter.
    3. >1 disagree -> Configured consequences for multi dissenters.
    """
    cur = conn.cursor()
    cur.execute("SELECT * FROM games WHERE id = ?", (game_id,))
    game = cur.fetchone()
    if not game or game["status"] != "ACTIVE":
        return {"status": "INACTIVE"}

    cur.execute("SELECT * FROM game_parameters WHERE game_id = ?", (game_id,))
    cfg = cur.fetchone()

    cur.execute("SELECT * FROM players WHERE game_id = ? AND is_alive = 1", (game_id,))
    living = cur.fetchall()
    living_count = len(living)

    agreed = [p for p in living if p["conference_vote"] == 1]
    disagreed = [p for p in living if p["conference_vote"] != 1]

    agree_count = len(agreed)
    disagree_count = len(disagreed)

    outcome_data = {
        "game_id": game_id,
        "living_count": living_count,
        "agree_count": agree_count,
        "disagree_count": disagree_count,
        "ended": False
    }

    # Case 1: Everyone agrees (100% Consensus)
    if disagree_count == 0:
        details = f"Unanimous diplomatic consensus! All {living_count} surviving players share collective victory."
        cur.execute(
            """
            UPDATE games
            SET status = 'ENDED', winner_id = NULL, winner_type = 'ARMISTICE',
                winner_details = ?, ended_at = ?, conference_status = 'RESOLVED', conference_outcome = 'ALL_AGREE'
            WHERE id = ?
            """,
            (details, now_iso, game_id)
        )
        living_names = [p["username"] for p in living]
        append_event_to_ledger(
            conn=conn,
            game_id=game_id,
            event_type="GAME_ENDED",
            payload={
                "winner_type": "ARMISTICE",
                "survivors": living_names,
                "condition": "ARMISTICE",
                "details": details
            },
            timestamp_ns=timestamp_ns,
            created_at_iso=now_iso,
            visibility="PUBLIC"
        )
        outcome_data.update({
            "ended": True,
            "outcome": "ALL_AGREE",
            "condition": "ARMISTICE",
            "survivors": living_names,
            "message": details
        })
        return outcome_data

    # Case 2: Exactly 1 player disagrees
    elif disagree_count == 1:
        default_mode = cfg["rogue_resolution_mode"] if (cfg and "rogue_resolution_mode" in cfg.keys()) else "GRANT_AP"
        raw_outcomes = cfg["conference_single_disagree_outcomes"] if (cfg and "conference_single_disagree_outcomes" in cfg.keys()) else None
        outcomes = _parse_outcomes_list(raw_outcomes, default_mode)
        bonus_ap = cfg["rogue_bonus_ap"] if (cfg and "rogue_bonus_ap" in cfg.keys()) else 3

        return _apply_conference_dissent_consequences(
            conn=conn,
            game_id=game_id,
            dissenters=disagreed,
            outcomes=outcomes,
            bonus_ap=bonus_ap,
            is_single=True,
            timestamp_ns=timestamp_ns,
            now_iso=now_iso,
            outcome_data=outcome_data
        )

    # Case 3: More than 1 player disagrees
    else:
        default_multi = cfg["armistice_outcome_multi_disagree"] if (cfg and "armistice_outcome_multi_disagree" in cfg.keys()) else "RESUME_COMBAT"
        raw_outcomes = cfg["conference_multi_disagree_outcomes"] if (cfg and "conference_multi_disagree_outcomes" in cfg.keys()) else None
        outcomes = _parse_outcomes_list(raw_outcomes, default_multi)
        bonus_ap = cfg["multi_disagree_bonus_ap"] if (cfg and "multi_disagree_bonus_ap" in cfg.keys()) else 2

        return _apply_conference_dissent_consequences(
            conn=conn,
            game_id=game_id,
            dissenters=disagreed,
            outcomes=outcomes,
            bonus_ap=bonus_ap,
            is_single=False,
            timestamp_ns=timestamp_ns,
            now_iso=now_iso,
            outcome_data=outcome_data
        )

