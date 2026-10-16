"""The GitLab pilot allows direct staging login only for its pinned identity."""
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
    monkeypatch.setattr(gitlab_auth.config, "APP_ENV", "production")
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
    monkeypatch.setattr(gitlab_auth.config, "APP_ENV", "production")
    monkeypatch.setattr(gitlab_auth.config, "GITLAB_PILOT_ENABLED", True)
    monkeypatch.setattr(gitlab_auth.config, "GITLAB_CLIENT_ID", "test-client")
    monkeypatch.setattr(gitlab_auth.config, "GITLAB_CLIENT_SECRET", "test-secret")
    get_session = AsyncMock(return_value=_existing_user())
    has_identity = AsyncMock(return_value=False)
    monkeypatch.setattr(gitlab_auth.models, "get_session", get_session, raising=False)
    monkeypatch.setattr(gitlab_auth.models, "has_auth_identity", has_identity, raising=False)

    assert asyncio.run(gitlab_auth.gitlab_status()) == {
        "enabled": True, "direct_login": False, "can_link": False, "linked": False,
    }
    assert asyncio.run(gitlab_auth.gitlab_status("pilot-session")) == {
        "enabled": True, "direct_login": False, "can_link": True, "linked": False,
    }
    has_identity.return_value = True
    assert asyncio.run(gitlab_auth.gitlab_status("pilot-session")) == {
        "enabled": True, "direct_login": False, "can_link": False, "linked": True,
    }
    get_session.return_value = {**_existing_user(), "email": "other@sifox.com"}
    assert asyncio.run(gitlab_auth.gitlab_status("other-session")) == {
        "enabled": True, "direct_login": False, "can_link": False, "linked": False,
    }


def test_staging_direct_login_enrolls_pinned_gitlab_user_without_google(monkeypatch):
    monkeypatch.setattr(gitlab_auth.config, "APP_ENV", "staging")
    monkeypatch.setattr(gitlab_auth.config, "GITLAB_PILOT_SUB", "220")
    monkeypatch.setattr(gitlab_auth, "_client", lambda: FakeClient({
        "sub": "220", "preferred_username": gitlab_auth.config.GITLAB_PILOT_USERNAME,
        "name": "Oksana",  # Email can stay private in GitLab.
    }))
    lookup = AsyncMock(return_value=None)
    enroll = AsyncMock(return_value={"id": 42, "email": gitlab_auth.config.GITLAB_PILOT_EMAIL})
    create_session = AsyncMock(return_value="new-session")
    monkeypatch.setattr(gitlab_auth.models, "get_user_by_auth_identity", lookup, raising=False)
    monkeypatch.setattr(gitlab_auth.models, "get_or_create_gitlab_pilot_user", enroll, raising=False)
    monkeypatch.setattr(gitlab_auth.models, "create_session", create_session, raising=False)

    req = _request(session={"gitlab_pilot": {"purpose": "login"}})
    result = asyncio.run(gitlab_auth.gitlab_callback(req))
    assert result.headers["location"].endswith("/")
    assert "session=new-session" in result.headers["set-cookie"]
    lookup.assert_awaited_once_with("gitlab", "220")
    enroll.assert_awaited_once()
    assert enroll.await_args.kwargs["subject"] == "220"
    assert enroll.await_args.kwargs["email"] == gitlab_auth.config.GITLAB_PILOT_EMAIL
    assert req.session["gitlab_email_check"] == {
        "user_id": 42, "present": False, "verification": "not_provided",
        "matches_account": None,
    }


def test_staging_gitlab_email_check_is_private_and_one_time(monkeypatch):
    monkeypatch.setattr(gitlab_auth.config, "APP_ENV", "staging")
    monkeypatch.setattr(gitlab_auth.models, "get_session",
                        AsyncMock(return_value={"user_id": 42, "is_active": True}),
                        raising=False)
    req = _request(session={"gitlab_email_check": {
        "user_id": 42, "present": True, "verification": "verified",
        "matches_account": True,
    }}, cookie="new-session")
    result = asyncio.run(gitlab_auth.gitlab_email_check(req, "new-session"))
    assert result == {
        "available": True, "present": True, "verification": "verified",
        "matches_account": True,
    }
    assert asyncio.run(gitlab_auth.gitlab_email_check(req, "new-session")) == {
        "available": False,
    }


def test_staging_gitlab_email_check_rejects_another_user(monkeypatch):
    monkeypatch.setattr(gitlab_auth.config, "APP_ENV", "staging")
    monkeypatch.setattr(gitlab_auth.models, "get_session",
                        AsyncMock(return_value={"user_id": 99, "is_active": True}),
                        raising=False)
    req = _request(session={"gitlab_email_check": {
        "user_id": 42, "present": True, "verification": "verified",
        "matches_account": True,
    }}, cookie="other-session")
    assert asyncio.run(gitlab_auth.gitlab_email_check(req, "other-session")) == {
        "available": False,
    }


def test_gitlab_email_check_is_unavailable_in_production(monkeypatch):
    monkeypatch.setattr(gitlab_auth.config, "APP_ENV", "production")
    req = _request(session={"gitlab_email_check": {
        "user_id": 42, "present": True, "verification": "verified",
        "matches_account": True,
    }}, cookie="new-session")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(gitlab_auth.gitlab_email_check(req, "new-session"))
    assert exc.value.status_code == 404


def test_staging_gitlab_email_check_reports_claims_without_address(monkeypatch):
    monkeypatch.setattr(gitlab_auth.config, "APP_ENV", "staging")
    monkeypatch.setattr(gitlab_auth.config, "GITLAB_PILOT_SUB", "220")
    monkeypatch.setattr(gitlab_auth, "_client", lambda: FakeClient({
        "sub": "220", "email": "different@sifox.com", "email_verified": False,
    }))
    monkeypatch.setattr(gitlab_auth.models, "get_user_by_auth_identity",
                        AsyncMock(return_value={"id": 42, "email": gitlab_auth.config.GITLAB_PILOT_EMAIL}),
                        raising=False)
    monkeypatch.setattr(gitlab_auth.models, "create_session",
                        AsyncMock(return_value="new-session"), raising=False)
    req = _request(session={"gitlab_pilot": {"purpose": "login"}})
    asyncio.run(gitlab_auth.gitlab_callback(req))
    assert req.session["gitlab_email_check"] == {
        "user_id": 42, "present": True, "verification": "unverified",
        "matches_account": False,
    }
    assert "different@sifox.com" not in str(req.session)


def test_staging_pinned_user_id_survives_username_change(monkeypatch):
    monkeypatch.setattr(gitlab_auth.config, "APP_ENV", "staging")
    monkeypatch.setattr(gitlab_auth.config, "GITLAB_PILOT_SUB", "220")
    monkeypatch.setattr(gitlab_auth, "_client", lambda: FakeClient({
        "sub": "220", "preferred_username": "renamed.profile",
    }))
    monkeypatch.setattr(gitlab_auth.models, "get_user_by_auth_identity",
                        AsyncMock(return_value=None), raising=False)
    enroll = AsyncMock(return_value={"id": 42, "email": gitlab_auth.config.GITLAB_PILOT_EMAIL})
    monkeypatch.setattr(gitlab_auth.models, "get_or_create_gitlab_pilot_user", enroll,
                        raising=False)
    monkeypatch.setattr(gitlab_auth.models, "create_session",
                        AsyncMock(return_value="new-session"), raising=False)

    result = asyncio.run(gitlab_auth.gitlab_callback(
        _request(session={"gitlab_pilot": {"purpose": "login"}})
    ))
    assert result.headers["location"].endswith("/")
    enroll.assert_awaited_once()


def test_staging_direct_login_rejects_wrong_subject_without_enrollment(monkeypatch):
    monkeypatch.setattr(gitlab_auth.config, "APP_ENV", "staging")
    monkeypatch.setattr(gitlab_auth.config, "GITLAB_PILOT_SUB", "220")
    monkeypatch.setattr(gitlab_auth, "_client", lambda: FakeClient({
        "sub": "221", "preferred_username": gitlab_auth.config.GITLAB_PILOT_USERNAME,
    }))
    enroll = AsyncMock()
    monkeypatch.setattr(gitlab_auth.models, "get_or_create_gitlab_pilot_user", enroll, raising=False)

    result = asyncio.run(gitlab_auth.gitlab_callback(
        _request(session={"gitlab_pilot": {"purpose": "login"}})
    ))
    assert "gitlab_pilot_id_mismatch" in result.headers["location"]
    enroll.assert_not_awaited()


def test_staging_direct_login_without_pin_requires_verified_matching_email(monkeypatch):
    monkeypatch.setattr(gitlab_auth.config, "APP_ENV", "staging")
    monkeypatch.setattr(gitlab_auth.config, "GITLAB_PILOT_SUB", "")
    monkeypatch.setattr(gitlab_auth, "_client", lambda: FakeClient({
        "sub": "220", "preferred_username": gitlab_auth.config.GITLAB_PILOT_USERNAME,
        "email": gitlab_auth.config.GITLAB_PILOT_EMAIL, "email_verified": False,
    }))
    monkeypatch.setattr(gitlab_auth.models, "get_user_by_auth_identity", AsyncMock(return_value=None), raising=False)
    enroll = AsyncMock()
    monkeypatch.setattr(gitlab_auth.models, "get_or_create_gitlab_pilot_user", enroll, raising=False)

    result = asyncio.run(gitlab_auth.gitlab_callback(
        _request(session={"gitlab_pilot": {"purpose": "login"}})
    ))
    assert "gitlab_identity_unverified" in result.headers["location"]
    enroll.assert_not_awaited()


def test_staging_status_does_not_offer_google_link_step(monkeypatch):
    monkeypatch.setattr(gitlab_auth.config, "APP_ENV", "staging")
    monkeypatch.setattr(gitlab_auth.config, "GITLAB_PILOT_ENABLED", True)
    monkeypatch.setattr(gitlab_auth.config, "GITLAB_CLIENT_ID", "test-client")
    monkeypatch.setattr(gitlab_auth.config, "GITLAB_CLIENT_SECRET", "test-secret")
    get_session = AsyncMock(return_value=_existing_user())
    monkeypatch.setattr(gitlab_auth.models, "get_session", get_session, raising=False)
    assert asyncio.run(gitlab_auth.gitlab_status("pilot-session")) == {
        "enabled": True, "direct_login": True, "can_link": False, "linked": False,
    }
