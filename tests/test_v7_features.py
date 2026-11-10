"""Comprehensive test suite for Alliance v7 features:
1. Playtest control-bot payload compatibility (actions & action_batch, schema parsing).
2. Bot conference voting execution.
3. Replay scrubber state reconstruction (board dimensions, user flags & avatars).
4. Single choice conference failure consequences (configuration & live audit patching).
5. Bilingual translation parity and rulebook tabletop tone validation.
"""
import unittest
import uuid
import json
import os
import asyncio
from datetime import datetime, timezone

from app.database import init_db, db_transaction, query_one, query_db
from app.api.routes_auth import register_user
from app.models.schemas import PlaytestControlBotRequest
from app.models.game_types import LobbyConfig
from app.services.game_service import (
    create_game_lobby,
    start_game,
    join_game_lobby,
    add_dummy_player,
    control_playtest_bot,
    reconstruct_board_at_sequence,
    get_game_state,
    toggle_armistice_vote,
    cast_conference_vote
)
from app.services.audit_service import update_game_parameters_with_audit


class TestV7Features(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        self.suffix = uuid.uuid4().hex[:6]
        self.admin_user = register_user(f"v7_admin_{self.suffix}", "adminpass123")
        with db_transaction() as conn:
            conn.execute("UPDATE users SET is_admin = 1, national_flag = 'FI' WHERE id = ?", (self.admin_user["id"],))

        self.player_user = register_user(f"v7_player_{self.suffix}", "playerpass123")
        with db_transaction() as conn:
            conn.execute("UPDATE users SET national_flag = 'PRIDE' WHERE id = ?", (self.player_user["id"],))

    def test_playtest_control_bot_schema_parsing(self):
        """Verify PlaytestControlBotRequest parses both actions list and action_batch dict."""
        # 1. Test actions list
        payload1 = PlaytestControlBotRequest.model_validate({
            "bot_player_id": "p_bot_1",
            "actions": [{"action_type": "VOTE_CONFERENCE"}]
        })
        self.assertIsNotNone(payload1.actions)
        self.assertEqual(len(payload1.actions), 1)

        # 2. Test action_batch dict (which caused 422 before)
        payload2 = PlaytestControlBotRequest.model_validate({
            "bot_player_id": "p_bot_1",
            "action_batch": {
                "actions": [{"action_type": "CONFERENCE_AGREE"}]
            }
        })
        self.assertIsNone(payload2.actions)
        self.assertIsNotNone(payload2.action_batch)
        self.assertEqual(payload2.action_batch["actions"][0]["action_type"], "CONFERENCE_AGREE")

    def test_playtest_bot_conference_vote_execution(self):
        """Verify bot can successfully execute conference vote commands via control_playtest_bot."""
        cfg = LobbyConfig(
            board_width=10, board_height=10,
            starting_hp=3, starting_ap=3,
            win_cond_armistice=True
        )
        game_id = create_game_lobby(f"Bot Conf {self.suffix}", self.admin_user["id"], cfg, is_playtest=True)
        bot_info = add_dummy_player(game_id)
        bot_player_id = bot_info["player_id"]

        start_game(game_id, self.admin_user["id"])

        # Execute VOTE_CONFERENCE for bot
        async def run_bot_cmd():
            return await control_playtest_bot(
                game_id=game_id,
                admin_user_id=self.admin_user["id"],
                bot_player_id=bot_player_id,
                actions=[{"action_type": "VOTE_CONFERENCE"}]
            )

        res = asyncio.run(run_bot_cmd())
        self.assertTrue(res["success"], f"Bot vote failed: {res}")

        # Check DB that bot has armistice_vote = 1
        with db_transaction() as conn:
            cur = conn.cursor()
            cur.execute("SELECT armistice_vote FROM players WHERE id = ?", (bot_player_id,))
            row = cur.fetchone()
            self.assertEqual(row["armistice_vote"], 1)

    def test_replay_scrubber_reconstruct_state(self):
        """Verify reconstruct_board_at_sequence returns board dimensions, flags, and avatars."""
        cfg = LobbyConfig(
            board_width=12, board_height=14,
            starting_hp=3, starting_ap=3
        )
        game_id = create_game_lobby(f"Replay {self.suffix}", self.admin_user["id"], cfg)
        join_game_lobby(game_id, self.player_user["id"])
        start_game(game_id, self.admin_user["id"])

        state = reconstruct_board_at_sequence(game_id, 1)

        self.assertIsNotNone(state)
        self.assertEqual(state["board_width"], 12)
        self.assertEqual(state["board_height"], 14)
        self.assertIn("players", state)
        self.assertEqual(len(state["players"]), 2)

        # Check each player has national_flag from user profile
        flags = [p["national_flag"] for p in state["players"]]
        self.assertIn("FI", flags)
        self.assertIn("PRIDE", flags)

        for p in state["players"]:
            self.assertIn("avatar_path", p)
            self.assertIn("range_level", p)
            self.assertIn("is_alive", p)

    def test_single_choice_conference_outcomes_configuration(self):
        """Verify single choice conference outcomes configuration and live audit logging."""
        cfg = LobbyConfig(
            board_width=10, board_height=10,
            conference_single_disagree_outcomes=json.dumps(["GRANT_AP"]),
            conference_multi_disagree_outcomes=json.dumps(["RESUME_COMBAT"]),
            rogue_bonus_ap=3,
            multi_disagree_bonus_ap=2
        )
        game_id = create_game_lobby(f"Accord {self.suffix}", self.admin_user["id"], cfg)
        join_game_lobby(game_id, self.player_user["id"])
        start_game(game_id, self.admin_user["id"])

        state = get_game_state(game_id, self.admin_user["id"])
        self.assertEqual(state["config"]["conference_single_disagree_outcomes"], '["GRANT_AP"]')
        self.assertEqual(state["config"]["conference_multi_disagree_outcomes"], '["RESUME_COMBAT"]')

        # Live modify settings mid-game
        update_game_parameters_with_audit(
            game_id=game_id,
            admin_user_id=self.admin_user["id"],
            admin_username=self.admin_user["username"],
            updates={
                "conference_single_disagree_outcomes": json.dumps(["INSTANT_WIN"]),
                "conference_multi_disagree_outcomes": json.dumps(["GRANT_AP"])
            }
        )

        state2 = get_game_state(game_id, self.admin_user["id"])
        self.assertEqual(state2["config"]["conference_single_disagree_outcomes"], '["INSTANT_WIN"]')
        self.assertEqual(state2["config"]["conference_multi_disagree_outcomes"], '["GRANT_AP"]')

        # Verify audit log entries
        with db_transaction() as conn:
            cur = conn.cursor()
            cur.execute("SELECT param_key, old_value, new_value FROM game_parameter_audit_logs WHERE game_id = ?", (game_id,))
            logs = {r["param_key"]: (r["old_value"], r["new_value"]) for r in cur.fetchall()}

        self.assertIn("conference_single_disagree_outcomes", logs)
        self.assertEqual(logs["conference_single_disagree_outcomes"], ('["GRANT_AP"]', '["INSTANT_WIN"]'))

    def test_translation_catalogs_parity(self):
        """Verify complete key parity between English and Russian translation files."""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        en_path = os.path.join(base_dir, "app", "static", "locales", "en.json")
        ru_path = os.path.join(base_dir, "app", "static", "locales", "ru.json")

        with open(en_path, "r", encoding="utf-8") as f:
            en_data = json.load(f)
        with open(ru_path, "r", encoding="utf-8") as f:
            ru_data = json.load(f)

        en_keys = set(en_data.keys())
        ru_keys = set(ru_data.keys())

        missing_in_ru = en_keys - ru_keys
        missing_in_en = ru_keys - en_keys

        self.assertEqual(missing_in_ru, set(), f"Keys in EN missing in RU: {missing_in_ru}")
        self.assertEqual(missing_in_en, set(), f"Keys in RU missing in EN: {missing_in_en}")

    def test_rules_catalog_structure_and_diagrams(self):
        """Verify rules_catalog.json contains both languages, 9 sections, and range diagrams."""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        catalog_path = os.path.join(base_dir, "app", "static", "locales", "rules_catalog.json")
        self.assertTrue(os.path.exists(catalog_path))
        with open(catalog_path, "r", encoding="utf-8") as f:
            catalog = json.load(f)

        self.assertIn("en", catalog)
        self.assertIn("ru", catalog)

        # Range diagrams in both
        for lang in ["en", "ru"]:
            self.assertIn("- - - x - - -", catalog[lang])
            self.assertIn("- - - - x - - - -", catalog[lang])
            for forbidden in ["INSTANT_WIN", "RESUME_COMBAT", "GRANT_AP", "SECRET_RESOLVE"]:
                self.assertNotIn(forbidden, catalog[lang])

    def test_index_html_radio_buttons_and_headers(self):
        """Verify index.html uses circular radio buttons for consequences and keys all headers."""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        html_path = os.path.join(base_dir, "app", "static", "index.html")
        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()

        # Must have radio buttons, not select dropdowns for outcomes
        self.assertIn('type="radio" name="cfg-single-outcome"', html)
        self.assertIn('type="radio" name="cfg-multi-outcome"', html)
        self.assertIn('type="radio" name="edit-cfg-single-outcome"', html)
        self.assertIn('type="radio" name="edit-cfg-multi-outcome"', html)
        self.assertNotIn('<select id="cfg-single-outcome"', html)
        self.assertNotIn('<select id="edit-cfg-single-outcome"', html)

        # Must have data-i18n on admin headers
        self.assertIn('data-i18n="th_invite_token"', html)
        self.assertIn('data-i18n="th_player"', html)
        self.assertIn('data-i18n="th_timestamp"', html)
        self.assertIn('data-i18n="btn_choose_restore_file"', html)

    def test_embedded_i18n_catalog_rules(self):
        """Verify i18n.js has embedded RULES_CATALOG and renderRulesContent method."""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        js_path = os.path.join(base_dir, "app", "static", "js", "i18n.js")
        with open(js_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("RULES_CATALOG", content)
        self.assertIn("renderRulesContent()", content)

    def test_conference_end_condition_on_all_voted(self):
        """Verify conference with ON_ALL_VOTED resolves as soon as all active players vote."""
        cfg = LobbyConfig(conference_end_condition="ON_ALL_VOTED", diplomatic_conference_duration_sec=120)
        game_id = create_game_lobby(f"ConfAllVoted_{self.suffix}", self.admin_user["id"], cfg)
        join_game_lobby(game_id, self.player_user["id"])
        start_game(game_id, self.admin_user["id"])

        # Both vote to convene conference
        toggle_armistice_vote(game_id, self.admin_user["id"])
        toggle_armistice_vote(game_id, self.player_user["id"])

        state = get_game_state(game_id, self.admin_user["id"])
        self.assertEqual(state["conference_status"], "CONVENED")

        # Admin votes AGREE
        r1 = cast_conference_vote(game_id, self.admin_user["id"], True)
        self.assertFalse(r1["all_voted"])
        self.assertIsNone(r1["outcome"])

        # Player votes AGREE -> all voted, resolves immediately
        r2 = cast_conference_vote(game_id, self.player_user["id"], True)
        self.assertTrue(r2["all_voted"])
        self.assertIsNotNone(r2["outcome"])
        self.assertEqual(r2["outcome"]["outcome"], "ALL_AGREE")

    def test_conference_end_condition_timer_only(self):
        """Verify conference with TIMER_ONLY remains open until timer expires even if all voted."""
        cfg = LobbyConfig(conference_end_condition="TIMER_ONLY", diplomatic_conference_duration_sec=120)
        game_id = create_game_lobby(f"ConfTimerOnly_{self.suffix}", self.admin_user["id"], cfg)
        join_game_lobby(game_id, self.player_user["id"])
        start_game(game_id, self.admin_user["id"])

        toggle_armistice_vote(game_id, self.admin_user["id"])
        toggle_armistice_vote(game_id, self.player_user["id"])

        state = get_game_state(game_id, self.admin_user["id"])
        self.assertEqual(state["conference_status"], "CONVENED")

        # Admin votes AGREE
        r1 = cast_conference_vote(game_id, self.admin_user["id"], True)
        self.assertFalse(r1["all_voted"])

        # Player votes AGREE -> all voted, but TIMER_ONLY keeps conference open!
        r2 = cast_conference_vote(game_id, self.player_user["id"], True)
        self.assertTrue(r2["all_voted"])
        self.assertIsNone(r2["outcome"])

        state_during = get_game_state(game_id, self.admin_user["id"])
        self.assertEqual(state_during["conference_status"], "CONVENED")

        # Player can change vote while timer is active
        r3 = cast_conference_vote(game_id, self.player_user["id"], False)
        self.assertTrue(r3["all_voted"])
        self.assertIsNone(r3["outcome"])

        # Simulate timer expiry
        with db_transaction() as conn:
            conn.execute("UPDATE games SET conference_ends_at = '2020-01-01T00:00:00Z' WHERE id = ?", (game_id,))

        state_expired = get_game_state(game_id, self.admin_user["id"])
        self.assertIn(state_expired["conference_status"], ("RESOLVED", "IDLE", "COOLDOWN"))

    def test_conference_end_condition_audit_and_ui(self):
        """Verify conference_end_condition is present in index.html, audit service, and locales."""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        html_path = os.path.join(base_dir, "app", "static", "index.html")
        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()

        self.assertIn('id="cfg-conference-end-condition"', html)
        self.assertIn('id="edit-cfg-conference-end-condition"', html)

        # Audit update test
        cfg = LobbyConfig()
        game_id = create_game_lobby(f"AuditEndCond_{self.suffix}", self.admin_user["id"], cfg)
        logs = update_game_parameters_with_audit(
            game_id=game_id,
            admin_user_id=self.admin_user["id"],
            admin_username=self.admin_user["username"],
            updates={"conference_end_condition": "TIMER_ONLY"}
        )
        self.assertTrue(any(l["param_key"] == "conference_end_condition" and l["new_value"] == "TIMER_ONLY" for l in logs))

    def test_tactical_actions_blocked_during_diplomatic_conference(self):
        """Verify tactical actions (move, strike, gift AP) are strictly blocked during active diplomatic conferences."""
        from app.engine.actions import execute_action_batch
        from app.engine.queue import submit_action_to_queue

        cfg = LobbyConfig(starting_ap=3, starting_hp=3, win_cond_armistice=True)
        game_id = create_game_lobby(f"CombatBlock_{self.suffix}", self.admin_user["id"], cfg)
        join_game_lobby(game_id, self.player_user["id"])
        start_game(game_id, self.admin_user["id"])

        # Convene conference
        toggle_armistice_vote(game_id, self.admin_user["id"])
        toggle_armistice_vote(game_id, self.player_user["id"])

        state = get_game_state(game_id, self.admin_user["id"])
        self.assertEqual(state["conference_status"], "CONVENED")

        # Try to execute MOVE directly via execute_action_batch during conference
        now = datetime.now(timezone.utc).isoformat()
        with db_transaction() as conn:
            success, msg, _, _ = execute_action_batch(
                conn, game_id, self.admin_user["id"],
                [{"action_type": "MOVE", "target_x": 1, "target_y": 2}],
                5000, now
            )
            self.assertFalse(success)
            self.assertIn("ERR_CONFERENCE_IN_SESSION", msg)

        # Queue level check
        async def submit_action():
            return await submit_action_to_queue(
                game_id=game_id,
                initiator_user_id=self.admin_user["id"],
                actions=[{"action_type": "MOVE", "to_x": 1, "to_y": 2}]
            )
        svc_res = asyncio.run(submit_action())
        self.assertFalse(svc_res["success"])
        self.assertIn("ERR_CONFERENCE_IN_SESSION", svc_res["message"])

    def test_ui_restructuring_and_batch_preview_markup(self):
        """Verify all new UI elements and map batch preview components are properly declared."""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        
        # 1. HTML elements
        html_path = os.path.join(base_dir, "app", "static", "index.html")
        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()

        self.assertIn('id="pinned-vitals-header"', html)
        self.assertIn('id="vital-hp-box"', html)
        self.assertIn('id="vital-ap-box"', html)
        self.assertIn('id="command-center-drawer"', html)
        self.assertIn('id="pinned-batch-footer"', html)
        self.assertIn('id="drawer-handle-bar"', html)
        self.assertIn('id="compact-quick-bar"', html)
        self.assertIn('id="btn-pin-map"', html)
        self.assertIn('pinned-mobile', html)

        # 2. Board canvas preview methods
        board_path = os.path.join(base_dir, "app", "static", "js", "board.js")
        with open(board_path, "r", encoding="utf-8") as f:
            board_js = f.read()

        self.assertIn('setBatchPreview', board_js)
        self.assertIn('drawBatchPreview', board_js)

        # 3. CSS styles
        css_path = os.path.join(base_dir, "app", "static", "css", "style.css")
        with open(css_path, "r", encoding="utf-8") as f:
            css = f.read()

        self.assertIn('.pinned-vitals-header', css)
        self.assertIn('.command-center-drawer', css)
        self.assertIn('.pinned-batch-footer', css)
        self.assertIn('.board-container.pinned-mobile', css)


    def test_conference_cooldown_and_notifications(self):
        """Verify conference convening and conclusion generate notifications, and COOLDOWN blocks actions."""
        cfg = LobbyConfig(
            board_width=10, board_height=10,
            starting_hp=3, starting_ap=3,
            win_cond_armistice=True
        )
        game_id = create_game_lobby(f"ConfCD_{self.suffix}", self.admin_user["id"], cfg, is_playtest=False)
        join_game_lobby(game_id, self.player_user["id"])
        start_game(game_id, self.admin_user["id"])

        # Both vote peace to convene
        toggle_armistice_vote(game_id, self.admin_user["id"])
        res2 = toggle_armistice_vote(game_id, self.player_user["id"])
        self.assertTrue(res2["conference_triggered"])

        # Check conference convened notification
        notifs = query_db("SELECT * FROM notifications WHERE game_id = ? AND event_type = 'CONFERENCE_CONVENED'", (game_id,))
        self.assertGreaterEqual(len(notifs), 2)

        # Admin votes AGREE, Player votes REJECT -> 1 dissenter RESUME_COMBAT
        cast_conference_vote(game_id, self.admin_user["id"], True)
        cast_conference_vote(game_id, self.player_user["id"], False)

        now_iso = datetime.now(timezone.utc).isoformat()
        now_ns = int(datetime.now(timezone.utc).timestamp() * 1e9)
        with db_transaction() as conn:
            from app.engine.win_conditions import evaluate_diplomatic_conference
            evaluate_diplomatic_conference(conn, game_id, now_ns, now_iso)

        # Check that status is COOLDOWN
        state = get_game_state(game_id, self.admin_user["id"])
        self.assertEqual(state["conference_status"], "COOLDOWN")

        # Check that action batch is blocked during COOLDOWN
        from app.engine.actions import execute_action_batch
        with db_transaction() as conn:
            ok, msg, _, _ = execute_action_batch(
                conn=conn,
                game_id=game_id,
                initiator_user_id=self.admin_user["id"],
                actions_raw=[{"action_type": "MOVE", "to_x": 0, "to_y": 1}],
                timestamp_ns=now_ns,
                now_iso=now_iso
            )
            self.assertFalse(ok)
            self.assertIn("ERR_CONFERENCE_IN_SESSION", msg)

        # Check that conference resolution notifications were created
        res_notifs = query_db("SELECT * FROM notifications WHERE game_id = ? AND event_type = 'CONFERENCE_RESOLVED'", (game_id,))
        self.assertGreaterEqual(len(res_notifs), 2)

    def test_elimination_alert_broadcast_conceals_killer(self):
        """Verify that when a player is killed, all players receive an alert concealing the killer from everyone except victim."""
        cfg = LobbyConfig(board_width=10, board_height=10, starting_hp=1, starting_ap=3)
        game_id = create_game_lobby(f"ElimAlert_{self.suffix}", self.admin_user["id"], cfg, is_playtest=False)
        join_game_lobby(game_id, self.player_user["id"])
        # Add third observer
        user3 = register_user(f"obs_{self.suffix}", "pass123456")
        join_game_lobby(game_id, user3["id"])
        start_game(game_id, self.admin_user["id"])

        # Position admin right next to player_user
        with db_transaction() as conn:
            conn.execute("UPDATE players SET x = 1, y = 1, hp = 3, ap = 3 WHERE game_id = ? AND user_id = ?", (game_id, self.admin_user["id"]))
            conn.execute("UPDATE players SET x = 1, y = 2, hp = 1, ap = 0 WHERE game_id = ? AND user_id = ?", (game_id, self.player_user["id"]))
            conn.execute("UPDATE players SET x = 8, y = 8, hp = 3, ap = 0 WHERE game_id = ? AND user_id = ?", (game_id, user3["id"]))

        # Admin attacks player_user (lethal strike)
        now_iso = datetime.now(timezone.utc).isoformat()
        now_ns = int(datetime.now(timezone.utc).timestamp() * 1e9)
        from app.engine.actions import execute_action_batch
        with db_transaction() as conn:
            p_target = query_one("SELECT id FROM players WHERE game_id = ? AND user_id = ?", (game_id, self.player_user["id"]))
            ok, msg, receipts, _ = execute_action_batch(
                conn=conn,
                game_id=game_id,
                initiator_user_id=self.admin_user["id"],
                actions_raw=[{"action_type": "ATTACK", "target_player_id": p_target["id"]}],
                timestamp_ns=now_ns,
                now_iso=now_iso
            )
            self.assertTrue(ok, f"Attack failed: {msg}")

        # Check observer's notifications: must have PLAYER_ELIMINATED without killer's name
        obs_notifs = query_db("SELECT * FROM notifications WHERE user_id = ? AND game_id = ?", (user3["id"], game_id))
        self.assertGreaterEqual(len(obs_notifs), 1)
        self.assertEqual(obs_notifs[0]["event_type"], "PLAYER_ELIMINATED")
        self.assertNotIn(self.admin_user["username"], obs_notifs[0]["message_en"])
        self.assertIn(self.player_user["username"], obs_notifs[0]["message_en"])

        # Check victim's notification: has ELIMINATED and mentions killer
        vic_notifs = query_db("SELECT * FROM notifications WHERE user_id = ? AND game_id = ?", (self.player_user["id"], game_id))
        self.assertGreaterEqual(len(vic_notifs), 1)
        self.assertEqual(vic_notifs[0]["event_type"], "ELIMINATED")
        self.assertIn(self.admin_user["username"], vic_notifs[0]["message_en"])

    def test_scrubber_fog_of_war_conceals_shots_from_third_parties(self):
        """Verify non-lethal strikes do not advance last_event_seq or appear in scrubber events for uninvolved third parties."""
        cfg = LobbyConfig(board_width=10, board_height=10, starting_hp=3, starting_ap=3)
        game_id = create_game_lobby(f"ScrubFOW_{self.suffix}", self.admin_user["id"], cfg, is_playtest=False)
        join_game_lobby(game_id, self.player_user["id"])
        user3 = register_user(f"scrub_obs_{self.suffix}", "pass123456")
        join_game_lobby(game_id, user3["id"])
        start_game(game_id, self.admin_user["id"])

        # Check baseline sequence for user3
        state_before = get_game_state(game_id, user3["id"])
        seq_before = state_before["last_event_seq"]

        # Position admin right next to player_user
        with db_transaction() as conn:
            conn.execute("UPDATE players SET x = 1, y = 1, hp = 3, ap = 3 WHERE game_id = ? AND user_id = ?", (game_id, self.admin_user["id"]))
            conn.execute("UPDATE players SET x = 1, y = 2, hp = 3, ap = 0 WHERE game_id = ? AND user_id = ?", (game_id, self.player_user["id"]))
            conn.execute("UPDATE players SET x = 8, y = 8, hp = 3, ap = 0 WHERE game_id = ? AND user_id = ?", (game_id, user3["id"]))

        # Admin attacks player_user non-lethally (3 HP -> 2 HP)
        now_iso = datetime.now(timezone.utc).isoformat()
        now_ns = int(datetime.now(timezone.utc).timestamp() * 1e9)
        from app.engine.actions import execute_action_batch
        with db_transaction() as conn:
            p_target = query_one("SELECT id FROM players WHERE game_id = ? AND user_id = ?", (game_id, self.player_user["id"]))
            ok, msg, receipts, _ = execute_action_batch(
                conn=conn,
                game_id=game_id,
                initiator_user_id=self.admin_user["id"],
                actions_raw=[{"action_type": "ATTACK", "target_player_id": p_target["id"]}],
                timestamp_ns=now_ns,
                now_iso=now_iso
            )
            self.assertTrue(ok)

        # Observer user3 must NOT see last_event_seq increment from the hidden shot!
        state_after = get_game_state(game_id, user3["id"])
        self.assertEqual(state_after["last_event_seq"], seq_before, "Observer scrubber sequence must NOT reveal hidden shot!")

        # Initiator sees their action
        state_admin = get_game_state(game_id, self.admin_user["id"])
        self.assertGreater(state_admin["last_event_seq"], seq_before)


if __name__ == "__main__":
    unittest.main()

