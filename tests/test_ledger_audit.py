"""Unit tests for cryptographic event ledger and parameter audit trail."""
import unittest
import sqlite3
import json
from app.engine.ledger import append_event_to_ledger, verify_ledger_chain, compute_event_hash
from app.services.audit_service import update_game_parameters_with_audit, get_game_audit_logs


class TestLedgerAndAudit(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, game_id TEXT, event_seq INTEGER, event_type TEXT,
            initiator_user_id TEXT, target_user_id TEXT, visibility TEXT, payload_json TEXT,
            prev_hash TEXT, hash TEXT, timestamp_ns INTEGER, created_at TEXT
        );
        """)
        self.game_id = "ledger_game"

    def tearDown(self):
        self.conn.close()

    def test_hash_chain_integrity(self):
        """Append events sequentially and verify SHA-256 hash chaining."""
        e1 = append_event_to_ledger(
            self.conn, self.game_id, "GAME_STARTED", {"players": 4}, 100, "2026-01-01T00:00:00Z"
        )
        self.assertEqual(e1["event_seq"], 1)
        self.assertEqual(e1["prev_hash"], "0" * 64)

        e2 = append_event_to_ledger(
            self.conn, self.game_id, "MOVE", {"x": 5, "y": 6}, 200, "2026-01-01T00:01:00Z"
        )
        self.assertEqual(e2["event_seq"], 2)
        self.assertEqual(e2["prev_hash"], e1["hash"])

        e3 = append_event_to_ledger(
            self.conn, self.game_id, "ATTACK", {"damage": 1}, 300, "2026-01-01T00:02:00Z"
        )
        self.assertEqual(e3["event_seq"], 3)
        self.assertEqual(e3["prev_hash"], e2["hash"])

        # Fetch all events and verify chain
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM events WHERE game_id = ? ORDER BY event_seq ASC", (self.game_id,))
        events = [dict(r) for r in cur.fetchall()]

        valid, msg = verify_ledger_chain(events)
        self.assertTrue(valid)
        self.assertEqual(msg, "LEDGER_INTEGRITY_VERIFIED")

    def test_tamper_detection(self):
        """Modifying any historical payload breaks SHA-256 verification."""
        append_event_to_ledger(self.conn, self.game_id, "EVENT_1", {"val": 1}, 100, "2026-01-01T00:00:00Z")
        append_event_to_ledger(self.conn, self.game_id, "EVENT_2", {"val": 2}, 200, "2026-01-01T00:01:00Z")

        cur = self.conn.cursor()
        cur.execute("SELECT * FROM events WHERE game_id = ? ORDER BY event_seq ASC", (self.game_id,))
        events = [dict(r) for r in cur.fetchall()]

        # Maliciously alter payload of event 1
        events[0]["payload_json"] = json.dumps({"val": 999})

        valid, msg = verify_ledger_chain(events)
        self.assertFalse(valid)
        self.assertIn("Hash tamper detected", msg)


if __name__ == "__main__":
    unittest.main()
