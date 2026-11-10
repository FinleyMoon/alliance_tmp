"""Unit tests for range geometry and core action validation pipeline."""
import unittest
import sqlite3
import os
import tempfile
import uuid
from datetime import datetime, timezone

from app.engine.range import is_in_range, get_range_tiles, get_core_radius
from app.engine.actions import execute_action_batch, calculate_upgrade_cost
from app.engine.win_conditions import evaluate_win_conditions
from app.models.game_types import ActionType


class TestRangeGeometry(unittest.TestCase):
    def test_level_1_tile_count(self):
        """Level 1 starting range: 5x5 core + 4 cardinal spikes at distance 3 = 28 tiles."""
        center = (8, 8)
        tiles = get_range_tiles(center, range_level=1, board_width=15, board_height=15)
        # 5x5 square is 25 tiles, minus center (8,8) is 24, plus 4 cardinal spikes = 28 tiles
        reachable = {t for t in tiles if t != center}
        self.assertEqual(len(reachable), 28)

        # Core boundaries
        self.assertTrue(is_in_range(center, (10, 10), 1))  # corner of 5x5 (|8-10| = 2)
        self.assertTrue(is_in_range(center, (6, 6), 1))

        # Cardinal spikes at distance 3
        self.assertTrue(is_in_range(center, (8, 11), 1))  # North spike (dy=3, dx=0)
        self.assertTrue(is_in_range(center, (8, 5), 1))   # South spike
        self.assertTrue(is_in_range(center, (11, 8), 1))  # East spike
        self.assertTrue(is_in_range(center, (5, 8), 1))   # West spike

        # Tiles outside range
        self.assertFalse(is_in_range(center, (8, 12), 1))  # distance 4
        self.assertFalse(is_in_range(center, (11, 11), 1)) # diagonal outside core

    def test_level_2_tile_count(self):
        """Level 2 range: 7x7 core + 4 cardinal spikes at distance 4 = 52 tiles."""
        center = (8, 8)
        tiles = get_range_tiles(center, range_level=2, board_width=15, board_height=15)
        reachable = {t for t in tiles if t != center}
        # 7x7 core = 49 tiles, minus center is 48, plus 4 spikes = 52 tiles
        self.assertEqual(len(reachable), 52)
        self.assertTrue(is_in_range(center, (8, 12), 2))   # North spike at distance 4


class TestActionPipeline(unittest.TestCase):
    def setUp(self):
        # Create an in-memory test database
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON;")

        # Run schema
        self.conn.executescript("""
        CREATE TABLE users (id TEXT PRIMARY KEY, username TEXT, password_hash TEXT, national_flag TEXT, avatar_path TEXT, preferred_lang TEXT, is_admin INTEGER, created_at TEXT);
        CREATE TABLE games (id TEXT PRIMARY KEY, name TEXT, status TEXT, created_by TEXT, winner_id TEXT, winner_type TEXT, winner_details TEXT, current_shrink_level INTEGER DEFAULT 0, last_combat_at TEXT, created_at TEXT, started_at TEXT, ended_at TEXT);
        CREATE TABLE game_parameters (
            game_id TEXT PRIMARY KEY, board_width INTEGER DEFAULT 15, board_height INTEGER DEFAULT 15,
            starting_hp INTEGER DEFAULT 3, starting_ap INTEGER DEFAULT 0, starting_range_level INTEGER DEFAULT 1,
            ap_drop_cron TEXT DEFAULT '0 22 * * *', ap_drop_timezone TEXT DEFAULT 'UTC', ap_drop_amount INTEGER DEFAULT 1,
            burst_cap_ap INTEGER DEFAULT 3, move_cost_ap INTEGER DEFAULT 1, attack_cost_ap INTEGER DEFAULT 1,
            give_ap_cost_ap INTEGER DEFAULT 1, range_upgrade_cost_base INTEGER DEFAULT 2, range_upgrade_cost_scale REAL DEFAULT 1.0,
            win_cond_last_man INTEGER DEFAULT 1, win_cond_armistice INTEGER DEFAULT 1, armistice_threshold REAL DEFAULT 1.0,
            win_cond_rogue INTEGER DEFAULT 1, rogue_resolution_mode TEXT DEFAULT 'GRANT_AP', rogue_bonus_ap INTEGER DEFAULT 3,
            win_cond_stagnation INTEGER DEFAULT 0, stagnation_days_limit INTEGER DEFAULT 5, stagnation_mode TEXT DEFAULT 'DRAW',
            reveal_killer_on_death INTEGER DEFAULT 0, allow_dead_chat INTEGER DEFAULT 1, fog_of_war_stats INTEGER DEFAULT 1,
            require_email_verify INTEGER DEFAULT 0, audit_log_public INTEGER DEFAULT 1
        );
        CREATE TABLE players (
            id TEXT PRIMARY KEY, game_id TEXT, user_id TEXT, username TEXT, x INTEGER, y INTEGER,
            hp INTEGER, ap INTEGER, range_level INTEGER DEFAULT 1, is_alive INTEGER DEFAULT 1,
            eliminated_by TEXT, eliminated_at TEXT, armistice_vote INTEGER DEFAULT 0,
            last_active_at TEXT, spawn_x INTEGER, spawn_y INTEGER
        );
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, game_id TEXT, event_seq INTEGER, event_type TEXT,
            initiator_user_id TEXT, target_user_id TEXT, visibility TEXT, payload_json TEXT,
            prev_hash TEXT, hash TEXT, timestamp_ns INTEGER, created_at TEXT
        );
        CREATE TABLE notifications (
            id TEXT PRIMARY KEY, user_id TEXT, game_id TEXT, event_type TEXT,
            title_en TEXT, title_ru TEXT, message_en TEXT, message_ru TEXT,
            is_read INTEGER DEFAULT 0, created_at TEXT
        );
        """)

        # Seed test game and players
        self.game_id = "test_game_1"
        now_iso = datetime.now(timezone.utc).isoformat()

        self.conn.execute("INSERT INTO games (id, name, status, created_by, created_at) VALUES (?, 'Test Arena', 'ACTIVE', 'u1', ?)", (self.game_id, now_iso))
        self.conn.execute("INSERT INTO game_parameters (game_id, burst_cap_ap) VALUES (?, 3)", (self.game_id,))

        # Player A (initiator): at (5, 5), HP=3, AP=5, range=1
        self.conn.execute(
            "INSERT INTO players (id, game_id, user_id, username, x, y, hp, ap, range_level, is_alive, last_active_at, spawn_x, spawn_y) VALUES ('p_a', ?, 'u_a', 'PlayerA', 5, 5, 3, 5, 1, 1, ?, 5, 5)",
            (self.game_id, now_iso)
        )
        # Player B (target in range): at (6, 5), HP=3, AP=0, range=1
        self.conn.execute(
            "INSERT INTO players (id, game_id, user_id, username, x, y, hp, ap, range_level, is_alive, last_active_at, spawn_x, spawn_y) VALUES ('p_b', ?, 'u_b', 'PlayerB', 6, 5, 3, 0, 1, 1, ?, 6, 5)",
            (self.game_id, now_iso)
        )

    def tearDown(self):
        self.conn.close()

    def test_valid_move_execution(self):
        """Orthogonal move decreases AP and updates coordinates."""
        actions = [{"action_type": "MOVE", "to_x": 5, "to_y": 6}]
        success, msg, receipts, ap_spent = execute_action_batch(
            self.conn, self.game_id, "u_a", actions, 1000, "2026-01-01T00:00:00Z"
        )
        self.assertTrue(success)
        self.assertEqual(ap_spent, 1)

        # Check updated player A state
        cur = self.conn.cursor()
        cur.execute("SELECT x, y, ap FROM players WHERE id = 'p_a'")
        p = cur.fetchone()
        self.assertEqual(p["x"], 5)
        self.assertEqual(p["y"], 6)
        self.assertEqual(p["ap"], 4)

    def test_invalid_diagonal_move_rejected(self):
        """Diagonal movement is strictly forbidden."""
        actions = [{"action_type": "MOVE", "to_x": 6, "to_y": 6}]
        success, msg, receipts, ap_spent = execute_action_batch(
            self.conn, self.game_id, "u_a", actions, 1000, "2026-01-01T00:00:00Z"
        )
        self.assertFalse(success)
        self.assertIn("ERR_INVALID_MOVE", msg)

    def test_occupied_tile_move_rejected(self):
        """Moving to a tile occupied by another living player is rejected."""
        # Player B is at (6, 5)
        actions = [{"action_type": "MOVE", "to_x": 6, "to_y": 5}]
        success, msg, receipts, ap_spent = execute_action_batch(
            self.conn, self.game_id, "u_a", actions, 1000, "2026-01-01T00:00:00Z"
        )
        self.assertFalse(success)
        self.assertIn("ERR_TILE_OCCUPIED", msg)

    def test_burst_cap_enforcement(self):
        """Payload exceeding configured burst cap is rejected with ERR_BURST_CAP_EXCEEDED."""
        # Burst cap is 3, submitting 4 moves
        actions = [
            {"action_type": "MOVE", "to_x": 5, "to_y": 6},
            {"action_type": "MOVE", "to_x": 5, "to_y": 7},
            {"action_type": "MOVE", "to_x": 5, "to_y": 8},
            {"action_type": "MOVE", "to_x": 5, "to_y": 9}
        ]
        success, msg, receipts, ap_spent = execute_action_batch(
            self.conn, self.game_id, "u_a", actions, 1000, "2026-01-01T00:00:00Z"
        )
        self.assertFalse(success)
        self.assertIn("ERR_BURST_CAP_EXCEEDED", msg)

    def test_attack_and_fatal_elimination(self):
        """Attacking damages victim; fatal hit marks victim dead and sets eliminated_by."""
        # Set target HP to 1
        self.conn.execute("UPDATE players SET hp = 1 WHERE id = 'p_b'")

        actions = [{"action_type": "ATTACK", "target_player_id": "p_b"}]
        success, msg, receipts, ap_spent = execute_action_batch(
            self.conn, self.game_id, "u_a", actions, 1000, "2026-01-01T00:00:00Z"
        )
        self.assertTrue(success)
        self.assertEqual(receipts[0]["result"], "FATAL")

        # Check Player B is eliminated
        cur = self.conn.cursor()
        cur.execute("SELECT hp, is_alive, eliminated_by FROM players WHERE id = 'p_b'")
        victim = cur.fetchone()
        self.assertEqual(victim["hp"], 0)
        self.assertEqual(victim["is_alive"], 0)
        self.assertEqual(victim["eliminated_by"], "PlayerA")

    def test_non_lethal_combat_fog_of_war(self):
        """Non-lethal hit does not disclose target remaining HP to attacker."""
        actions = [{"action_type": "ATTACK", "target_player_id": "p_b"}]
        success, msg, receipts, ap_spent = execute_action_batch(
            self.conn, self.game_id, "u_a", actions, 1000, "2026-01-01T00:00:00Z"
        )
        self.assertTrue(success)
        self.assertEqual(receipts[0]["target_remaining_hp"], "UNKNOWN")

        # Confirm victim took 1 damage
        cur = self.conn.cursor()
        cur.execute("SELECT hp FROM players WHERE id = 'p_b'")
        self.assertEqual(cur.fetchone()["hp"], 2)


if __name__ == "__main__":
    unittest.main()
