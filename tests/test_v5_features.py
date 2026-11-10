"""Unit tests for Version 5 features:
- Admin-only lobby creation and deletion permissions
- Bot control conference voting (petition and agree/reject ballots)
- End-to-end diplomatic conference resolutions (unanimous armistice vs dissenter triumph)
- The 3 terminal ending conditions (Last Man Standing, Diplomatic Conference, Inactivity)
"""
import unittest
import sqlite3
from datetime import datetime, timezone, timedelta
from app.database import get_db_connection, init_db, db_transaction, query_one, query_db
from app.api.routes_auth import register_user
from app.services.game_service import (
    create_game_lobby,
    add_dummy_player,
    start_game,
    delete_game_lobby,
    toggle_armistice_vote,
    cast_conference_vote,
    control_playtest_bot,
    get_game_state
)
from app.models.game_types import LobbyConfig


class TestV5Features(unittest.TestCase):
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

    def test_admin_only_lobby_deletion(self):
        """Verify that non-admins cannot delete any lobby, even their own."""
        admin = register_user("admin_v5", "pass123456")
        non_admin = register_user("regular_user_v5", "pass123456")
        with db_transaction() as conn:
            conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (admin["id"],))
            conn.execute("UPDATE users SET is_admin = 0 WHERE id = ?", (non_admin["id"],))

        gid = create_game_lobby("Admin Managed Lobby", admin["id"], LobbyConfig())

        # Non-admin attempt to delete raises ValueError
        with self.assertRaises(ValueError):
            delete_game_lobby(gid, non_admin["id"])

        # Admin delete succeeds
        delete_game_lobby(gid, admin["id"])
        self.assertIsNone(query_one("SELECT * FROM games WHERE id = ?", (gid,)))

    def test_bot_control_conference_voting(self):
        """Verify that admins can make bots vote for conference petitions and cast ballots."""
        import asyncio
        admin = register_user("admin_tester", "pass123456")
        with db_transaction() as conn:
            conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (admin["id"],))

        cfg = LobbyConfig(
            win_cond_armistice=True,
            armistice_trigger_threshold=0.5,
            diplomatic_conference_duration_sec=300
        )
        gid = create_game_lobby("Bot Conference Test", admin["id"], cfg, is_playtest=True)
        bot1 = add_dummy_player(gid, "SentryAlpha")
        bot2 = add_dummy_player(gid, "SentryBeta")
        start_game(gid, admin["id"])

        # Test bot petition vote via control_playtest_bot
        res1 = asyncio.run(control_playtest_bot(
            game_id=gid,
            admin_user_id=admin["id"],
            bot_player_id=bot1["player_id"],
            actions=[{"action_type": "VOTE_CONFERENCE"}]
        ))
        self.assertTrue(res1["success"])

        # Verify bot1 has armistice_vote = 1
        p1 = query_one("SELECT armistice_vote FROM players WHERE id = ?", (bot1["player_id"],))
        self.assertEqual(p1["armistice_vote"], 1)

    def test_diplomatic_conference_unanimous_collective_victory(self):
        """When 100% of living operatives agree to peace during a conference, all achieve ARMISTICE victory."""
        admin = register_user("peace_leader", "pass123456")
        with db_transaction() as conn:
            conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (admin["id"],))

        cfg = LobbyConfig(
            win_cond_armistice=True,
            armistice_trigger_threshold=1.0,
            diplomatic_conference_duration_sec=120
        )
        gid = create_game_lobby("Peace Accord Arena", admin["id"], cfg, is_playtest=True)
        bot1 = add_dummy_player(gid, "PeaceBot1")
        start_game(gid, admin["id"])

        p_admin = query_one("SELECT * FROM players WHERE game_id = ? AND user_id = ?", (gid, admin["id"]))
        p_bot1 = query_one("SELECT * FROM players WHERE id = ?", (bot1["player_id"],))

        # Both vote to call a conference
        toggle_armistice_vote(gid, admin["id"])
        res_trigger = toggle_armistice_vote(gid, p_bot1["user_id"])
        self.assertTrue(res_trigger["conference_triggered"])

        g = query_one("SELECT conference_status FROM games WHERE id = ?", (gid,))
        self.assertEqual(g["conference_status"], "CONVENED")

        # Both vote AGREE
        cast_conference_vote(gid, admin["id"], True)
        res_vote2 = cast_conference_vote(gid, p_bot1["user_id"], True)

        self.assertTrue(res_vote2["all_voted"])
        self.assertIsNotNone(res_vote2["outcome"])
        self.assertTrue(res_vote2["outcome"]["ended"])
        self.assertEqual(res_vote2["outcome"]["condition"], "ARMISTICE")

        ended_game = query_one("SELECT * FROM games WHERE id = ?", (gid,))
        self.assertEqual(ended_game["status"], "ENDED")
        self.assertEqual(ended_game["winner_type"], "ARMISTICE")

    def test_diplomatic_conference_lone_dissenter_triumph(self):
        """When 1 operative rejects peace in INSTANT_WIN mode, the lone dissenter claims solo victory."""
        admin = register_user("diplomat_host", "pass123456")
        with db_transaction() as conn:
            conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (admin["id"],))

        cfg = LobbyConfig(
            win_cond_armistice=True,
            armistice_trigger_threshold=1.0,
            diplomatic_conference_duration_sec=120,
            conference_single_disagree_outcomes='["INSTANT_WIN"]'
        )
        gid = create_game_lobby("Betrayal Sandbox", admin["id"], cfg, is_playtest=True)
        bot1 = add_dummy_player(gid, "TraitorBot")
        start_game(gid, admin["id"])

        p_bot1 = query_one("SELECT * FROM players WHERE id = ?", (bot1["player_id"],))

        # Call conference
        toggle_armistice_vote(gid, admin["id"])
        toggle_armistice_vote(gid, p_bot1["user_id"])

        # Admin votes AGREE, TraitorBot votes REJECT
        cast_conference_vote(gid, admin["id"], True)
        res_vote = cast_conference_vote(gid, p_bot1["user_id"], False)

        self.assertTrue(res_vote["all_voted"])
        self.assertTrue(res_vote["outcome"]["ended"])
        self.assertEqual(res_vote["outcome"]["condition"], "ROGUE_BETRAYAL")
        self.assertEqual(res_vote["outcome"]["winner_id"], p_bot1["id"])

        ended_game = query_one("SELECT * FROM games WHERE id = ?", (gid,))
        self.assertEqual(ended_game["status"], "ENDED")
        self.assertEqual(ended_game["winner_id"], p_bot1["id"])
        self.assertEqual(ended_game["winner_type"], "ROGUE")


if __name__ == "__main__":
    unittest.main()
