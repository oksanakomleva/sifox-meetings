"""GitLab OIDC sign-in: a pinned pilot or verified-email Sifox accounts."""
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
        (config.GITLAB_PILOT_ENABLED or config.GITLAB_ALL_USERS_ENABLED)
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
    """Expose login mode without sending users to a disabled OAuth route."""
    enabled = bool(
        (config.GITLAB_PILOT_ENABLED or config.GITLAB_ALL_USERS_ENABLED)
        and config.GITLAB_CLIENT_ID
        and config.GITLAB_CLIENT_SECRET
    )
    all_users = enabled and config.GITLAB_ALL_USERS_ENABLED
    if not enabled or not session_token:
        return {"enabled": enabled, "direct_login": all_users,
                "all_users": all_users, "can_link": False, "linked": False}
    session = await models.get_session(session_token)
    can_link = bool(
        not all_users
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
    return {"enabled": enabled, "direct_login": all_users,
            "all_users": all_users, "can_link": can_link and not linked,
            "linked": linked}


@router.get("/link")
async def link_gitlab(
    request: Request,
    session_token: Annotated[str | None, Cookie(alias="session")] = None,
):
    """Start linking while already signed in with the pilot's Google account."""
    client = _client()
    if config.GITLAB_ALL_USERS_ENABLED:
        raise HTTPException(404, "Direct GitLab sign-in does not need manual linking")
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
    """Start GitLab OIDC login with server-side state and PKCE."""
    client = _client()
    request.session["gitlab_pilot"] = {"purpose": "login"}
    return await client.authorize_redirect(
        request, f"{config.BASE_URL}/api/auth/gitlab/callback"
    )


async def _finish_login(
    request: Request, user: dict, *, invite_token: str | None = None,
) -> RedirectResponse:
    session_token = await models.create_session(user["id"], config.SESSION_TTL_DAYS)
    request.session.clear()
    response = RedirectResponse(f"{config.BASE_URL}/")
    response.set_cookie(
        key="session", value=session_token, httponly=True, samesite="lax",
        secure=config.BASE_URL.startswith("https"),
        max_age=config.SESSION_TTL_DAYS * 86400, path="/",
    )
    if invite_token:
        response.delete_cookie("invite_token", path="/")
    return response


async def _verified_email_login(request: Request, userinfo: dict, subject: str) -> RedirectResponse:
    """Register or sign in by exact, verified Sifox email and stable GitLab subject."""
    email = (userinfo.get("email") or "").strip().lower()
    if userinfo.get("email_verified") is not True or not email:
        return _error("gitlab_identity_unverified")
    if (email.count("@") != 1 or not email.split("@", 1)[0]
            or email.rsplit("@", 1)[-1] not in {"sifox.com", "sifox.ru"}):
        return _error("domain_not_allowed")

    invite_token = request.cookies.get("invite_token")
    if invite_token:
        invitation = await models.get_invitation_by_token(invite_token)
        if not invitation:
            return _error("invite_invalid")
        if invitation.get("accepted_at"):
            return _error("invite_used")
        if invitation["email"].lower() != email:
            return _error("invite_email_mismatch")

    user = await models.get_user_by_auth_identity("gitlab", subject)
    if user:
        if not user.get("is_active", True) or user["email"].lower() != email:
            return _error("gitlab_account_conflict")
    else:
        # The exact email reuses an existing Google user's meetings and rights.
        # .com and .ru are never merged by similar names.
        user = await models.get_or_create_gitlab_user(
            subject=subject, email=email,
            name=userinfo.get("name") or userinfo.get("preferred_username") or email,
            avatar_url=userinfo.get("picture"),
            is_admin_default=email in config.admin_email_list,
        )
        if not user:
            return _error("gitlab_account_conflict")
    if invite_token:
        await models.accept_invitation(invite_token)
    return await _finish_login(request, user, invite_token=invite_token)


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
    if config.GITLAB_ALL_USERS_ENABLED:
        return await _verified_email_login(request, userinfo, subject)
    user = await models.get_user_by_auth_identity("gitlab", subject)
    if (
        not user
        or not user.get("is_active", True)
        or user["email"].lower() != config.GITLAB_PILOT_EMAIL.lower()
    ):
        return _error("gitlab_not_linked")

    return await _finish_login(request, user)
