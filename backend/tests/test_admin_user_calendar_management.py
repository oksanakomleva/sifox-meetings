"""Admin calendar selection and mailed invitation regressions."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api import admin, auth
from services import calendar_sync


ADMIN = {"user_id": 7, "email": "admin@sifox.com", "is_admin": True}


def _invite(email="new@example.com"):
    return {
        "id": 3,
        "email": email,
        "token": "secret-invitation-token",
        "expires_at": datetime.now(timezone.utc) + timedelta(days=7),
        "created_at": datetime.now(timezone.utc),
    }


def test_calendar_remove_and_add_change_only_tracking(monkeypatch):
    setter = AsyncMock(return_value=True)
    monkeypatch.setattr(admin.models, "set_calendar_managed", setter, raising=False)
    asyncio.run(admin.remove_calendar(12, ADMIN))
    asyncio.run(admin.add_calendar(12, ADMIN))
    assert setter.await_args_list[0].args == (12, False)
    assert setter.await_args_list[1].args == (12, True)


def test_invitation_needs_sender_before_creating_row(monkeypatch):
    monkeypatch.setattr(admin.models, "get_user_by_email", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(admin.models, "get_gmail_send_token", AsyncMock(return_value=None), raising=False)
    create = AsyncMock()
    monkeypatch.setattr(admin.models, "create_invitation", create, raising=False)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin.create_invitation(admin.InviteRequest(email="new@example.com"), ADMIN))
    assert exc.value.status_code == 409
    create.assert_not_awaited()


def test_ru_invitation_does_not_promise_unavailable_gitlab_login(monkeypatch):
    from config import config
    monkeypatch.setattr(config, "APP_ENV", "production")
    monkeypatch.setattr(config, "GITLAB_ALL_USERS_ENABLED", False)
    create = AsyncMock()
    monkeypatch.setattr(admin.models, "create_invitation", create, raising=False)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin.create_invitation(admin.InviteRequest(email="new@sifox.ru"), ADMIN))
    assert exc.value.status_code == 409
    create.assert_not_awaited()


def test_invitation_marked_sent_only_after_gmail_success(monkeypatch):
    monkeypatch.setattr(admin.models, "get_user_by_email", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(admin.models, "get_gmail_send_token", AsyncMock(return_value={"access_token": "encrypted"}), raising=False)
    monkeypatch.setattr(admin.models, "get_pending_invitation", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(admin.models, "create_invitation", AsyncMock(return_value=_invite()), raising=False)
    mark = AsyncMock()
    monkeypatch.setattr(admin.models, "mark_invitation_delivery", mark, raising=False)
    from services import gmail_send
    send = AsyncMock(return_value={"ok": True, "message_id": "msg-1"})
    monkeypatch.setattr(gmail_send, "send_invitation_email", send)
    result = asyncio.run(admin.create_invitation(admin.InviteRequest(email="New@Example.com"), ADMIN))
    assert result["invitation"]["email"] == "new@example.com"
    assert "secret-invitation-token" in send.await_args.args[-1]
    mark.assert_awaited_once_with(3)


def test_failed_delivery_is_not_reported_as_sent(monkeypatch):
    monkeypatch.setattr(admin.models, "get_user_by_email", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(admin.models, "get_gmail_send_token", AsyncMock(return_value={"access_token": "encrypted"}), raising=False)
    monkeypatch.setattr(admin.models, "get_pending_invitation", AsyncMock(return_value=_invite()), raising=False)
    mark = AsyncMock()
    monkeypatch.setattr(admin.models, "mark_invitation_delivery", mark, raising=False)
    from services import gmail_send
    monkeypatch.setattr(gmail_send, "send_invitation_email", AsyncMock(return_value={"ok": False, "error": "provider failed"}))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(admin.create_invitation(admin.InviteRequest(email="new@example.com"), ADMIN))
    assert exc.value.status_code == 502
    mark.assert_awaited_once_with(3, "Не удалось отправить письмо")


def test_invitation_email_contains_clickable_safe_link(monkeypatch):
    from services import gmail_send
    sender = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(gmail_send, "_send_email", sender)
    url = 'https://example.com/api/auth/invite/a"b'
    asyncio.run(gmail_send.send_invitation_email(7, {}, "admin@sifox.com", "new@example.com", url))
    html_body, text_body = sender.await_args.args[-2:]
    assert 'href="https://example.com/api/auth/invite/a&quot;b"' in html_body
    assert url in text_body


def test_staging_invitation_to_ru_uses_gitlab_and_allowed_sender(monkeypatch):
    from config import config
    monkeypatch.setattr(config, "APP_ENV", "staging")
    monkeypatch.setattr(config, "GITLAB_STAGING_ALL_USERS_ENABLED", True)
    monkeypatch.setattr(config, "STAGING_INVITE_SENDER_EMAIL", "admin@sifox.com")
    monkeypatch.setattr(admin.models, "get_user_by_email", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(admin.models, "get_gmail_send_token",
                        AsyncMock(return_value={"access_token": "encrypted"}), raising=False)
    monkeypatch.setattr(admin.models, "get_pending_invitation", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(admin.models, "create_invitation",
                        AsyncMock(return_value=_invite("new@sifox.ru")), raising=False)
    monkeypatch.setattr(admin.models, "mark_invitation_delivery", AsyncMock(), raising=False)
    from services import gmail_send
    send = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(gmail_send, "send_invitation_email", send)
    result = asyncio.run(admin.create_invitation(admin.InviteRequest(email="new@sifox.ru"), ADMIN))
    assert result["ok"] is True
    assert send.await_args.kwargs["gitlab_available"] is True
    assert send.await_args.args[3] == "new@sifox.ru"


def test_staging_invitation_rejects_other_sender_and_external_email(monkeypatch):
    from config import config
    monkeypatch.setattr(config, "APP_ENV", "staging")
    monkeypatch.setattr(config, "STAGING_INVITE_SENDER_EMAIL", "admin@sifox.com")
    for recipient, sender in (("new@sifox.ru", "other@sifox.com"),
                              ("new@example.com", "admin@sifox.com")):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(admin.create_invitation(admin.InviteRequest(email=recipient),
                                                {**ADMIN, "email": sender}))
        assert exc.value.status_code == 403


def test_gitlab_invitation_mail_explains_the_right_login(monkeypatch):
    from services import gmail_send
    sender = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(gmail_send, "_send_email", sender)
    asyncio.run(gmail_send.send_invitation_email(
        7, {}, "admin@sifox.com", "new@sifox.ru", "https://stage.example/invite",
        gitlab_available=True,
    ))
    html_body, text_body = sender.await_args.args[-2:]
    assert "Войдите через GitLab" in html_body
    assert "Войдите через GitLab" in text_body


def test_in_domain_invitation_is_accepted_after_google_login(monkeypatch):
    invite = {**_invite("new@sifox.com"), "accepted_at": None}
    monkeypatch.setattr(auth.models, "get_invitation_by_token", AsyncMock(return_value=invite), raising=False)
    monkeypatch.setattr(auth.models, "upsert_user", AsyncMock(return_value={"id": 18, "is_admin": False}), raising=False)
    accept = AsyncMock()
    monkeypatch.setattr(auth.models, "accept_invitation", accept, raising=False)
    monkeypatch.setattr(auth.models, "create_session", AsyncMock(return_value="session"), raising=False)
    result = asyncio.run(auth._process_callback({
        "purpose": "login", "user_info": {"sub": "google-id", "email": "new@sifox.com", "email_verified": True, "name": "New"},
    }, "secret-invitation-token"))
    assert result.status_code == 307
    accept.assert_awaited_once_with("secret-invitation-token")


def test_calendar_removed_during_google_fetch_cannot_recreate_pending_meeting(monkeypatch):
    monkeypatch.setattr(calendar_sync.models, "get_google_token", AsyncMock(return_value={"access_token": "token"}), raising=False)
    monkeypatch.setattr(calendar_sync.models, "get_calendars", AsyncMock(side_effect=[
        [{"google_calendar_id": "cal-1", "record_enabled": True}],
        [],
    ]), raising=False)
    monkeypatch.setattr(calendar_sync, "_fetch_events_sync", lambda *_: ([{"google_id": "event-1"}], []))
    upsert = AsyncMock()
    monkeypatch.setattr(calendar_sync.models, "upsert_meeting", upsert, raising=False)
    asyncio.run(calendar_sync.sync_user_events(7))
    upsert.assert_not_awaited()
