"""Verified-email production login, invitations and independent calendars."""
import asyncio
from unittest.mock import AsyncMock

from api import auth


def test_invitation_link_offers_provider_choice_in_full_mode(monkeypatch):
    monkeypatch.setattr(auth.config, "GITLAB_ALL_USERS_ENABLED", True)
    monkeypatch.setattr(auth.models, "get_invitation_by_token", AsyncMock(return_value={
        "email": "new@sifox.ru", "accepted_at": None,
    }), raising=False)
    result = asyncio.run(auth.accept_invite("invite-token"))
    assert result.headers["location"].endswith("/login?invite=1")
    assert "invite_token=invite-token" in result.headers["set-cookie"]


def test_google_login_rejects_unverified_email(monkeypatch):
    upsert = AsyncMock()
    monkeypatch.setattr(auth.models, "upsert_user", upsert, raising=False)
    result = asyncio.run(auth._process_callback({
        "purpose": "login",
        "user_info": {"sub": "g-1", "email": "person@sifox.com", "email_verified": False},
    }, None))
    assert "email_unverified" in result.headers["location"]
    upsert.assert_not_awaited()


def test_google_login_rejects_conflicting_identity(monkeypatch):
    monkeypatch.setattr(auth.models, "upsert_user", AsyncMock(return_value=None), raising=False)
    create_session = AsyncMock()
    monkeypatch.setattr(auth.models, "create_session", create_session, raising=False)
    result = asyncio.run(auth._process_callback({
        "purpose": "login",
        "user_info": {"sub": "g-2", "email": "person@sifox.com", "email_verified": True},
    }, None))
    assert "account_conflict" in result.headers["location"]
    create_session.assert_not_awaited()


def _calendar_result(email="other@gmail.com", subject="calendar-google-1"):
    return {
        "purpose": "calendar", "existing_user_id": 7,
        "user_info": {"sub": subject, "email": email, "email_verified": True},
        "tokens": {"token": "access", "refresh_token": "refresh", "expiry": None},
    }


def test_calendar_can_use_a_different_google_account_without_auto_recording(monkeypatch):
    monkeypatch.setattr(auth.models, "get_session", AsyncMock(return_value={
        "user_id": 7, "email": "person@sifox.ru", "is_active": True,
    }), raising=False)
    monkeypatch.setattr(auth.models, "get_google_token", AsyncMock(return_value=None), raising=False)
    save = AsyncMock()
    auto_enable = AsyncMock()
    monkeypatch.setattr(auth.models, "save_google_token", save, raising=False)
    monkeypatch.setattr(auth.models, "auto_enable_primary_calendar", auto_enable, raising=False)
    from services import calendar_sync
    monkeypatch.setattr(calendar_sync, "sync_user_calendars", AsyncMock())
    monkeypatch.setattr(calendar_sync, "sync_user_events", AsyncMock())
    result = asyncio.run(auth._process_callback(_calendar_result(), None, "web-session"))
    assert "calendar_connected=1&calendar_requires_selection=1" in result.headers["location"]
    auto_enable.assert_not_awaited()
    assert save.await_args.kwargs == {
        "account_id": "calendar-google-1", "account_email": "other@gmail.com",
    }


def test_calendar_refuses_silent_account_switch(monkeypatch):
    monkeypatch.setattr(auth.models, "get_session", AsyncMock(return_value={
        "user_id": 7, "email": "person@sifox.ru", "is_active": True,
    }), raising=False)
    monkeypatch.setattr(auth.models, "get_google_token", AsyncMock(return_value={
        "google_account_id": "already-connected", "google_account_email": "old@gmail.com",
    }), raising=False)
    save = AsyncMock()
    monkeypatch.setattr(auth.models, "save_google_token", save, raising=False)
    result = asyncio.run(auth._process_callback(_calendar_result(), None, "web-session"))
    assert "calendar_error=account_conflict" in result.headers["location"]
    save.assert_not_awaited()
