import asyncio
import time
from urllib.parse import parse_qs, urlencode, urlparse

from auth import google_oauth as oauth


class _FakeLoginFlow:
    def authorization_url(self, **kwargs):
        query = urlencode(
            {
                **kwargs,
                "client_id": oauth._CLIENT_CONFIG["web"]["client_id"],
                "redirect_uri": oauth._CLIENT_CONFIG["web"]["redirect_uris"][0],
                "response_type": "code",
            }
        )
        return f'{oauth._CLIENT_CONFIG["web"]["auth_uri"]}?{query}', kwargs["state"]


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


def test_login_oauth_roundtrip_uses_v2_endpoint_and_single_use_state(monkeypatch):
    """Cover login URL creation through callback state consumption."""
    oauth._states.clear()
    monkeypatch.setattr(oauth, "_make_flow", lambda scopes: _FakeLoginFlow())

    login_url = oauth.get_login_url()
    parsed = urlparse(login_url)
    query = parse_qs(parsed.query)

    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == (
        "https://accounts.google.com/o/oauth2/v2/auth"
    )
    assert query["prompt"] == ["select_account"]
    assert query["access_type"] == ["online"]
    state = query["state"][0]
    assert oauth._states[state].purpose == "login"

    monkeypatch.setattr(
        oauth,
        "_exchange_code_sync",
        lambda code, scopes: {
            "token": "access",
            "refresh_token": None,
            "expiry": None,
        },
    )
    monkeypatch.setattr(
        oauth,
        "_fetch_userinfo_sync",
        lambda token: {"sub": "google-1", "email": "user@sifox.com"},
    )

    result = asyncio.run(oauth.handle_callback("authorization-code", state))

    assert result is not None
    assert result["purpose"] == "login"
    assert result["user_info"]["email"] == "user@sifox.com"
    assert asyncio.run(oauth.handle_callback("replayed-code", state)) is None
