"""Google OAuth2 for web login (sign-in) and calendar access (separate consent)."""
import asyncio
import logging
import secrets
import time
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

# In-memory state store: state -> (purpose, user_id|None, expires_at)
_states: dict[str, tuple[str, int | None, float]] = {}

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


def get_login_url() -> str:
    """Generate Google OAuth URL for login."""
    state = secrets.token_urlsafe(16)
    _states[state] = ("login", None, time.monotonic() + 600)
    flow = _make_flow(_LOGIN_SCOPES)
    url, _ = flow.authorization_url(
        access_type="online",
        state=state,
        prompt="select_account",
    )
    return url


def get_calendar_url(user_id: int) -> str:
    """Generate Google OAuth URL for calendar read access (all users)."""
    state = secrets.token_urlsafe(16)
    _states[state] = ("calendar", user_id, time.monotonic() + 600)
    flow = _make_flow(_CALENDAR_SCOPES)
    url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="false",
        state=state,
        prompt="consent",
    )
    return url


def get_calendar_write_url(user_id: int) -> str:
    """Generate Google OAuth URL for calendar write access (admin E2E only)."""
    state = secrets.token_urlsafe(16)
    _states[state] = ("calendar_write", user_id, time.monotonic() + 600)
    flow = _make_flow(_CALENDAR_WRITE_SCOPES)
    url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="false",
        state=state,
        prompt="consent",
    )
    return url


def _pop_state(state: str) -> tuple[str, int | None] | None:
    entry = _states.pop(state, None)
    if not entry:
        return None
    purpose, user_id, expires_at = entry
    if time.monotonic() > expires_at:
        return None
    return purpose, user_id


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
    code: str, state: str
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

    purpose, existing_user_id = entry
    if purpose == "calendar_write":
        scopes = _CALENDAR_WRITE_SCOPES
    elif purpose == "calendar":
        scopes = _CALENDAR_SCOPES
    else:
        scopes = _LOGIN_SCOPES

    loop = asyncio.get_running_loop()
    tokens = await loop.run_in_executor(None, _exchange_code_sync, code, scopes)
    user_info = await loop.run_in_executor(
        None, _fetch_userinfo_sync, tokens["token"]
    )

    return {
        "purpose": purpose,
        "user_info": user_info,
        "tokens": tokens,
        "existing_user_id": existing_user_id,
    }
