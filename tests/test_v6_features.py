"""Tests verifying all Version 6 features and constraints:
1. Anonymous non-lethal strikes (victim does NOT know who shot them; only killer revealed on fatal elimination).
2. Conference default transparency: REVEAL_COUNTS_AT_END.
3. Treaty Ratification Rule: Abstention (null vote upon timer expiration) counts as dissent.
4. Streamlined 3 consequences for single and multi-dissenter conference collapse.
5. Exact Level 1 and Level 2 range geometry (core square + 4 cardinal spikes).
"""
import unittest
import uuid
from datetime import datetime, timezone
from app.database import init_db, db_transaction, query_one, query_db
from app.api.routes_auth import register_user
from app.services.game_service import (
    create_game_lobby,
    start_game,
    join_game_lobby,
    toggle_armistice_vote,
    cast_conference_vote
)
from app.engine.actions import execute_action_batch
from app.engine.range import is_in_range
from app.engine.win_conditions import evaluate_diplomatic_conference
from app.models.game_types import LobbyConfig, ActionBatchPayload, ActionCommand, ActionType


class TestV6Features(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        self.suffix = uuid.uuid4().hex[:6]
        self.u1 = register_user(f"v6_alpha_{self.suffix}", "pass1234")
        self.u2 = register_user(f"v6_bravo_{self.suffix}", "pass1234")
        self.u3 = register_user(f"v6_charlie_{self.suffix}", "pass1234")

    def test_anonymous_non_lethal_strike_and_fatal_strike_killer_reveal(self):
        """Victim must not learn attacker callsign on non-fatal hits; killer revealed only on elimination."""
        cfg = LobbyConfig(
            board_width=10, board_height=10,
            starting_hp=2, starting_ap=5,
            starting_range_level=1
        )
        game_id = create_game_lobby(f"Combat V6 {self.suffix}", self.u1["id"], cfg)
        join_game_lobby(game_id, self.u2["id"])

        start_game(game_id, self.u1["id"])

        with db_transaction() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM players WHERE game_id = ? AND user_id = ?", (game_id, self.u1["id"]))
            p1 = cur.fetchone()["id"]
            cur.execute("SELECT id FROM players WHERE game_id = ? AND user_id = ?", (game_id, self.u2["id"]))
            p2 = cur.fetchone()["id"]
            # Position them adjacent
            cur.execute("UPDATE players SET x = 3, y = 3, hp = 2, ap = 5 WHERE id = ?", (p1,))
            cur.execute("UPDATE players SET x = 3, y = 4, hp = 2, ap = 0 WHERE id = ?", (p2,))

        # 1. Non-lethal strike (Target drops from 2 HP to 1 HP)
        actions = [{"action_type": "ATTACK", "target_player_id": p2}]
        with db_transaction() as conn:
            success, msg, receipts, ap_spent = execute_action_batch(
                conn, game_id, self.u1["id"], actions, 1000, "2026-01-01T00:00:00Z"
            )
        self.assertTrue(success)
        self.assertEqual(receipts[0]["result"], "HIT")
        self.assertEqual(receipts[0]["target_remaining_hp"], "UNKNOWN")

        # Inspect notification created for victim
        victim_notif = query_one(
            "SELECT * FROM notifications WHERE user_id = ? AND game_id = ? ORDER BY created_at DESC LIMIT 1",
            (self.u2["id"], game_id)
        )
        self.assertIsNotNone(victim_notif)
        self.assertEqual(victim_notif["event_type"], "UNDER_ATTACK")
        # Attacker's username MUST NOT appear in the notification message
        self.assertNotIn(self.u1["username"], victim_notif["message_en"])
        self.assertNotIn(self.u1["username"], victim_notif["message_ru"])
        self.assertIn("unknown shooter", victim_notif["message_en"].lower())
        self.assertIn("неизвестным противником", victim_notif["message_ru"].lower())

        # 2. Fatal strike (Target drops from 1 HP to 0 HP)
        with db_transaction() as conn:
            success_fatal, msg_fatal, receipts_fatal, _ = execute_action_batch(
                conn, game_id, self.u1["id"], actions, 2000, "2026-01-01T00:01:00Z"
            )
        self.assertTrue(success_fatal)
        self.assertEqual(receipts_fatal[0]["result"], "FATAL")

        fatal_notif = query_one(
            "SELECT * FROM notifications WHERE user_id = ? AND game_id = ? AND event_type = 'ELIMINATED' ORDER BY created_at DESC LIMIT 1",
            (self.u2["id"], game_id)
        )
        self.assertIsNotNone(fatal_notif)
        # Killer's username MUST appear in the elimination notification
        self.assertIn(self.u1["username"], fatal_notif["message_en"])
        self.assertIn(self.u1["username"], fatal_notif["message_ru"])

    def test_conference_default_transparency_and_abstention_dissent(self):
        """Default transparency is REVEAL_COUNTS_AT_END, and abstention (null vote) counts as dissent."""
        cfg = LobbyConfig(board_width=10, board_height=10, starting_hp=3)
        self.assertEqual(cfg.conference_transparency, "REVEAL_COUNTS_AT_END")

        game_id = create_game_lobby(f"Accord V6 {self.suffix}", self.u1["id"], cfg)
        join_game_lobby(game_id, self.u2["id"])
        join_game_lobby(game_id, self.u3["id"])
        start_game(game_id, self.u1["id"])

        now_iso = datetime.now(timezone.utc).isoformat()

        # All 3 living players vote for conference petition
        toggle_armistice_vote(game_id, self.u1["id"])
        toggle_armistice_vote(game_id, self.u2["id"])
        res_trig = toggle_armistice_vote(game_id, self.u3["id"])
        self.assertTrue(res_trig["conference_triggered"])

        g = query_one("SELECT conference_status FROM games WHERE id = ?", (game_id,))
        self.assertEqual(g["conference_status"], "CONVENED")

        # u1 votes AGREE, u2 votes AGREE, u3 ABSTAINS (does not vote)
        cast_conference_vote(game_id, self.u1["id"], True)
        cast_conference_vote(game_id, self.u2["id"], True)

        # Deliberation expires with u3 abstaining (vote is NULL)
        with db_transaction() as conn:
            outcome = evaluate_diplomatic_conference(conn, game_id, 2000, now_iso)

        # Silence is not consent: u3's abstention must count as a dissenter!
        self.assertEqual(outcome["agree_count"], 2)
        self.assertEqual(outcome["disagree_count"], 1)
        self.assertEqual(outcome["outcome"], "ONE_DISAGREES")

    def test_range_geometry_level1_and_level2(self):
        """Verify the exact Level 1 and Level 2 range geometry."""
        # Level 1: Core box radius 2 (5x5 box) + 4 spikes at distance 3
        center = (5, 5)
        # Inside core box
        self.assertTrue(is_in_range(center, (5, 7), range_level=1))  # dy=2, dx=0
        self.assertTrue(is_in_range(center, (7, 5), range_level=1))  # dx=2, dy=0
        self.assertTrue(is_in_range(center, (7, 7), range_level=1))  # dx=2, dy=2 (corners of 5x5)
        self.assertTrue(is_in_range(center, (3, 3), range_level=1))  # dx=-2, dy=-2
        # Cardinal spikes at distance 3
        self.assertTrue(is_in_range(center, (5, 8), range_level=1))  # North spike: dy=3, dx=0
        self.assertTrue(is_in_range(center, (5, 2), range_level=1))  # South spike: dy=-3, dx=0
        self.assertTrue(is_in_range(center, (8, 5), range_level=1))  # East spike: dx=3, dy=0
        self.assertTrue(is_in_range(center, (2, 5), range_level=1))  # West spike: dx=-3, dy=0
        # Beyond spikes or off-axis distance 3
        self.assertFalse(is_in_range(center, (6, 8), range_level=1)) # dx=1, dy=3 (not on cardinal axis)
        self.assertFalse(is_in_range(center, (5, 9), range_level=1)) # dy=4

        # Level 2: Core box radius 3 (7x7 box) + 4 spikes at distance 4
        self.assertTrue(is_in_range(center, (8, 8), range_level=2))  # dx=3, dy=3 (corner of 7x7)
        self.assertTrue(is_in_range(center, (5, 9), range_level=2))  # North spike: dy=4, dx=0
        self.assertTrue(is_in_range(center, (5, 1), range_level=2))  # South spike: dy=-4, dx=0
        self.assertTrue(is_in_range(center, (9, 5), range_level=2))  # East spike: dx=4, dy=0
        self.assertTrue(is_in_range(center, (1, 5), range_level=2))  # West spike: dx=-4, dy=0
        self.assertFalse(is_in_range(center, (6, 9), range_level=2)) # dx=1, dy=4
        self.assertFalse(is_in_range(center, (5, 10), range_level=2))# dy=5


if __name__ == "__main__":
    unittest.main()
