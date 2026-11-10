"""Background Task Runner for periodic AP drop, stagnation checks, and notifications."""
import asyncio
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List

from app.config import get_server_now, get_server_timezone
from app.database import db_transaction, query_db, query_one, init_db
from app.engine.ledger import append_event_to_ledger
from app.engine.win_conditions import evaluate_stagnation, evaluate_win_conditions, evaluate_diplomatic_conference
from app.services.notification_service import create_in_app_notification, ws_manager


async def process_ap_drops() -> None:
    """Award AP to all living players across active games according to schedule in server time."""
    now_server = get_server_now()
    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.isoformat()
    now_ns = int(now_utc.timestamp() * 1e9)
    today_str = now_server.strftime("%Y-%m-%d")
    current_time_str = now_server.strftime("%H:%M")

    active_games = query_db("SELECT id, started_at FROM games WHERE status = 'ACTIVE'")
    for g in active_games:
        game_id = g["id"]
        cfg = query_one(
            "SELECT ap_drop_amount, first_ap_drop_days, ap_drop_server_time FROM game_parameters WHERE game_id = ?",
            (game_id,)
        )
        if not cfg:
            continue

        drop_time = cfg["ap_drop_server_time"] or "22:00"
        # Check if the scheduled drop time for today has arrived
        if current_time_str < drop_time:
            continue

        # Check if already dropped today in server timezone
        last_drop = query_one(
            "SELECT created_at FROM events WHERE game_id = ? AND event_type = 'DROP_AP' ORDER BY event_seq DESC LIMIT 1",
            (game_id,)
        )
        if last_drop and last_drop["created_at"]:
            try:
                last_drop_dt = datetime.fromisoformat(last_drop["created_at"].replace("Z", "+00:00")).astimezone(get_server_timezone())
                if last_drop_dt.strftime("%Y-%m-%d") == today_str:
                    continue
            except Exception:
                if last_drop["created_at"].startswith(today_str):
                    continue

        amount = cfg["ap_drop_amount"] if cfg else 1
        first_drop_days = cfg["first_ap_drop_days"] if "first_ap_drop_days" in cfg.keys() else 1

        if g["started_at"]:
            try:
                start_dt = datetime.fromisoformat(g["started_at"])
                if now_utc < start_dt + timedelta(days=first_drop_days):
                    continue
            except Exception:
                pass

        with db_transaction() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, user_id, username, ap FROM players WHERE game_id = ? AND is_alive = 1", (game_id,))
            living_players = cur.fetchall()

            if living_players:
                cur.execute(
                    "UPDATE players SET ap = ap + ? WHERE game_id = ? AND is_alive = 1",
                    (amount, game_id)
                )

                append_event_to_ledger(
                    conn=conn,
                    game_id=game_id,
                    event_type="DROP_AP",
                    payload={"amount": amount, "affected_players_count": len(living_players)},
                    timestamp_ns=now_ns,
                    created_at_iso=now_iso,
                    visibility="PUBLIC"
                )

        # Notify players
        for p in living_players:
            create_in_app_notification(
                user_id=p["user_id"],
                event_type="DROP_AP",
                title_en="Daily AP Drop",
                title_ru="Ежедневная раздача AP",
                message_en=f"Daily AP distribution complete (+{amount} AP ready).",
                message_ru=f"Ежедневная раздача очков завершена (+{amount} AP начислено).",
                game_id=game_id
            )

        # Broadcast real-time WS alert
        await ws_manager.broadcast_to_game(game_id, {
            "type": "AP_DROP",
            "amount": amount,
            "timestamp": now_iso
        })


async def process_conference_expirations() -> None:
    """Evaluate diplomatic conferences whose countdown timer has concluded."""
    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.isoformat()
    now_ns = int(now_utc.timestamp() * 1e9)

    convened_games = query_db(
        "SELECT id, conference_ends_at FROM games WHERE status = 'ACTIVE' AND conference_status = 'CONVENED' AND conference_ends_at IS NOT NULL"
    )
    for g in convened_games:
        try:
            ends_dt = datetime.fromisoformat(g["conference_ends_at"].replace("Z", "+00:00"))
            if now_utc >= ends_dt:
                with db_transaction() as conn:
                    outcome = evaluate_diplomatic_conference(conn, g["id"], now_ns, now_iso)
                await ws_manager.broadcast_to_game(g["id"], {
                    "type": "CONFERENCE_RESOLVED",
                    "outcome": outcome
                })
        except Exception:
            pass


async def process_map_voting_expirations() -> None:
    """Evaluate map placement RCV voting whose timer has elapsed."""
    from app.services.game_service import resolve_map_rcv_vote
    now_utc = datetime.now(timezone.utc)
    voting_games = query_db(
        "SELECT id, map_voting_ends_at FROM games WHERE status = 'MAP_VOTING' AND map_voting_ends_at IS NOT NULL"
    )
    for g in voting_games:
        try:
            ends_dt = datetime.fromisoformat(g["map_voting_ends_at"].replace("Z", "+00:00"))
            if now_utc >= ends_dt:
                resolve_map_rcv_vote(g["id"])
                await ws_manager.broadcast_to_game(g["id"], {
                    "type": "MAP_VOTING_RESOLVED",
                    "status": "ACTIVE"
                })
        except Exception:
            pass


async def process_stagnation_checks() -> None:
    """Evaluate stagnation and sudden death border collapse for all active games."""
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    now_ns = int(now.timestamp() * 1e9)

    active_games = query_db("SELECT id FROM games WHERE status = 'ACTIVE'")
    for g in active_games:
        game_id = g["id"]
        with db_transaction() as conn:
            evaluate_stagnation(conn, game_id, now_ns, now_iso)


async def scheduler_loop(interval_seconds: int = 15) -> None:
    """Main background scheduler loop checking cron intervals, conference timers, and stagnation."""
    while True:
        try:
            await process_ap_drops()
            await process_conference_expirations()
            await process_map_voting_expirations()
            await process_stagnation_checks()
        except asyncio.CancelledError:
            break
        except Exception:
            pass

        await asyncio.sleep(interval_seconds)


if __name__ == "__main__":
    init_db()
    asyncio.run(scheduler_loop())
