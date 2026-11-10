"""Public parameter audit log service for game integrity tracking."""
import sqlite3
from datetime import datetime, timezone
from typing import Dict, Any, List
from app.database import db_transaction, query_db


def update_game_parameters_with_audit(
    game_id: str,
    admin_user_id: str,
    admin_username: str,
    updates: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """
    Update game parameters mid-game and record audit trail entries in game_parameter_audit_logs.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    recorded_logs: List[Dict[str, Any]] = []

    with db_transaction() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM game_parameters WHERE game_id = ?", (game_id,))
        current_cfg = cur.fetchone()
        if not current_cfg:
            raise ValueError("Game parameters not found")

        for key, new_val in updates.items():
            if key in current_cfg.keys() and key != "game_id":
                old_val = str(current_cfg[key])
                new_val_str = str(new_val)

                if old_val != new_val_str:
                    # Update column in table
                    cur.execute(
                        f"UPDATE game_parameters SET {key} = ? WHERE game_id = ?",
                        (new_val, game_id)
                    )

                    # Insert immutable audit entry
                    cur.execute(
                        """
                        INSERT INTO game_parameter_audit_logs (
                            game_id, param_key, old_value, new_value,
                            admin_user_id, admin_username, timestamp
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (game_id, key, old_val, new_val_str, admin_user_id, admin_username, now_iso)
                    )

                    recorded_logs.append({
                        "param_key": key,
                        "old_value": old_val,
                        "new_value": new_val_str,
                        "admin_username": admin_username,
                        "timestamp": now_iso
                    })

    return recorded_logs


def get_game_audit_logs(game_id: str) -> List[Dict[str, Any]]:
    """Retrieve full immutable audit log for Integrity & Settings Changelog tab."""
    rows = query_db(
        """
        SELECT id, game_id, param_key, old_value, new_value, admin_username, timestamp
        FROM game_parameter_audit_logs
        WHERE game_id = ?
        ORDER BY id DESC
        """,
        (game_id,)
    )
    return [dict(r) for r in rows]
