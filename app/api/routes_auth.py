"""Authentication and identity REST endpoints."""
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from app.database import db_transaction, query_one
from app.security import (
    hash_password,
    verify_password,
    rate_limiter,
    create_session,
    destroy_session,
    get_user_from_session_cookie,
)
from app.avatar import sanitize_and_save_avatar
from app.flags import is_valid_country_code, code_to_emoji, ISO_FLAGS
from app.config import SESSION_COOKIE_NAME, SESSION_MAX_AGE_SECONDS


def register_user(
    username: str,
    password: str,
    national_flag: str = "UN",
    preferred_lang: str = "en",
    invite_code: Optional[str] = None
) -> Dict[str, Any]:
    """Register a new user account with Argon2id password hashing and invite code verification."""
    username = username.strip()
    if len(username) < 3 or len(username) > 30:
        raise ValueError("Username must be between 3 and 30 characters")
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters")
    flag_val = code_to_emoji(national_flag) if is_valid_country_code(national_flag) else "🇺🇳"

    existing = query_one("SELECT id FROM users WHERE username = ?", (username,))
    if existing:
        raise ValueError("Username already taken")

    # Check if registrations are disabled
    setting = query_one("SELECT value FROM system_settings WHERE key = 'registrations_enabled'")
    reg_enabled = (setting is None or setting["value"] == "1")

    valid_invite = None
    if invite_code and invite_code.strip():
        inv = query_one("SELECT * FROM invite_tokens WHERE token = ?", (invite_code.strip(),))
        if not inv or inv["uses_left"] <= 0:
            raise ValueError("Invalid or depleted invite code")
        if inv["expires_at"]:
            try:
                exp = datetime.fromisoformat(inv["expires_at"])
                if datetime.now(timezone.utc) > exp:
                    raise ValueError("Invite code has expired")
            except Exception:
                pass
        valid_invite = inv["token"]
    elif not reg_enabled:
        raise ValueError("Public registration is disabled. A valid invite code is required.")

    # First registered user becomes system administrator
    user_count_row = query_one("SELECT COUNT(*) as cnt FROM users")
    is_admin = 1 if (user_count_row and user_count_row["cnt"] == 0) else 0

    user_id = f"usr_{uuid.uuid4().hex[:12]}"
    pwd_hash = hash_password(password)
    now_iso = datetime.now(timezone.utc).isoformat()

    with db_transaction() as conn:
        conn.execute(
            """
            INSERT INTO users (id, username, password_hash, national_flag, preferred_lang, is_admin, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, username, pwd_hash, flag_val, preferred_lang, is_admin, now_iso)
        )
        if valid_invite:
            conn.execute(
                """
                UPDATE invite_tokens
                SET uses_left = uses_left - 1, used_count = used_count + 1
                WHERE token = ?
                """,
                (valid_invite,)
            )

    return {
        "id": user_id,
        "username": username,
        "national_flag": national_flag.upper(),
        "preferred_lang": preferred_lang,
        "is_admin": bool(is_admin)
    }


def authenticate_user(username: str, password: str, ip_address: str = "127.0.0.1") -> Dict[str, Any]:
    """Authenticate credentials with sliding-window brute-force rate limiting."""
    rate_limit_key = f"{ip_address}:{username.lower()}"

    if rate_limiter.is_rate_limited(rate_limit_key):
        raise ValueError("ERR_RATE_LIMITED: Too many failed attempts. Try again in 15 minutes.")

    user = query_one("SELECT * FROM users WHERE username = ?", (username.strip(),))
    if not user or not verify_password(password, user["password_hash"]):
        rate_limiter.record_attempt(rate_limit_key)
        raise ValueError("Invalid username or password")

    # Reset failed attempts on success
    rate_limiter.reset(rate_limit_key)

    raw_sid, signed_cookie = create_session(user["id"])
    return {
        "user": {
            "id": user["id"],
            "username": user["username"],
            "national_flag": user["national_flag"],
            "avatar_path": user["avatar_path"],
            "preferred_lang": user["preferred_lang"],
            "is_admin": bool(user["is_admin"])
        },
        "session_cookie": signed_cookie
    }


def delete_user_account(user_id: str) -> bool:
    """Permanently delete an operative account and cascade all user-associated records."""
    with db_transaction() as conn:
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM push_subscriptions WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM notifications WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM players WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return True

