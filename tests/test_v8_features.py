"""Tests for Version 8 (V8) release features:
1. User alert preferences (GET/POST) and event preference filtering (moves, eliminations, conference).
2. System notifications include originating lobby name (game_name).
3. Conference resolution data structure, unanimous immediate victory, and collapse cooldown handling.
4. Minimal Caddyfile verification.
5. PWA manifest and service worker shell caching verification.
"""
import unittest
import json
import os
import sqlite3
from datetime import datetime, timezone

from app.database import init_db, db_transaction, query_one, query_db
from app.services.notification_service import (
    get_user_alert_preferences,
    update_user_alert_preferences,
    create_in_app_notification,
    get_user_notifications,
    DEFAULT_ALERT_PREFS
)
from app.services.game_service import (
    create_game_lobby,
    get_game_state,
    join_game_lobby,
    remove_player_from_lobby,
    start_game,
)
from app.services.push_service import (
    save_push_subscription,
    get_user_push_subscriptions,
    send_web_push_notification,
    delete_push_subscription,
    revoke_all_push_subscriptions,
    build_vapid_auth_header,
    encrypt_payload_aes128gcm,
    get_or_create_vapid_keys,
)
from app.engine.win_conditions import evaluate_diplomatic_conference
from app.security import hash_password
from app.api.routes_auth import delete_user_account



class TestV8Features(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        self.test_suffix = datetime.now(timezone.utc).strftime("%f")
        self.user_a_id = f"v8_user_a_{self.test_suffix}"
        self.user_b_id = f"v8_user_b_{self.test_suffix}"
        now = datetime.now(timezone.utc).isoformat()
        pw = hash_password("Secret123!")

        with db_transaction() as conn:
            conn.execute(
                "INSERT INTO users (id, username, password_hash, created_at, is_admin) VALUES (?, ?, ?, ?, 1)",
                (self.user_a_id, f"alpha_{self.test_suffix}", pw, now)
            )
            conn.execute(
                "INSERT INTO users (id, username, password_hash, created_at, is_admin) VALUES (?, ?, ?, ?, 0)",
                (self.user_b_id, f"bravo_{self.test_suffix}", pw, now)
            )

    def test_01_user_alert_preferences_defaults_and_updates(self):
        """Verify default alert preferences and dynamic updating."""
        prefs = get_user_alert_preferences(self.user_a_id)
        self.assertEqual(prefs["eliminations"], True)
        self.assertEqual(prefs["conference"], True)
        self.assertEqual(prefs["moves"], False)
        self.assertEqual(prefs["transfers"], True)
        self.assertEqual(prefs["browser_push"], False)

        # Update moves to True and browser_push to True
        updated = update_user_alert_preferences(self.user_a_id, {"moves": True, "browser_push": True})
        self.assertEqual(updated["moves"], True)
        self.assertEqual(updated["browser_push"], True)

        # Retrieve again to verify persistence in SQLite
        persisted = get_user_alert_preferences(self.user_a_id)
        self.assertEqual(persisted["moves"], True)
        self.assertEqual(persisted["browser_push"], True)
        self.assertEqual(persisted["eliminations"], True)

    def test_02_alert_preference_filtering_for_moves(self):
        """Verify notifications respect user alert preferences (e.g. moves)."""
        # User B has default moves=False
        notif_id_1 = create_in_app_notification(
            user_id=self.user_b_id,
            event_type="PLAYER_MOVED",
            title_en="Movement Observed",
            title_ru="Движение замечено",
            message_en="Player moved to (5, 5).",
            message_ru="Игрок переместился в (5, 5)."
        )
        # Should be suppressed (returned None)
        self.assertIsNone(notif_id_1)

        # Enable moves for User B
        update_user_alert_preferences(self.user_b_id, {"moves": True})
        notif_id_2 = create_in_app_notification(
            user_id=self.user_b_id,
            event_type="PLAYER_MOVED",
            title_en="Movement Observed",
            title_ru="Движение замечено",
            message_en="Player moved to (5, 5).",
            message_ru="Игрок переместился в (5, 5)."
        )
        self.assertIsNotNone(notif_id_2)

    def test_03_notification_includes_originating_game_name(self):
        """Verify get_user_notifications includes game_name from the joined games table."""
        lobby_name = f"V8 Test Sector {self.test_suffix}"
        game_id = create_game_lobby(lobby_name, self.user_a_id)

        create_in_app_notification(
            user_id=self.user_a_id,
            event_type="CONFERENCE_CONVENED",
            title_en="Diplomatic Accord Convened",
            title_ru="Созвана конференция",
            message_en="All active operatives have opened deliberations.",
            message_ru="Все активные игроки открыли переговоры.",
            game_id=game_id
        )

        res = get_user_notifications(self.user_a_id, lang="en")
        notifs = res.get("notifications", [])
        self.assertTrue(len(notifs) >= 1)
        found = False
        for n in notifs:
            if n.get("game_id") == game_id:
                self.assertEqual(n.get("game_name"), lobby_name)
                found = True
                break
        self.assertTrue(found, "Notification did not include correct originating game_name")

    def test_04_conference_resolution_data_on_collapse(self):
        """Verify conference collapse writes structured conference_resolution_data and triggers COOLDOWN."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript("""
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
            conference_transparency TEXT DEFAULT 'REVEAL_COUNTS_AT_END',
            conference_end_condition TEXT DEFAULT 'ON_ALL_VOTED',
            conference_single_disagree_outcomes TEXT DEFAULT '["GRANT_AP"]',
            conference_multi_disagree_outcomes TEXT DEFAULT '["RESUME_COMBAT"]',
            armistice_outcome_multi_disagree TEXT DEFAULT 'RESUME_COMBAT',
            win_cond_rogue INTEGER DEFAULT 1, rogue_resolution_mode TEXT DEFAULT 'GRANT_AP', rogue_bonus_ap INTEGER DEFAULT 3,
            multi_disagree_bonus_ap INTEGER DEFAULT 2,
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
        now = datetime.now(timezone.utc).isoformat()
        gid = "test_conf_v8"
        conn.execute("INSERT INTO games (id, name, status, created_by, created_at, conference_status) VALUES (?, 'ConfV8', 'ACTIVE', 'u1', ?, 'CONVENED')", (gid, now))
        conn.execute("INSERT INTO game_parameters (game_id, rogue_bonus_ap) VALUES (?, 4)", (gid,))

        # 3 living players: u1 and u2 AGREE (1), u3 REJECTS (0)
        conn.execute("INSERT INTO players (id, game_id, user_id, username, x, y, hp, ap, is_alive, conference_vote, last_active_at, spawn_x, spawn_y) VALUES ('p1', ?, 'u1', 'User1', 0, 0, 3, 0, 1, 1, ?, 0, 0)", (gid, now))
        conn.execute("INSERT INTO players (id, game_id, user_id, username, x, y, hp, ap, is_alive, conference_vote, last_active_at, spawn_x, spawn_y) VALUES ('p2', ?, 'u2', 'User2', 1, 1, 3, 0, 1, 1, ?, 1, 1)", (gid, now))
        conn.execute("INSERT INTO players (id, game_id, user_id, username, x, y, hp, ap, is_alive, conference_vote, last_active_at, spawn_x, spawn_y) VALUES ('p3', ?, 'u3', 'User3', 2, 2, 3, 0, 1, 0, ?, 2, 2)", (gid, now))

        res = evaluate_diplomatic_conference(conn, gid, 1000, now)
        self.assertEqual(res["outcome"], "ONE_DISAGREES")
        self.assertEqual(res["condition"], "ROGUE_GRANT_AP")

        # Verify conference_resolution_data written in games table
        row = conn.execute("SELECT conference_status, conference_outcome, conference_resolution_data FROM games WHERE id = ?", (gid,)).fetchone()
        self.assertEqual(row["conference_status"], "COOLDOWN")
        self.assertIsNotNone(row["conference_resolution_data"])
        res_data = json.loads(row["conference_resolution_data"])
        self.assertEqual(res_data["agree_count"], 2)
        self.assertEqual(res_data["disagree_count"], 1)
        self.assertEqual(res_data["bonus_ap"], 4)
        self.assertIn("u3", res_data["awarded_user_ids"])

        # Dissenter u3 received +4 AP
        p3 = conn.execute("SELECT ap FROM players WHERE id = 'p3'").fetchone()
        self.assertEqual(p3["ap"], 4)

    def test_05_caddyfile_is_minimal(self):
        """Verify Caddyfile is ultra-minimal for Caddy v2 reverse proxying."""
        caddyfile_path = os.path.join(os.path.dirname(__file__), "..", "Caddyfile")
        self.assertTrue(os.path.exists(caddyfile_path))
        with open(caddyfile_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
        # Minimal configuration should be <= 5 non-comment lines
        self.assertLessEqual(len(lines), 5)
        content = "\n".join(lines)
        self.assertIn("reverse_proxy", content)
        self.assertIn("127.0.0.1:8000", content)

    def test_06_pwa_manifest_and_sw(self):
        """Verify PWA manifest and service worker shell caching configuration."""
        manifest_path = os.path.join(os.path.dirname(__file__), "..", "app", "static", "manifest.json")
        self.assertTrue(os.path.exists(manifest_path))
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["start_url"], "/")
        self.assertTrue(len(manifest["icons"]) >= 2)

        sw_path = os.path.join(os.path.dirname(__file__), "..", "app", "static", "sw.js")
        self.assertTrue(os.path.exists(sw_path))
        with open(sw_path, "r", encoding="utf-8") as f:
            sw_code = f.read()
        self.assertIn("alliance-shell-v8", sw_code)
        self.assertIn("caches.open", sw_code)
        self.assertIn("notificationclick", sw_code)

    def test_07_multi_device_push_subscriptions_and_dispatch(self):
        """Verify support for registering multiple devices per user and dispatching pushes to all."""
        from app.services.push_service import save_push_subscription, get_user_push_subscriptions, send_web_push_notification

        user_id = self.user_a_id
        # Register device 1 (e.g. mobile)
        save_push_subscription(
            user_id=user_id,
            endpoint="https://push.example.com/endpoint/mobile_dev_1",
            p256dh="key_p256dh_mobile",
            auth="auth_token_mobile"
        )
        # Register device 2 (e.g. desktop)
        save_push_subscription(
            user_id=user_id,
            endpoint="https://push.example.com/endpoint/desktop_dev_2",
            p256dh="key_p256dh_desktop",
            auth="auth_token_desktop"
        )
        # Register device 3 (e.g. tablet fallback token)
        save_push_subscription(
            user_id=user_id,
            endpoint="device://tablet_safari_token_3",
            p256dh="self_contained",
            auth="self_contained"
        )

        subs = get_user_push_subscriptions(user_id)
        self.assertEqual(len(subs), 3, "Expected 3 distinct device subscriptions for the user")
        endpoints = [s["endpoint"] for s in subs]
        self.assertIn("https://push.example.com/endpoint/mobile_dev_1", endpoints)
        self.assertIn("https://push.example.com/endpoint/desktop_dev_2", endpoints)
        self.assertIn("device://tablet_safari_token_3", endpoints)

        # Dispatch push notification to user across all devices
        dispatched_count = send_web_push_notification(
            user_id=user_id,
            title="Tactical Alert",
            body="Incoming strike detected",
            data={"sector": "B3"}
        )
        self.assertEqual(dispatched_count, 3, "All 3 registered devices should receive the dispatch")

    def test_08_first_login_prompt_markup_and_translations(self):
        """Verify modal-notif-prompt exists in index.html and has complete bilingual translations."""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        html_path = os.path.join(base_dir, "app", "static", "index.html")
        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()

        self.assertIn('id="modal-notif-prompt"', html)
        self.assertIn('AllianceApp.acceptNotificationPrompt()', html)
        self.assertIn('AllianceApp.dismissNotificationPrompt()', html)
        self.assertIn('data-i18n="notif_prompt_title"', html)
        self.assertIn('data-i18n="btn_enable_notifications"', html)

        en_path = os.path.join(base_dir, "app", "static", "locales", "en.json")
        ru_path = os.path.join(base_dir, "app", "static", "locales", "ru.json")
        with open(en_path, "r", encoding="utf-8") as f:
            en = json.load(f)
        with open(ru_path, "r", encoding="utf-8") as f:
            ru = json.load(f)

        for key in ["notif_prompt_title", "notif_prompt_desc", "btn_enable_notifications", "btn_notif_maybe_later"]:
            self.assertIn(key, en)
            self.assertIn(key, ru)
            self.assertTrue(len(en[key]) > 0)
            self.assertTrue(len(ru[key]) > 0)

    def test_09_mobile_layout_css_rules(self):
        """Verify mobile layout styles prevent dark empty panels and correctly size tactical view."""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        css_path = os.path.join(base_dir, "app", "static", "css", "style.css")
        with open(css_path, "r", encoding="utf-8") as f:
            css = f.read()

        self.assertIn("@media (max-width: 980px)", css)
        self.assertIn("calc(100dvh - 64px)", css)
        self.assertIn("flex: 0 0 44%", css)
        self.assertIn(".sidebar-middle-scroll", css)

    def test_10_remove_player_from_lobby_waiting_status(self):
        """Verify lobby creator/admin can remove joined players in WAITING status, but not once ACTIVE."""
        lobby_name = f"Removal Arena {self.test_suffix}"
        # Creator is user_b (non-admin)
        game_id = create_game_lobby(lobby_name, self.user_b_id)
        # User A joins
        join_res = join_game_lobby(game_id, self.user_a_id)
        player_a_id = join_res["player_id"]

        state = get_game_state(game_id)
        self.assertEqual(len(state["players"]), 2)

        # Unauthorized user (random user) cannot remove player
        with self.assertRaises(PermissionError):
            remove_player_from_lobby(game_id, "unauthorized_user_xyz", player_a_id)

        # Host cannot remove themselves if not admin
        host_player_id = [p["id"] for p in state["players"] if p["user_id"] == self.user_b_id][0]
        with self.assertRaises(ValueError):
            remove_player_from_lobby(game_id, self.user_b_id, host_player_id)

        # Host successfully removes User A
        rem_res = remove_player_from_lobby(game_id, self.user_b_id, player_a_id)
        self.assertEqual(rem_res["status"], "REMOVED")

        state2 = get_game_state(game_id)
        self.assertEqual(len(state2["players"]), 1)
        self.assertNotIn(player_a_id, [p["id"] for p in state2["players"]])

        # User A re-joins and game starts
        join_res2 = join_game_lobby(game_id, self.user_a_id)
        start_game(game_id, self.user_b_id)

        # Cannot remove once match is ACTIVE
        with self.assertRaises(ValueError):
            remove_player_from_lobby(game_id, self.user_b_id, join_res2["player_id"])

    def test_11_device_push_subscription_management(self):
        """Verify individual device push deletion and revoke-all operations."""
        user_id = self.user_a_id
        save_push_subscription(user_id, "endpoint_dev_1", "p256dh_1", "auth_1")
        save_push_subscription(user_id, "endpoint_dev_2", "p256dh_2", "auth_2")
        save_push_subscription(user_id, "endpoint_dev_3", "p256dh_3", "auth_3")

        subs = get_user_push_subscriptions(user_id)
        self.assertEqual(len(subs), 3)

        # Delete endpoint_dev_2 by endpoint
        deleted = delete_push_subscription(user_id, "endpoint_dev_2")
        self.assertTrue(deleted)
        subs_after = get_user_push_subscriptions(user_id)
        self.assertEqual(len(subs_after), 2)
        self.assertNotIn("endpoint_dev_2", [s["endpoint"] for s in subs_after])

        # Revoke all remaining subscriptions
        revoked_count = revoke_all_push_subscriptions(user_id)
        self.assertEqual(revoked_count, 2)
        self.assertEqual(len(get_user_push_subscriptions(user_id)), 0)

    def test_12_rulebook_table_rendering(self):
        """Verify rules catalog renders Section 5 as structured HTML table without unparsed markdown pipes."""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        catalog_path = os.path.join(base_dir, "app", "static", "locales", "rules_catalog.json")
        with open(catalog_path, "r", encoding="utf-8") as f:
            cat = json.load(f)

        for lang in ["en", "ru"]:
            rules_html = cat[lang]
            self.assertIn('<table class="admin-table"', rules_html)
            self.assertIn('rules-table-wrap', rules_html)
            # Ensure no unparsed markdown table lines like <p>| Action | Cost ...</p>
            self.assertNotIn('<p>| Action |', rules_html)
            self.assertNotIn('<p>| Действие |', rules_html)

    def test_13_mobile_staging_and_conference_css(self):
        """Verify mobile CSS rules for staging overflow and conference takeover."""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        css_path = os.path.join(base_dir, "app", "static", "css", "style.css")
        with open(css_path, "r", encoding="utf-8") as f:
            css = f.read()

        # Match controls only show when active
        self.assertIn("#tactical-view:not(.active)", css)
        self.assertIn("staging-active", css)
        self.assertIn("conference-mode", css)
        self.assertIn("staging-table-wrap", css)

    def test_14_translation_parity(self):
        """Verify complete key parity between EN and RU locale dictionaries."""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        en_path = os.path.join(base_dir, "app", "static", "locales", "en.json")
        ru_path = os.path.join(base_dir, "app", "static", "locales", "ru.json")
        with open(en_path, "r", encoding="utf-8") as f:
            en = json.load(f)
        with open(ru_path, "r", encoding="utf-8") as f:
            ru = json.load(f)

        missing_in_ru = set(en.keys()) - set(ru.keys())
        missing_in_en = set(ru.keys()) - set(en.keys())
        self.assertEqual(len(missing_in_ru), 0, f"Keys in EN missing in RU: {missing_in_ru}")
        self.assertEqual(len(missing_in_en), 0, f"Keys in RU missing in EN: {missing_in_en}")

    def test_15_account_deletion(self):
        """Verify delete_user_account removes user, sessions, push subscriptions, and notifications."""
        del_user_id = f"v8_del_user_{self.test_suffix}"
        now = datetime.now(timezone.utc).isoformat()
        pw = hash_password("DelPass123!")

        with db_transaction() as conn:
            conn.execute(
                "INSERT INTO users (id, username, password_hash, created_at, is_admin) VALUES (?, ?, ?, ?, 0)",
                (del_user_id, f"del_{self.test_suffix}", pw, now)
            )
            conn.execute(
                "INSERT INTO sessions (session_id, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (f"sess_{self.test_suffix}", del_user_id, now, now)
            )

        # Add subscription and notification
        save_push_subscription(del_user_id, f"https://push.example.com/{self.test_suffix}", "p256", "auth")
        create_in_app_notification(
            user_id=del_user_id,
            event_type="SYSTEM",
            title_en="Welcome",
            title_ru="Добро пожаловать",
            message_en="Account created",
            message_ru="Аккаунт создан"
        )

        # Verify records exist before deletion
        self.assertIsNotNone(query_one("SELECT id FROM users WHERE id = ?", (del_user_id,)))
        self.assertTrue(len(get_user_push_subscriptions(del_user_id)) >= 1)

        # Execute account deletion
        res = delete_user_account(del_user_id)
        self.assertTrue(res)

        # Verify all records purged
        self.assertIsNone(query_one("SELECT id FROM users WHERE id = ?", (del_user_id,)))
        self.assertIsNone(query_one("SELECT session_id FROM sessions WHERE user_id = ?", (del_user_id,)))
        self.assertEqual(len(get_user_push_subscriptions(del_user_id)), 0)
        self.assertEqual(len(query_db("SELECT id FROM notifications WHERE user_id = ?", (del_user_id,))), 0)

    def test_16_w3c_webpush_vapid_and_rfc8291_encryption(self):
        """Verify W3C Web Push RFC 8292 VAPID auth header generation and RFC 8291 aes128gcm payload encryption."""
        from cryptography.hazmat.primitives.asymmetric import ec
        import base64

        pub_key, priv_pem = get_or_create_vapid_keys()
        self.assertTrue(pub_key.startswith("B"))

        # Test VAPID header generation
        endpoint = "https://fcm.googleapis.com/fcm/send/test-sub-123"
        auth_hdr = build_vapid_auth_header(endpoint, priv_pem, pub_key)
        self.assertTrue(auth_hdr.startswith("vapid t="))
        self.assertIn(", k=", auth_hdr)

        # Generate receiver keypair (P-256)
        client_priv = ec.generate_private_key(ec.SECP256R1())
        client_pub_bytes = client_priv.public_key().public_bytes(
            encoding=ec._serialization.Encoding.X962,
            format=ec._serialization.PublicFormat.UncompressedPoint
        )
        client_p256dh_b64 = base64.urlsafe_b64encode(client_pub_bytes).decode('utf-8').rstrip('=')
        client_auth_b64 = base64.urlsafe_b64encode(os.urandom(16)).decode('utf-8').rstrip('=')

        payload_data = json.dumps({"title": "Hostile Grid Strike", "body": "Sector 4 damaged"}).encode('utf-8')
        encrypted_bytes = encrypt_payload_aes128gcm(payload_data, client_p256dh_b64, client_auth_b64)

        # RFC 8291 aes128gcm wire format:
        # salt (16 bytes) || rs (4 bytes, default 4096 = \x00\x00\x10\x00) || idlen (1 byte = 65) || server_pub (65 bytes) || ciphertext + tag
        self.assertGreater(len(encrypted_bytes), 16 + 4 + 1 + 65 + 16)
        salt = encrypted_bytes[:16]
        rs = int.from_bytes(encrypted_bytes[16:20], 'big')
        idlen = encrypted_bytes[20]
        self.assertEqual(idlen, 65)
        self.assertEqual(rs, 4096)

    def test_17_multi_device_push_registration_and_push_test(self):
        """Verify registering multiple devices per user and dispatching push notifications across all devices."""
        user_id = self.user_a_id
        dev1_endpoint = "device://chrome_mobile_abcdef123"
        dev2_endpoint = "https://fcm.googleapis.com/fcm/send/dev2_token_xyz"

        # Register device 1
        save_push_subscription(user_id, dev1_endpoint, "p256dh_1", "auth_1")
        subs = get_user_push_subscriptions(user_id)
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["endpoint"], dev1_endpoint)

        # Register device 2 (does not overwrite device 1)
        save_push_subscription(user_id, dev2_endpoint, "p256dh_2", "auth_2")
        subs = get_user_push_subscriptions(user_id)
        self.assertEqual(len(subs), 2)
        endpoints = [s["endpoint"] for s in subs]
        self.assertIn(dev1_endpoint, endpoints)
        self.assertIn(dev2_endpoint, endpoints)

        # Dispatch test notification to all registered devices
        count = send_web_push_notification(user_id, "Test Alert", "Verification ping")
        self.assertEqual(count, 2)

        # Delete single device by endpoint
        delete_push_subscription(user_id, dev1_endpoint)
        subs = get_user_push_subscriptions(user_id)
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["endpoint"], dev2_endpoint)

        # Revoke all remaining devices
        revoked_count = revoke_all_push_subscriptions(user_id)
        self.assertEqual(revoked_count, 1)
        subs = get_user_push_subscriptions(user_id)
        self.assertEqual(len(subs), 0)


if __name__ == "__main__":
    unittest.main()



