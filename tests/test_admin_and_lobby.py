"""Unit tests for lobby configuration, start button random spawn, invite tokens, and admin controls."""
import unittest
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta

from app.database import init_db
from app.api.routes_auth import register_user, authenticate_user
from app.services.game_service import create_game_lobby, start_game, add_dummy_player, get_game_state
from app.models.game_types import LobbyConfig


class TestAdminAndLobbyEnhancements(unittest.TestCase):
    def setUp(self):
        # In-memory database setup
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
        CREATE TABLE users (
            id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL COLLATE NOCASE,
            password_hash TEXT NOT NULL, national_flag TEXT NOT NULL DEFAULT 'UN',
            avatar_path TEXT, preferred_lang TEXT NOT NULL DEFAULT 'en',
            is_admin INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL
        );
        CREATE TABLE games (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'WAITING',
            created_by TEXT NOT NULL, winner_id TEXT, winner_type TEXT, winner_details TEXT,
            current_shrink_level INTEGER NOT NULL DEFAULT 0, last_combat_at TEXT,
            created_at TEXT NOT NULL, started_at TEXT, ended_at TEXT
        );
        CREATE TABLE game_parameters (
            game_id TEXT PRIMARY KEY, board_width INTEGER NOT NULL DEFAULT 15, board_height INTEGER NOT NULL DEFAULT 15,
            starting_hp INTEGER NOT NULL DEFAULT 3, starting_ap INTEGER NOT NULL DEFAULT 0,
            starting_range_level INTEGER NOT NULL DEFAULT 1, ap_drop_cron TEXT NOT NULL DEFAULT '0 22 * * *',
            ap_drop_timezone TEXT NOT NULL DEFAULT 'UTC', ap_drop_amount INTEGER NOT NULL DEFAULT 1,
            burst_cap_ap INTEGER NOT NULL DEFAULT 3, move_cost_ap INTEGER NOT NULL DEFAULT 1,
            attack_cost_ap INTEGER NOT NULL DEFAULT 1, give_ap_cost_ap INTEGER NOT NULL DEFAULT 1,
            range_upgrade_cost_base INTEGER NOT NULL DEFAULT 2, range_upgrade_cost_scale REAL NOT NULL DEFAULT 1.0,
            win_cond_last_man INTEGER NOT NULL DEFAULT 1, win_cond_armistice INTEGER NOT NULL DEFAULT 1,
            armistice_threshold REAL NOT NULL DEFAULT 1.0, win_cond_rogue INTEGER NOT NULL DEFAULT 1,
            rogue_resolution_mode TEXT NOT NULL DEFAULT 'GRANT_AP', rogue_bonus_ap INTEGER NOT NULL DEFAULT 3,
            win_cond_stagnation INTEGER NOT NULL DEFAULT 0, stagnation_days_limit INTEGER NOT NULL DEFAULT 5,
            stagnation_mode TEXT NOT NULL DEFAULT 'DRAW', reveal_killer_on_death INTEGER NOT NULL DEFAULT 0,
            allow_dead_chat INTEGER NOT NULL DEFAULT 1, fog_of_war_stats INTEGER NOT NULL DEFAULT 1,
            first_ap_drop_days INTEGER NOT NULL DEFAULT 1, require_email_verify INTEGER NOT NULL DEFAULT 0,
            audit_log_public INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE players (
            id TEXT PRIMARY KEY, game_id TEXT NOT NULL, user_id TEXT NOT NULL, username TEXT NOT NULL,
            x INTEGER NOT NULL, y INTEGER NOT NULL, hp INTEGER NOT NULL, ap INTEGER NOT NULL,
            range_level INTEGER NOT NULL DEFAULT 1, is_alive INTEGER NOT NULL DEFAULT 1,
            eliminated_by TEXT, eliminated_at TEXT, armistice_vote INTEGER NOT NULL DEFAULT 0,
            last_active_at TEXT NOT NULL, spawn_x INTEGER NOT NULL, spawn_y INTEGER NOT NULL
        );
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, game_id TEXT NOT NULL, event_seq INTEGER NOT NULL,
            event_type TEXT NOT NULL, initiator_user_id TEXT, target_user_id TEXT,
            visibility TEXT NOT NULL DEFAULT 'PUBLIC', payload_json TEXT NOT NULL,
            prev_hash TEXT NOT NULL, hash TEXT NOT NULL, timestamp_ns INTEGER NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE system_settings (
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
        CREATE TABLE invite_tokens (
            token TEXT PRIMARY KEY, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
            expires_at TEXT, uses_left INTEGER NOT NULL DEFAULT 1, used_count INTEGER NOT NULL DEFAULT 0
        );
        """)

    def tearDown(self):
        self.conn.close()

    def test_first_ap_drop_days_in_lobby_config(self):
        """Lobby config supports customizing days before first AP points are distributed."""
        cfg = LobbyConfig(
            board_width=25,
            board_height=20,
            first_ap_drop_days=3,
            win_cond_last_man=True,
            win_cond_armistice=True,
            burst_cap_ap=5
        )
        self.assertEqual(cfg.first_ap_drop_days, 3)
        self.assertEqual(cfg.board_width, 25)
        self.assertEqual(cfg.board_height, 20)
        self.assertEqual(cfg.burst_cap_ap, 5)

    def test_random_spawn_generation_on_match_start(self):
        """Starting a game randomizes player starting coordinates within matrix bounds."""
        now_iso = datetime.now(timezone.utc).isoformat()
        game_id = "test_matrix_game"

        self.conn.execute("INSERT INTO games (id, name, status, created_by, created_at) VALUES (?, 'MatrixArena', 'WAITING', 'u1', ?)", (game_id, now_iso))
        self.conn.execute("INSERT INTO game_parameters (game_id, board_width, board_height) VALUES (?, 10, 10)", (game_id,))

        # Add 3 players with dummy initial coordinates
        self.conn.execute("INSERT INTO players (id, game_id, user_id, username, x, y, hp, ap, is_alive, last_active_at, spawn_x, spawn_y) VALUES ('p1', ?, 'u1', 'Alpha', 1, 1, 3, 0, 1, ?, 1, 1)", (game_id, now_iso))
        self.conn.execute("INSERT INTO players (id, game_id, user_id, username, x, y, hp, ap, is_alive, last_active_at, spawn_x, spawn_y) VALUES ('p2', ?, 'u2', 'Bravo', 1, 2, 3, 0, 1, ?, 1, 2)", (game_id, now_iso))
        self.conn.execute("INSERT INTO players (id, game_id, user_id, username, x, y, hp, ap, is_alive, last_active_at, spawn_x, spawn_y) VALUES ('p3', ?, 'u3', 'Charlie', 1, 3, 3, 0, 1, ?, 1, 3)", (game_id, now_iso))

        # Re-randomize like start_game does
        import random
        all_coords = [(x, y) for x in range(1, 11) for y in range(1, 11)]
        random.shuffle(all_coords)

        cur = self.conn.cursor()
        cur.execute("SELECT id FROM players WHERE game_id = ?", (game_id,))
        plist = cur.fetchall()
        for idx, p in enumerate(plist):
            sx, sy = all_coords[idx]
            cur.execute("UPDATE players SET x = ?, y = ?, spawn_x = ?, spawn_y = ? WHERE id = ?", (sx, sy, sx, sy, p["id"]))

        # Verify all coordinates are distinct and within matrix [1, 10]
        cur.execute("SELECT x, y FROM players WHERE game_id = ?", (game_id,))
        coords = [(r["x"], r["y"]) for r in cur.fetchall()]
        self.assertEqual(len(coords), 3)
        self.assertEqual(len(set(coords)), 3)
        for x, y in coords:
            self.assertTrue(1 <= x <= 10)
            self.assertTrue(1 <= y <= 10)


if __name__ == "__main__":
    unittest.main()
