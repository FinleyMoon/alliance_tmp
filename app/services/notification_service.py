"""In-app notification center and real-time WebSocket connection manager."""
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Set
import asyncio
import json

from app.database import db_transaction, query_db, query_one
from app.services.push_service import send_web_push_notification


DEFAULT_ALERT_PREFS = {
    "eliminations": True,
    "conference": True,
    "moves": False,
    "transfers": True,
    "browser_push": False
}


def get_user_alert_preferences(user_id: str) -> Dict[str, Any]:
    """Retrieve user alert preferences with fallback to system defaults."""
    row = query_one("SELECT alert_preferences FROM users WHERE id = ?", (user_id,))
    if not row or not row["alert_preferences"]:
        return dict(DEFAULT_ALERT_PREFS)
    try:
        data = json.loads(row["alert_preferences"])
        prefs = dict(DEFAULT_ALERT_PREFS)
        if isinstance(data, dict):
            prefs.update(data)
        return prefs
    except Exception:
        return dict(DEFAULT_ALERT_PREFS)


def update_user_alert_preferences(user_id: str, new_prefs: Dict[str, Any]) -> Dict[str, Any]:
    """Update user alert preferences JSON."""
    current = get_user_alert_preferences(user_id)
    current.update(new_prefs)
    with db_transaction() as conn:
        conn.execute("UPDATE users SET alert_preferences = ? WHERE id = ?", (json.dumps(current), user_id))
    return current


def create_in_app_notification(
    user_id: str,
    event_type: str,
    title_en: str,
    title_ru: str,
    message_en: str,
    message_ru: str,
    game_id: Optional[str] = None
) -> Optional[str]:
    """Insert a persistent notification into user's notification center and trigger Web Push, respecting user alert settings."""
    prefs = get_user_alert_preferences(user_id)
    if event_type == "PLAYER_MOVED" and not prefs.get("moves", False):
        return None
    if event_type == "PLAYER_ELIMINATED" and not prefs.get("eliminations", True):
        return None
    if event_type in ("CONFERENCE_CONVENED", "CONFERENCE_RESOLVED") and not prefs.get("conference", True):
        return None
    if event_type in ("AP_TRANSFER_SENT", "AP_TRANSFER_RECEIVED") and not prefs.get("transfers", True):
        return None

    notif_id = f"notif_{uuid.uuid4().hex[:12]}"
    now_iso = datetime.now(timezone.utc).isoformat()

    with db_transaction() as conn:
        conn.execute(
            """
            INSERT INTO notifications (
                id, user_id, game_id, event_type, title_en, title_ru,
                message_en, message_ru, is_read, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (notif_id, user_id, game_id, event_type, title_en, title_ru, message_en, message_ru, now_iso)
        )

    # Dispatch web push notification
    try:
        send_web_push_notification(user_id=user_id, title=title_en, body=message_en, data={"game_id": game_id})
    except Exception:
        pass

    # Dispatch real-time WebSocket event to all connected devices for this user
    try:
        loop = asyncio.get_running_loop()
        notif_msg = {
            "type": "NEW_NOTIFICATION",
            "notification": {
                "id": notif_id,
                "game_id": game_id,
                "event_type": event_type,
                "title_en": title_en,
                "title_ru": title_ru,
                "message_en": message_en,
                "message_ru": message_ru,
                "created_at": now_iso
            }
        }
        loop.create_task(ws_manager.send_to_user(user_id, notif_msg))
    except RuntimeError:
        pass

    return notif_id


def get_user_notifications(user_id: str, lang: str = "en", limit: int = 50) -> Dict[str, Any]:
    """Retrieve notifications with unread count badge for the persistent bell icon and associated lobby name."""
    rows = query_db(
        """
        SELECT n.id, n.game_id, n.event_type, n.title_en, n.title_ru, n.message_en, n.message_ru, n.is_read, n.created_at, g.name AS game_name
        FROM notifications n
        LEFT JOIN games g ON n.game_id = g.id
        WHERE n.user_id = ?
        ORDER BY n.created_at DESC
        LIMIT ?
        """,
        (user_id, limit)
    )

    unread_row = query_one(
        "SELECT COUNT(*) as unread_count FROM notifications WHERE user_id = ? AND is_read = 0",
        (user_id,)
    )
    unread_count = unread_row["unread_count"] if unread_row else 0

    items = []
    for r in rows:
        title = r["title_ru"] if lang == "ru" else r["title_en"]
        message = r["message_ru"] if lang == "ru" else r["message_en"]
        items.append({
            "id": r["id"],
            "game_id": r["game_id"],
            "game_name": r["game_name"] or "",
            "event_type": r["event_type"],
            "title": title,
            "message": message,
            "is_read": bool(r["is_read"]),
            "created_at": r["created_at"]
        })

    return {
        "unread_count": unread_count,
        "notifications": items
    }


def mark_notifications_read(user_id: str, notif_ids: Optional[List[str]] = None) -> None:
    """Mark specific or all notifications as read."""
    with db_transaction() as conn:
        if notif_ids:
            placeholders = ",".join("?" for _ in notif_ids)
            conn.execute(
                f"UPDATE notifications SET is_read = 1 WHERE user_id = ? AND id IN ({placeholders})",
                [user_id] + notif_ids
            )
        else:
            conn.execute("UPDATE notifications SET is_read = 1 WHERE user_id = ?", (user_id,))


# -------------------------------------------------------------------------
# Real-Time WebSocket Manager
# -------------------------------------------------------------------------
class GameWebSocketManager:
    """Manages active WebSocket connections per game lobby and user session."""

    def __init__(self):
        # game_id -> set of websocket connection objects
        self.game_rooms: Dict[str, Set[Any]] = {}
        # user_id -> set of websocket connection objects
        self.user_sockets: Dict[str, Set[Any]] = {}

    def connect(self, game_id: str, user_id: str, websocket: Any) -> None:
        if game_id not in self.game_rooms:
            self.game_rooms[game_id] = set()
        self.game_rooms[game_id].add(websocket)

        if user_id not in self.user_sockets:
            self.user_sockets[user_id] = set()
        self.user_sockets[user_id].add(websocket)

    def disconnect(self, game_id: str, user_id: str, websocket: Any) -> None:
        if game_id in self.game_rooms:
            self.game_rooms[game_id].discard(websocket)
            if not self.game_rooms[game_id]:
                self.game_rooms.pop(game_id, None)

        if user_id in self.user_sockets:
            self.user_sockets[user_id].discard(websocket)
            if not self.user_sockets[user_id]:
                self.user_sockets.pop(user_id, None)

    async def broadcast_to_game(self, game_id: str, message: Dict[str, Any]) -> None:
        """Broadcast payload to all active clients in a game room."""
        if game_id not in self.game_rooms:
            return
        payload_text = json.dumps(message)
        dead_sockets = []
        for ws in self.game_rooms[game_id]:
            try:
                await ws.send_text(payload_text)
            except Exception:
                dead_sockets.append(ws)
        for ws in dead_sockets:
            self.game_rooms[game_id].discard(ws)

    async def send_to_user(self, user_id: str, message: Dict[str, Any]) -> None:
        """Send private payload directly to an authorized user session."""
        if user_id not in self.user_sockets:
            return
        payload_text = json.dumps(message)
        dead_sockets = []
        for ws in self.user_sockets[user_id]:
            try:
                await ws.send_text(payload_text)
            except Exception:
                dead_sockets.append(ws)
        for ws in dead_sockets:
            self.user_sockets[user_id].discard(ws)


ws_manager = GameWebSocketManager()
