"""Core action validation and execution pipeline."""
import sqlite3
import math
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple, Optional
from app.engine.range import is_in_range
from app.engine.ledger import append_event_to_ledger
from app.engine.win_conditions import evaluate_win_conditions
from app.models.game_types import ActionType


def calculate_upgrade_cost(current_level: int, base_cost: int, scale: float) -> int:
    """Calculate AP cost to upgrade from current_level to current_level + 1."""
    exponent = max(0, current_level - 1)
    cost = float(base_cost) * (scale ** exponent)
    return max(1, int(math.ceil(cost)))


def calculate_action_cost(action_type: str, current_range_level: int, cfg: sqlite3.Row) -> int:
    """Calculate AP cost for a single action command."""
    if action_type == ActionType.MOVE.value:
        return cfg["move_cost_ap"]
    elif action_type == ActionType.ATTACK.value:
        return cfg["attack_cost_ap"]
    elif action_type == ActionType.GIVE_AP.value:
        fee = cfg["give_ap_cost_ap"] if (cfg and "give_ap_cost_ap" in cfg.keys()) else 0
        return 1 + fee
    elif action_type == ActionType.UPGRADE_RANGE.value:
        return calculate_upgrade_cost(
            current_range_level,
            cfg["range_upgrade_cost_base"],
            cfg["range_upgrade_cost_scale"]
        )
    return 1


def execute_action_batch(
    conn: sqlite3.Connection,
    game_id: str,
    initiator_user_id: str,
    actions_raw: List[Dict[str, Any]],
    timestamp_ns: int,
    now_iso: str,
    expected_seq: Optional[int] = None,
    as_player_id: Optional[str] = None
) -> Tuple[bool, str, List[Dict[str, Any]], int]:
    """
    Execute a batch of actions inside an immediate transaction.
    Returns (success, error_code_or_msg, receipts, total_ap_spent).
    """
    cur = conn.cursor()

    # 1. Verify game status
    cur.execute("SELECT * FROM games WHERE id = ?", (game_id,))
    game = cur.fetchone()
    if not game or game["status"] != "ACTIVE":
        return False, "ERR_GAME_NOT_ACTIVE: Game is not in ACTIVE state", [], 0

    # 1a. Verify no active diplomatic conference or cooldown (combat and movement suspended during deliberations and resolution cooldown)
    if "conference_status" in game.keys() and game["conference_status"] in ("CONVENED", "COOLDOWN"):
        if game["conference_status"] == "COOLDOWN" and game["conference_ends_at"]:
            try:
                ends_dt = datetime.fromisoformat(game["conference_ends_at"])
                if datetime.now(timezone.utc) >= ends_dt:
                    cur.execute("UPDATE games SET conference_status = 'IDLE', conference_ends_at = NULL WHERE id = ?", (game_id,))
                    game = dict(game)
                    game["conference_status"] = "IDLE"
            except Exception:
                pass
        if game["conference_status"] in ("CONVENED", "COOLDOWN"):
            return False, "ERR_CONFERENCE_IN_SESSION: Tactical actions and combat are suspended while a Diplomatic Conference or resolution cooldown is active.", [], 0

    # 1b. Validate sequence synchronization if expected_seq is specified
    if expected_seq is not None:
        cur.execute("SELECT MAX(event_seq) as max_seq FROM events WHERE game_id = ?", (game_id,))
        ev_row = cur.fetchone()
        current_seq = ev_row["max_seq"] if (ev_row and ev_row["max_seq"]) else 0
        if expected_seq != current_seq:
            return False, f"ERR_STALE_STATE: Board sequence changed (client seq {expected_seq} != server seq {current_seq})", [], 0

    # 2. Verify game parameters
    cur.execute("SELECT * FROM game_parameters WHERE game_id = ?", (game_id,))
    cfg = cur.fetchone()
    if not cfg:
        return False, "ERR_CONFIG_NOT_FOUND", [], 0

    # 3. Verify initiator
    if as_player_id:
        cur.execute("SELECT * FROM players WHERE id = ? AND game_id = ?", (as_player_id, game_id))
    else:
        cur.execute("SELECT * FROM players WHERE game_id = ? AND user_id = ?", (game_id, initiator_user_id))
    initiator = cur.fetchone()
    if not initiator:
        return False, "ERR_PLAYER_NOT_IN_GAME", [], 0
    if initiator["is_alive"] != 1 or initiator["hp"] <= 0:
        return False, "ERR_PLAYER_DEAD: Dead players cannot perform actions", [], 0

    current_ap = initiator["ap"]
    current_x = initiator["x"]
    current_y = initiator["y"]
    current_range = initiator["range_level"]
    shrink = game["current_shrink_level"]
    burst_cap = cfg["burst_cap_ap"]

    # Calculate total AP required
    simulated_range = current_range
    total_cost = 0
    for act in actions_raw:
        cost = calculate_action_cost(act["action_type"], simulated_range, cfg)
        total_cost += cost
        if act["action_type"] == ActionType.UPGRADE_RANGE.value:
            simulated_range += 1

    # Check Burst Cap enforcement
    if total_cost > burst_cap:
        return False, f"ERR_BURST_CAP_EXCEEDED: Batch AP cost ({total_cost}) exceeds burst limit of {burst_cap} AP", [], 0

    # Check sufficient AP balance
    if current_ap < total_cost:
        return False, f"ERR_INSUFFICIENT_AP: Required {total_cost} AP, but initiator only has {current_ap} AP", [], 0

    # Validate all actions sequentially before state mutations
    receipts: List[Dict[str, Any]] = []
    ap_spent = 0

    for act in actions_raw:
        a_type = act["action_type"]

        if a_type == ActionType.MOVE.value:
            to_x = act.get("to_x")
            to_y = act.get("to_y")
            if to_x is None or to_y is None:
                return False, "ERR_INVALID_MOVE_COORDINATES", [], 0

            # Orthogonal adjacency check: dx + dy == 1
            if abs(to_x - current_x) + abs(to_y - current_y) != 1:
                return False, f"ERR_INVALID_MOVE: Tile ({to_x}, {to_y}) is not orthogonally adjacent to ({current_x}, {current_y})", [], 0

            # Boundary check
            min_x = 1 + shrink
            max_x = cfg["board_width"] - shrink
            min_y = 1 + shrink
            max_y = cfg["board_height"] - shrink
            if not (min_x <= to_x <= max_x and min_y <= to_y <= max_y):
                return False, f"ERR_INVALID_MOVE: Coordinate ({to_x}, {to_y}) is outside valid grid boundaries", [], 0

            # Check occupancy
            cur.execute(
                "SELECT id FROM players WHERE game_id = ? AND x = ? AND y = ? AND is_alive = 1",
                (game_id, to_x, to_y)
            )
            occ = cur.fetchone()
            if occ:
                return False, f"ERR_TILE_OCCUPIED: Tile ({to_x}, {to_y}) is already occupied by a living player", [], 0

            # Apply Move
            cost = cfg["move_cost_ap"]
            ap_spent += cost
            current_ap -= cost
            from_x, from_y = current_x, current_y
            current_x, current_y = to_x, to_y

            cur.execute(
                "UPDATE players SET x = ?, y = ?, ap = ?, last_active_at = ? WHERE id = ?",
                (current_x, current_y, current_ap, now_iso, initiator["id"])
            )

            # Record event in ledger
            append_event_to_ledger(
                conn=conn,
                game_id=game_id,
                event_type="MOVE",
                payload={
                    "player_id": initiator["id"],
                    "username": initiator["username"],
                    "from_x": from_x,
                    "from_y": from_y,
                    "to_x": to_x,
                    "to_y": to_y
                },
                timestamp_ns=timestamp_ns,
                created_at_iso=now_iso,
                initiator_user_id=initiator_user_id,
                visibility="PUBLIC"
            )

            # Movement alert to other players if their movement alert preference is enabled
            from app.services.notification_service import create_in_app_notification
            cur.execute("SELECT DISTINCT user_id FROM players WHERE game_id = ? AND user_id IS NOT NULL AND user_id != ?", (game_id, initiator_user_id))
            for other_u in cur.fetchall():
                create_in_app_notification(
                    user_id=other_u["user_id"],
                    event_type="PLAYER_MOVED",
                    title_en="Grid Maneuver",
                    title_ru="Перемещение на сетке",
                    message_en=f"@{initiator['username']} maneuvered to ({to_x}, {to_y}).",
                    message_ru=f"@{initiator['username']} переместился в ({to_x}, {to_y}).",
                    game_id=game_id
                )
            receipts.append({
                "action": "MOVE",
                "from": [from_x, from_y],
                "to": [to_x, to_y],
                "cost": cost
            })

        elif a_type == ActionType.ATTACK.value:
            target_id = act.get("target_player_id")
            if not target_id:
                return False, "ERR_MISSING_TARGET_ID", [], 0

            cur.execute("SELECT * FROM players WHERE id = ? AND game_id = ?", (target_id, game_id))
            target = cur.fetchone()
            if not target or target["is_alive"] != 1 or target["hp"] <= 0:
                return False, "ERR_INVALID_TARGET: Target is dead or does not exist", [], 0

            # Range Geometry check
            if not is_in_range((current_x, current_y), (target["x"], target["y"]), current_range):
                return False, "ERR_TARGET_OUT_OF_RANGE: Target is outside your interaction range", [], 0

            cost = cfg["attack_cost_ap"]
            ap_spent += cost
            current_ap -= cost

            new_target_hp = target["hp"] - 1
            is_fatal = (new_target_hp <= 0)

            cur.execute(
                "UPDATE players SET ap = ?, last_active_at = ? WHERE id = ?",
                (current_ap, now_iso, initiator["id"])
            )
            cur.execute("UPDATE games SET last_combat_at = ? WHERE id = ?", (now_iso, game_id))

            if is_fatal:
                # Elimination lifecycle
                cur.execute(
                    """
                    UPDATE players
                    SET hp = 0, ap = 0, is_alive = 0, eliminated_by = ?, eliminated_at = ?, last_active_at = ?
                    WHERE id = ?
                    """,
                    (initiator["username"], now_iso, now_iso, target["id"])
                )

                # Public death notice (hiding killer unless reveal_killer_on_death)
                public_payload = {
                    "victim_id": target["id"],
                    "victim_username": target["username"],
                    "x": target["x"],
                    "y": target["y"],
                    "is_fatal": True
                }
                if cfg["reveal_killer_on_death"]:
                    public_payload["killer_username"] = initiator["username"]

                append_event_to_ledger(
                    conn=conn,
                    game_id=game_id,
                    event_type="ELIMINATION",
                    payload=public_payload,
                    timestamp_ns=timestamp_ns,
                    created_at_iso=now_iso,
                    initiator_user_id=initiator_user_id,
                    target_user_id=target["user_id"],
                    visibility="PUBLIC"
                )

                # Global notification: victim receives notice with killer, everyone else receives alert concealing killer
                cur.execute("SELECT DISTINCT user_id FROM players WHERE game_id = ? AND user_id IS NOT NULL", (game_id,))
                all_game_users = [r["user_id"] for r in cur.fetchall()]
                for u_id in all_game_users:
                    if u_id == target["user_id"]:
                        notif_id = uuid.uuid4().hex
                        cur.execute(
                            """
                            INSERT INTO notifications (id, user_id, game_id, event_type, title_en, title_ru, message_en, message_ru, created_at)
                            VALUES (?, ?, ?, 'ELIMINATED', 'Player Eliminated', 'Игрок выбыл', ?, ?, ?)
                            """,
                            (
                                notif_id,
                                u_id,
                                game_id,
                                f"You were eliminated from the grid by @{initiator['username']}.",
                                f"Вы выбыли из игры (устранены игроком @{initiator['username']}).",
                                now_iso
                            )
                        )
                    else:
                        notif_id = uuid.uuid4().hex
                        cur.execute(
                            """
                            INSERT INTO notifications (id, user_id, game_id, event_type, title_en, title_ru, message_en, message_ru, created_at)
                            VALUES (?, ?, ?, 'PLAYER_ELIMINATED', 'Player Eliminated', 'Игрок выбыл', ?, ?, ?)
                            """,
                            (
                                notif_id,
                                u_id,
                                game_id,
                                f"@{target['username']} has been eliminated from the grid.",
                                f"@{target['username']} выбыл из игры.",
                                now_iso
                            )
                        )

                receipts.append({
                    "action": "ATTACK",
                    "target_id": target["id"],
                    "result": "FATAL",
                    "cost": cost
                })
            else:
                # Non-lethal hit: Strictly private receipts, public ledger gets NO event
                cur.execute(
                    "UPDATE players SET hp = ?, last_active_at = ? WHERE id = ?",
                    (new_target_hp, now_iso, target["id"])
                )

                # Attacker receipt: target HP is UNKNOWN
                receipts.append({
                    "action": "ATTACK",
                    "target_id": target["id"],
                    "result": "HIT",
                    "target_remaining_hp": "UNKNOWN",
                    "cost": cost
                })

                # Private alert for victim (Conceal shooter identity on non-lethal hits)
                notif_id = uuid.uuid4().hex
                cur.execute(
                    """
                    INSERT INTO notifications (id, user_id, game_id, event_type, title_en, title_ru, message_en, message_ru, created_at)
                    VALUES (?, ?, ?, 'UNDER_ATTACK', 'Under Attack', 'Под атакой', ?, ?, ?)
                    """,
                    (
                        notif_id,
                        target["user_id"],
                        game_id,
                        f"Under attack! 1 damage taken from an unknown shooter. Current HP: {new_target_hp}",
                        f"Вы атакованы неизвестным противником! Получен 1 урон. Текущее HP: {new_target_hp}",
                        now_iso
                    )
                )

                # Append private event to ledger
                append_event_to_ledger(
                    conn=conn,
                    game_id=game_id,
                    event_type="COMBAT_HIT",
                    payload={
                        "attacker_user_id": initiator_user_id,
                        "victim_user_id": target["user_id"],
                        "damage": 1
                    },
                    timestamp_ns=timestamp_ns,
                    created_at_iso=now_iso,
                    initiator_user_id=initiator_user_id,
                    target_user_id=target["user_id"],
                    visibility="PRIVATE"
                )

        elif a_type == ActionType.GIVE_AP.value:
            target_id = act.get("target_player_id")
            if not target_id:
                return False, "ERR_MISSING_TARGET_ID", [], 0

            cur.execute("SELECT * FROM players WHERE id = ? AND game_id = ?", (target_id, game_id))
            target = cur.fetchone()
            if not target or target["is_alive"] != 1:
                return False, "ERR_INVALID_TARGET: Target is dead or invalid", [], 0

            if not is_in_range((current_x, current_y), (target["x"], target["y"]), current_range):
                return False, "ERR_TARGET_OUT_OF_RANGE: Target is outside your interaction range", [], 0

            fee = cfg["give_ap_cost_ap"] if (cfg and "give_ap_cost_ap" in cfg.keys()) else 0
            cost = 1 + fee
            ap_spent += cost
            current_ap -= cost

            # Deduct from initiator, credit to target
            cur.execute(
                "UPDATE players SET ap = ?, last_active_at = ? WHERE id = ?",
                (current_ap, now_iso, initiator["id"])
            )
            cur.execute(
                "UPDATE players SET ap = ap + 1 WHERE id = ?",
                (target["id"],)
            )

            # Private notification to target
            notif_id = uuid.uuid4().hex
            cur.execute(
                """
                INSERT INTO notifications (id, user_id, game_id, event_type, title_en, title_ru, message_en, message_ru, created_at)
                VALUES (?, ?, ?, 'AP_RECEIVED', 'AP Gift Received', 'Получено AP', ?, ?, ?)
                """,
                (
                    notif_id,
                    target["user_id"],
                    game_id,
                    f"Received 1 AP from {initiator['username']}.",
                    f"Получено 1 AP от {initiator['username']}.",
                    now_iso
                )
            )

            # Private ledger entry
            append_event_to_ledger(
                conn=conn,
                game_id=game_id,
                event_type="GIVE_AP",
                payload={
                    "sender_id": initiator["id"],
                    "target_id": target["id"],
                    "amount": 1
                },
                timestamp_ns=timestamp_ns,
                created_at_iso=now_iso,
                initiator_user_id=initiator_user_id,
                target_user_id=target["user_id"],
                visibility="PRIVATE"
            )
            receipts.append({
                "action": "GIVE_AP",
                "target_id": target["id"],
                "amount": 1,
                "cost": cost
            })

        elif a_type == ActionType.UPGRADE_RANGE.value:
            cost = calculate_upgrade_cost(
                current_range,
                cfg["range_upgrade_cost_base"],
                cfg["range_upgrade_cost_scale"]
            )
            ap_spent += cost
            current_ap -= cost
            current_range += 1

            cur.execute(
                "UPDATE players SET range_level = ?, ap = ?, last_active_at = ? WHERE id = ?",
                (current_range, current_ap, now_iso, initiator["id"])
            )

            # Private ledger entry
            append_event_to_ledger(
                conn=conn,
                game_id=game_id,
                event_type="UPGRADE_RANGE",
                payload={"new_range_level": current_range, "cost": cost},
                timestamp_ns=timestamp_ns,
                created_at_iso=now_iso,
                initiator_user_id=initiator_user_id,
                visibility="PRIVATE"
            )
            receipts.append({
                "action": "UPGRADE_RANGE",
                "new_tier": current_range,
                "cost": cost
            })

    # Evaluate win conditions after applying entire action batch
    evaluate_win_conditions(conn, game_id, timestamp_ns, now_iso)

    return True, "SUCCESS", receipts, ap_spent
