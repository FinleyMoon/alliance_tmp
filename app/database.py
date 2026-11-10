"""Database access layer with SQLite WAL mode and serialized transactions."""
import sqlite3
import threading
import contextlib
import json
from datetime import datetime, timezone
from typing import Generator, Any, Dict, List, Optional
from app.config import DB_PATH

# Process-level serialized writer lock for SQLite atomic transactions
_write_lock = threading.RLock()


def get_db_connection() -> sqlite3.Connection:
    """Create and configure a SQLite connection with WAL mode."""
    conn = sqlite3.connect(
        str(DB_PATH),
        timeout=30.0,
        check_same_thread=False,
        isolation_level=None  # autocommit mode, transactions handled explicitly
    )
    conn.row_factory = sqlite3.Row
    # Configure SQLite for high performance and durability
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 10000;")
    return conn


@contextlib.contextmanager
def db_transaction() -> Generator[sqlite3.Connection, None, None]:
    """Context manager for immediate, atomic serialized write transactions."""
    with _write_lock:
        conn = get_db_connection()
        try:
            conn.execute("BEGIN IMMEDIATE TRANSACTION;")
            yield conn
            try:
                conn.execute("COMMIT;")
            except sqlite3.OperationalError as e:
                # In case an internal command already committed the active transaction
                if "no transaction is active" not in str(e).lower():
                    raise
        except Exception:
            try:
                conn.execute("ROLLBACK;")
            except Exception:
                pass
            raise
        finally:
            conn.close()


def query_db(query: str, params: tuple = ()) -> List[sqlite3.Row]:
    """Execute a read query and return rows."""
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(query, params)
        return cur.fetchall()
    finally:
        conn.close()


def query_one(query: str, params: tuple = ()) -> Optional[sqlite3.Row]:
    """Execute a read query and return a single row or None."""
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(query, params)
        return cur.fetchone()
    finally:
        conn.close()


def init_db() -> None:
    """Initialize database tables and indexes."""
    # executescript() automatically issues and manages its own commits
    with _write_lock:
        conn = get_db_connection()
        try:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                national_flag TEXT NOT NULL DEFAULT 'UN',
                avatar_path TEXT,
                preferred_lang TEXT NOT NULL DEFAULT 'en',
                is_admin INTEGER NOT NULL DEFAULT 0,
                is_bot INTEGER NOT NULL DEFAULT 0,
                alert_preferences TEXT DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS games (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'WAITING', -- WAITING, ACTIVE, PAUSED, ENDED
                created_by TEXT NOT NULL,
                winner_id TEXT,
                winner_type TEXT, -- LAST_MAN, ARMISTICE, ROGUE, DRAW, MAX_TURN
                winner_details TEXT,
                current_shrink_level INTEGER NOT NULL DEFAULT 0,
                last_combat_at TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                ended_at TEXT,
                conference_status TEXT NOT NULL DEFAULT 'IDLE', -- IDLE, CONVENED, RESOLVED
                conference_ends_at TEXT,
                conference_triggered_at TEXT,
                conference_outcome TEXT,
                conference_resolution_data TEXT,
                map_voting_ends_at TEXT,
                is_playtest INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(created_by) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS game_parameters (
                game_id TEXT PRIMARY KEY,
                board_width INTEGER NOT NULL DEFAULT 15,
                board_height INTEGER NOT NULL DEFAULT 15,
                starting_hp INTEGER NOT NULL DEFAULT 3,
                starting_ap INTEGER NOT NULL DEFAULT 0,
                starting_range_level INTEGER NOT NULL DEFAULT 1,
                ap_drop_cron TEXT NOT NULL DEFAULT '0 22 * * *',
                ap_drop_timezone TEXT NOT NULL DEFAULT 'UTC',
                ap_drop_amount INTEGER NOT NULL DEFAULT 1,
                ap_drop_server_time TEXT NOT NULL DEFAULT '22:00',
                burst_cap_ap INTEGER NOT NULL DEFAULT 3,
                move_cost_ap INTEGER NOT NULL DEFAULT 1,
                attack_cost_ap INTEGER NOT NULL DEFAULT 1,
                give_ap_cost_ap INTEGER NOT NULL DEFAULT 0, -- Default: no transfer fee
                range_upgrade_cost_base INTEGER NOT NULL DEFAULT 2,
                range_upgrade_cost_scale REAL NOT NULL DEFAULT 1.0,
                win_cond_last_man INTEGER NOT NULL DEFAULT 1,
                win_cond_armistice INTEGER NOT NULL DEFAULT 1,
                armistice_threshold REAL NOT NULL DEFAULT 1.0,
                armistice_trigger_threshold REAL NOT NULL DEFAULT 1.0,
                armistice_hide_vote_count INTEGER NOT NULL DEFAULT 0,
                diplomatic_conference_duration_sec INTEGER NOT NULL DEFAULT 120,
                conference_transparency TEXT NOT NULL DEFAULT 'REVEAL_COUNTS_AT_END',
                conference_end_condition TEXT NOT NULL DEFAULT 'ON_ALL_VOTED', -- ON_ALL_VOTED, TIMER_ONLY
                conference_single_disagree_outcomes TEXT NOT NULL DEFAULT '["GRANT_AP"]',
                conference_multi_disagree_outcomes TEXT NOT NULL DEFAULT '["RESUME_COMBAT"]',
                armistice_outcome_multi_disagree TEXT NOT NULL DEFAULT 'RESUME_COMBAT',
                win_cond_rogue INTEGER NOT NULL DEFAULT 1,
                rogue_resolution_mode TEXT NOT NULL DEFAULT 'GRANT_AP', -- GRANT_AP, INSTANT_WIN, REVEAL_IDENTITY
                rogue_bonus_ap INTEGER NOT NULL DEFAULT 3,
                multi_disagree_bonus_ap INTEGER NOT NULL DEFAULT 2,
                win_cond_stagnation INTEGER NOT NULL DEFAULT 0,
                stagnation_days_limit INTEGER NOT NULL DEFAULT 5,
                stagnation_mode TEXT NOT NULL DEFAULT 'DRAW', -- DRAW, SUDDEN_DEATH
                reveal_killer_on_death INTEGER NOT NULL DEFAULT 0,
                allow_dead_chat INTEGER NOT NULL DEFAULT 1,
                fog_of_war_stats INTEGER NOT NULL DEFAULT 1,
                first_ap_drop_days INTEGER NOT NULL DEFAULT 1,
                require_email_verify INTEGER NOT NULL DEFAULT 0,
                audit_log_public INTEGER NOT NULL DEFAULT 1,
                allow_late_join INTEGER NOT NULL DEFAULT 0,
                map_voting_enabled INTEGER NOT NULL DEFAULT 0,
                map_voting_duration_minutes INTEGER NOT NULL DEFAULT 10,
                FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS players (
                id TEXT PRIMARY KEY,
                game_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                username TEXT NOT NULL,
                x INTEGER NOT NULL,
                y INTEGER NOT NULL,
                hp INTEGER NOT NULL,
                ap INTEGER NOT NULL,
                range_level INTEGER NOT NULL DEFAULT 1,
                is_alive INTEGER NOT NULL DEFAULT 1,
                eliminated_by TEXT,
                eliminated_at TEXT,
                armistice_vote INTEGER NOT NULL DEFAULT 0,
                conference_vote INTEGER DEFAULT NULL, -- NULL: pending, 1: AGREE, 0: REJECT
                is_bot INTEGER NOT NULL DEFAULT 0,
                last_active_at TEXT NOT NULL,
                spawn_x INTEGER NOT NULL,
                spawn_y INTEGER NOT NULL,
                FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                UNIQUE(game_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id TEXT NOT NULL,
                event_seq INTEGER NOT NULL,
                event_type TEXT NOT NULL, -- MOVE, ATTACK, GIVE_AP, UPGRADE_RANGE, ELIMINATION, ARMISTICE_VOTE, WIN, DROP_AP, SHRINK
                initiator_user_id TEXT,
                target_user_id TEXT,
                visibility TEXT NOT NULL DEFAULT 'PUBLIC', -- PUBLIC, PRIVATE
                payload_json TEXT NOT NULL,
                prev_hash TEXT NOT NULL,
                hash TEXT NOT NULL,
                timestamp_ns INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE,
                UNIQUE(game_id, event_seq)
            );

            CREATE TABLE IF NOT EXISTS game_parameter_audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id TEXT NOT NULL,
                param_key TEXT NOT NULL,
                old_value TEXT NOT NULL,
                new_value TEXT NOT NULL,
                admin_user_id TEXT NOT NULL,
                admin_username TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS notifications (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                game_id TEXT,
                event_type TEXT NOT NULL,
                title_en TEXT NOT NULL,
                title_ru TEXT NOT NULL,
                message_en TEXT NOT NULL,
                message_ru TEXT NOT NULL,
                is_read INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                endpoint TEXT UNIQUE NOT NULL,
                p256dh TEXT NOT NULL,
                auth TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                username TEXT NOT NULL,
                message TEXT NOT NULL,
                is_dead INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS system_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS invite_tokens (
                token TEXT PRIMARY KEY,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                uses_left INTEGER NOT NULL DEFAULT 1,
                used_count INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS game_join_requests (
                id TEXT PRIMARY KEY,
                game_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                username TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING', -- PENDING, APPROVED, DENIED
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS game_map_options (
                id TEXT PRIMARY KEY,
                game_id TEXT NOT NULL,
                option_idx INTEGER NOT NULL, -- 1, 2, 3, 4
                option_label TEXT NOT NULL, -- Alpha, Bravo, Charlie, Delta
                placements_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS game_map_votes (
                game_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                ranks_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(game_id, user_id),
                FOREIGN KEY(game_id) REFERENCES games(id) ON DELETE CASCADE,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
            CREATE INDEX IF NOT EXISTS idx_players_game ON players(game_id);
            CREATE INDEX IF NOT EXISTS idx_events_game_seq ON events(game_id, event_seq);
            CREATE INDEX IF NOT EXISTS idx_audit_game ON game_parameter_audit_logs(game_id);
            CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, is_read);
            CREATE INDEX IF NOT EXISTS idx_chat_game ON chat_messages(game_id);
            CREATE INDEX IF NOT EXISTS idx_join_req_game ON game_join_requests(game_id, status);
            CREATE INDEX IF NOT EXISTS idx_map_opts_game ON game_map_options(game_id);
            """)

            # Forward migrations for existing schemas
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(game_parameters);")
            gp_cols = [c["name"] for c in cur.fetchall()]
            if "first_ap_drop_days" not in gp_cols:
                conn.execute("ALTER TABLE game_parameters ADD COLUMN first_ap_drop_days INTEGER NOT NULL DEFAULT 1;")
            if "ap_drop_server_time" not in gp_cols:
                conn.execute("ALTER TABLE game_parameters ADD COLUMN ap_drop_server_time TEXT NOT NULL DEFAULT '22:00';")
            if "armistice_trigger_threshold" not in gp_cols:
                conn.execute("ALTER TABLE game_parameters ADD COLUMN armistice_trigger_threshold REAL NOT NULL DEFAULT 1.0;")
            if "diplomatic_conference_duration_sec" not in gp_cols:
                conn.execute("ALTER TABLE game_parameters ADD COLUMN diplomatic_conference_duration_sec INTEGER NOT NULL DEFAULT 120;")
            if "armistice_outcome_multi_disagree" not in gp_cols:
                conn.execute("ALTER TABLE game_parameters ADD COLUMN armistice_outcome_multi_disagree TEXT NOT NULL DEFAULT 'RESUME_COMBAT';")
            if "armistice_hide_vote_count" not in gp_cols:
                conn.execute("ALTER TABLE game_parameters ADD COLUMN armistice_hide_vote_count INTEGER NOT NULL DEFAULT 0;")
            if "conference_transparency" not in gp_cols:
                conn.execute("ALTER TABLE game_parameters ADD COLUMN conference_transparency TEXT NOT NULL DEFAULT 'SHOW_ALL_VOTES';")
            if "conference_single_disagree_outcomes" not in gp_cols:
                conn.execute("ALTER TABLE game_parameters ADD COLUMN conference_single_disagree_outcomes TEXT NOT NULL DEFAULT '[\"GRANT_AP\"]';")
            if "conference_multi_disagree_outcomes" not in gp_cols:
                conn.execute("ALTER TABLE game_parameters ADD COLUMN conference_multi_disagree_outcomes TEXT NOT NULL DEFAULT '[\"RESUME_COMBAT\"]';")
            if "conference_end_condition" not in gp_cols:
                conn.execute("ALTER TABLE game_parameters ADD COLUMN conference_end_condition TEXT NOT NULL DEFAULT 'ON_ALL_VOTED';")
            if "multi_disagree_bonus_ap" not in gp_cols:
                conn.execute("ALTER TABLE game_parameters ADD COLUMN multi_disagree_bonus_ap INTEGER NOT NULL DEFAULT 2;")
            if "allow_late_join" not in gp_cols:
                conn.execute("ALTER TABLE game_parameters ADD COLUMN allow_late_join INTEGER NOT NULL DEFAULT 0;")
            if "map_voting_enabled" not in gp_cols:
                conn.execute("ALTER TABLE game_parameters ADD COLUMN map_voting_enabled INTEGER NOT NULL DEFAULT 0;")
            if "map_voting_duration_minutes" not in gp_cols:
                conn.execute("ALTER TABLE game_parameters ADD COLUMN map_voting_duration_minutes INTEGER NOT NULL DEFAULT 10;")

            cur.execute("PRAGMA table_info(games);")
            g_cols = [c["name"] for c in cur.fetchall()]
            if "conference_status" not in g_cols:
                conn.execute("ALTER TABLE games ADD COLUMN conference_status TEXT NOT NULL DEFAULT 'IDLE';")
            if "conference_ends_at" not in g_cols:
                conn.execute("ALTER TABLE games ADD COLUMN conference_ends_at TEXT;")
            if "conference_triggered_at" not in g_cols:
                conn.execute("ALTER TABLE games ADD COLUMN conference_triggered_at TEXT;")
            if "conference_outcome" not in g_cols:
                conn.execute("ALTER TABLE games ADD COLUMN conference_outcome TEXT;")
            if "conference_resolution_data" not in g_cols:
                conn.execute("ALTER TABLE games ADD COLUMN conference_resolution_data TEXT;")
            if "is_playtest" not in g_cols:
                conn.execute("ALTER TABLE games ADD COLUMN is_playtest INTEGER NOT NULL DEFAULT 0;")
            if "map_voting_ends_at" not in g_cols:
                conn.execute("ALTER TABLE games ADD COLUMN map_voting_ends_at TEXT;")

            cur.execute("PRAGMA table_info(players);")
            p_cols = [c["name"] for c in cur.fetchall()]
            if "conference_vote" not in p_cols:
                conn.execute("ALTER TABLE players ADD COLUMN conference_vote INTEGER DEFAULT NULL;")
            if "is_bot" not in p_cols:
                conn.execute("ALTER TABLE players ADD COLUMN is_bot INTEGER NOT NULL DEFAULT 0;")

            cur.execute("PRAGMA table_info(users);")
            u_cols = [c["name"] for c in cur.fetchall()]
            if "is_bot" not in u_cols:
                conn.execute("ALTER TABLE users ADD COLUMN is_bot INTEGER NOT NULL DEFAULT 0;")
            if "alert_preferences" not in u_cols:
                conn.execute("ALTER TABLE users ADD COLUMN alert_preferences TEXT DEFAULT '{}';")

            # Seed default system settings
            cur.execute("SELECT value FROM system_settings WHERE key = 'registrations_enabled'")
            if not cur.fetchone():
                conn.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES ('registrations_enabled', '1')")
        finally:
            conn.close()
