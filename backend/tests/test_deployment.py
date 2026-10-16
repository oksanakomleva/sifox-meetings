from types import SimpleNamespace
import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from deployment import validate_runtime


def _settings(**overrides):
    values = {
        "APP_ENV": "staging",
        "BASE_URL": "https://sifox-meetings-staging.up.railway.app",
        "DATABASE_URL": "postgresql://test:secret@postgres.railway.internal:5432/railway",
        "AUDIO_DIR": "/audio",
        "MM_TOKEN": None,
        "MM_RECORDER_TOKEN": None,
        "GOOGLE_SERVICE_ACCOUNT_JSON": None,
        "ALERT_MM_TOKEN": None,
        "ALERTS_ENABLED": False,
        "MM_RECORDER_COMMANDS_ENABLED": False,
        "MEGAFON_ENABLED": False,
        "GITLAB_PILOT_ENABLED": False,
        "GITLAB_CLIENT_ID": "",
        "GITLAB_CLIENT_SECRET": "",
        "GITLAB_ISSUER": "https://gitlab.sifox.ru",
        "GITLAB_PILOT_EMAIL": "oksana.komleva@sifox.com",
        "GITLAB_PILOT_USERNAME": "oksana.komleva",
        "ADMIN_EMAILS": "oksana.komleva@sifox.com",
        "STAGING_ALLOWED_EMAILS": "",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_staging_uses_its_own_private_db_and_audio_volume():
    validate_runtime(_settings(), railway_environment="staging", volume_mount="/audio")


@pytest.mark.parametrize("changes, reason", [
    ({"APP_ENV": "production"}, "APP_ENV=staging"),
    ({"BASE_URL": "https://sifox-meetings.up.railway.app"}, "production domain"),
    ({"DATABASE_URL": "postgresql://test:secret@public.example:5432/railway"}, "private database"),
    ({"MM_TOKEN": "copied-token"}, "external integrations"),
    ({"GOOGLE_SERVICE_ACCOUNT_JSON": "copied-key"}, "external integrations"),
    ({"ALERTS_ENABLED": True}, "external integrations"),
])
def test_staging_rejects_unsafe_configuration(changes, reason):
    with pytest.raises(RuntimeError, match=reason):
        validate_runtime(_settings(**changes), railway_environment="staging", volume_mount="/audio")


def test_staging_rejects_missing_volume():
    with pytest.raises(RuntimeError, match="volume"):
        validate_runtime(_settings(), railway_environment="staging", volume_mount="")


def test_staging_flag_cannot_run_in_production_environment():
    with pytest.raises(RuntimeError, match="Railway staging environment"):
        validate_runtime(_settings(), railway_environment="production", volume_mount="/audio")


def test_production_configuration_keeps_existing_behavior():
    validate_runtime(_settings(APP_ENV="production"), railway_environment="production")


def test_staging_allows_gitlab_pilot_for_named_tester_with_credentials():
    validate_runtime(_settings(
        GITLAB_PILOT_ENABLED=True,
        GITLAB_CLIENT_ID="staging-client",
        GITLAB_CLIENT_SECRET="staging-secret",
    ), railway_environment="staging", volume_mount="/audio")


@pytest.mark.parametrize("changes, reason", [
    ({"GITLAB_CLIENT_SECRET": ""}, "OAuth client credentials"),
    ({"GITLAB_ISSUER": "https://other.example"}, "Sifox GitLab issuer"),
    ({"GITLAB_PILOT_EMAIL": "other@sifox.com"}, "allowed tester"),
    ({"GITLAB_PILOT_USERNAME": ""}, "allowed tester"),
])
def test_staging_gitlab_pilot_rejects_broad_or_incomplete_configuration(changes, reason):
    settings = _settings(**{
        "GITLAB_PILOT_ENABLED": True,
        "GITLAB_CLIENT_ID": "staging-client",
        "GITLAB_CLIENT_SECRET": "staging-secret",
        **changes,
    })
    with pytest.raises(RuntimeError, match=reason):
        validate_runtime(settings, railway_environment="staging", volume_mount="/audio")


def test_staging_google_login_rejects_other_domain_user(monkeypatch):
    from api import auth
    monkeypatch.setattr(auth.config, "APP_ENV", "staging")
    monkeypatch.setattr(auth.config, "STAGING_ALLOWED_EMAILS", "")
    upsert = AsyncMock()
    monkeypatch.setattr(auth.models, "upsert_user", upsert, raising=False)
    result = asyncio.run(auth._process_callback({
        "purpose": "login",
        "user_info": {"sub": "google-id", "email": "someone-else@sifox.com"},
    }, None))
    assert "error=domain_not_allowed" in result.headers["location"]
    upsert.assert_not_awaited()


def test_staging_google_login_allows_named_tester(monkeypatch):
    from api import auth
    monkeypatch.setattr(auth.config, "APP_ENV", "staging")
    monkeypatch.setattr(auth.config, "STAGING_ALLOWED_EMAILS", "tester@sifox.com")
    monkeypatch.setattr(auth.models, "upsert_user",
                        AsyncMock(return_value={"id": 20, "is_admin": False}), raising=False)
    monkeypatch.setattr(auth.models, "create_session", AsyncMock(return_value="session"), raising=False)
    result = asyncio.run(auth._process_callback({
        "purpose": "login",
        "user_info": {"sub": "google-id", "email": "tester@sifox.com"},
    }, None))
    assert result.headers["location"].endswith("/")


def test_staging_cannot_connect_real_calendar_or_mail(monkeypatch):
    from api import auth
    monkeypatch.setattr(auth.config, "APP_ENV", "staging")
    for connect in (auth.connect_calendar, auth.connect_calendar_write, auth.connect_gmail_send):
        with pytest.raises(HTTPException) as error:
            asyncio.run(connect(session_token="session"))
        assert error.value.status_code == 403
    with pytest.raises(HTTPException) as error:
        asyncio.run(auth._process_callback({"purpose": "calendar"}, None))
    assert error.value.status_code == 403


def test_staging_cannot_trigger_manual_calendar_sync(monkeypatch):
    from api import admin
    from config import config
    monkeypatch.setattr(config, "APP_ENV", "staging")
    with pytest.raises(HTTPException) as error:
        asyncio.run(admin.trigger_sync({"user_id": 1}))
    assert error.value.status_code == 403
