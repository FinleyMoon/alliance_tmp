"""Tests for diplomatic conference, armistice disabled gating, zero-fee transfer, and profile/settings."""
import unittest
import sqlite3
from datetime import datetime, timezone

from app.engine.win_conditions import evaluate_diplomatic_conference
from app.engine.actions import calculate_action_cost, execute_action_batch
from app.security import hash_password, verify_password


class TestConferenceAndSettings(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
        CREATE TABLE games (
            id TEXT PRIMARY KEY, name TEXT, status TEXT, created_by TEXT,
            winner_id TEXT, winner_type TEXT, winner_details TEXT,
            current_shrink_level INTEGER DEFAULT 0, last_combat_at TEXT,
            created_at TEXT, started_at TEXT, ended_at TEXT,
            conference_status TEXT DEFAULT 'IDLE', conference_ends_at TEXT,
            conference_triggered_at TEXT, conference_outcome TEXT,
            conference_resolution_data TEXT
        );
        CREATE TABLE game_parameters (
            game_id TEXT PRIMARY KEY, board_width INTEGER DEFAULT 15, board_height INTEGER DEFAULT 15,
            starting_hp INTEGER DEFAULT 3, starting_ap INTEGER DEFAULT 0, starting_range_level INTEGER DEFAULT 1,
            ap_drop_cron TEXT DEFAULT '0 22 * * *', ap_drop_timezone TEXT DEFAULT 'UTC', ap_drop_amount INTEGER DEFAULT 1,
            ap_drop_server_time TEXT DEFAULT '22:00', burst_cap_ap INTEGER DEFAULT 3,
            move_cost_ap INTEGER DEFAULT 1, attack_cost_ap INTEGER DEFAULT 1, give_ap_cost_ap INTEGER DEFAULT 0,
            range_upgrade_cost_base INTEGER DEFAULT 2, range_upgrade_cost_scale REAL DEFAULT 1.0,
            win_cond_last_man INTEGER DEFAULT 1, win_cond_armistice INTEGER DEFAULT 1, armistice_threshold REAL DEFAULT 1.0,
            armistice_trigger_threshold REAL DEFAULT 1.0, diplomatic_conference_duration_sec INTEGER DEFAULT 120,
            armistice_outcome_multi_disagree TEXT DEFAULT 'RESUME_COMBAT',
            win_cond_rogue INTEGER DEFAULT 1, rogue_resolution_mode TEXT DEFAULT 'GRANT_AP', rogue_bonus_ap INTEGER DEFAULT 3,
            win_cond_stagnation INTEGER DEFAULT 0, stagnation_days_limit INTEGER DEFAULT 5, stagnation_mode TEXT DEFAULT 'DRAW',
            reveal_killer_on_death INTEGER DEFAULT 0, allow_dead_chat INTEGER DEFAULT 1, fog_of_war_stats INTEGER DEFAULT 1,
            first_ap_drop_days INTEGER DEFAULT 1, require_email_verify INTEGER DEFAULT 0, audit_log_public INTEGER DEFAULT 1
        );
        CREATE TABLE players (
            id TEXT PRIMARY KEY, game_id TEXT, user_id TEXT, username TEXT, x INTEGER, y INTEGER,
            hp INTEGER, ap INTEGER, range_level INTEGER DEFAULT 1, is_alive INTEGER DEFAULT 1,
            eliminated_by TEXT, eliminated_at TEXT, armistice_vote INTEGER DEFAULT 0,
            conference_vote INTEGER DEFAULT NULL,
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
        self.game_id = "conf_game"
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT INTO games (id, name, status, created_by, created_at, conference_status) VALUES (?, 'ConfTest', 'ACTIVE', 'u1', ?, 'CONVENED')",
            (self.game_id, now)
        )
        self.conn.execute("INSERT INTO game_parameters (game_id) VALUES (?)", (self.game_id,))

    def tearDown(self):
        self.conn.close()

    def test_conference_all_agree_armistice_victory(self):
        """When 100% of living players agree, collective Armistice victory is awarded."""
        now = datetime.now(timezone.utc).isoformat()
        for i in range(1, 4):
            self.conn.execute(
                "INSERT INTO players (id, game_id, user_id, username, x, y, hp, ap, is_alive, conference_vote, last_active_at, spawn_x, spawn_y) VALUES (?, ?, ?, ?, ?, ?, 3, 0, 1, 1, ?, ?, ?)",
                (f"p{i}", self.game_id, f"u{i}", f"Player{i}", i, i, now, i, i)
            )

        res = evaluate_diplomatic_conference(self.conn, self.game_id, 1000, now)
        self.assertTrue(res["ended"])
        self.assertEqual(res["outcome"], "ALL_AGREE")
        self.assertEqual(res["condition"], "ARMISTICE")

        cur = self.conn.cursor()
        cur.execute("SELECT status, winner_type FROM games WHERE id = ?", (self.game_id,))
        g = cur.fetchone()
        self.assertEqual(g["status"], "ENDED")
        self.assertEqual(g["winner_type"], "ARMISTICE")

    def test_conference_one_disagrees_rogue_grant_ap(self):
        """When living=3 and exactly 1 disagrees, rogue resolution GRANT_AP awards bonus AP."""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT INTO players (id, game_id, user_id, username, x, y, hp, ap, is_alive, conference_vote, last_active_at, spawn_x, spawn_y) VALUES ('p1', ?, 'u1', 'Player1', 1, 1, 3, 0, 1, 1, ?, 1, 1)",
            (self.game_id, now)
        )
        self.conn.execute(
            "INSERT INTO players (id, game_id, user_id, username, x, y, hp, ap, is_alive, conference_vote, last_active_at, spawn_x, spawn_y) VALUES ('p2', ?, 'u2', 'Player2', 2, 2, 3, 0, 1, 1, ?, 2, 2)",
            (self.game_id, now)
        )
        self.conn.execute(
            "INSERT INTO players (id, game_id, user_id, username, x, y, hp, ap, is_alive, conference_vote, last_active_at, spawn_x, spawn_y) VALUES ('p3', ?, 'u3', 'RoguePlayer', 3, 3, 3, 1, 1, 0, ?, 3, 3)",
            (self.game_id, now)
        )

        res = evaluate_diplomatic_conference(self.conn, self.game_id, 2000, now)
        self.assertFalse(res["ended"])
        self.assertEqual(res["outcome"], "ONE_DISAGREES")
        self.assertEqual(res["condition"], "ROGUE_GRANT_AP")
        self.assertEqual(res["bonus_ap"], 3)

        cur = self.conn.cursor()
        cur.execute("SELECT ap FROM players WHERE id = 'p3'")
        rogue_ap = cur.fetchone()["ap"]
        self.assertEqual(rogue_ap, 4) # initial 1 + 3 bonus

    def test_conference_multi_disagree_resume_combat(self):
        """When >1 disagree with RESUME_COMBAT mode, conference collapses and combat resumes."""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "INSERT INTO players (id, game_id, user_id, username, x, y, hp, ap, is_alive, conference_vote, last_active_at, spawn_x, spawn_y) VALUES ('p1', ?, 'u1', 'Player1', 1, 1, 3, 0, 1, 1, ?, 1, 1)",
            (self.game_id, now)
        )
        self.conn.execute(
            "INSERT INTO players (id, game_id, user_id, username, x, y, hp, ap, is_alive, conference_vote, last_active_at, spawn_x, spawn_y) VALUES ('p2', ?, 'u2', 'Player2', 2, 2, 3, 0, 1, 0, ?, 2, 2)",
            (self.game_id, now)
        )
        self.conn.execute(
            "INSERT INTO players (id, game_id, user_id, username, x, y, hp, ap, is_alive, conference_vote, last_active_at, spawn_x, spawn_y) VALUES ('p3', ?, 'u3', 'Player3', 3, 3, 3, 0, 1, 0, ?, 3, 3)",
            (self.game_id, now)
        )

        res = evaluate_diplomatic_conference(self.conn, self.game_id, 3000, now)
        self.assertFalse(res["ended"])
        self.assertEqual(res["outcome"], "MULTI_DISAGREE")
        self.assertEqual(res["condition"], "RESUME_COMBAT")

        cur = self.conn.cursor()
        cur.execute("SELECT conference_status FROM games WHERE id = ?", (self.game_id,))
        self.assertEqual(cur.fetchone()["conference_status"], "COOLDOWN")

    def test_conference_multi_disagree_punish_dissenters(self):
        """When multi-disagree mode is PUNISH_DISAGREERS, dissenters lose 1 HP."""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE game_parameters SET armistice_outcome_multi_disagree = 'PUNISH_DISAGREERS' WHERE game_id = ?",
            (self.game_id,)
        )
        self.conn.execute(
            "INSERT INTO players (id, game_id, user_id, username, x, y, hp, ap, is_alive, conference_vote, last_active_at, spawn_x, spawn_y) VALUES ('p1', ?, 'u1', 'Player1', 1, 1, 3, 0, 1, 1, ?, 1, 1)",
            (self.game_id, now)
        )
        self.conn.execute(
            "INSERT INTO players (id, game_id, user_id, username, x, y, hp, ap, is_alive, conference_vote, last_active_at, spawn_x, spawn_y) VALUES ('p2', ?, 'u2', 'Dissenter1', 2, 2, 3, 0, 1, 0, ?, 2, 2)",
            (self.game_id, now)
        )
        self.conn.execute(
            "INSERT INTO players (id, game_id, user_id, username, x, y, hp, ap, is_alive, conference_vote, last_active_at, spawn_x, spawn_y) VALUES ('p3', ?, 'u3', 'Dissenter2', 3, 3, 2, 0, 1, 0, ?, 3, 3)",
            (self.game_id, now)
        )

        res = evaluate_diplomatic_conference(self.conn, self.game_id, 4000, now)
        self.assertFalse(res["ended"])
        self.assertEqual(res["condition"], "PUNISH_DISAGREERS")

        cur = self.conn.cursor()
        cur.execute("SELECT id, hp FROM players WHERE id IN ('p2', 'p3')")
        hp_map = {r["id"]: r["hp"] for r in cur.fetchall()}
        self.assertEqual(hp_map["p2"], 2) # was 3
        self.assertEqual(hp_map["p3"], 1) # was 2

    def test_conference_multi_disagree_draw(self):
        """When multi-disagree mode is DRAW, match terminates in Automatic Draw."""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE game_parameters SET armistice_outcome_multi_disagree = 'DRAW' WHERE game_id = ?",
            (self.game_id,)
        )
        self.conn.execute(
            "INSERT INTO players (id, game_id, user_id, username, x, y, hp, ap, is_alive, conference_vote, last_active_at, spawn_x, spawn_y) VALUES ('p1', ?, 'u1', 'Player1', 1, 1, 3, 0, 1, 1, ?, 1, 1)",
            (self.game_id, now)
        )
        self.conn.execute(
            "INSERT INTO players (id, game_id, user_id, username, x, y, hp, ap, is_alive, conference_vote, last_active_at, spawn_x, spawn_y) VALUES ('p2', ?, 'u2', 'Dissenter1', 2, 2, 3, 0, 1, 0, ?, 2, 2)",
            (self.game_id, now)
        )
        self.conn.execute(
            "INSERT INTO players (id, game_id, user_id, username, x, y, hp, ap, is_alive, conference_vote, last_active_at, spawn_x, spawn_y) VALUES ('p3', ?, 'u3', 'Dissenter2', 3, 3, 3, 0, 1, 0, ?, 3, 3)",
            (self.game_id, now)
        )

        res = evaluate_diplomatic_conference(self.conn, self.game_id, 4500, now)
        self.assertTrue(res["ended"])
        self.assertEqual(res["condition"], "DRAW")

        cur = self.conn.cursor()
        cur.execute("SELECT status, winner_type FROM games WHERE id = ?", (self.game_id,))
        g = cur.fetchone()
        self.assertEqual(g["status"], "ENDED")
        self.assertEqual(g["winner_type"], "DRAW")

    def test_zero_fee_ap_transfer(self):
        """Default give_ap_cost_ap=0 transfers 1 AP directly with no fee."""
        cur = self.conn.cursor()
        cur.execute("SELECT give_ap_cost_ap FROM game_parameters WHERE game_id = ?", (self.game_id,))
        cfg = cur.fetchone()
        self.assertEqual(cfg["give_ap_cost_ap"], 0)

        cost = calculate_action_cost("GIVE_AP", 1, cfg)
        self.assertEqual(cost, 1) # 1 AP transferred + 0 AP fee

        now = datetime.now(timezone.utc).isoformat()
        # Ensure game is in IDLE conference status for normal tactical actions
        self.conn.execute("UPDATE games SET conference_status = 'IDLE' WHERE id = ?", (self.game_id,))

        # Verify that if conference is CONVENED, actions are suspended
        self.conn.execute("UPDATE games SET conference_status = 'CONVENED' WHERE id = ?", (self.game_id,))
        blocked_ok, blocked_msg, _, _ = execute_action_batch(self.conn, self.game_id, "u1", [{"action_type": "MOVE", "target_x": 1, "target_y": 2}], 5000, now)
        self.assertFalse(blocked_ok)
        self.assertIn("ERR_CONFERENCE_IN_SESSION", blocked_msg)

        # Restore IDLE to test normal zero-fee AP transfer
        self.conn.execute("UPDATE games SET conference_status = 'IDLE' WHERE id = ?", (self.game_id,))

        # Seed sender p1 with 3 AP, target p2 with 0 AP
        self.conn.execute(
            "INSERT INTO players (id, game_id, user_id, username, x, y, hp, ap, is_alive, last_active_at, spawn_x, spawn_y) VALUES ('p1', ?, 'u1', 'Sender', 1, 1, 3, 3, 1, ?, 1, 1)",
            (self.game_id, now)
        )
        self.conn.execute(
            "INSERT INTO players (id, game_id, user_id, username, x, y, hp, ap, is_alive, last_active_at, spawn_x, spawn_y) VALUES ('p2', ?, 'u2', 'Receiver', 1, 2, 3, 0, 1, ?, 1, 2)",
            (self.game_id, now)
        )

        actions = [{"action_type": "GIVE_AP", "target_player_id": "p2"}]
        success, msg, receipts, ap_spent = execute_action_batch(self.conn, self.game_id, "u1", actions, 5000, now)

        self.assertTrue(success, f"Action execution failed: {msg}")
        self.assertEqual(ap_spent, 1)

        cur.execute("SELECT ap FROM players WHERE id = 'p1'")
        sender_ap = cur.fetchone()["ap"]
        self.assertEqual(sender_ap, 2)

        cur.execute("SELECT ap FROM players WHERE id = 'p2'")
        receiver_ap = cur.fetchone()["ap"]
        self.assertEqual(receiver_ap, 1)

    def test_password_hashing_and_verification(self):
        """Passcode hashing with Argon2id/PBKDF2 and verification."""
        pwd = "TacticalPassword123!"
        hashed = hash_password(pwd)
        self.assertTrue(verify_password(pwd, hashed))
        self.assertFalse(verify_password("WrongPassword", hashed))

    def test_armistice_disabled_gating(self):
        """When win_cond_armistice is 0, toggle_armistice_vote raises ValueError."""
        from app.services.game_service import toggle_armistice_vote
        from app.database import get_db_connection, init_db

        init_db()
        conn = get_db_connection()
        now = datetime.now(timezone.utc).isoformat()
        gid = "test_dis_armistice"
        conn.execute("INSERT OR IGNORE INTO users (id, username, password_hash, created_at) VALUES ('u1', 'TestUser1', 'hash', ?)", (now,))
        conn.execute("INSERT OR REPLACE INTO games (id, name, status, created_by, created_at) VALUES (?, 'NoArm', 'ACTIVE', 'u1', ?)", (gid, now))
        conn.execute("INSERT OR REPLACE INTO game_parameters (game_id, win_cond_armistice) VALUES (?, 0)", (gid,))
        conn.execute(
            "INSERT OR REPLACE INTO players (id, game_id, user_id, username, x, y, hp, ap, is_alive, last_active_at, spawn_x, spawn_y) VALUES ('p_arm', ?, 'u1', 'Operative', 1, 1, 3, 0, 1, ?, 1, 1)",
            (gid, now)
        )
        conn.commit()
        conn.close()

        with self.assertRaises(ValueError) as ctx:
            toggle_armistice_vote(gid, "u1")
        self.assertIn("disabled", str(ctx.exception).lower())

    def test_mid_game_parameter_audit_trail(self):
        """Admin updating parameters mid-game updates table and records audit log."""
        from app.services.audit_service import update_game_parameters_with_audit, get_game_audit_logs
        from app.database import get_db_connection, init_db

        init_db()
        gid = "test_audit_game"
        now = datetime.now(timezone.utc).isoformat()
        conn = get_db_connection()
        conn.execute("INSERT OR IGNORE INTO users (id, username, password_hash, created_at) VALUES ('u1', 'TestUser1', 'hash', ?)", (now,))
        conn.execute("INSERT OR REPLACE INTO games (id, name, status, created_by, created_at) VALUES (?, 'AuditG', 'ACTIVE', 'u1', ?)", (gid, now))
        conn.execute("INSERT OR REPLACE INTO game_parameters (game_id, burst_cap_ap, move_cost_ap) VALUES (?, 3, 1)", (gid,))
        conn.commit()
        conn.close()

        logs = update_game_parameters_with_audit(
            game_id=gid,
            admin_user_id="u_admin",
            admin_username="MasterAdmin",
            updates={"burst_cap_ap": 5, "move_cost_ap": 2}
        )
        self.assertEqual(len(logs), 2)

        audit_trail = get_game_audit_logs(gid)
        self.assertGreaterEqual(len(audit_trail), 2)
        params_modified = {l["param_key"] for l in audit_trail}
        self.assertIn("burst_cap_ap", params_modified)
        self.assertIn("move_cost_ap", params_modified)


if __name__ == "__main__":
    unittest.main()
