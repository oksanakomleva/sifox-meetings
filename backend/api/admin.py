"""Admin routes: users, calendars, meetings management."""
import asyncio
import logging
import os
import sys
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Annotated

from auth.deps import get_test_or_admin_user
from database import models

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"])

# Accepts both session cookie (browser) and X-Test-Api-Key header (automated E2E)
AdminUser = Annotated[dict, Depends(get_test_or_admin_user)]
TestOrAdminUser = AdminUser  # alias kept for clarity in test endpoints


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


@router.post("/meetings/{meeting_id}/force-error")
async def force_error_meeting(meeting_id: str, admin: AdminUser):
    """Force a stuck recording/transcribing meeting into error state."""
    from database.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE meetings
               SET status = 'error',
                   error_message = 'Принудительно сброшено администратором',
                   updated_at = NOW()
             WHERE id = $1
            RETURNING id, status
            """,
            meeting_id,
        )
    if not row:
        raise HTTPException(404, "Meeting not found")
    return {"ok": True, "meeting_id": meeting_id, "status": row["status"]}


@router.post("/meetings/{meeting_id}/restart")
async def restart_meeting(meeting_id: str, admin: AdminUser):
    """Reset a done/error meeting back to pending so the recorder picks it up again."""
    from database.connection import get_pool
    from datetime import datetime, timezone

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE meetings
               SET status        = 'pending',
                   start_time    = NOW(),
                   error_message = NULL,
                   updated_at    = NOW()
             WHERE id = $1
            RETURNING id, status, start_time
            """,
            meeting_id,
        )
    if not row:
        raise HTTPException(404, "Meeting not found")
    return {"ok": True, "meeting_id": meeting_id, "status": row["status"], "start_time": str(row["start_time"])}


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


# ── Invitations ───────────────────────────────────────────────────────────────

class InviteRequest(BaseModel):
    email: str


@router.get("/invitations")
async def list_invitations(admin: AdminUser):
    invitations = await models.list_invitations()
    return {"invitations": invitations}


@router.post("/invitations")
async def create_invitation(req: InviteRequest, admin: AdminUser):
    from config import config as app_config
    email = req.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "Некорректный email")
    invitation = await models.create_invitation(email, admin["user_id"])
    invite_url = f"{app_config.BASE_URL}/api/auth/invite/{invitation['token']}"
    return {
        "ok": True,
        "invitation": {
            "id": invitation["id"],
            "email": invitation["email"],
            "token": invitation["token"],
            "expires_at": str(invitation["expires_at"]),
            "created_at": str(invitation["created_at"]),
            "url": invite_url,
        },
    }


@router.delete("/invitations/{invitation_id}")
async def delete_invitation(invitation_id: int, admin: AdminUser):
    await models.delete_invitation(invitation_id)
    return {"ok": True}


# ── Maintenance ───────────────────────────────────────────────────────────────

@router.get("/meetings/{meeting_id}/url")
async def get_meeting_url(meeting_id: str, admin: AdminUser):
    """Return raw meeting_url stored in DB (for debugging URL extraction issues)."""
    from database.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, title, meeting_url, status, error_message FROM meetings WHERE id = $1",
            meeting_id,
        )
    if not row:
        raise HTTPException(404, "Meeting not found")
    return dict(row)


class PatchMeetingUrlRequest(BaseModel):
    meeting_url: str


@router.patch("/meetings/{meeting_id}/url")
async def patch_meeting_url(meeting_id: str, req: PatchMeetingUrlRequest, admin: AdminUser):
    """Fix a corrupted meeting_url directly in DB."""
    from database.connection import get_pool
    url = req.meeting_url.strip()
    if not url.startswith("https://telemost.yandex.ru/"):
        raise HTTPException(400, "Not a valid Telemost URL")
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE meetings SET meeting_url = $1, updated_at = NOW() WHERE id = $2 RETURNING id, meeting_url",
            url, meeting_id,
        )
    if not row:
        raise HTTPException(404, "Meeting not found")
    return {"ok": True, "meeting_url": row["meeting_url"]}


@router.delete("/meetings/errors")
async def delete_error_meetings(admin: AdminUser):
    """One-shot cleanup: delete all meetings with status=error."""
    from database.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM meetings WHERE status = 'error'")
    count = int(result.split()[-1])
    logger.info("Deleted %d error meetings by admin user_id=%s", count, admin["user_id"])
    return {"ok": True, "deleted": count}


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
    try:
        return await _start_e2e_test_impl(caller)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("start_e2e_test unhandled error")
        raise HTTPException(500, f"Internal error: {type(exc).__name__}: {exc}") from exc


async def _start_e2e_test_impl(caller: dict) -> dict:
    from config import config
    from services.calendar_sync import _create_test_event_sync, sync_all_users

    meeting_url = config.TEST_MEETING_URL
    if not meeting_url:
        raise HTTPException(400, "TEST_MEETING_URL not set in Railway Variables")

    # Find admin user with calendar WRITE scope (required for creating test events)
    user_id = caller["user_id"]
    if user_id == 0:
        # Called via TEST_API_KEY — find any admin with write token
        write = await models.get_any_admin_write_token()
        if not write:
            raise HTTPException(
                400,
                "No admin has E2E calendar write access. "
                "Open /api/auth/connect-calendar-write in browser as admin first.",
            )
        user_id, token_row = write
    else:
        token_row = await models.get_google_write_token(user_id)
        if not token_row:
            raise HTTPException(
                400,
                "Your Google token lacks calendar write scope. "
                "Visit /api/auth/connect-calendar-write to authorize.",
            )

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

    # Sync immediately so the bot sees the new event right away
    await sync_all_users()

    # NOTE: Test Speaker is NOT auto-scheduled here anymore.
    # The smoke test polls for status=recording and then calls POST /test/launch-speaker.
    # This avoids long-lived asyncio tasks that get killed on Railway redeploy.

    # Look up the meeting record so we can return its ID to the smoke test
    meeting = await models.get_meeting_by_url(meeting_url)
    meeting_id = str(meeting["id"]) if meeting else None

    return {
        "status": "started",
        "meeting_url": meeting_url,
        "meeting_id": meeting_id,
        "calendar_event_id": event_id,
        "calendar_id": cal["google_calendar_id"],
        "message": "Calendar event created for +3 min, sync triggered, Test Speaker will join at +6 min",
    }


class InjectAudioRequest(BaseModel):
    meeting_id: str   # to derive sink_name = meet_{meeting_id[:8]}


@router.post("/test/inject-audio")
async def inject_test_audio(req: InjectAudioRequest, caller: TestOrAdminUser):
    """
    Play test_audio.wav directly into the PulseAudio sink of a running recording.
    No second Chromium needed — zero extra memory cost.
    The recorder's parec captures it as if it were real meeting audio.
    """
    import shutil
    sink_name = f"meet_{req.meeting_id[:8]}"
    audio_file = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "tests", "e2e", "test_audio.wav")
    )
    if not os.path.exists(audio_file):
        raise HTTPException(400, f"test_audio.wav not found at {audio_file}")

    paplay = shutil.which("paplay")
    if not paplay:
        raise HTTPException(500, "paplay not found — PulseAudio utils not installed")

    # Run paplay in background — returns immediately, audio plays asynchronously
    asyncio.create_task(_play_audio_to_sink(paplay, audio_file, sink_name))
    return {"ok": True, "sink": sink_name, "audio": audio_file}


async def _play_audio_to_sink(paplay: str, audio_file: str, sink_name: str) -> None:
    logger.info("Injecting test audio into sink %s via paplay", sink_name)
    proc = await asyncio.create_subprocess_exec(
        paplay, "--device", sink_name, audio_file,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if stderr:
        logger.warning("paplay stderr: %s", stderr.decode(errors="replace")[:500])
    logger.info("paplay finished with code %d", proc.returncode)


class LaunchSpeakerRequest(BaseModel):
    meeting_url: str
    duration_minutes: int = 5


@router.post("/test/run-speaker-sync")
async def run_speaker_sync(req: LaunchSpeakerRequest, caller: TestOrAdminUser):
    """Run test_speaker.py synchronously for up to 30s and return its output (for debugging)."""
    speaker_script = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "tests", "e2e", "test_speaker.py")
    )
    try:
        proc = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                sys.executable, speaker_script,
                "--url", req.meeting_url,
                "--duration", "1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            ),
            timeout=5,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            stdout, stderr = await proc.communicate()
        return {
            "returncode": proc.returncode,
            "stdout": stdout.decode(errors="replace")[-3000:],
            "stderr": stderr.decode(errors="replace")[-3000:],
        }
    except Exception as e:
        return {"error": str(e)}


@router.get("/test/debug-speaker")
async def debug_test_speaker(caller: TestOrAdminUser):
    """Check test_speaker.py environment: audio file, playwright, python path."""
    import subprocess as _sp
    speaker_script = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "tests", "e2e", "test_speaker.py")
    )
    audio_file = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "tests", "e2e", "test_audio.wav")
    )
    result = {
        "speaker_script_exists": os.path.exists(speaker_script),
        "speaker_script_path": speaker_script,
        "audio_exists": os.path.exists(audio_file),
        "audio_size": os.path.getsize(audio_file) if os.path.exists(audio_file) else 0,
        "audio_path": audio_file,
        "display": os.environ.get("DISPLAY"),
        "pulse_server": os.environ.get("PULSE_SERVER"),
        "python": sys.executable,
    }
    # Quick import check
    try:
        proc = _sp.run(
            [sys.executable, "-c", "from playwright.async_api import async_playwright; print('OK')"],
            capture_output=True, text=True, timeout=10,
        )
        result["playwright_import"] = proc.stdout.strip() or proc.stderr.strip()[:200]
    except Exception as e:
        result["playwright_import"] = str(e)
    return result


@router.post("/test/launch-speaker")
async def launch_test_speaker(req: LaunchSpeakerRequest, caller: TestOrAdminUser):
    """
    Launch Test Speaker immediately (called by smoke test when it detects status=recording).
    Returns immediately — speaker runs in background.
    """
    asyncio.create_task(_launch_speaker(req.meeting_url, req.duration_minutes))
    return {"ok": True, "message": f"Test Speaker launching for {req.duration_minutes} min"}


async def _launch_speaker(meeting_url: str, duration_minutes: int) -> None:
    """Launch test_speaker.py as a subprocess — no delay."""
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
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if stdout:
        logger.info("Test Speaker stdout:\n%s", stdout.decode(errors="replace"))
    if stderr:
        logger.warning("Test Speaker stderr:\n%s", stderr.decode(errors="replace"))
    logger.info("Test Speaker finished with exit code %d", proc.returncode)


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
