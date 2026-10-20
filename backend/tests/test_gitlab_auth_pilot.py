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


def _request(session=None, cookie=None, invite_token=None):
    headers = []
    cookies = []
    if cookie:
        cookies.append(f"session={cookie}")
    if invite_token:
        cookies.append(f"invite_token={invite_token}")
    if cookies:
        headers.append((b"cookie", "; ".join(cookies).encode()))
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


def test_status_only_offers_linking_to_unlinked_pilot(monkeypatch):
    monkeypatch.setattr(gitlab_auth.config, "GITLAB_PILOT_ENABLED", True)
    monkeypatch.setattr(gitlab_auth.config, "GITLAB_CLIENT_ID", "test-client")
    monkeypatch.setattr(gitlab_auth.config, "GITLAB_CLIENT_SECRET", "test-secret")
    get_session = AsyncMock(return_value=_existing_user())
    has_identity = AsyncMock(return_value=False)
    monkeypatch.setattr(gitlab_auth.models, "get_session", get_session, raising=False)
    monkeypatch.setattr(gitlab_auth.models, "has_auth_identity", has_identity, raising=False)

    assert asyncio.run(gitlab_auth.gitlab_status()) == {
        "enabled": True, "direct_login": False, "all_users": False,
        "can_link": False, "linked": False,
    }
    assert asyncio.run(gitlab_auth.gitlab_status("pilot-session")) == {
        "enabled": True, "direct_login": False, "all_users": False,
        "can_link": True, "linked": False,
    }
    has_identity.return_value = True
    assert asyncio.run(gitlab_auth.gitlab_status("pilot-session")) == {
        "enabled": True, "direct_login": False, "all_users": False,
        "can_link": False, "linked": True,
    }
    get_session.return_value = {**_existing_user(), "email": "other@sifox.com"}
    assert asyncio.run(gitlab_auth.gitlab_status("other-session")) == {
        "enabled": True, "direct_login": False, "all_users": False,
        "can_link": False, "linked": False,
    }


def test_all_user_mode_registers_verified_sifox_email(monkeypatch):
    monkeypatch.setattr(gitlab_auth.config, "GITLAB_ALL_USERS_ENABLED", True)
    monkeypatch.setattr(gitlab_auth, "_client", lambda: FakeClient({
        "sub": "901", "email": "person@sifox.ru", "email_verified": True,
    }))
    monkeypatch.setattr(gitlab_auth.models, "get_user_by_auth_identity",
                        AsyncMock(return_value=None), raising=False)
    enroll = AsyncMock(return_value={"id": 91, "email": "person@sifox.ru"})
    monkeypatch.setattr(gitlab_auth.models, "get_or_create_gitlab_user", enroll, raising=False)
    monkeypatch.setattr(gitlab_auth.models, "create_session",
                        AsyncMock(return_value="session-ru"), raising=False)
    result = asyncio.run(gitlab_auth.gitlab_callback(
        _request(session={"gitlab_pilot": {"purpose": "login"}})
    ))
    assert "session=session-ru" in result.headers["set-cookie"]
    assert enroll.await_args.kwargs["email"] == "person@sifox.ru"
    assert enroll.await_args.kwargs["subject"] == "901"


def test_all_user_mode_rejects_missing_or_wrong_email(monkeypatch):
    monkeypatch.setattr(gitlab_auth.config, "GITLAB_ALL_USERS_ENABLED", True)
    lookup = AsyncMock(return_value=None)
    enroll = AsyncMock()
    monkeypatch.setattr(gitlab_auth.models, "get_user_by_auth_identity", lookup, raising=False)
    monkeypatch.setattr(gitlab_auth.models, "get_or_create_gitlab_user", enroll, raising=False)
    for claim, error in (
        ({"sub": "901"}, "gitlab_identity_unverified"),
        ({"sub": "901", "email": "person@sifox.ru", "email_verified": False}, "gitlab_identity_unverified"),
        ({"sub": "901", "email": "person@example.com", "email_verified": True}, "domain_not_allowed"),
    ):
        monkeypatch.setattr(gitlab_auth, "_client", lambda claim=claim: FakeClient(claim))
        result = asyncio.run(gitlab_auth.gitlab_callback(
            _request(session={"gitlab_pilot": {"purpose": "login"}})
        ))
        assert error in result.headers["location"]
    lookup.assert_not_awaited()
    enroll.assert_not_awaited()


def test_all_user_mode_rejects_changed_email_for_linked_subject(monkeypatch):
    monkeypatch.setattr(gitlab_auth.config, "GITLAB_ALL_USERS_ENABLED", True)
    monkeypatch.setattr(gitlab_auth, "_client", lambda: FakeClient({
        "sub": "901", "email": "person@sifox.ru", "email_verified": True,
    }))
    monkeypatch.setattr(gitlab_auth.models, "get_user_by_auth_identity", AsyncMock(
        return_value={"id": 91, "email": "person@sifox.com", "is_active": True},
    ), raising=False)
    result = asyncio.run(gitlab_auth.gitlab_callback(
        _request(session={"gitlab_pilot": {"purpose": "login"}})
    ))
    assert "gitlab_account_conflict" in result.headers["location"]


def test_gitlab_invitation_requires_exact_email_and_marks_accepted(monkeypatch):
    monkeypatch.setattr(gitlab_auth.config, "GITLAB_ALL_USERS_ENABLED", True)
    monkeypatch.setattr(gitlab_auth, "_client", lambda: FakeClient({
        "sub": "901", "email": "person@sifox.ru", "email_verified": True,
    }))
    invitation = {"email": "other@sifox.ru", "accepted_at": None}
    monkeypatch.setattr(gitlab_auth.models, "get_invitation_by_token",
                        AsyncMock(return_value=invitation), raising=False)
    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(gitlab_auth.models, "get_user_by_auth_identity", lookup, raising=False)
    accept = AsyncMock()
    monkeypatch.setattr(gitlab_auth.models, "accept_invitation", accept, raising=False)
    first = asyncio.run(gitlab_auth.gitlab_callback(
        _request(session={"gitlab_pilot": {"purpose": "login"}}, invite_token="invite-token")
    ))
    assert "invite_email_mismatch" in first.headers["location"]
    lookup.assert_not_awaited()
    accept.assert_not_awaited()
    invitation["email"] = "person@sifox.ru"
    monkeypatch.setattr(gitlab_auth.models, "get_or_create_gitlab_user", AsyncMock(
        return_value={"id": 91, "email": "person@sifox.ru"}), raising=False)
    monkeypatch.setattr(gitlab_auth.models, "create_session", AsyncMock(return_value="new-session"), raising=False)
    second = asyncio.run(gitlab_auth.gitlab_callback(
        _request(session={"gitlab_pilot": {"purpose": "login"}}, invite_token="invite-token")
    ))
    assert second.headers["location"].endswith("/")
    accept.assert_awaited_once_with("invite-token")
    assert any("invite_token=" in value for value in second.headers.getlist("set-cookie"))
