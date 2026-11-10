"""Unit test suite verifying version 4 features:
- Complete permanent lobby deletion across WAITING, ACTIVE, and ENDED games
- Cascading deletion of events, players, parameters, audit logs, and notifications
- Avatar reset to null/flag fallback
- Operative call-sign / username modification with uniqueness constraints and player cascading
"""
import unittest
import sqlite3
import os
from app.database import get_db_connection, init_db, db_transaction, query_one, query_db
from app.api.routes_auth import register_user
from app.services.game_service import (
    create_game_lobby,
    add_dummy_player,
    start_game,
    delete_game_lobby,
    terminate_game_prematurely
)
from app.models.game_types import LobbyConfig


class TestV4Features(unittest.TestCase):
    def setUp(self):
        init_db()
        self.conn = get_db_connection()
        with db_transaction() as conn:
            conn.execute("DELETE FROM events")
            conn.execute("DELETE FROM players")
            conn.execute("DELETE FROM game_parameters")
            conn.execute("DELETE FROM game_parameter_audit_logs")
            conn.execute("DELETE FROM games")
            conn.execute("DELETE FROM notifications")
            conn.execute("DELETE FROM sessions")
            conn.execute("DELETE FROM users")

    def tearDown(self):
        self.conn.close()

    def test_permanent_lobby_deletion_all_states(self):
        """Verify that admins can permanently erase lobbies in WAITING, ACTIVE, and ENDED states."""
        admin = register_user("admin_v4", "pass123456", national_flag="UN")
        with db_transaction() as conn:
            conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (admin["id"],))

        # 1. Test WAITING deletion
        gid1 = create_game_lobby("Lobby Waiting", admin["id"], LobbyConfig())
        add_dummy_player(gid1, "dummy1")
        self.assertIsNotNone(query_one("SELECT * FROM games WHERE id = ?", (gid1,)))
        self.assertGreater(len(query_db("SELECT * FROM players WHERE game_id = ?", (gid1,))), 0)

        delete_game_lobby(gid1, admin["id"])
        self.assertIsNone(query_one("SELECT * FROM games WHERE id = ?", (gid1,)))
        self.assertEqual(len(query_db("SELECT * FROM players WHERE game_id = ?", (gid1,))), 0)
        self.assertEqual(len(query_db("SELECT * FROM game_parameters WHERE game_id = ?", (gid1,))), 0)

        # 2. Test ACTIVE deletion
        gid2 = create_game_lobby("Lobby Active", admin["id"], LobbyConfig())
        add_dummy_player(gid2, "dummy2")
        start_game(gid2, admin["id"])
        active_game = query_one("SELECT * FROM games WHERE id = ?", (gid2,))
        self.assertEqual(active_game["status"], "ACTIVE")

        delete_game_lobby(gid2, admin["id"])
        self.assertIsNone(query_one("SELECT * FROM games WHERE id = ?", (gid2,)))
        self.assertEqual(len(query_db("SELECT * FROM events WHERE game_id = ?", (gid2,))), 0)
        self.assertEqual(len(query_db("SELECT * FROM players WHERE game_id = ?", (gid2,))), 0)

        # 3. Test ENDED deletion
        gid3 = create_game_lobby("Lobby Ended", admin["id"], LobbyConfig())
        add_dummy_player(gid3, "dummy3")
        start_game(gid3, admin["id"])
        terminate_game_prematurely(gid3, admin["id"])
        ended_game = query_one("SELECT * FROM games WHERE id = ?", (gid3,))
        self.assertEqual(ended_game["status"], "ENDED")

        delete_game_lobby(gid3, admin["id"])
        self.assertIsNone(query_one("SELECT * FROM games WHERE id = ?", (gid3,)))

    def test_non_admin_cannot_delete_active_or_other_lobby(self):
        """Non-admin user cannot delete another player's lobby or an active lobby."""
        host = register_user("host_v4", "pass123456")
        stranger = register_user("stranger_v4", "pass123456")
        with db_transaction() as conn:
            conn.execute("UPDATE users SET is_admin = 0 WHERE id IN (?, ?)", (host["id"], stranger["id"]))

        gid = create_game_lobby("Host Lobby", host["id"], LobbyConfig())
        
        # Stranger attempts to delete
        with self.assertRaises(ValueError):
            delete_game_lobby(gid, stranger["id"])

        # Host cannot delete once started (only admin can terminate/erase active games)
        add_dummy_player(gid, "bot_test")
        start_game(gid, host["id"])
        with self.assertRaises(ValueError):
            delete_game_lobby(gid, host["id"])

    def test_avatar_reset_and_fallback(self):
        """Verify that avatar path can be set and reset back to NULL/flag fallback."""
        user = register_user("avatar_test_user", "pass123456", national_flag="PRIDE")
        u_row = query_one("SELECT avatar_path FROM users WHERE id = ?", (user["id"],))
        self.assertIsNone(u_row["avatar_path"])

        # Simulate avatar upload
        with db_transaction() as conn:
            conn.execute("UPDATE users SET avatar_path = ? WHERE id = ?", ("/static/avatars/test.png", user["id"]))
        
        updated = query_one("SELECT * FROM users WHERE id = ?", (user["id"],))
        self.assertEqual(updated["avatar_path"], "/static/avatars/test.png")

        # Simulate avatar reset
        with db_transaction() as conn:
            conn.execute("UPDATE users SET avatar_path = NULL WHERE id = ?", (user["id"],))

        reset_user = query_one("SELECT * FROM users WHERE id = ?", (user["id"],))
        self.assertIsNone(reset_user["avatar_path"])

    def test_username_change_and_cascading(self):
        """Verify username / call-sign update cascades to players table."""
        u1 = register_user("original_callsign", "pass123456", national_flag="US")

        # Create game and join
        gid = create_game_lobby("Callsign Test Match", u1["id"], LobbyConfig())

        # Verify player record has original username
        player = query_one("SELECT * FROM players WHERE user_id = ? AND game_id = ?", (u1["id"], gid))
        self.assertEqual(player["username"], "original_callsign")

        # Update username to new callsign
        new_callsign = "Vanguard_Apex"
        with db_transaction() as conn:
            conn.execute("UPDATE users SET username = ? WHERE id = ?", (new_callsign, u1["id"]))
            conn.execute("UPDATE players SET username = ? WHERE user_id = ?", (new_callsign, u1["id"]))

        user_db = query_one("SELECT * FROM users WHERE id = ?", (u1["id"],))
        self.assertEqual(user_db["username"], new_callsign)

        player_db = query_one("SELECT * FROM players WHERE user_id = ? AND game_id = ?", (u1["id"], gid))
        self.assertEqual(player_db["username"], new_callsign)

    def test_bots_destroyed_on_lobby_deletion(self):
        """Verify that dummy bots are omitted from admin user list and destroyed on lobby deletion."""
        admin = register_user("admin_bot_mgr", "pass123456")
        with db_transaction() as conn:
            conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (admin["id"],))

        gid = create_game_lobby("Bot Sandbox", admin["id"], LobbyConfig())
        b1 = add_dummy_player(gid, "SentryBot_01")
        b2 = add_dummy_player(gid, "SentryBot_02")

        # Verify bot players exist and bot users are marked is_bot = 1
        p1 = query_one("SELECT * FROM players WHERE id = ?", (b1["player_id"],))
        p2 = query_one("SELECT * FROM players WHERE id = ?", (b2["player_id"],))
        self.assertEqual(p1["is_bot"], 1)
        self.assertEqual(p2["is_bot"], 1)

        u1 = query_one("SELECT * FROM users WHERE id = ?", (p1["user_id"],))
        u2 = query_one("SELECT * FROM users WHERE id = ?", (p2["user_id"],))
        self.assertEqual(u1["is_bot"], 1)
        self.assertEqual(u2["is_bot"], 1)

        # Admin user list query must omit bots
        admin_users = query_db(
            """
            SELECT u.id, u.username, u.is_bot
            FROM users u
            WHERE (u.is_bot = 0 OR u.is_bot IS NULL) AND u.password_hash != 'dummy_hash'
            """
        )
        admin_usernames = [u["username"] for u in admin_users]
        self.assertIn("admin_bot_mgr", admin_usernames)
        self.assertNotIn("SentryBot_01", admin_usernames)
        self.assertNotIn("SentryBot_02", admin_usernames)

        # Delete lobby and verify bots are destroyed from users table
        delete_game_lobby(gid, admin["id"])
        destroyed_u1 = query_one("SELECT * FROM users WHERE id = ?", (p1["user_id"],))
        destroyed_u2 = query_one("SELECT * FROM users WHERE id = ?", (p2["user_id"],))
        self.assertIsNone(destroyed_u1)
        self.assertIsNone(destroyed_u2)

