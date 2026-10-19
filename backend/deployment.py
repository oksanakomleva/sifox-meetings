"""Fail-closed guardrails for the isolated Railway staging environment."""

import os
from urllib.parse import urlsplit


_PRODUCTION_HOST = "sifox-meetings.up.railway.app"
_EXTERNAL_CREDENTIALS = (
    "MM_TOKEN", "MM_RECORDER_TOKEN", "GOOGLE_SERVICE_ACCOUNT_JSON", "ALERT_MM_TOKEN",
)
_EXTERNAL_FEATURES = (
    "ALERTS_ENABLED", "MM_RECORDER_COMMANDS_ENABLED", "MEGAFON_ENABLED",
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
        if getattr(settings, "GITLAB_STAGING_ALL_USERS_ENABLED", False):
            raise RuntimeError("Staging GitLab rollout switch cannot run in production")
        return
    if getattr(settings, "GITLAB_ALL_USERS_ENABLED", False):
        raise RuntimeError("Production GitLab rollout switch is forbidden in staging")
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
    invite_sender = (getattr(settings, "STAGING_INVITE_SENDER_EMAIL", "") or "").strip().lower()
    if invite_sender:
        if not getattr(settings, "GITLAB_STAGING_ALL_USERS_ENABLED", False):
            raise RuntimeError("Staging invitation sender needs verified-email GitLab login")
        admins = {email.strip().lower() for email in settings.ADMIN_EMAILS.split(",")}
        if invite_sender not in admins or not invite_sender.endswith("@sifox.com"):
            raise RuntimeError("Staging invitation sender must be a Sifox Google admin")
    if settings.GITLAB_PILOT_ENABLED or getattr(settings, "GITLAB_STAGING_ALL_USERS_ENABLED", False):
        allowed = {
            address.strip().lower()
            for name in ("ADMIN_EMAILS", "STAGING_ALLOWED_EMAILS")
            for address in (getattr(settings, name, "") or "").split(",")
            if address.strip()
        }
        if not settings.GITLAB_CLIENT_ID or not settings.GITLAB_CLIENT_SECRET:
            raise RuntimeError("Staging GitLab needs its own OAuth client credentials")
        if settings.GITLAB_ISSUER.rstrip("/") != "https://gitlab.sifox.ru":
            raise RuntimeError("Staging GitLab must use the Sifox GitLab issuer")
        if settings.GITLAB_PILOT_ENABLED:
            if not settings.GITLAB_PILOT_USERNAME or settings.GITLAB_PILOT_EMAIL.lower() not in allowed:
                raise RuntimeError("Staging GitLab pilot must be limited to an allowed tester")
            pilot_sub = getattr(settings, "GITLAB_PILOT_SUB", "") or ""
            if pilot_sub and not pilot_sub.isdecimal():
                raise RuntimeError("Staging GitLab pilot subject must be a numeric GitLab user ID")
