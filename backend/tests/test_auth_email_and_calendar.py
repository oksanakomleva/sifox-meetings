"""Identity email and independent Google Calendar consent safeguards."""

import asyncio
from unittest.mock import AsyncMock

from api import auth


def test_google_login_rejects_unverified_email_before_account_lookup(monkeypatch):
    upsert = AsyncMock()
    monkeypatch.setattr(auth.models, "upsert_user", upsert, raising=False)
    result = asyncio.run(auth._process_callback({
        "purpose": "login",
        "user_info": {"sub": "g-1", "email": "person@sifox.com", "email_verified": False},
    }, None))
    assert "email_unverified" in result.headers["location"]
    upsert.assert_not_awaited()


def test_google_login_does_not_accept_conflicting_identity(monkeypatch):
    monkeypatch.setattr(auth.models, "upsert_user", AsyncMock(return_value=None), raising=False)
    session = AsyncMock()
    monkeypatch.setattr(auth.models, "create_session", session, raising=False)
    result = asyncio.run(auth._process_callback({
        "purpose": "login",
        "user_info": {"sub": "g-2", "email": "person@sifox.com", "email_verified": True},
    }, None))
    assert "account_conflict" in result.headers["location"]
    session.assert_not_awaited()


def _calendar_result(email="other@gmail.com", subject="calendar-google-1"):
    return {
        "purpose": "calendar", "existing_user_id": 7,
        "user_info": {"sub": subject, "email": email, "email_verified": True},
        "tokens": {"token": "access", "refresh_token": "refresh", "expiry": None},
    }


def test_calendar_can_use_a_different_verified_google_account(monkeypatch):
    monkeypatch.setattr(auth.config, "APP_ENV", "production")
    monkeypatch.setattr(auth.models, "get_session", AsyncMock(return_value={
        "user_id": 7, "email": "person@sifox.com", "is_active": True,
    }), raising=False)
    monkeypatch.setattr(auth.models, "get_google_token", AsyncMock(return_value=None), raising=False)
    save = AsyncMock()
    monkeypatch.setattr(auth.models, "save_google_token", save, raising=False)
    auto_enable = AsyncMock()
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


def test_calendar_refuses_silent_switch_to_another_account(monkeypatch):
    monkeypatch.setattr(auth.config, "APP_ENV", "production")
    monkeypatch.setattr(auth.models, "get_session", AsyncMock(return_value={
        "user_id": 7, "email": "person@sifox.com", "is_active": True,
    }), raising=False)
    monkeypatch.setattr(auth.models, "get_google_token", AsyncMock(return_value={
        "google_account_id": "already-connected", "google_account_email": "old@gmail.com",
    }), raising=False)
    save = AsyncMock()
    monkeypatch.setattr(auth.models, "save_google_token", save, raising=False)
    result = asyncio.run(auth._process_callback(_calendar_result(), None, "web-session"))
    assert "calendar_error=account_conflict" in result.headers["location"]
    save.assert_not_awaited()


def test_staging_calendar_pilot_rejects_non_test_google_account(monkeypatch):
    monkeypatch.setattr(auth.config, "APP_ENV", "staging")
    monkeypatch.setattr(auth.config, "STAGING_CALENDAR_TEST_EMAIL", "calendar-test@gmail.com")
    monkeypatch.setattr(auth.models, "get_session", AsyncMock(return_value={
        "user_id": 7, "email": auth.config.GITLAB_PILOT_EMAIL, "is_active": True,
    }), raising=False)
    save = AsyncMock()
    monkeypatch.setattr(auth.models, "save_google_token", save, raising=False)
    result = asyncio.run(auth._process_callback(_calendar_result(), None, "web-session"))
    assert "calendar_error=account_not_allowed" in result.headers["location"]
    save.assert_not_awaited()


def test_calendar_rejects_other_web_session_and_unverified_email(monkeypatch):
    monkeypatch.setattr(auth.config, "APP_ENV", "production")
    monkeypatch.setattr(auth.models, "get_session", AsyncMock(return_value={
        "user_id": 8, "email": "other@sifox.com", "is_active": True,
    }), raising=False)
    save = AsyncMock()
    monkeypatch.setattr(auth.models, "save_google_token", save, raising=False)
    result = asyncio.run(auth._process_callback(_calendar_result(), None, "web-session"))
    assert "calendar_error=session_expired" in result.headers["location"]

    auth.models.get_session.return_value = {
        "user_id": 7, "email": "person@sifox.com", "is_active": True,
    }
    unverified = _calendar_result()
    unverified["user_info"]["email_verified"] = False
    result = asyncio.run(auth._process_callback(unverified, None, "web-session"))
    assert "calendar_error=email_unverified" in result.headers["location"]
    save.assert_not_awaited()
