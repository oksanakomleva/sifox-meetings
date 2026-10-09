"""The GitLab pilot may only link/sign in the existing Google user."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import RedirectResponse

from api import gitlab_auth


class FakeClient:
    def __init__(self, userinfo=None):
        self.userinfo = userinfo or {
            "sub": "gitlab-stable-user-id",
            "preferred_username": "oksana.komleva",
        }

    async def authorize_redirect(self, request, redirect_uri):
        assert redirect_uri.endswith("/api/auth/gitlab/callback")
        return RedirectResponse("https://gitlab.sifox.ru/oauth/authorize")

    async def authorize_access_token(self, request):
        return {"userinfo": self.userinfo}


def _request(session=None, cookie=None):
    headers = []
    if cookie:
        headers.append((b"cookie", f"session={cookie}".encode()))
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/api/auth/gitlab/callback",
        "query_string": b"",
        "headers": headers,
        "session": session if session is not None else {},
    })


def _existing_user():
    return {
        "user_id": 42,
        "id": 42,
        "email": gitlab_auth.config.GITLAB_PILOT_EMAIL,
        "google_id": "google-stable-user-id",
        "is_active": True,
        "is_preview": False,
    }


def test_link_requires_existing_google_pilot_session(monkeypatch):
    monkeypatch.setattr(gitlab_auth, "_client", lambda: FakeClient())
    monkeypatch.setattr(
        gitlab_auth.models, "get_session", AsyncMock(return_value=_existing_user()),
        raising=False,
    )
    req = _request(cookie="original-session")
    result = asyncio.run(gitlab_auth.link_gitlab(req, "original-session"))
    assert result.status_code == 307
    assert req.session["gitlab_pilot"]["user_id"] == 42
    assert "original-session" not in str(req.session)

    another = {**_existing_user(), "email": "another@sifox.com"}
    monkeypatch.setattr(
        gitlab_auth.models, "get_session", AsyncMock(return_value=another),
        raising=False,
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(gitlab_auth.link_gitlab(_request(), "another-session"))
    assert exc.value.status_code == 403


def test_callback_links_gitlab_to_same_user_not_a_new_account(monkeypatch):
    monkeypatch.setattr(gitlab_auth, "_client", lambda: FakeClient())
    get_session = AsyncMock(return_value=_existing_user())
    link_identity = AsyncMock(return_value=True)
    monkeypatch.setattr(gitlab_auth.models, "get_session", get_session, raising=False)
    monkeypatch.setattr(gitlab_auth.models, "link_auth_identity", link_identity, raising=False)
    req = _request(
        session={"gitlab_pilot": {
            "purpose": "link", "user_id": 42,
            "fingerprint": gitlab_auth._fingerprint("original-session"),
        }},
        cookie="original-session",
    )
    result = asyncio.run(gitlab_auth.gitlab_callback(req))
    assert result.headers["location"].endswith("/?gitlab_linked=1")
    link_identity.assert_awaited_once_with(42, "gitlab", "gitlab-stable-user-id")
    assert req.session == {}


def test_link_rejects_a_different_google_session(monkeypatch):
    monkeypatch.setattr(gitlab_auth, "_client", lambda: FakeClient())
    link_identity = AsyncMock(return_value=True)
    monkeypatch.setattr(gitlab_auth.models, "link_auth_identity", link_identity, raising=False)
    req = _request(
        session={"gitlab_pilot": {
            "purpose": "link", "user_id": 42,
            "fingerprint": gitlab_auth._fingerprint("original-session"),
        }},
        cookie="different-session",
    )
    result = asyncio.run(gitlab_auth.gitlab_callback(req))
    assert "gitlab_error=gitlab_link_failed" in result.headers["location"]
    link_identity.assert_not_awaited()


def test_link_rejects_a_different_gitlab_username(monkeypatch):
    monkeypatch.setattr(
        gitlab_auth, "_client",
        lambda: FakeClient({"sub": "other-gitlab-id", "preferred_username": "another.user"}),
    )
    monkeypatch.setattr(
        gitlab_auth.models, "get_session", AsyncMock(return_value=_existing_user()),
        raising=False,
    )
    link_identity = AsyncMock(return_value=True)
    monkeypatch.setattr(gitlab_auth.models, "link_auth_identity", link_identity, raising=False)
    req = _request(
        session={"gitlab_pilot": {
            "purpose": "link", "user_id": 42,
            "fingerprint": gitlab_auth._fingerprint("original-session"),
        }},
        cookie="original-session",
    )
    result = asyncio.run(gitlab_auth.gitlab_callback(req))
    assert "gitlab_link_failed" in result.headers["location"]
    link_identity.assert_not_awaited()


def test_login_only_accepts_already_linked_active_pilot(monkeypatch):
    monkeypatch.setattr(gitlab_auth, "_client", lambda: FakeClient())
    lookup = AsyncMock(return_value={**_existing_user(), "id": 42})
    create_session = AsyncMock(return_value="new-session")
    monkeypatch.setattr(
        gitlab_auth.models, "get_user_by_auth_identity", lookup, raising=False,
    )
    monkeypatch.setattr(
        gitlab_auth.models, "create_session", create_session, raising=False,
    )
    req = _request(session={"gitlab_pilot": {"purpose": "login"}})
    result = asyncio.run(gitlab_auth.gitlab_callback(req))
    lookup.assert_awaited_once_with("gitlab", "gitlab-stable-user-id")
    create_session.assert_awaited_once_with(42, gitlab_auth.config.SESSION_TTL_DAYS)
    assert "session=new-session" in result.headers["set-cookie"]

    lookup.return_value = None
    req = _request(session={"gitlab_pilot": {"purpose": "login"}})
    result = asyncio.run(gitlab_auth.gitlab_callback(req))
    assert "gitlab_not_linked" in result.headers["location"]
    assert create_session.await_count == 1

    lookup.return_value = {**_existing_user(), "id": 42, "is_active": False}
    req = _request(session={"gitlab_pilot": {"purpose": "login"}})
    result = asyncio.run(gitlab_auth.gitlab_callback(req))
    assert "gitlab_not_linked" in result.headers["location"]
    assert create_session.await_count == 1


def test_pilot_is_off_without_flag_and_credentials(monkeypatch):
    monkeypatch.setattr(gitlab_auth.config, "GITLAB_PILOT_ENABLED", False)
    with pytest.raises(HTTPException) as exc:
        gitlab_auth._require_pilot()
    assert exc.value.status_code == 404
