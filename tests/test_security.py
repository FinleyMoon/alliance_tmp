"""Unit tests for Argon2id password hashing, sliding-window rate limiting, and avatar validation."""
import unittest
import time
from app.security import (
    hash_password,
    verify_password,
    SlidingWindowRateLimiter,
    sign_cookie_value,
    verify_cookie_value,
)
from app.avatar import inspect_magic_bytes, sanitize_and_save_avatar


class TestSecurity(unittest.TestCase):
    def test_argon2id_password_hashing(self):
        """Test password hashing and constant-time verification."""
        password = "CyberTacticalSecret2026!"
        h = hash_password(password)
        self.assertTrue(h.startswith("$argon2id$") or h.startswith("$pbkdf2_sha256$"))
        self.assertTrue(verify_password(password, h))
        self.assertFalse(verify_password("wrong_password", h))

    def test_sliding_window_rate_limiter(self):
        """Max 5 attempts in sliding window; 6th attempt is blocked."""
        limiter = SlidingWindowRateLimiter(max_attempts=5, window_seconds=60)
        key = "127.0.0.1:user_alpha"

        for i in range(5):
            self.assertFalse(limiter.is_rate_limited(key))
            limiter.record_attempt(key)

        # 6th attempt should be blocked
        self.assertTrue(limiter.is_rate_limited(key))

        # Resetting should unblock
        limiter.reset(key)
        self.assertFalse(limiter.is_rate_limited(key))

    def test_session_cookie_signing(self):
        """Signed session cookies cannot be forged or tampered with."""
        raw_id = "test_session_token_12345"
        signed = sign_cookie_value(raw_id)
        self.assertEqual(verify_cookie_value(signed), raw_id)

        # Tampered cookie
        tampered = signed + "bad"
        self.assertIsNone(verify_cookie_value(tampered))

    def test_avatar_magic_bytes_inspection(self):
        """Reject spoofed file extensions by inspecting magic bytes."""
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        jpeg_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 20
        webp_bytes = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 20
        spoofed_exe = b"MZ\x90\x00" + b"\x00" * 20

        self.assertEqual(inspect_magic_bytes(png_bytes), "png")
        self.assertEqual(inspect_magic_bytes(jpeg_bytes), "jpeg")
        self.assertEqual(inspect_magic_bytes(webp_bytes), "webp")
        self.assertIsNone(inspect_magic_bytes(spoofed_exe))


if __name__ == "__main__":
    unittest.main()
