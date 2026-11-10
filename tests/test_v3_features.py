"""Unit test suite verifying all version 3 features and capabilities:
- Universal multi-consequence conference evaluations (both 1 and >1 dissenters)
- Conference transparency modes and vote count masking
- Board sequence synchronization and stale detection
- Playtest game mode: admin bot control, AP awards, bot-only enforcement
- Premature termination and lobby deletion
- Database backup and restore roundtrip
- Extended Unicode flags registry and emoji resolution
"""
import unittest
import json
import sqlite3
import os
import asyncio
from app.database import get_db_connection, init_db, db_transaction, query_one, query_db
from app.flags import code_to_emoji, is_valid_country_code, ISO_FLAGS
from app.services.backup_service import export_database_backup, restore_database_backup
from app.api.routes_auth import register_user, authenticate_user
from app.services.game_service import (
    create_game_lobby,
    join_game_lobby,
    add_dummy_player,
    start_game,
    delete_game_lobby,
    terminate_game_prematurely,
    award_playtest_ap,
    control_playtest_bot,
    get_game_state
)
from app.models.game_types import LobbyConfig, ActionType
from app.engine.actions import execute_action_batch
from app.engine.win_conditions import evaluate_diplomatic_conference


class TestV3Features(unittest.TestCase):
    def setUp(self):
        # Initialize database tables
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

    def test_unicode_flags_resolution(self):
        """Verify that extended Unicode flags correctly resolve to emojis."""
        self.assertEqual(code_to_emoji("FI"), "🇫🇮")
        self.assertEqual(code_to_emoji("fi"), "🇫🇮")
        self.assertEqual(code_to_emoji("PRIDE"), "🏳️‍🌈")
        self.assertEqual(code_to_emoji("FINISH"), "🏁")
        self.assertEqual(code_to_emoji("PIRATE"), "🏴‍☠️")
        self.assertEqual(code_to_emoji("PEACE"), "🏳️")
        self.assertEqual(code_to_emoji("BLACK"), "🏴")
        self.assertEqual(code_to_emoji("EU"), "🇪🇺")
        self.assertEqual(code_to_emoji("US"), "🇺🇸")
        self.assertEqual(code_to_emoji("RU"), "🇷🇺")
        # Emoji string itself passes through
        self.assertEqual(code_to_emoji("🇫🇮"), "🇫🇮")
        self.assertEqual(code_to_emoji("🏳️‍🌈"), "🏳️‍🌈")

        self.assertTrue(is_valid_country_code("FI"))
        self.assertTrue(is_valid_country_code("PRIDE"))
        self.assertTrue(is_valid_country_code("FINISH"))
        self.assertTrue(is_valid_country_code("PIRATE"))

    def test_stale_sequence_detection(self):
        """Verify that execute_action_batch rejects commands when expected_seq is stale."""
        # Setup admin and game
        admin = register_user("admin_seq", "password123", national_flag="UN")
        with db_transaction() as conn:
            conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (admin["id"],))

        cfg = LobbyConfig(board_width=10, board_height=10, burst_cap_ap=5, starting_ap=5)
        gid = create_game_lobby("Seq Test", admin["id"], cfg)
        add_dummy_player(gid, "DummyTarget")
        start_game(gid, admin["id"])

        # Fetch current max event sequence
        cur = self.conn.cursor()
        cur.execute("SELECT MAX(event_seq) as max_seq FROM events WHERE game_id = ?", (gid,))
        max_seq = cur.fetchone()["max_seq"] or 0

        # Execute action with correct expected_seq
        with db_transaction() as conn:
            ok, err, receipts, spent = execute_action_batch(
                conn=conn,
                game_id=gid,
                initiator_user_id=admin["id"],
                actions_raw=[{"action_type": "UPGRADE_RANGE"}],
                timestamp_ns=1000000,
                now_iso="2026-11-07T00:00:00Z",
                expected_seq=max_seq
            )
            self.assertTrue(ok, f"Expected success with matching seq, got error: {err}")

        # Now attempt action with the old max_seq (which is now stale!)
        with db_transaction() as conn:
            ok, err, receipts, spent = execute_action_batch(
                conn=conn,
                game_id=gid,
                initiator_user_id=admin["id"],
                actions_raw=[{"action_type": "UPGRADE_RANGE"}],
                timestamp_ns=2000000,
                now_iso="2026-11-07T00:01:00Z",
                expected_seq=max_seq
            )
            self.assertFalse(ok)
            self.assertIn("ERR_STALE_STATE", err)

    def test_playtest_mode_rules_and_controls(self):
        """Verify playtest match restrictions, AP distribution, and bot control."""
        # 1. Admin creator
        admin = register_user("admin_pt", "password123", national_flag="FI")
        with db_transaction() as conn:
            conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (admin["id"],))

        # 2. Non-admin player
        civilian = register_user("civilian_pt", "password123", national_flag="US")

        # 3. Create playtest match
        cfg = LobbyConfig(board_width=12, board_height=12, starting_ap=0)
        gid = create_game_lobby("Playtest Grid", admin["id"], cfg, is_playtest=True)

        # 4. Verify civilian cannot join playtest match
        with self.assertRaises(ValueError) as ctx:
            join_game_lobby(gid, civilian["id"])
        self.assertIn("Only bots are permitted", str(ctx.exception))

        # 5. Add bot to game
        bot_p = add_dummy_player(gid, "PlaytestBot")
        cur = self.conn.cursor()
        cur.execute("SELECT is_bot FROM players WHERE id = ?", (bot_p["player_id"],))
        self.assertEqual(cur.fetchone()["is_bot"], 1)

        # 6. Start game
        start_game(gid, admin["id"])

        # 7. Admin awards +2 AP to all living combatants
        award_playtest_ap(gid, admin["id"], amount=2)
        cur.execute("SELECT ap FROM players WHERE id = ?", (bot_p["player_id"],))
        bot_ap_after_award = cur.fetchone()["ap"]
        self.assertEqual(bot_ap_after_award, 2)

        # 8. Admin commands bot to upgrade range
        asyncio.run(control_playtest_bot(
            game_id=gid,
            admin_user_id=admin["id"],
            bot_player_id=bot_p["player_id"],
            action_batch={"actions": [{"action_type": "UPGRADE_RANGE"}]}
        ))
        cur.execute("SELECT range_level, ap FROM players WHERE id = ?", (bot_p["player_id"],))
        bot_row = cur.fetchone()
        self.assertEqual(bot_row["range_level"], 2)
        self.assertEqual(bot_row["ap"], 0)

        # 9. Verify premature termination by admin
        terminate_game_prematurely(gid, admin["id"])
        cur.execute("SELECT status FROM games WHERE id = ?", (gid,))
        self.assertEqual(cur.fetchone()["status"], "ENDED")

    def test_lobby_deletion(self):
        """Verify that waiting lobbies can be deleted by host/admin."""
        admin = register_user("admin_del", "password123", national_flag="UN")
        cfg = LobbyConfig()
        gid = create_game_lobby("Lobby to Delete", admin["id"], cfg)

        # Delete waiting lobby
        delete_game_lobby(gid, admin["id"])
        cur = self.conn.cursor()
        cur.execute("SELECT id FROM games WHERE id = ?", (gid,))
        self.assertIsNone(cur.fetchone())

    def test_conference_multi_consequences_evaluation(self):
        """Verify universal consequences application for single and multi-dissenters."""
        admin = register_user("admin_conf", "password123", national_flag="UN")
        with db_transaction() as conn:
            conn.execute("UPDATE users SET is_admin = 1 WHERE id = ?", (admin["id"],))

        # Test single dissenter with [REVEAL_IDENTITY, GRANT_AP]
        cfg = LobbyConfig(
            win_cond_armistice=True,
            conference_single_disagree_outcomes='["REVEAL_IDENTITY", "GRANT_AP"]',
            rogue_bonus_ap=5
        )
        gid = create_game_lobby("Conf Single", admin["id"], cfg)
        p1 = add_dummy_player(gid, "Unit1")
        p2 = add_dummy_player(gid, "Unit2")
        start_game(gid, admin["id"])

        # Set conference active: admin and Unit1 agree, Unit2 disagrees
        with db_transaction() as conn:
            conn.execute(
                """
                UPDATE games
                SET conference_status = 'CONVENED', conference_ends_at = '2026-11-07T00:00:00Z'
                WHERE id = ?
                """,
                (gid,)
            )
            # Admin and Unit1 vote agree (1), Unit2 votes reject (0)
            conn.execute("UPDATE players SET conference_vote = 1 WHERE game_id = ? AND username != 'Unit2'", (gid,))
            conn.execute("UPDATE players SET conference_vote = 0 WHERE game_id = ? AND username = 'Unit2'", (gid,))

            outcome = evaluate_diplomatic_conference(conn, gid, 1000, "2026-11-07T00:00:00Z")

        self.assertFalse(outcome["ended"])
        self.assertIn("REVEAL_IDENTITY", outcome["applied_effects"])
        self.assertIn("GRANT_AP", outcome["applied_effects"])

        # Verify Unit2 received 5 bonus AP
        cur = self.conn.cursor()
        cur.execute("SELECT ap FROM players WHERE game_id = ? AND username = 'Unit2'", (gid,))
        self.assertEqual(cur.fetchone()["ap"], 5)

        # Test multi-dissenter with [PUNISH_DISAGREERS, DRAW]
        cfg2 = LobbyConfig(
            win_cond_armistice=True,
            conference_multi_disagree_outcomes='["PUNISH_DISAGREERS", "DRAW"]'
        )
        gid2 = create_game_lobby("Conf Multi", admin["id"], cfg2)
        m1 = add_dummy_player(gid2, "MUnit1")
        m2 = add_dummy_player(gid2, "MUnit2")
        start_game(gid2, admin["id"])

        with db_transaction() as conn:
            conn.execute(
                """
                UPDATE games
                SET conference_status = 'CONVENED', conference_ends_at = '2026-11-07T00:00:00Z'
                WHERE id = ?
                """,
                (gid2,)
            )
            # Admin votes 1, MUnit1 and MUnit2 vote 0 (2 dissenters)
            conn.execute("UPDATE players SET conference_vote = 1 WHERE game_id = ? AND username = 'admin_conf'", (gid2,))
            conn.execute("UPDATE players SET conference_vote = 0 WHERE game_id = ? AND username IN ('MUnit1', 'MUnit2')", (gid2,))

            outcome2 = evaluate_diplomatic_conference(conn, gid2, 2000, "2026-11-07T00:00:00Z")

        self.assertTrue(outcome2["ended"])
        self.assertEqual(outcome2["condition"], "DRAW")
        self.assertIn("PUNISH_DISAGREERS", outcome2["applied_effects"])
        self.assertIn("DRAW", outcome2["applied_effects"])

        # Verify dissenters lost 1 HP
        cur.execute("SELECT hp FROM players WHERE game_id = ? AND username = 'MUnit1'", (gid2,))
        self.assertEqual(cur.fetchone()["hp"], 2)

    def test_conference_transparency_masking(self):
        """Verify that get_game_state masks conference votes according to transparency settings."""
        admin = register_user("admin_mask", "password123", national_flag="UN")
        # 1. SHOW_TOTAL_AGREE mode
        cfg = LobbyConfig(conference_transparency="SHOW_TOTAL_AGREE")
        gid = create_game_lobby("Transp Game", admin["id"], cfg)
        d1 = add_dummy_player(gid, "TUnit1")
        start_game(gid, admin["id"])

        with db_transaction() as conn:
            conn.execute("UPDATE games SET conference_status = 'CONVENED' WHERE id = ?", (gid,))
            conn.execute("UPDATE players SET conference_vote = 1 WHERE game_id = ? AND username = 'admin_mask'", (gid,))
            conn.execute("UPDATE players SET conference_vote = 0 WHERE game_id = ? AND username = 'TUnit1'", (gid,))

        state = get_game_state(gid, current_user_id=admin["id"])
        # In SHOW_TOTAL_AGREE mode, individual vote for TUnit1 should be None
        tunit1 = [p for p in state["players"] if p["username"] == "TUnit1"][0]
        self.assertIsNone(tunit1["conference_vote"])
        # Self vote is always visible to oneself
        self_p = [p for p in state["players"] if p["username"] == "admin_mask"][0]
        self.assertEqual(self_p["conference_vote"], 1)
        # Total agree is exposed
        self.assertEqual(state["conference_total_agree"], 1)

    def test_database_backup_and_restore(self):
        """Verify that export_database_backup and restore_database_backup roundtrip correctly."""
        # Export initial backup
        backup_data = export_database_backup()
        self.assertIn("tables", backup_data)
        self.assertIn("users", backup_data["tables"])
        self.assertIn("games", backup_data["tables"])

        # Create a marker user
        u = register_user("marker_user", "password123", national_flag="FI")
        cur = self.conn.cursor()
        cur.execute("SELECT username FROM users WHERE username = 'marker_user'")
        self.assertIsNotNone(cur.fetchone())

        # Restore from the earlier backup (before marker_user existed)
        restore_database_backup(backup_data)

        # Verify marker_user no longer exists
        cur.execute("SELECT username FROM users WHERE username = 'marker_user'")
        self.assertIsNone(cur.fetchone())


if __name__ == "__main__":
    unittest.main()
