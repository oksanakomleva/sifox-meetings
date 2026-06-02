"""Auth routes: login, callback, logout, me, calendar connect."""
import logging
from fastapi import APIRouter, HTTPException, Response, Cookie, Depends
from fastapi.responses import RedirectResponse
from typing import Annotated

from auth import google_oauth
from auth.google_oauth import OAuthError
from auth.deps import get_current_user
from config import config
from database import models

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/login")
async def login():
    """Redirect to Google OAuth."""
    url = google_oauth.get_login_url()
    return RedirectResponse(url)


@router.get("/invite/{token}")
async def accept_invite(token: str):
    """
    User clicks invitation link → validates token, sets cookie, redirects to Google login.
    After OAuth the callback reads the cookie and bypasses the domain check for invited emails.
    """
    invitation = await models.get_invitation_by_token(token)
    if not invitation:
        return RedirectResponse(f"{config.BASE_URL}/login?error=invite_invalid")
    if invitation.get("accepted_at"):
        return RedirectResponse(f"{config.BASE_URL}/login?error=invite_used")

    # Set short-lived invite_token cookie, then redirect to Google OAuth
    login_url = google_oauth.get_login_url()
    redirect = RedirectResponse(login_url)
    redirect.set_cookie(
        key="invite_token",
        value=token,
        httponly=True,
        samesite="lax",
        secure=config.BASE_URL.startswith("https"),
        max_age=600,   # 10 min — enough to complete OAuth flow
        path="/",
    )
    return redirect


@router.get("/callback")
async def callback(
    code: str,
    state: str,
    response: Response,
    invite_token: Annotated[str | None, Cookie(alias="invite_token")] = None,
):
    """Google OAuth callback — handles both login and calendar flows."""
    try:
        result = await google_oauth.handle_callback(code, state)
    except OAuthError as e:
        logger.error("OAuth callback failed: %s", e)
        return RedirectResponse(f"{config.BASE_URL}/login?error=oauth_failed")
    except Exception as e:
        logger.exception("Unexpected error in OAuth callback: %s", e)
        return RedirectResponse(f"{config.BASE_URL}/login?error=oauth_failed")

    if not result:
        return RedirectResponse(f"{config.BASE_URL}/login?error=state_expired")

    try:
        return await _process_callback(result, invite_token)
    except Exception as e:
        logger.exception("Error processing OAuth callback (purpose=%s, email=%s): %s",
                         result.get("purpose"), result.get("user_info", {}).get("email"), e)
        return RedirectResponse(f"{config.BASE_URL}/login?error=oauth_failed")


async def _process_callback(result: dict, invite_token: str | None):
    """Inner callback logic, separated for clean error handling."""
    user_info = result["user_info"]
    email: str = user_info.get("email", "").lower()
    domain = email.split("@")[-1] if "@" in email else ""

    if result["purpose"] == "login":
        # ── Login flow ────────────────────────────────────────────────────
        allowed = domain == config.ALLOWED_DOMAIN

        # Check invitation bypass for users outside the allowed domain
        invitation = None
        if not allowed and invite_token:
            invitation = await models.get_invitation_by_token(invite_token)
            if invitation and invitation["email"] == email and not invitation.get("accepted_at"):
                allowed = True

        if not allowed:
            return RedirectResponse(
                f"{config.BASE_URL}/login?error=domain_not_allowed"
            )

        is_admin_default = email in config.admin_email_list
        user = await models.upsert_user(
            google_id=user_info["sub"],
            email=email,
            name=user_info.get("name", ""),
            avatar_url=user_info.get("picture"),
            is_admin_default=is_admin_default,
        )

        # Force admin if in admin list
        if is_admin_default and not user.get("is_admin"):
            await models.set_user_admin(user["id"], True)

        # Mark invitation as accepted
        if invitation:
            await models.accept_invitation(invite_token)

        token_session = await models.create_session(user["id"], config.SESSION_TTL_DAYS)

        redirect = RedirectResponse(f"{config.BASE_URL}/")
        redirect.set_cookie(
            key="session",
            value=token_session,
            httponly=True,
            samesite="lax",
            secure=config.BASE_URL.startswith("https"),
            max_age=config.SESSION_TTL_DAYS * 86400,
            path="/",
        )
        # Clear invite cookie after use
        redirect.delete_cookie("invite_token", path="/")
        return redirect

    elif result["purpose"] == "calendar":
        # ── Calendar connect flow (read-only) ─────────────────────────────
        user_id = result["existing_user_id"]
        tokens = result["tokens"]
        await models.save_google_token(
            user_id,
            tokens["token"],
            tokens.get("refresh_token"),
            tokens.get("expiry"),
        )
        # Sync calendars list immediately so we can auto-enable
        from services.calendar_sync import sync_user_calendars, sync_user_events
        try:
            await sync_user_calendars(user_id)
            # Auto-enable primary calendar so recording starts right away
            await models.auto_enable_primary_calendar(user_id)
            # Kick off first events sync in background
            import asyncio
            asyncio.create_task(sync_user_events(user_id))
        except Exception as e:
            logger.warning("Calendar sync after connect failed: %s", e)

        # Redirect to dashboard with success flag (works for both admin and regular users)
        return RedirectResponse(f"{config.BASE_URL}/?calendar_connected=1")

    elif result["purpose"] == "calendar_write":
        # ── Calendar write connect (E2E admin only) ───────────────────────
        user_id = result["existing_user_id"]
        tokens = result["tokens"]
        await models.save_google_write_token(
            user_id,
            tokens["token"],
            tokens.get("refresh_token"),
            tokens.get("expiry"),
        )
        return RedirectResponse(f"{config.BASE_URL}/admin/calendars?write_connected=1")

    raise HTTPException(400, "Unknown OAuth purpose")


@router.post("/logout")
async def logout(
    response: Response,
    session_token: Annotated[str | None, Cookie(alias="session")] = None,
):
    if session_token:
        await models.delete_session(session_token)
    response.delete_cookie("session", path="/")
    return {"ok": True}


@router.get("/me")
async def me(user: Annotated[dict, Depends(get_current_user)]):
    return {
        "id": user["user_id"],
        "email": user["email"],
        "name": user["name"],
        "avatar_url": user["avatar_url"],
        "is_admin": user["is_admin"],
        "is_preview": bool(user.get("is_preview")),
    }


@router.get("/preview/{token}")
async def preview_session(token: str):
    """
    Admin-generated preview link — sets a session cookie that impersonates a
    real user and redirects to the homepage. Open in incognito to see the UI as
    that user. Only sessions flagged is_preview can be activated this way, so a
    leaked real login token can't be turned into a session via this endpoint.
    """
    session = await models.get_session(token)
    if not session or not session.get("is_preview"):
        raise HTTPException(400, "Invalid or expired preview link")

    # Set a SEPARATE `preview` cookie — the admin's real `session` cookie is left
    # intact, so exiting preview just clears this cookie and restores the admin.
    redirect = RedirectResponse(f"{config.BASE_URL}/")
    redirect.set_cookie(
        key="preview",
        value=token,
        httponly=True,
        samesite="lax",
        secure=config.BASE_URL.startswith("https"),
        max_age=3600,
        path="/",
    )
    return redirect


@router.get("/exit-preview")
async def exit_preview(
    session_token: Annotated[str | None, Cookie(alias="session")] = None,
    preview_token: Annotated[str | None, Cookie(alias="preview")] = None,
):
    """Leave preview mode.

    New flow: the preview lives in a separate `preview` cookie — clear it and the
    admin's real `session` cookie restores them with no re-login.

    Legacy/edge: older builds put the preview session into the main `session`
    cookie itself (overwriting the admin session). If we detect that, clear it too
    so the user isn't stuck — they'll land on login and sign back in as themselves.
    """
    redirect = RedirectResponse(f"{config.BASE_URL}/")
    if preview_token:
        await models.delete_session(preview_token)
        redirect.delete_cookie("preview", path="/")
    if session_token:
        s = await models.get_session(session_token)
        if s and s.get("is_preview"):
            await models.delete_session(session_token)
            redirect.delete_cookie("session", path="/")
    return redirect


@router.get("/connect-calendar")
async def connect_calendar(
    session_token: Annotated[str | None, Cookie(alias="session")] = None,
):
    """Start Google Calendar OAuth for current user (read-only)."""
    if not session_token:
        raise HTTPException(401, "Not authenticated")
    session = await models.get_session(session_token)
    if not session:
        raise HTTPException(401, "Session expired")
    url = google_oauth.get_calendar_url(session["user_id"])
    return RedirectResponse(url)


@router.get("/connect-calendar-write")
async def connect_calendar_write(
    session_token: Annotated[str | None, Cookie(alias="session")] = None,
):
    """Start Google Calendar OAuth with write scope (admin only, for E2E tests)."""
    if not session_token:
        raise HTTPException(401, "Not authenticated")
    session = await models.get_session(session_token)
    if not session:
        raise HTTPException(401, "Session expired")
    if not session.get("is_admin"):
        raise HTTPException(403, "Admin only")
    url = google_oauth.get_calendar_write_url(session["user_id"])
    return RedirectResponse(url)
