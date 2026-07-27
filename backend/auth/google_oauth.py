"""Google OAuth2 for web login (sign-in) and calendar access (separate consent)."""
import asyncio
import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from config import config

logger = logging.getLogger(__name__)

# Scopes for web login only
_LOGIN_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

# Scopes for calendar access (all users — read only)
_CALENDAR_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/calendar.readonly",
]

# Scopes for E2E test calendar management (admin only — needs write to create test events)
_CALENDAR_WRITE_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/calendar",
]

# Scopes for sending the meeting protocol from the user's own mailbox.
# Personal OAuth (NOT service-account/DWD) — no Workspace super-admin needed,
# works for any Google account including personal gmail. Kept minimal (no
# calendar) so the send token stays independent of calendar sync.
_GMAIL_SEND_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/gmail.send",
]

_STATE_TTL_SECONDS = 600
_MAX_PENDING_STATES = 10_000


@dataclass(frozen=True)
class _OAuthState:
    purpose: str
    user_id: int | None
    expires_at: float
    session_fingerprint: str | None = None
    expected_google_id: str | None = None
    expected_email: str | None = None
    next_path: str | None = None


# A single-process store is sufficient while uvicorn runs with --workers 1.
# It is bounded and expired entries are pruned on every new flow.
_states: dict[str, _OAuthState] = {}

_CLIENT_CONFIG = {
    "web": {
        "client_id": config.GOOGLE_CLIENT_ID,
        "client_secret": config.GOOGLE_CLIENT_SECRET,
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [config.GOOGLE_REDIRECT_URI],
    }
}


def _make_flow(scopes: list[str]):
    from google_auth_oauthlib.flow import Flow
    return Flow.from_client_config(
        _CLIENT_CONFIG,
        scopes=scopes,
        redirect_uri=config.GOOGLE_REDIRECT_URI,
    )


def _session_fingerprint(session_token: str) -> str:
    """Bind a consent flow to the web session that initiated it."""
    return hmac.new(
        config.SECRET_KEY.encode("utf-8"),
        session_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _prune_states() -> None:
    now = time.monotonic()
    for state in [s for s, entry in _states.items() if entry.expires_at <= now]:
        _states.pop(state, None)
    while len(_states) >= _MAX_PENDING_STATES:
        _states.pop(next(iter(_states)))


def _store_state(
    purpose: str,
    *,
    user_id: int | None = None,
    session_token: str | None = None,
    expected_google_id: str | None = None,
    expected_email: str | None = None,
    next_path: str | None = None,
) -> str:
    _prune_states()
    state = secrets.token_urlsafe(24)
    _states[state] = _OAuthState(
        purpose=purpose,
        user_id=user_id,
        expires_at=time.monotonic() + _STATE_TTL_SECONDS,
        session_fingerprint=_session_fingerprint(session_token) if session_token else None,
        expected_google_id=expected_google_id,
        expected_email=(expected_email or "").lower() or None,
        next_path=next_path,
    )
    return state


def get_login_url() -> str:
    """Generate Google OAuth URL for login."""
    state = _store_state("login")
    flow = _make_flow(_LOGIN_SCOPES)
    url, _ = flow.authorization_url(
        access_type="online",
        state=state,
        prompt="select_account",
    )
    return url


def get_calendar_url(
    user_id: int,
    *,
    session_token: str,
    expected_google_id: str | None,
    expected_email: str,
) -> str:
    """Generate Google OAuth URL for calendar read access (all users)."""
    state = _store_state(
        "calendar",
        user_id=user_id,
        session_token=session_token,
        expected_google_id=expected_google_id,
        expected_email=expected_email,
    )
    flow = _make_flow(_CALENDAR_SCOPES)
    url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="false",
        state=state,
        prompt="consent",
    )
    return url


def get_calendar_write_url(
    user_id: int,
    *,
    session_token: str,
    expected_google_id: str | None,
    expected_email: str,
) -> str:
    """Generate Google OAuth URL for calendar write access (admin E2E only)."""
    state = _store_state(
        "calendar_write",
        user_id=user_id,
        session_token=session_token,
        expected_google_id=expected_google_id,
        expected_email=expected_email,
    )
    flow = _make_flow(_CALENDAR_WRITE_SCOPES)
    url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="false",
        state=state,
        prompt="consent",
    )
    return url


def get_gmail_send_url(
    user_id: int,
    next_path: str | None = None,
    *,
    session_token: str,
    expected_google_id: str | None,
    expected_email: str,
) -> str:
    """Generate Google OAuth URL for personal gmail.send consent."""
    state = _store_state(
        "gmail_send",
        user_id=user_id,
        session_token=session_token,
        expected_google_id=expected_google_id,
        expected_email=expected_email,
        next_path=next_path,
    )
    flow = _make_flow(_GMAIL_SEND_SCOPES)
    url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="false",
        state=state,
        prompt="consent",
    )
    return url


def _pop_state(state: str) -> _OAuthState | None:
    entry = _states.pop(state, None)
    if not entry:
        return None
    if time.monotonic() > entry.expires_at:
        return None
    return entry


class OAuthError(Exception):
    """Raised when Google OAuth code exchange or userinfo fetch fails."""
    pass


def _exchange_code_sync(code: str, scopes: list[str]) -> dict:
    try:
        flow = _make_flow(scopes)
        flow.fetch_token(code=code)
        creds = flow.credentials
        return {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "expiry": creds.expiry,
            "id_token": creds.id_token,
        }
    except Exception as e:
        logger.error("OAuth code exchange failed: %s: %s", type(e).__name__, e)
        raise OAuthError(f"Code exchange failed: {e}") from e


def _fetch_userinfo_sync(access_token: str) -> dict:
    import requests
    try:
        resp = requests.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error("Userinfo fetch failed: %s: %s", type(e).__name__, e)
        raise OAuthError(f"Userinfo fetch failed: {e}") from e


async def handle_callback(
    code: str, state: str, session_token: str | None = None
) -> dict | None:
    """
    Handle OAuth callback.
    Returns dict with keys: purpose, user_info, tokens, user_id (for calendar flow).
    Raises OAuthError if Google rejects the code.
    """
    entry = _pop_state(state)
    if not entry:
        logger.warning("Unknown or expired OAuth state: %s", state)
        return None

    purpose = entry.purpose
    existing_user_id = entry.user_id
    if entry.session_fingerprint:
        if not session_token or not hmac.compare_digest(
            entry.session_fingerprint, _session_fingerprint(session_token)
        ):
            logger.warning("OAuth consent callback used from a different web session")
            return None
    if purpose == "calendar_write":
        scopes = _CALENDAR_WRITE_SCOPES
    elif purpose == "calendar":
        scopes = _CALENDAR_SCOPES
    elif purpose == "gmail_send":
        scopes = _GMAIL_SEND_SCOPES
    else:
        scopes = _LOGIN_SCOPES

    loop = asyncio.get_running_loop()
    tokens = await loop.run_in_executor(None, _exchange_code_sync, code, scopes)
    user_info = await loop.run_in_executor(
        None, _fetch_userinfo_sync, tokens["token"]
    )

    if purpose != "login":
        google_id = user_info.get("sub")
        email = (user_info.get("email") or "").lower()
        if entry.expected_google_id and google_id != entry.expected_google_id:
            logger.warning("OAuth consent Google account does not match the signed-in user")
            return None
        if entry.expected_email and email != entry.expected_email:
            logger.warning("OAuth consent email does not match the signed-in user")
            return None

    return {
        "purpose": purpose,
        "user_info": user_info,
        "tokens": tokens,
        "existing_user_id": existing_user_id,
        "next_path": entry.next_path,
    }
