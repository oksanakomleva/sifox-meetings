"""Admin routes: users, calendars, meetings management."""
import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Annotated

from auth.deps import get_admin_user
from database import models

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])

AdminUser = Annotated[dict, Depends(get_admin_user)]


# ── Users ─────────────────────────────────────────────────────────────────────

@router.get("/users")
async def list_users(admin: AdminUser):
    users = await models.list_users()
    return {"users": users}


class SetAdminRequest(BaseModel):
    is_admin: bool


@router.patch("/users/{user_id}/admin")
async def set_admin(user_id: int, req: SetAdminRequest, admin: AdminUser):
    if user_id == admin["user_id"] and not req.is_admin:
        raise HTTPException(400, "Cannot remove your own admin role")
    await models.set_user_admin(user_id, req.is_admin)
    return {"ok": True}


# ── Calendars ─────────────────────────────────────────────────────────────────

@router.get("/calendars")
async def list_calendars(admin: AdminUser):
    cals = await models.get_calendars()
    return {"calendars": cals}


class SetCalendarEnabledRequest(BaseModel):
    record_enabled: bool


@router.patch("/calendars/{calendar_id}")
async def set_calendar_enabled(
    calendar_id: int,
    req: SetCalendarEnabledRequest,
    admin: AdminUser,
):
    await models.set_calendar_record_enabled(calendar_id, req.record_enabled)
    return {"ok": True}


@router.post("/calendars/sync")
async def trigger_sync(admin: AdminUser):
    """Manually trigger calendar sync for all users."""
    from services.calendar_sync import sync_all_users
    import asyncio
    asyncio.create_task(sync_all_users())
    return {"ok": True, "message": "Sync started in background"}


# ── Meetings ──────────────────────────────────────────────────────────────────

@router.get("/meetings")
async def list_all_meetings(admin: AdminUser, limit: int = 100, offset: int = 0):
    meetings = await models.get_all_meetings(limit=limit, offset=offset)
    return {"meetings": meetings}


@router.post("/meetings/{meeting_id}/reanalyze")
async def reanalyze_meeting(meeting_id: str, admin: AdminUser):
    """Re-run analysis on a meeting that already has a transcript."""
    import asyncio
    from services.analyzer import analyze_meeting

    meeting = await models.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(404, "Meeting not found")
    transcript = meeting.get("transcript")
    if not transcript:
        raise HTTPException(400, "No transcript to analyze")

    async def _run():
        try:
            await models.update_meeting_status(meeting_id, "analyzing")
            analysis = await analyze_meeting(transcript)
            await models.save_analysis(
                meeting_id,
                summary=analysis["summary"],
                tags=analysis["tags"],
                topic=analysis["topic"],
                meeting_type=analysis["meeting_type"],
            )
            await models.update_meeting_status(meeting_id, "done")
        except Exception as e:
            logger.exception("Reanalyze failed for %s", meeting_id)
            await models.update_meeting_status(meeting_id, "error", str(e)[:500])

    asyncio.create_task(_run())
    return {"ok": True, "message": "Re-analysis started"}


class GrantAccessRequest(BaseModel):
    user_id: int
    meeting_id: str


@router.post("/grant-access")
async def grant_access(req: GrantAccessRequest, admin: AdminUser):
    await models.grant_meeting_access(req.user_id, req.meeting_id, admin["user_id"])
    return {"ok": True}


@router.delete("/grant-access")
async def revoke_access(req: GrantAccessRequest, admin: AdminUser):
    await models.revoke_meeting_access(req.user_id, req.meeting_id)
    return {"ok": True}
