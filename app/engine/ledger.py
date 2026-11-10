"""Cryptographic append-only event ledger with SHA-256 hash chaining."""
import hashlib
import json
from typing import Dict, Any, List, Optional, Tuple
import sqlite3

GENESIS_PREV_HASH = "0" * 64


def compute_event_hash(
    event_seq: int,
    prev_hash: str,
    timestamp_ns: int,
    game_id: str,
    event_type: str,
    payload_json: str
) -> str:
    """Compute deterministic SHA-256 hash for ledger entry."""
    data = f"{event_seq}:{prev_hash}:{timestamp_ns}:{game_id}:{event_type}:{payload_json}"
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def append_event_to_ledger(
    conn: sqlite3.Connection,
    game_id: str,
    event_type: str,
    payload: Dict[str, Any],
    timestamp_ns: int,
    created_at_iso: str,
    initiator_user_id: Optional[str] = None,
    target_user_id: Optional[str] = None,
    visibility: str = "PUBLIC"
) -> Dict[str, Any]:
    """Append a validated event to the deterministic hash-chained ledger."""
    # Find latest event sequence and hash for this game
    cur = conn.cursor()
    cur.execute(
        "SELECT event_seq, hash FROM events WHERE game_id = ? ORDER BY event_seq DESC LIMIT 1",
        (game_id,)
    )
    last_event = cur.fetchone()

    if last_event:
        event_seq = last_event["event_seq"] + 1
        prev_hash = last_event["hash"]
    else:
        event_seq = 1
        prev_hash = GENESIS_PREV_HASH

    payload_json = json.dumps(payload, sort_keys=True)
    event_hash = compute_event_hash(
        event_seq=event_seq,
        prev_hash=prev_hash,
        timestamp_ns=timestamp_ns,
        game_id=game_id,
        event_type=event_type,
        payload_json=payload_json
    )

    cur.execute(
        """
        INSERT INTO events (
            game_id, event_seq, event_type, initiator_user_id, target_user_id,
            visibility, payload_json, prev_hash, hash, timestamp_ns, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            game_id, event_seq, event_type, initiator_user_id, target_user_id,
            visibility, payload_json, prev_hash, event_hash, timestamp_ns, created_at_iso
        )
    )

    return {
        "event_seq": event_seq,
        "event_type": event_type,
        "payload": payload,
        "prev_hash": prev_hash,
        "hash": event_hash,
        "timestamp_ns": timestamp_ns,
        "created_at": created_at_iso
    }


def verify_ledger_chain(events: List[Dict[str, Any]]) -> Tuple[bool, str]:
    """Cryptographically verify the integrity of an entire event sequence."""
    expected_prev = GENESIS_PREV_HASH
    for idx, ev in enumerate(events):
        seq = ev["event_seq"]
        if seq != idx + 1:
            return False, f"Event sequence mismatch at index {idx}: expected {idx + 1}, got {seq}"
        if ev["prev_hash"] != expected_prev:
            return False, f"Hash broken at event #{seq}: prev_hash mismatch"

        if "payload" in ev and isinstance(ev["payload"], dict):
            payload_json = json.dumps(ev["payload"], sort_keys=True)
        else:
            payload_json = ev["payload_json"]
        calc_hash = compute_event_hash(
            event_seq=seq,
            prev_hash=ev["prev_hash"],
            timestamp_ns=ev["timestamp_ns"],
            game_id=ev["game_id"],
            event_type=ev["event_type"],
            payload_json=payload_json
        )
        if calc_hash != ev["hash"]:
            return False, f"Hash tamper detected at event #{seq}: expected {calc_hash}, got {ev['hash']}"
        expected_prev = ev["hash"]

    return True, "LEDGER_INTEGRITY_VERIFIED"
