"""One-account GitLab OIDC pilot with isolated direct sign-in in staging."""
import hashlib
import hmac
import logging
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Cookie, HTTPException, Request
from fastapi.responses import RedirectResponse

from config import config
from database import models

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth/gitlab", tags=["auth"])


def _require_pilot() -> None:
    if not (
        config.GITLAB_PILOT_ENABLED
        and config.GITLAB_CLIENT_ID
        and config.GITLAB_CLIENT_SECRET
    ):
        raise HTTPException(404, "GitLab pilot is not enabled")


@lru_cache(maxsize=1)
def _client():
    _require_pilot()
    from authlib.integrations.starlette_client import OAuth

    oauth = OAuth()
    oauth.register(
        name="gitlab",
        client_id=config.GITLAB_CLIENT_ID,
        client_secret=config.GITLAB_CLIENT_SECRET,
        server_metadata_url=(
            f"{config.GITLAB_ISSUER.rstrip('/')}/.well-known/openid-configuration"
        ),
        client_kwargs={
            "scope": "openid profile email",
            "code_challenge_method": "S256",
        },
    )
    return oauth.create_client("gitlab")


def _fingerprint(session_token: str) -> str:
    return hmac.new(
        config.SECRET_KEY.encode("utf-8"),
        session_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _error(code: str, *, linking: bool = False) -> RedirectResponse:
    if linking:
        return RedirectResponse(f"{config.BASE_URL}/?gitlab_error={code}")
    return RedirectResponse(f"{config.BASE_URL}/login?error={code}")


@router.get("/status")
async def gitlab_status(
    session_token: Annotated[str | None, Cookie(alias="session")] = None,
):
    """Expose pilot availability without sending users to a disabled OAuth route."""
    enabled = bool(
        config.GITLAB_PILOT_ENABLED
        and config.GITLAB_CLIENT_ID
        and config.GITLAB_CLIENT_SECRET
    )
    direct_login = enabled and config.APP_ENV == "staging"
    if not enabled or not session_token:
        return {"enabled": enabled, "direct_login": direct_login,
                "can_link": False, "linked": False}
    session = await models.get_session(session_token)
    can_link = bool(
        not direct_login
        and
        session
        and session.get("is_active", True)
        and not session.get("is_preview")
        and session.get("google_id")
        and session["email"].lower() == config.GITLAB_PILOT_EMAIL.lower()
    )
    linked = bool(
        can_link
        and await models.has_auth_identity(session["user_id"], "gitlab")
    )
    return {"enabled": enabled, "direct_login": direct_login,
            "can_link": can_link and not linked, "linked": linked}


@router.get("/link")
async def link_gitlab(
    request: Request,
    session_token: Annotated[str | None, Cookie(alias="session")] = None,
):
    """Start linking while already signed in with the pilot's Google account."""
    client = _client()
    if request.cookies.get("preview"):
        raise HTTPException(403, "Exit user preview before linking GitLab")
    session = await models.get_session(session_token) if session_token else None
    if not session or not session.get("is_active", True) or session.get("is_preview"):
        raise HTTPException(401, "Sign in with Google first")
    if (
        session["email"].lower() != config.GITLAB_PILOT_EMAIL.lower()
        or not session.get("google_id")
    ):
        raise HTTPException(403, "GitLab pilot is restricted to one account")

    request.session["gitlab_pilot"] = {
        "purpose": "link",
        "user_id": session["user_id"],
        "fingerprint": _fingerprint(session_token),
    }
    return await client.authorize_redirect(
        request, f"{config.BASE_URL}/api/auth/gitlab/callback"
    )


@router.get("/login")
async def login_gitlab(request: Request):
    """Start GitLab OIDC login; staging can enroll its one approved tester."""
    client = _client()
    request.session["gitlab_pilot"] = {"purpose": "login"}
    return await client.authorize_redirect(
        request, f"{config.BASE_URL}/api/auth/gitlab/callback"
    )


async def _finish_login(request: Request, user: dict) -> RedirectResponse:
    session_token = await models.create_session(user["id"], config.SESSION_TTL_DAYS)
    request.session.clear()
    response = RedirectResponse(f"{config.BASE_URL}/")
    response.set_cookie(
        key="session", value=session_token, httponly=True, samesite="lax",
        secure=config.BASE_URL.startswith("https"),
        max_age=config.SESSION_TTL_DAYS * 86400, path="/",
    )
    return response


async def _staging_direct_login(request: Request, userinfo: dict, subject: str) -> RedirectResponse:
    """Admit only the configured GitLab person, with no Google prerequisite."""
    pinned_sub = config.GITLAB_PILOT_SUB.strip()
    if pinned_sub:
        # The verified OIDC subject is stable; usernames can be renamed. Once
        # an operator pins the exact user ID, do not reject the same person
        # merely because their displayed username differs.
        if subject != pinned_sub:
            return _error("gitlab_pilot_id_mismatch")
    elif userinfo.get("preferred_username") != config.GITLAB_PILOT_USERNAME:
        return _error("gitlab_pilot_mismatch")

    email = config.GITLAB_PILOT_EMAIL.strip().lower()
    user = await models.get_user_by_auth_identity("gitlab", subject)
    if user:
        if not user.get("is_active", True) or user["email"].lower() != email:
            return _error("gitlab_pilot_access_denied")
    else:
        # GitLab omits email claims when the user's public email is hidden.
        # In that case, require the stable, operator-pinned OIDC subject. If
        # no subject is pinned, only a verified exact email can enroll.
        claim_email = (userinfo.get("email") or "").strip().lower()
        if not pinned_sub and not (
            claim_email == email and userinfo.get("email_verified") is True
        ):
            return _error("gitlab_identity_unverified")
        user = await models.get_or_create_gitlab_pilot_user(
            subject=subject, email=email,
            name=userinfo.get("name") or config.GITLAB_PILOT_USERNAME,
            avatar_url=userinfo.get("picture"),
            is_admin_default=email in config.admin_email_list,
        )
        if not user:
            return _error("gitlab_pilot_access_denied")
    return await _finish_login(request, user)


@router.get("/callback")
async def gitlab_callback(request: Request):
    client = _client()
    pilot = request.session.pop("gitlab_pilot", None)
    if not pilot:
        return _error("gitlab_state_expired")
    try:
        token = await client.authorize_access_token(request)
        # Authlib validates the ID token against GitLab's OIDC metadata/JWKS,
        # including the nonce. Never trust an unverified userinfo response.
        userinfo = token.get("userinfo") or {}
        subject = userinfo.get("sub")
        if not subject:
            raise ValueError("GitLab ID token has no subject")
    except Exception:
        logger.exception("GitLab OIDC pilot callback failed")
        request.session.clear()
        return _error("gitlab_failed", linking=pilot.get("purpose") == "link")

    if pilot.get("purpose") == "link":
        if request.cookies.get("preview"):
            return _error("gitlab_link_failed", linking=True)
        session_token = request.cookies.get("session")
        if not session_token or not hmac.compare_digest(
            pilot.get("fingerprint", ""), _fingerprint(session_token)
        ):
            return _error("gitlab_link_failed", linking=True)
        session = await models.get_session(session_token)
        if not session or not session.get("is_active", True) or session.get("is_preview"):
            return _error("gitlab_link_failed", linking=True)
        if (
            session["user_id"] != pilot.get("user_id")
            or session["email"].lower() != config.GITLAB_PILOT_EMAIL.lower()
            or not session.get("google_id")
            or userinfo.get("preferred_username") != config.GITLAB_PILOT_USERNAME
        ):
            return _error("gitlab_link_failed", linking=True)
        if not await models.link_auth_identity(session["user_id"], "gitlab", subject):
            return _error("gitlab_link_conflict", linking=True)
        request.session.clear()
        return RedirectResponse(f"{config.BASE_URL}/?gitlab_linked=1")

    if pilot.get("purpose") != "login":
        return _error("gitlab_failed")
    if config.APP_ENV == "staging":
        return await _staging_direct_login(request, userinfo, subject)

    user = await models.get_user_by_auth_identity("gitlab", subject)
    if (
        not user
        or not user.get("is_active", True)
        or user["email"].lower() != config.GITLAB_PILOT_EMAIL.lower()
    ):
        return _error("gitlab_not_linked")

    return await _finish_login(request, user)
