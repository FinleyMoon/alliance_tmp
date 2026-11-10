"""Database backup and restore service for administrator maintenance."""
import json
from datetime import datetime, timezone
from typing import Dict, Any, Tuple
import sqlite3

from app.database import db_transaction, query_db, query_one

BACKUP_TABLES = [
    "users",
    "system_settings",
    "games",
    "game_parameters",
    "players",
    "events",
    "game_parameter_audit_logs",
    "invite_tokens",
    "notifications",
    "chat_messages"
]


def export_database_backup() -> Dict[str, Any]:
    """Generate comprehensive snapshot JSON dump of the entire database."""
    now_iso = datetime.now(timezone.utc).isoformat()
    backup_data: Dict[str, Any] = {
        "version": "3.0",
        "created_at": now_iso,
        "tables": {}
    }

    for table in BACKUP_TABLES:
        try:
            rows = query_db(f"SELECT * FROM {table}")
            backup_data["tables"][table] = [dict(r) for r in rows]
        except Exception:
            backup_data["tables"][table] = []

    backup_data["table_counts"] = {t: len(backup_data["tables"][t]) for t in BACKUP_TABLES}
    return backup_data


def restore_database_backup(backup_payload: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Restore database from backup payload inside an atomic transaction.
    Returns (success, message_or_error).
    """
    if not isinstance(backup_payload, dict) or "tables" not in backup_payload:
        return False, "ERR_INVALID_BACKUP_FORMAT: Missing tables root object"

    tables = backup_payload["tables"]
    if not isinstance(tables, dict):
        return False, "ERR_INVALID_BACKUP_FORMAT: Tables must be a dictionary"

    with db_transaction() as conn:
        conn.execute("PRAGMA foreign_keys = OFF;")
        try:
            for table in reversed(BACKUP_TABLES):
                conn.execute(f"DELETE FROM {table};")

            for table in BACKUP_TABLES:
                rows = tables.get(table, [])
                if not rows:
                    continue
                # Determine columns from first row
                cols = list(rows[0].keys())
                placeholders = ", ".join(["?"] * len(cols))
                col_names = ", ".join(cols)
                sql = f"INSERT OR REPLACE INTO {table} ({col_names}) VALUES ({placeholders})"

                params_list = [tuple(r.get(c) for c in cols) for r in rows]
                conn.executemany(sql, params_list)

            conn.execute("PRAGMA foreign_keys = ON;")
            return True, f"Database restored successfully from backup ({backup_payload.get('created_at', 'unknown date')})."
        except Exception as e:
            conn.execute("PRAGMA foreign_keys = ON;")
            return False, f"Restore failed: {str(e)}"
