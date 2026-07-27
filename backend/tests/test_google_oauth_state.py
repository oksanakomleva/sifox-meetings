import asyncio
import time

from auth import google_oauth as oauth


def _entry(*, session: str, google_id: str = "google-1", email: str = "user@sifox.com"):
    return oauth._OAuthState(
        purpose="calendar",
        user_id=42,
        expires_at=time.monotonic() + 60,
        session_fingerprint=oauth._session_fingerprint(session),
        expected_google_id=google_id,
        expected_email=email,
    )


def test_consent_callback_rejects_different_web_session(monkeypatch):
    state = "state-different-session"
    oauth._states[state] = _entry(session="original")

    result = asyncio.run(oauth.handle_callback("code", state, "attacker"))

    assert result is None


def test_consent_callback_rejects_different_google_identity(monkeypatch):
    state = "state-different-google-user"
    oauth._states[state] = _entry(session="original")
    monkeypatch.setattr(
        oauth,
        "_exchange_code_sync",
        lambda code, scopes: {"token": "access", "refresh_token": None, "expiry": None},
    )
    monkeypatch.setattr(
        oauth,
        "_fetch_userinfo_sync",
        lambda token: {"sub": "google-2", "email": "other@sifox.com"},
    )

    result = asyncio.run(oauth.handle_callback("code", state, "original"))

    assert result is None


def test_consent_callback_accepts_same_session_and_identity(monkeypatch):
    state = "state-valid"
    oauth._states[state] = _entry(session="original")
    monkeypatch.setattr(
        oauth,
        "_exchange_code_sync",
        lambda code, scopes: {"token": "access", "refresh_token": None, "expiry": None},
    )
    monkeypatch.setattr(
        oauth,
        "_fetch_userinfo_sync",
        lambda token: {"sub": "google-1", "email": "USER@sifox.com"},
    )

    result = asyncio.run(oauth.handle_callback("code", state, "original"))

    assert result is not None
    assert result["existing_user_id"] == 42
