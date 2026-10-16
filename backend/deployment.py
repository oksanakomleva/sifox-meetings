"""Fail-closed guardrails for the isolated Railway staging environment."""

import os
from urllib.parse import urlsplit


_PRODUCTION_HOST = "sifox-meetings.up.railway.app"
_EXTERNAL_CREDENTIALS = (
    "MM_TOKEN", "MM_RECORDER_TOKEN", "GOOGLE_SERVICE_ACCOUNT_JSON", "ALERT_MM_TOKEN",
)
_EXTERNAL_FEATURES = (
    "ALERTS_ENABLED", "MM_RECORDER_COMMANDS_ENABLED", "MEGAFON_ENABLED",
    "GITLAB_PILOT_ENABLED",
)


def validate_runtime(settings, *, railway_environment: str | None = None,
                     volume_mount: str | None = None) -> None:
    """Reject staging configurations that could touch production resources.

    The private Railway database hostname resolves within the current
    environment.  Public/external database URLs are intentionally forbidden
    in staging because they could point at the production database.
    """
    if railway_environment is None:
        railway_environment = os.getenv("RAILWAY_ENVIRONMENT_NAME")
    if railway_environment == "staging" and settings.APP_ENV != "staging":
        raise RuntimeError("Railway staging must set APP_ENV=staging")
    if settings.APP_ENV != "staging":
        return
    if railway_environment and railway_environment != "staging":
        raise RuntimeError("APP_ENV=staging requires the Railway staging environment")
    base = urlsplit(settings.BASE_URL)
    if base.hostname == _PRODUCTION_HOST:
        raise RuntimeError("Staging BASE_URL must not be the production domain")
    if railway_environment:
        if base.scheme != "https":
            raise RuntimeError("Railway staging BASE_URL must use HTTPS")
        db_host = urlsplit(settings.DATABASE_URL).hostname or ""
        if not db_host.endswith(".railway.internal"):
            raise RuntimeError("Railway staging DATABASE_URL must use its private database")
        if volume_mount is None:
            volume_mount = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
        if volume_mount != settings.AUDIO_DIR:
            raise RuntimeError("Railway staging needs its own volume at AUDIO_DIR")
    enabled = [name for name in _EXTERNAL_CREDENTIALS + _EXTERNAL_FEATURES
               if getattr(settings, name, None)]
    if enabled:
        raise RuntimeError(f"Staging external integrations must be disabled: {', '.join(enabled)}")
