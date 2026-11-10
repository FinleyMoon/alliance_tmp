"""Tests for Version 9 (V9) features:
1. Recreate Lobby button and workflow (preserves all players, allows changing grid dimensions and settings).
2. Late Join system (allows players to join active match up to 3 hours before first AP drop with host approval).
3. Ranked Choice Voting (RCV / Instant Runoff) for 4 randomized map placement options with configurable duration.
4. Push notifications device list enrollment and persistence.
"""
import unittest
import json
from datetime import datetime, timezone, timedelta

from app.database import init_db, db_transaction, query_one, query_db
from app.models.game_types import LobbyConfig, GameStatus
from app.services.game_service import (
    create_game_lobby,
    get_game_state,
    join_game_lobby,
    add_dummy_player,
    start_game,
    recreate_game_lobby,
    get_first_ap_drop_cutoff,
    create_join_request,
    get_game_join_requests,
    approve_join_request,
    deny_join_request,
    get_map_voting_state,
    cast_map_vote,
    resolve_map_rcv_vote,
)
from app.services.push_service import (
    save_push_subscription,
    get_user_push_subscriptions,
    delete_push_subscription,
    revoke_all_push_subscriptions,
)
from app.security import hash_password


class TestV9Features(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        self.test_suffix = datetime.now(timezone.utc).strftime("%f")
        self.host_id = f"v9_host_{self.test_suffix}"
        self.player_b_id = f"v9_user_b_{self.test_suffix}"
        self.player_c_id = f"v9_user_c_{self.test_suffix}"
        self.late_joiner_id = f"v9_late_{self.test_suffix}"
        now = datetime.now(timezone.utc).isoformat()
        pw = hash_password("SecretPass123!")

        with db_transaction() as conn:
            conn.execute(
                "INSERT INTO users (id, username, password_hash, created_at, is_admin) VALUES (?, ?, ?, ?, 0)",
                (self.host_id, f"host_{self.test_suffix}", pw, now)
            )
            conn.execute(
                "INSERT INTO users (id, username, password_hash, created_at, is_admin) VALUES (?, ?, ?, ?, 0)",
                (self.player_b_id, f"playerb_{self.test_suffix}", pw, now)
            )
            conn.execute(
                "INSERT INTO users (id, username, password_hash, created_at, is_admin) VALUES (?, ?, ?, ?, 0)",
                (self.player_c_id, f"playerc_{self.test_suffix}", pw, now)
            )
            conn.execute(
                "INSERT INTO users (id, username, password_hash, created_at, is_admin) VALUES (?, ?, ?, ?, 0)",
                (self.late_joiner_id, f"late_{self.test_suffix}", pw, now)
            )

    def test_01_recreate_lobby_preserves_players_and_updates_grid(self):
        """Verify recreating a lobby preserves all players while applying new grid dimensions and configs."""
        # 1. Create source lobby (15x15) with host
        cfg = LobbyConfig(grid_width=15, grid_height=15, burst_cap_ap=3)
        source_game_id = create_game_lobby("Original Arena", self.host_id, config=cfg)

        # 2. Join second player and add a dummy bot
        join_game_lobby(source_game_id, self.player_b_id)
        add_dummy_player(source_game_id, f"Bot_{self.test_suffix}")

        source_state = get_game_state(source_game_id)
        self.assertEqual(len(source_state["players"]), 3)

        # 3. Recreate lobby with new dimensions (24x28), new burst cap (6), and allow_late_join=True
        new_cfg = LobbyConfig(
            grid_width=24,
            grid_height=28,
            burst_cap_ap=6,
            ap_drop_amount=2,
            allow_late_join=True,
            map_voting_enabled=True,
            map_voting_duration_minutes=15
        )
        recreate_result = recreate_game_lobby(
            source_game_id=source_game_id,
            user_id=self.host_id,
            name="Rematch Arena V9",
            config=new_cfg,
            is_playtest=False
        )
        new_game_id = recreate_result["game_id"]
        self.assertEqual(recreate_result["players_transferred_count"], 3)

        # 4. Verify new lobby state
        new_state = get_game_state(new_game_id)
        self.assertEqual(new_state["name"], "Rematch Arena V9")
        self.assertEqual(new_state["status"], "WAITING")
        self.assertEqual(new_state["config"]["grid_width"], 24)
        self.assertEqual(new_state["config"]["grid_height"], 28)
        self.assertEqual(new_state["config"]["burst_cap_ap"], 6)
        self.assertEqual(new_state["config"]["allow_late_join"], 1)
        self.assertEqual(new_state["config"]["map_voting_enabled"], 1)
        self.assertEqual(new_state["config"]["map_voting_duration_minutes"], 15)

        # Verify all 3 players transferred
        players_in_new = new_state["players"]
        self.assertEqual(len(players_in_new), 3)
        user_ids = {p["user_id"] for p in players_in_new if p["user_id"]}
        self.assertIn(self.host_id, user_ids)
        self.assertIn(self.player_b_id, user_ids)
        bot_count = sum(1 for p in players_in_new if p["is_bot"])
        self.assertEqual(bot_count, 1)

        # Verify permission check: non-host cannot recreate
        with self.assertRaises(PermissionError):
            recreate_game_lobby(source_game_id, self.player_b_id, "Unauthorized", new_cfg)

    def test_02_late_join_cutoff_and_request_workflow(self):
        """Verify late join window calculation, 3-hour cutoff before first drop, and approval."""
        # 1. Start a game with allow_late_join=True
        cfg = LobbyConfig(
            grid_width=12,
            grid_height=12,
            first_ap_drop_days=2,
            ap_drop_server_time="20:00",
            allow_late_join=True
        )
        game_id = create_game_lobby("Late Join Match", self.host_id, config=cfg)
        join_game_lobby(game_id, self.player_b_id)
        start_game(game_id, self.host_id)

        # 2. Check first AP drop cutoff
        is_open, reason, cutoff_iso = get_first_ap_drop_cutoff(game_id)
        self.assertTrue(is_open)
        self.assertIsNotNone(cutoff_iso)
        cutoff_dt = datetime.fromisoformat(cutoff_iso)
        now_utc = datetime.now(timezone.utc)
        self.assertGreater(cutoff_dt, now_utc)

        # 3. Late joiner submits join request
        req = create_join_request(game_id, self.late_joiner_id)
        self.assertEqual(req["status"], "PENDING")
        self.assertEqual(req["user_id"], self.late_joiner_id)

        # Verify duplicate request rejected
        with self.assertRaises(ValueError):
            create_join_request(game_id, self.late_joiner_id)

        # 4. Host views pending requests
        requests_list = get_game_join_requests(game_id)
        self.assertEqual(len(requests_list), 1)
        self.assertEqual(requests_list[0]["id"], req["id"])

        # 5. Non-host cannot approve
        with self.assertRaises(PermissionError):
            approve_join_request(game_id, req["id"], self.player_b_id)

        # 6. Host approves request
        approved = approve_join_request(game_id, req["id"], self.host_id)
        self.assertEqual(approved["status"], "APPROVED")

        # Verify late joiner is now enlisted as active player with valid coordinates
        state = get_game_state(game_id)
        enlisted_users = {p["user_id"] for p in state["players"]}
        self.assertIn(self.late_joiner_id, enlisted_users)
        late_player = next(p for p in state["players"] if p["user_id"] == self.late_joiner_id)
        self.assertTrue(late_player["is_alive"])
        self.assertGreaterEqual(late_player["x"], 1)
        self.assertLessEqual(late_player["x"], 12)
        self.assertGreaterEqual(late_player["y"], 1)
        self.assertLessEqual(late_player["y"], 12)

    def test_03_late_join_denial_and_disabled_setting(self):
        """Verify late join denial and behavior when allow_late_join is disabled."""
        # Lobby with allow_late_join=False
        cfg = LobbyConfig(allow_late_join=False)
        game_id = create_game_lobby("No Late Joins", self.host_id, config=cfg)
        join_game_lobby(game_id, self.player_b_id)
        start_game(game_id, self.host_id)

        # Submission should be rejected
        with self.assertRaises(ValueError):
            create_join_request(game_id, self.late_joiner_id)

        # Now test denial on a lobby that allows late join
        cfg_yes = LobbyConfig(allow_late_join=True, first_ap_drop_days=3)
        game2_id = create_game_lobby("Allow Late Join 2", self.host_id, config=cfg_yes)
        join_game_lobby(game2_id, self.player_b_id)
        start_game(game2_id, self.host_id)

        req2 = create_join_request(game2_id, self.late_joiner_id)
        denied = deny_join_request(game2_id, req2["id"], self.host_id)
        self.assertEqual(denied["status"], "DENIED")

        # Verify player was not added to the match
        state2 = get_game_state(game2_id)
        enlisted2 = {p["user_id"] for p in state2["players"]}
        self.assertNotIn(self.late_joiner_id, enlisted2)

    def test_04_map_voting_generation_and_rcv_instant_runoff(self):
        """Verify 4 random candidate maps generation and Instant Runoff Voting (RCV) tally."""
        # 1. Create lobby with map voting enabled
        cfg = LobbyConfig(
            grid_width=14,
            grid_height=14,
            map_voting_enabled=True,
            map_voting_duration_minutes=20
        )
        game_id = create_game_lobby("RCV Map Election Arena", self.host_id, config=cfg)
        join_game_lobby(game_id, self.player_b_id)
        join_game_lobby(game_id, self.player_c_id)

        # 2. Start game -> should enter MAP_VOTING state
        start_res = start_game(game_id, self.host_id)
        self.assertEqual(start_res["status"], "MAP_VOTING")

        state = get_game_state(game_id)
        self.assertEqual(state["status"], "MAP_VOTING")
        self.assertIsNotNone(state.get("map_voting_ends_at"))

        # 3. Inspect candidate map options (must be 4 distinct candidate options)
        voting_state = get_map_voting_state(game_id, self.host_id)
        self.assertEqual(len(voting_state["options"]), 4)
        option_indices = [opt["option_idx"] for opt in voting_state["options"]]
        self.assertEqual(option_indices, [1, 2, 3, 4])

        # Verify each option contains placements for all 3 players
        for opt in voting_state["options"]:
            self.assertEqual(len(opt["placements"]), 3)
            for p in opt["placements"]:
                self.assertGreaterEqual(p["x"], 1)
                self.assertLessEqual(p["x"], 14)
                self.assertGreaterEqual(p["y"], 1)
                self.assertLessEqual(p["y"], 14)

        # 4. Cast ranked choice ballots
        # Host prefers: 2 > 1 > 3 > 4
        # Player B prefers: 2 > 3 > 4 > 1
        # Player C prefers: 1 > 2 > 3 > 4
        cast_map_vote(game_id, self.host_id, [2, 1, 3, 4])
        cast_map_vote(game_id, self.player_b_id, [2, 3, 4, 1])
        cast_map_vote(game_id, self.player_c_id, [1, 2, 3, 4])

        # 5. Resolve RCV election: Option 2 has 2 out of 3 first-choice votes (>50% majority in Round 1)
        resolution = resolve_map_rcv_vote(game_id, forced=True)
        self.assertEqual(resolution["winning_option_idx"], 2)
        self.assertEqual(resolution["status"], "ACTIVE")

        # 6. Verify game is now ACTIVE and players were placed according to winning Option 2
        active_state = get_game_state(game_id)
        self.assertEqual(active_state["status"], "ACTIVE")

        # Check players have coordinates from Option 2
        opt2 = next(o for o in voting_state["options"] if o["option_idx"] == 2)
        opt2_lookup = {p["player_id"]: (p["x"], p["y"]) for p in opt2["placements"]}
        for p in active_state["players"]:
            expected_pos = opt2_lookup[p["id"]]
            self.assertEqual((p["x"], p["y"]), expected_pos)

    def test_05_map_voting_multi_round_elimination(self):
        """Verify RCV elimination when no candidate achieves >50% on first round."""
        cfg = LobbyConfig(
            grid_width=10,
            grid_height=10,
            map_voting_enabled=True,
            map_voting_duration_minutes=5
        )
        game_id = create_game_lobby("RCV Multi Round", self.host_id, config=cfg)
        join_game_lobby(game_id, self.player_b_id)
        join_game_lobby(game_id, self.player_c_id)
        start_game(game_id, self.host_id)

        # Ballots (3 voters):
        # Host: 1 > 3 > 2 > 4 (First choice: 1)
        # Player B: 2 > 3 > 1 > 4 (First choice: 2)
        # Player C: 3 > 1 > 2 > 4 (First choice: 3)
        cast_map_vote(game_id, self.host_id, [1, 3, 2, 4])
        cast_map_vote(game_id, self.player_b_id, [2, 3, 1, 4])
        cast_map_vote(game_id, self.player_c_id, [3, 1, 2, 4])

        resolution = resolve_map_rcv_vote(game_id, forced=True)
        self.assertIn(resolution["winning_option_idx"], [1, 2, 3])
        self.assertEqual(resolution["status"], "ACTIVE")

    def test_06_push_subscriptions_device_list(self):
        """Verify enrolled devices list is populated, displays device info, and manages subscriptions."""
        user_id = self.host_id

        # Initially 0 subscriptions
        initial_subs = get_user_push_subscriptions(user_id)
        self.assertEqual(len(initial_subs), 0)

        # Enroll Device 1 (Desktop)
        endpoint_1 = "device://Chrome_Desktop_macOS_101"
        save_push_subscription(
            user_id=user_id,
            endpoint=endpoint_1,
            p256dh="key1",
            auth="auth1"
        )

        # Enroll Device 2 (Mobile)
        endpoint_2 = "device://Safari_Mobile_iOS_202"
        save_push_subscription(
            user_id=user_id,
            endpoint=endpoint_2,
            p256dh="key2",
            auth="auth2"
        )

        # Verify device list is NOT empty and contains both devices
        subs = get_user_push_subscriptions(user_id)
        self.assertEqual(len(subs), 2)
        endpoints = [s["endpoint"] for s in subs]
        self.assertIn(endpoint_1, endpoints)
        self.assertIn(endpoint_2, endpoints)

        for s in subs:
            self.assertIsNotNone(s["created_at"])

        # Unsubscribe Device 1
        delete_push_subscription(user_id, endpoint_1)
        active_after_del = get_user_push_subscriptions(user_id)
        self.assertEqual(len(active_after_del), 1)
        self.assertEqual(active_after_del[0]["endpoint"], endpoint_2)

        # Unsubscribe all devices
        revoke_all_push_subscriptions(user_id)
        active_all = get_user_push_subscriptions(user_id)
        self.assertEqual(len(active_all), 0)

    def test_07_timezone_env_configuration_and_moscow_default(self):
        """Verify .env TIMEZONE setting with Europe/Moscow default and alias mapping."""
        import os
        from unittest.mock import patch
        from app.config import get_server_timezone, get_server_timezone_name, get_server_now

        # 1. Default should be Europe/Moscow when no env var set
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_server_timezone_name(), "Europe/Moscow")
            tz = get_server_timezone()
            self.assertEqual(str(tz), "Europe/Moscow")

        # 2. 'Moscow' alias maps to Europe/Moscow
        with patch.dict(os.environ, {"TIMEZONE": "Moscow"}):
            self.assertEqual(get_server_timezone_name(), "Europe/Moscow")
            tz = get_server_timezone()
            self.assertEqual(str(tz), "Europe/Moscow")

        # 3. 'MSK' alias maps to Europe/Moscow
        with patch.dict(os.environ, {"TIMEZONE": "MSK"}):
            self.assertEqual(get_server_timezone_name(), "Europe/Moscow")

        # 4. Standard IANA zone e.g. America/New_York
        with patch.dict(os.environ, {"TIMEZONE": "America/New_York"}):
            self.assertEqual(get_server_timezone_name(), "America/New_York")
            tz = get_server_timezone()
            self.assertEqual(str(tz), "America/New_York")

        # 5. Verify get_server_now returns localized time with valid offset
        now_server = get_server_now()
        self.assertIsNotNone(now_server.tzinfo)

    def test_08_ap_drop_at_2130_moscow_time(self):
        """Verify AP drop awards points at scheduled 21:30 Moscow time and prevents duplicate drops."""
        import asyncio
        from unittest.mock import patch
        from app.config import get_server_timezone
        from app.scheduler.cron_worker import process_ap_drops

        cfg = LobbyConfig(
            grid_width=10,
            grid_height=10,
            ap_drop_amount=3,
            ap_drop_server_time="21:30",
            first_ap_drop_days=0,
            starting_ap=0
        )
        game_id = create_game_lobby("AP Drop 21:30 Test", self.host_id, config=cfg)
        join_game_lobby(game_id, self.player_b_id)
        start_game(game_id, self.host_id)

        # Confirm initial AP is 0
        p_host = query_one("SELECT ap FROM players WHERE game_id = ? AND user_id = ?", (game_id, self.host_id))
        self.assertEqual(p_host["ap"], 0)

        msk_tz = get_server_timezone()

        # Case A: Simulated time before 21:30 (e.g. 21:00 MSK) -> No AP awarded
        mock_before = datetime(2026, 11, 10, 21, 0, 0, tzinfo=msk_tz)
        with patch("app.scheduler.cron_worker.get_server_now", return_value=mock_before):
            asyncio.run(process_ap_drops())

        p_host_before = query_one("SELECT ap FROM players WHERE game_id = ? AND user_id = ?", (game_id, self.host_id))
        self.assertEqual(p_host_before["ap"], 0)

        # Case B: Simulated time at scheduled 21:30 MSK -> AP awarded (+3)
        mock_at_drop = datetime(2026, 11, 10, 21, 30, 0, tzinfo=msk_tz)
        with patch("app.scheduler.cron_worker.get_server_now", return_value=mock_at_drop):
            asyncio.run(process_ap_drops())

        p_host_after = query_one("SELECT ap FROM players WHERE game_id = ? AND user_id = ?", (game_id, self.host_id))
        self.assertEqual(p_host_after["ap"], 3)
        p_b_after = query_one("SELECT ap FROM players WHERE game_id = ? AND user_id = ?", (game_id, self.player_b_id))
        self.assertEqual(p_b_after["ap"], 3)

        # Case C: Same day subsequent run (e.g. 21:35 MSK) -> Idempotent, no extra AP
        mock_later = datetime(2026, 11, 10, 21, 35, 0, tzinfo=msk_tz)
        with patch("app.scheduler.cron_worker.get_server_now", return_value=mock_later):
            asyncio.run(process_ap_drops())

        p_host_idempotent = query_one("SELECT ap FROM players WHERE game_id = ? AND user_id = ?", (game_id, self.host_id))
        self.assertEqual(p_host_idempotent["ap"], 3)


if __name__ == "__main__":
    unittest.main()
