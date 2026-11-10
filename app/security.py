"""Authentication, identity, session management, and brute-force rate limiting."""
from __future__ import annotations

import os
import time
import base64
import hmac
import hashlib
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Tuple, Set, Any
from collections import defaultdict

from app.config import (
    SECRET_KEY,
    SESSION_MAX_AGE_SECONDS,
    RATE_LIMIT_MAX_ATTEMPTS,
    RATE_LIMIT_WINDOW_SECONDS,
)
from app.database import query_one, db_transaction

try:
    from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
    HAS_CRYPTO_ARGON2 = True
except ImportError:
    Argon2id = None
    HAS_CRYPTO_ARGON2 = False


def hash_password(password: str) -> str:
    """Hash password using Argon2id (m=64MiB, t=3, p=4) per spec."""
    if HAS_CRYPTO_ARGON2 and Argon2id is not None:
        salt = os.urandom(16)
        # memory_cost in KiB: 64 MiB = 65536 KiB
        kdf = Argon2id(salt=salt, length=32, iterations=3, lanes=4, memory_cost=65536)
        raw_hash = kdf.derive(password.encode("utf-8"))
        salt_b64 = base64.b64encode(salt).decode("ascii")
        hash_b64 = base64.b64encode(raw_hash).decode("ascii")
        return f"$argon2id$v=19$m=65536,t=3,p=4${salt_b64}${hash_b64}"
    else:
        # Fallback to PBKDF2 with 200k iterations if Argon2id is unavailable
        salt = os.urandom(16)
        derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200000)
        return f"$pbkdf2_sha256$200000${salt.hex()}${derived.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    """Verify password against stored Argon2id or fallback hash."""
    try:
        if hashed.startswith("$argon2id$") and HAS_CRYPTO_ARGON2 and Argon2id is not None:
            parts = hashed.split("$")
            salt = base64.b64decode(parts[4])
            expected = base64.b64decode(parts[5])
            kdf = Argon2id(salt=salt, length=32, iterations=3, lanes=4, memory_cost=65536)
            derived = kdf.derive(password.encode("utf-8"))
            return hmac.compare_digest(derived, expected)
        elif hashed.startswith("$pbkdf2_sha256$"):
            parts = hashed.split("$")
            iterations = int(parts[2])
            salt = bytes.fromhex(parts[3])
            expected = bytes.fromhex(parts[4])
            derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
            return hmac.compare_digest(derived, expected)
    except Exception:
        return False
    return False


# -------------------------------------------------------------------------
# Sliding-Window Rate Limiter
# -------------------------------------------------------------------------
class SlidingWindowRateLimiter:
    """In-memory sliding-window token bucket to prevent brute-force attacks."""

    def __init__(self, max_attempts: int = RATE_LIMIT_MAX_ATTEMPTS, window_seconds: int = RATE_LIMIT_WINDOW_SECONDS):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._history: Dict[str, list[float]] = defaultdict(list)

    def is_rate_limited(self, key: str) -> bool:
        """Check if key has exceeded allowed attempts in sliding window."""
        now = time.time()
        cutoff = now - self.window_seconds
        timestamps = [t for t in self._history[key] if t > cutoff]
        self._history[key] = timestamps
        return len(timestamps) >= self.max_attempts

    def record_attempt(self, key: str) -> None:
        """Record a failed attempt."""
        now = time.time()
        cutoff = now - self.window_seconds
        timestamps = [t for t in self._history[key] if t > cutoff]
        timestamps.append(now)
        self._history[key] = timestamps

    def reset(self, key: str) -> None:
        """Reset attempts on successful login."""
        self._history.pop(key, None)


rate_limiter = SlidingWindowRateLimiter()


# -------------------------------------------------------------------------
# Session Management
# -------------------------------------------------------------------------
def sign_cookie_value(raw_id: str) -> str:
    """Sign a raw session ID with HMAC-SHA256."""
    sig = hmac.new(SECRET_KEY.encode("utf-8"), raw_id.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{raw_id}.{sig}"


def verify_cookie_value(cookie_val: str) -> Optional[str]:
    """Verify and extract raw session ID from signed cookie value."""
    if not cookie_val or "." not in cookie_val:
        return None
    raw_id, sig = cookie_val.split(".", 1)
    expected_sig = hmac.new(SECRET_KEY.encode("utf-8"), raw_id.encode("utf-8"), hashlib.sha256).hexdigest()
    if hmac.compare_digest(sig, expected_sig):
        return raw_id
    return None


def create_session(user_id: str) -> Tuple[str, str]:
    """Create a new database session and return (raw_session_id, signed_cookie_value)."""
    raw_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=SESSION_MAX_AGE_SECONDS)

    with db_transaction() as conn:
        conn.execute(
            "INSERT INTO sessions (session_id, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (raw_id, user_id, now.isoformat(), expires.isoformat()),
        )

    signed_cookie = sign_cookie_value(raw_id)
    return raw_id, signed_cookie


def get_user_from_session_cookie(cookie_val: Optional[str]) -> Optional[Dict[str, Any]]:
    """Validate session cookie and return user row dict or None."""
    if not cookie_val:
        return None
    raw_id = verify_cookie_value(cookie_val)
    if not raw_id:
        return None

    now_iso = datetime.now(timezone.utc).isoformat()
    row = query_one(
        """
        SELECT u.id, u.username, u.national_flag, u.avatar_path, u.preferred_lang, u.is_admin
        FROM sessions s
        JOIN users u ON s.user_id = u.id
        WHERE s.session_id = ? AND s.expires_at > ?
        """,
        (raw_id, now_iso),
    )
    if row:
        return dict(row)
    return None


def destroy_session(cookie_val: Optional[str]) -> None:
    """Delete session from database on logout."""
    if not cookie_val:
        return
    raw_id = verify_cookie_value(cookie_val)
    if not raw_id:
        return
    with db_transaction() as conn:
        conn.execute("DELETE FROM sessions WHERE session_id = ?", (raw_id,))
