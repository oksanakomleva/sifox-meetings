"""Admin routes: users, calendars, meetings management."""
import asyncio
import logging
import os
import sys
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Annotated

from auth.deps import get_admin_user, get_test_or_admin_user
from database import models

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])

TestOrAdminUser = Annotated[dict, Depends(get_test_or_admin_user)]

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


# ── E2E Testing ───────────────────────────────────────────────────────────────

@router.post("/test/start-e2e")
async def start_e2e_test(caller: TestOrAdminUser):
    """
    Start a fully automated E2E test:
    1. Creates a Google Calendar event (now + 3 min) with TEST_MEETING_URL
    2. Triggers calendar sync so the bot picks it up
    3. Schedules test_speaker.py to join the meeting and stream test_audio.wav

    Auth: session cookie (admin) OR X-Test-Api-Key header (automated tests).
    Requires TEST_MEETING_URL env var (permanent Telemost room link).
    """
    from config import config
    from services.calendar_sync import _create_test_event_sync, sync_all_users

    meeting_url = config.TEST_MEETING_URL
    if not meeting_url:
        raise HTTPException(400, "TEST_MEETING_URL not set in Railway Variables")

    # When called via test API key, user_id=0 — find first admin with a google token
    user_id = caller["user_id"]
    if user_id == 0:
        all_users = await models.get_all_users_with_tokens()
        admin_users = [u for u in all_users if u.get("is_admin")]
        if not admin_users:
            raise HTTPException(400, "No admin users with Google Calendar connected")
        user_id = admin_users[0]["id"]

    token_row = await models.get_google_token(user_id)
    if not token_row:
        raise HTTPException(400, "Admin must have Google Calendar connected (/api/auth/google)")

    user_cals = await models.get_calendars(owner_user_id=user_id)
    enabled_cals = [c for c in user_cals if c.get("record_enabled")]
    primary_cal = next((c for c in user_cals if c.get("is_primary")), None)
    cal = enabled_cals[0] if enabled_cals else primary_cal
    if not cal:
        raise HTTPException(400, "No calendars found — connect Google Calendar first")

    loop = asyncio.get_running_loop()
    event_id = await loop.run_in_executor(
        None,
        _create_test_event_sync,
        token_row,
        cal["google_calendar_id"],
        meeting_url,
        "[E2E Test] Тестовая встреча",
        3,   # start_minutes_from_now
        10,  # duration_minutes
    )

    # Immediately sync so bot sees the new event
    asyncio.create_task(sync_all_users())

    # Schedule test speaker: join 3 min after event start (= 6 min from now),
    # so the recorder bot has time to arrive first
    asyncio.create_task(_launch_speaker_after_delay(meeting_url, delay_seconds=210, duration_minutes=5))

    return {
        "status": "started",
        "meeting_url": meeting_url,
        "calendar_event_id": event_id,
        "calendar_id": cal["google_calendar_id"],
        "message": "Calendar event created for +3 min, sync triggered, Test Speaker will join at +6 min",
    }


async def _launch_speaker_after_delay(meeting_url: str, delay_seconds: int, duration_minutes: int) -> None:
    """Wait, then launch test_speaker.py as a subprocess on Railway."""
    await asyncio.sleep(delay_seconds)

    speaker_script = os.path.join(
        os.path.dirname(__file__), "..", "tests", "e2e", "test_speaker.py"
    )
    speaker_script = os.path.abspath(speaker_script)

    if not os.path.exists(speaker_script):
        logger.error("test_speaker.py not found at %s", speaker_script)
        return

    logger.info("Launching Test Speaker: %s --url %s --duration %d", speaker_script, meeting_url, duration_minutes)
    proc = await asyncio.create_subprocess_exec(
        sys.executable, speaker_script,
        "--url", meeting_url,
        "--duration", str(duration_minutes),
    )
    returncode = await proc.wait()
    logger.info("Test Speaker finished with exit code %d", returncode)


@router.delete("/test/calendar-event/{event_id}")
async def delete_test_calendar_event(event_id: str, calendar_id: str, caller: TestOrAdminUser):
    """Delete the test calendar event created by start_e2e_test (cleanup)."""
    from services.calendar_sync import _delete_event_sync

    user_id = caller["user_id"]
    if user_id == 0:
        all_users = await models.get_all_users_with_tokens()
        admin_users = [u for u in all_users if u.get("is_admin")]
        if not admin_users:
            raise HTTPException(400, "No admin users with Google token")
        user_id = admin_users[0]["id"]

    token_row = await models.get_google_token(user_id)
    if not token_row:
        raise HTTPException(400, "No Google token for admin")

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _delete_event_sync, token_row, calendar_id, event_id)
    return {"ok": True, "deleted_event_id": event_id}
