"""Public meeting-share helpers: password hashing + signed audio tokens.

Stdlib only (no bcrypt/passlib C-dependency). Used by the public share router
so anyone with the link + password can view a meeting without logging in.
"""
import base64
import hashlib
import hmac
import os
import secrets
import time

from config import config

_PBKDF2_ITERATIONS = 200_000


# ── Password hashing (PBKDF2-HMAC-SHA256) ──────────────────────────────────────

def hash_password(password: str) -> str:
    """Return 'iterations$salt_hex$hash_hex' for storage."""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"{_PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verify against a 'iterations$salt_hex$hash_hex' string."""
    try:
        iters_s, salt_hex, hash_hex = stored.split("$")
        iters = int(iters_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iters)
    return hmac.compare_digest(dk, expected)


# ── Signed, short-lived audio tokens ───────────────────────────────────────────
# Let the public <audio> element fetch the mp3 without a session: after the
# password is verified we hand out a token = "<exp>.<sig>" where sig signs the
# share token + expiry with SECRET_KEY. No DB row needed.

def _sign(share_token: str, exp: int) -> str:
    msg = f"{share_token}:{exp}".encode("utf-8")
    sig = hmac.new(config.SECRET_KEY.encode("utf-8"), msg, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).decode("utf-8").rstrip("=")


def make_audio_token(share_token: str, ttl_seconds: int = 6 * 3600) -> str:
    exp = int(time.time()) + ttl_seconds
    return f"{exp}.{_sign(share_token, exp)}"


def verify_audio_token(share_token: str, token: str) -> bool:
    try:
        exp_s, sig = token.split(".", 1)
        exp = int(exp_s)
    except (ValueError, AttributeError):
        return False
    if exp < int(time.time()):
        return False
    return hmac.compare_digest(sig, _sign(share_token, exp))


def new_share_token() -> str:
    return secrets.token_urlsafe(24)
