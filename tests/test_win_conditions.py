"""Unit tests for multi-win condition engine: Last Man, Armistice, Rogue, and Stagnation."""
import unittest
import sqlite3
from datetime import datetime, timezone, timedelta
from app.engine.win_conditions import evaluate_win_conditions, evaluate_stagnation


class TestWinConditions(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
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
        """)
        self.game_id = "test_game"
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute("INSERT INTO games (id, name, status, created_by, created_at) VALUES (?, 'Test', 'ACTIVE', 'u1', ?)", (self.game_id, now))
        self.conn.execute("INSERT INTO game_parameters (game_id) VALUES (?)", (self.game_id,))

    def tearDown(self):
        self.conn.close()

    def test_last_man_standing(self):
        """When exactly 1 player is alive, Last Man Standing resolves."""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT INTO players (id, game_id, user_id, username, x, y, hp, ap, is_alive, last_active_at, spawn_x, spawn_y) VALUES ('p1', ?, 'u1', 'SoleSurvivor', 5, 5, 3, 0, 1, ?, 5, 5)",
            (self.game_id, now)
        )
        self.conn.execute(
            "INSERT INTO players (id, game_id, user_id, username, x, y, hp, ap, is_alive, last_active_at, spawn_x, spawn_y) VALUES ('p2', ?, 'u2', 'DeadPlayer', 6, 6, 0, 0, 0, ?, 6, 6)",
            (self.game_id, now)
        )

        res = evaluate_win_conditions(self.conn, self.game_id, 1000, now)
        self.assertIsNotNone(res)
        self.assertTrue(res["ended"])
        self.assertEqual(res["condition"], "LAST_MAN")
        self.assertEqual(res["winner_id"], "p1")

    def test_unanimous_armistice(self):
        """100% peace votes by all living players ends match with ARMISTICE victory."""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute("INSERT INTO players (id, game_id, user_id, username, x, y, hp, ap, is_alive, armistice_vote, last_active_at, spawn_x, spawn_y) VALUES ('p1', ?, 'u1', 'Alice', 1, 1, 3, 0, 1, 1, ?, 1, 1)", (self.game_id, now))
        self.conn.execute("INSERT INTO players (id, game_id, user_id, username, x, y, hp, ap, is_alive, armistice_vote, last_active_at, spawn_x, spawn_y) VALUES ('p2', ?, 'u2', 'Bob', 2, 2, 3, 0, 1, 1, ?, 2, 2)", (self.game_id, now))

        res = evaluate_win_conditions(self.conn, self.game_id, 1000, now)
        self.assertIsNotNone(res)
        self.assertTrue(res["ended"])
        self.assertEqual(res["condition"], "ARMISTICE")

    def test_rogue_traitor_rule_grant_ap(self):
        """When N-1 vote peace and 1 holds out, rogue receives bonus AP in GRANT_AP mode."""
        now = datetime.now(timezone.utc).isoformat()
        # 3 living players: 2 vote peace, 1 rogue holds out
        self.conn.execute("INSERT INTO players (id, game_id, user_id, username, x, y, hp, ap, is_alive, armistice_vote, last_active_at, spawn_x, spawn_y) VALUES ('p1', ?, 'u1', 'Alice', 1, 1, 3, 0, 1, 1, ?, 1, 1)", (self.game_id, now))
        self.conn.execute("INSERT INTO players (id, game_id, user_id, username, x, y, hp, ap, is_alive, armistice_vote, last_active_at, spawn_x, spawn_y) VALUES ('p2', ?, 'u2', 'Bob', 2, 2, 3, 0, 1, 1, ?, 2, 2)", (self.game_id, now))
        self.conn.execute("INSERT INTO players (id, game_id, user_id, username, x, y, hp, ap, is_alive, armistice_vote, last_active_at, spawn_x, spawn_y) VALUES ('p3', ?, 'u3', 'RogueCharlie', 3, 3, 3, 0, 1, 0, ?, 3, 3)", (self.game_id, now))

        res = evaluate_win_conditions(self.conn, self.game_id, 1000, now)
        self.assertIsNotNone(res)
        self.assertEqual(res["condition"], "ROGUE_GRANT_AP")
        self.assertEqual(res["bonus_ap"], 3)

        # Confirm RogueCharlie received 3 AP
        cur = self.conn.cursor()
        cur.execute("SELECT ap FROM players WHERE id = 'p3'")
        self.assertEqual(cur.fetchone()["ap"], 3)

    def test_rogue_instant_win_mode(self):
        """In INSTANT_WIN mode, the rogue holdout instantly wins the game."""
        self.conn.execute("UPDATE game_parameters SET rogue_resolution_mode = 'INSTANT_WIN' WHERE game_id = ?", (self.game_id,))
        now = datetime.now(timezone.utc).isoformat()

        self.conn.execute("INSERT INTO players (id, game_id, user_id, username, x, y, hp, ap, is_alive, armistice_vote, last_active_at, spawn_x, spawn_y) VALUES ('p1', ?, 'u1', 'Alice', 1, 1, 3, 0, 1, 1, ?, 1, 1)", (self.game_id, now))
        self.conn.execute("INSERT INTO players (id, game_id, user_id, username, x, y, hp, ap, is_alive, armistice_vote, last_active_at, spawn_x, spawn_y) VALUES ('p2', ?, 'u2', 'Bob', 2, 2, 3, 0, 1, 1, ?, 2, 2)", (self.game_id, now))
        self.conn.execute("INSERT INTO players (id, game_id, user_id, username, x, y, hp, ap, is_alive, armistice_vote, last_active_at, spawn_x, spawn_y) VALUES ('p3', ?, 'u3', 'RogueCharlie', 3, 3, 3, 0, 1, 0, ?, 3, 3)", (self.game_id, now))

        res = evaluate_win_conditions(self.conn, self.game_id, 1000, now)
        self.assertIsNotNone(res)
        self.assertTrue(res["ended"])
        self.assertEqual(res["condition"], "ROGUE")
        self.assertEqual(res["winner_id"], "p3")

    def test_stagnation_draw(self):
        """Stagnation timeout without combat terminates match with DRAW."""
        old_time = (datetime.now(timezone.utc) - timedelta(days=6)).isoformat()
        now_time = datetime.now(timezone.utc).isoformat()

        self.conn.execute("UPDATE games SET last_combat_at = ? WHERE id = ?", (old_time, self.game_id))
        self.conn.execute(
            "UPDATE game_parameters SET win_cond_stagnation = 1, stagnation_days_limit = 5, stagnation_mode = 'DRAW' WHERE game_id = ?",
            (self.game_id,)
        )

        res = evaluate_stagnation(self.conn, self.game_id, 2000, now_time)
        self.assertIsNotNone(res)
        self.assertTrue(res["ended"])
        self.assertEqual(res["condition"], "STAGNATION_DRAW")


if __name__ == "__main__":
    unittest.main()
