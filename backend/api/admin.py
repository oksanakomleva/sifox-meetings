"""Admin routes: users, calendars, meetings management."""
import asyncio
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from typing import Annotated, Literal

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
    """Manually trigger calendar sync.

    Refreshes the LIST of available calendars from Google right now (so newly
    shared/added calendars show up), then kicks off event sync in the background.
    """
    from services.calendar_sync import sync_user_calendars, sync_all_users
    import asyncio

    users = await models.get_all_users_with_tokens()
    refreshed, failed = 0, 0
    for u in users:
        try:
            await sync_user_calendars(u["id"])
            refreshed += 1
        except Exception as e:
            failed += 1
            logger.warning("Calendar list refresh failed for user %s: %s", u["id"], e)

    # Events in the background (longer-running).
    asyncio.create_task(sync_all_users())
    return {"ok": True, "calendars_refreshed": refreshed, "failed": failed}


# ── Meetings ──────────────────────────────────────────────────────────────────

@router.get("/meetings")
async def list_all_meetings(admin: AdminUser, limit: int = 100, offset: int = 0):
    meetings = await models.get_all_meetings(limit=limit, offset=offset)
    return {"meetings": meetings}


@router.get("/upcoming")
async def list_all_upcoming(admin: AdminUser):
    """All pending/active meetings (admin "Все встречи" → Запланированные)."""
    meetings = await models.get_upcoming_meetings_for_user(admin["user_id"], True)
    return {"meetings": meetings}


class SetMeetingAssistantRequest(BaseModel):
    assistant_enabled: bool


class SetMeetingPublicInfoRequest(BaseModel):
    public_info_enabled: bool


@router.patch("/meetings/{meeting_id}/assistant")
async def set_meeting_assistant(
    meeting_id: str,
    req: SetMeetingAssistantRequest,
    admin: AdminUser,
):
    """Opt one pending meeting into or out of the live voice assistant.

    The recorder reads this flag when it atomically claims the meeting. Changes
    after that point would not affect the already-running browser, so reject
    them instead of showing admins a misleading successful toggle.
    """
    from config import config
    from services.assistant_toggle import (
        AssistantToggleError,
        validate_assistant_toggle,
    )

    meeting = await models.get_meeting(meeting_id)
    try:
        validate_assistant_toggle(
            meeting,
            req.assistant_enabled,
            live_assistant_enabled=config.LIVE_ASSISTANT_ENABLED,
            live_assistant_speak=config.LIVE_ASSISTANT_SPEAK,
            live_assistant_all_meetings=config.LIVE_ASSISTANT_ALL_MEETINGS,
        )
    except AssistantToggleError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc

    updated = await models.set_meeting_assistant_enabled(
        meeting_id,
        req.assistant_enabled,
    )
    if not updated:
        # The scheduler may have claimed the meeting between our read and write.
        raise HTTPException(
            409,
            "Встреча уже началась; изменить настройку ассистента не удалось",
        )

    logger.info(
        "Admin %s set live assistant=%s for meeting %s",
        admin["user_id"],
        req.assistant_enabled,
        meeting_id[:8],
    )
    return {
        "ok": True,
        "meeting_id": meeting_id,
        "assistant_enabled": req.assistant_enabled,
    }


@router.patch("/meetings/{meeting_id}/assistant-public-info")
async def set_meeting_public_info(
    meeting_id: str,
    req: SetMeetingPublicInfoRequest,
    admin: AdminUser,
):
    """Allow public web answers for one opted-in, not-yet-started meeting."""
    from config import config
    from services.assistant_toggle import (
        AssistantToggleError,
        validate_public_info_toggle,
    )

    meeting = await models.get_meeting(meeting_id)
    try:
        validate_public_info_toggle(
            meeting,
            req.public_info_enabled,
            live_public_info_enabled=config.LIVE_PUBLIC_INFO_ENABLED,
        )
    except AssistantToggleError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc

    updated = await models.set_meeting_public_info_enabled(
        meeting_id,
        req.public_info_enabled,
    )
    if not updated:
        raise HTTPException(
            409,
            "Встреча уже началась или живой ассистент не включён",
        )
    logger.info(
        "Admin %s set public info=%s for meeting %s",
        admin["user_id"],
        req.public_info_enabled,
        meeting_id[:8],
    )
    return {
        "ok": True,
        "meeting_id": meeting_id,
        "public_info_enabled": req.public_info_enabled,
    }


@router.get("/meetings/{meeting_id}/live-qa")
async def admin_live_qa(meeting_id: str, admin: AdminUser):
    """Inspect the live in-meeting assistant Q&A for a meeting (+ flag state).
    Accepts the X-Test-Api-Key header, so it's usable for validation."""
    from config import config
    from services.live_assistant import get_live_diagnostic
    meeting = await models.get_meeting(meeting_id)
    return {
        "live_assistant_enabled": config.LIVE_ASSISTANT_ENABLED,
        "live_assistant_speak": config.LIVE_ASSISTANT_SPEAK,
        "live_assistant_all_meetings": config.LIVE_ASSISTANT_ALL_MEETINGS,
        "live_public_info_enabled": config.LIVE_PUBLIC_INFO_ENABLED,
        "meeting_assistant_enabled": bool(
            meeting and meeting.get("assistant_enabled")
        ),
        "meeting_public_info_enabled": bool(
            meeting and meeting.get("assistant_public_info_enabled")
        ),
        "diagnostic": get_live_diagnostic(meeting_id),
        "items": await models.get_live_qa(meeting_id),
        "notes": await models.get_live_notes(meeting_id),
    }


@router.post("/meetings/{meeting_id}/reanalyze")
async def reanalyze_meeting(meeting_id: str, admin: AdminUser, meeting_type: str | None = None):
    """Re-run analysis on a meeting that already has a transcript.

    Optional `meeting_type` query param pins the protocol structure (and stored
    type) to a specific kind instead of relying on auto-detection."""
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
            analysis = await analyze_meeting(
                transcript,
                meeting.get("title"),
                force_type=meeting_type,
                meeting_id=meeting_id,
            )
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


@router.post("/meetings/{meeting_id}/retranscribe")
async def retranscribe_meeting(meeting_id: str, admin: AdminUser):
    """Re-run the FULL transcribe→analyze pipeline from the meeting's audio file
    on the volume. Unlike /reanalyze (which needs an existing transcript), this
    recovers meetings that errored mid-transcription: the WAV/MP3 survives on the
    persistent volume even when the DB never recorded audio_path."""
    from services.recorder import find_audio_on_disk, spawn_tracked, _recover_pipeline

    meeting = await models.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(404, "Meeting not found")
    audio = await find_audio_on_disk(meeting_id)
    if audio is None:
        raise HTTPException(400, "Аудиофайл встречи не найден в хранилище")

    started = spawn_tracked(meeting_id, _recover_pipeline(meeting_id, audio), name=f"retranscribe-{meeting_id[:8]}")
    if not started:
        return {"ok": True, "message": "Уже обрабатывается"}
    return {"ok": True, "message": "Перетранскрибация запущена", "audio": audio.name}


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
    try:
        await models.grant_meeting_access(req.user_id, req.meeting_id, admin["user_id"])
    except Exception as e:
        logger.exception("grant_meeting_access failed")
        raise HTTPException(500, f"DB error: {type(e).__name__}: {e}")
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


# ── Preview as regular user ───────────────────────────────────────────────────

@router.post("/preview-self")
async def create_preview_session(admin: AdminUser):
    """
    Create a short-lived session that lets the admin view THEIR OWN account in
    regular-user mode (admin rights stripped). The preview lives in a separate
    `preview` cookie, so the admin's real session is untouched and is restored
    by clearing the cookie ("exit preview"). Returns the activation URL.
    """
    from config import config as app_config

    token = await models.create_preview_session(admin["user_id"])
    url = f"{app_config.BASE_URL}/api/auth/preview/{token}"
    return {"ok": True, "url": url, "expires_in": "1 hour"}


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
    return await _do_fix_url(meeting_id, req)


@router.post("/meetings/{meeting_id}/fixurl")
async def fix_meeting_url(meeting_id: str, req: PatchMeetingUrlRequest, admin: AdminUser):
    """Fix a corrupted meeting_url directly in DB (POST alternative to PATCH)."""
    return await _do_fix_url(meeting_id, req)


async def _do_fix_url(meeting_id: str, req: PatchMeetingUrlRequest) -> dict:
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


@router.delete("/meetings/{meeting_id}")
async def delete_meeting(meeting_id: str, admin: AdminUser):
    """Delete a single meeting: its DB row (cascades to participants / calendar
    links / live_qa) and its audio file. For cleanup of test/junk meetings."""
    from config import config
    from database.connection import get_pool
    from services import fsio

    if "/" in meeting_id or "\\" in meeting_id or ".." in meeting_id:
        raise HTTPException(400, "Invalid meeting_id")

    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM meetings WHERE id = $1", meeting_id)
    if result.split()[-1] == "0":
        raise HTTPException(404, "Meeting not found")

    removed_files = []
    for ext in (".mp3", ".wav", ".webm", ".ogg", ".opus", ".m4a", ".mp4", ".webm.part"):
        fpath = Path(config.AUDIO_DIR) / f"{meeting_id}{ext}"
        if await fsio.exists(fpath):
            await fsio.unlink_quiet(fpath)
            if not await fsio.exists(fpath):
                removed_files.append(fpath.name)
    logger.info("Admin %s deleted meeting %s (files: %s)", admin["user_id"], meeting_id, removed_files)
    return {"ok": True, "meeting_id": meeting_id, "files_removed": removed_files}


# ── Recording upload (admin) ──────────────────────────────────────────────────

@router.post("/recordings/upload", status_code=202)
async def upload_recording(
    admin: AdminUser,
    file: UploadFile = File(...),
    title: str | None = Form(None),
    started_at: str | None = Form(None),
):
    """Upload an external recording (e.g. a Zoom mp4) → standard pipeline
    (transcribe + summarize); audio is stored as mp3. Returns 202 immediately."""
    from services.uploads import save_upload_and_process
    meeting_id = await save_upload_and_process(
        file, title=title, recorder_user_id=admin["user_id"], started_at=started_at,
    )
    return {"meeting_id": meeting_id, "status": "processing"}


# ── Public share links + visibility ───────────────────────────────────────────

class CreateShareRequest(BaseModel):
    password: str
    expires_at: str | None = None


@router.post("/meetings/{meeting_id}/share")
async def create_meeting_share(meeting_id: str, body: CreateShareRequest, admin: AdminUser):
    """Create a public, password-protected view link for a meeting."""
    from datetime import datetime
    from config import config
    from services import share as share_svc

    if not await models.get_meeting(meeting_id):
        raise HTTPException(404, "Meeting not found")
    if not body.password or len(body.password) < 4:
        raise HTTPException(400, "Пароль слишком короткий (минимум 4 символа)")
    exp = None
    if body.expires_at:
        try:
            exp = datetime.fromisoformat(body.expires_at.replace("Z", "+00:00"))
        except ValueError:
            pass
    token = share_svc.new_share_token()
    await models.create_meeting_share(
        token, meeting_id, share_svc.hash_password(body.password), admin.get("user_id"), exp,
    )
    return {"token": token, "url": f"{config.BASE_URL}/share/{token}"}


@router.get("/meetings/{meeting_id}/shares")
async def list_meeting_shares(meeting_id: str, admin: AdminUser):
    from config import config
    shares = await models.list_meeting_shares(meeting_id)
    for s in shares:
        s["url"] = f"{config.BASE_URL}/share/{s['token']}"
    return {"shares": shares}


@router.delete("/meetings/share/{token}")
async def revoke_meeting_share(token: str, admin: AdminUser):
    if not await models.delete_meeting_share(token):
        raise HTTPException(404, "Share not found")
    return {"ok": True}


class VisibleToAllRequest(BaseModel):
    value: bool


@router.post("/meetings/{meeting_id}/visible-to-all")
async def set_visible_to_all(meeting_id: str, body: VisibleToAllRequest, admin: AdminUser):
    """Show/hide a meeting in EVERY user's 'Мои встречи' (company-wide recording)."""
    if not await models.get_meeting(meeting_id):
        raise HTTPException(404, "Meeting not found")
    await models.set_meeting_visible_to_all(meeting_id, body.value)
    return {"ok": True, "visible_to_all": body.value}


# ── Storage ───────────────────────────────────────────────────────────────────

@router.get("/storage")
async def list_storage(admin: AdminUser):
    """List WAV files in AUDIO_DIR enriched with meeting title and recorder."""
    from config import config
    from datetime import datetime
    from database.connection import get_pool
    from services import fsio

    audio_dir = config.AUDIO_DIR
    files = []
    total_bytes = 0

    def _scan_storage():
        found = []
        total = 0
        try:
            entries = os.listdir(audio_dir)
        except FileNotFoundError:
            return found, total
        for fname in entries:
            if not fname.endswith((".wav", ".mp3", ".webm", ".ogg", ".opus", ".m4a", ".mp4", ".webm.part")):
                continue
            fpath = os.path.join(audio_dir, fname)
            try:
                stat = os.stat(fpath)
                found.append({
                    "filename": fname,
                    "meeting_id": fname.removesuffix(".webm.part") if fname.endswith(".webm.part") else os.path.splitext(fname)[0],
                    "size_bytes": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                })
                total += stat.st_size
            except OSError:
                continue
        return found, total

    try:
        raw_files, total_bytes = await fsio.run_io(_scan_storage)
    except (asyncio.TimeoutError, OSError) as e:
        raise HTTPException(503, "Storage temporarily unavailable") from e

    # Enrich with meeting title + user info from DB (single query for all IDs)
    meta_by_id: dict[str, dict] = {}
    if raw_files:
        meeting_ids = [f["meeting_id"] for f in raw_files]
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    m.id::text         AS meeting_id,
                    m.title            AS title,
                    m.status           AS status,
                    m.start_time       AS start_time,
                    u.id               AS user_id,
                    u.name             AS user_name,
                    u.email            AS user_email
                FROM meetings m
                LEFT JOIN users u ON u.id = m.recorder_user_id
                WHERE m.id::text = ANY($1::text[])
                """,
                meeting_ids,
            )
            for r in rows:
                meta_by_id[r["meeting_id"]] = {
                    "title": r["title"],
                    "status": r["status"],
                    "start_time": r["start_time"].isoformat() if r["start_time"] else None,
                    "user_name": r["user_name"],
                    "user_email": r["user_email"],
                }

            # For meetings without recorder_user_id, fall back to the first user
            # whose calendar produced this meeting (calendar_meeting_links).
            missing_user_ids = [
                f["meeting_id"] for f in raw_files
                if not meta_by_id.get(f["meeting_id"], {}).get("user_name")
                   and f["meeting_id"] in meta_by_id
            ]
            if missing_user_ids:
                link_rows = await conn.fetch(
                    """
                    SELECT DISTINCT ON (l.meeting_id)
                        l.meeting_id::text AS meeting_id,
                        u.name             AS user_name,
                        u.email            AS user_email
                    FROM calendar_meeting_links l
                    JOIN users u ON u.id = l.user_id
                    WHERE l.meeting_id::text = ANY($1::text[])
                    ORDER BY l.meeting_id, l.created_at ASC NULLS LAST
                    """,
                    missing_user_ids,
                )
                for r in link_rows:
                    meta_by_id[r["meeting_id"]]["user_name"] = r["user_name"]
                    meta_by_id[r["meeting_id"]]["user_email"] = r["user_email"]

    # Merge file info with meeting metadata
    for f in raw_files:
        meta = meta_by_id.get(f["meeting_id"]) or {}
        f["title"] = meta.get("title")
        f["status"] = meta.get("status")
        f["meeting_start_time"] = meta.get("start_time")
        f["user_name"] = meta.get("user_name")
        f["user_email"] = meta.get("user_email")
        files.append(f)

    files.sort(key=lambda f: f["modified_at"], reverse=True)
    return {"files": files, "total_bytes": total_bytes, "audio_dir": audio_dir}


@router.delete("/storage/{meeting_id}")
async def delete_audio_file(meeting_id: str, admin: AdminUser):
    """Delete the audio file (.mp3 or .wav) for a meeting."""
    from config import config
    from services import fsio

    # Basic safety: meeting_id should be a UUID-like string, no path traversal
    if "/" in meeting_id or "\\" in meeting_id or ".." in meeting_id:
        raise HTTPException(400, "Invalid meeting_id")

    deleted = []
    freed = 0
    for ext in (".mp3", ".wav", ".webm", ".ogg", ".opus", ".m4a", ".mp4", ".webm.part"):
        fpath = Path(config.AUDIO_DIR) / f"{meeting_id}{ext}"
        size = await fsio.size(fpath)
        if size >= 0:
            await fsio.unlink_quiet(fpath)
            if await fsio.exists(fpath):
                raise HTTPException(503, "Storage temporarily unavailable")
            deleted.append(fpath.name)
            freed += size
            logger.info("Admin %s deleted %s (%d bytes)", admin["user_id"], fpath, size)

    if not deleted:
        raise HTTPException(404, "File not found")

    return {"ok": True, "deleted": deleted, "freed_bytes": freed}


# ── E2E Testing ───────────────────────────────────────────────────────────────

class StartE2ERequest(BaseModel):
    live_assistant: bool = False


@router.post("/test/start-e2e")
async def start_e2e_test(
    caller: TestOrAdminUser,
    req: StartE2ERequest | None = None,
):
    """
    Start a fully automated E2E test:
    1. Creates a Google Calendar event (now + 3 min) with TEST_MEETING_URL
    2. Triggers calendar sync so the bot picks it up
    3. Returns the meeting to the smoke runner, which launches test_speaker.py
       as soon as the recorder reaches status=recording.

    Auth: session cookie (admin) OR X-Test-Api-Key header (automated tests).
    Requires TEST_MEETING_URL env var (permanent Telemost room link).
    """
    try:
        return await _start_e2e_test_impl(
            caller,
            live_assistant=bool(req and req.live_assistant),
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("start_e2e_test unhandled error")
        raise HTTPException(500, f"Internal error: {type(exc).__name__}: {exc}") from exc


async def _start_e2e_test_impl(caller: dict, *, live_assistant: bool = False) -> dict:
    from config import config
    from services.calendar_sync import _create_test_event_sync, sync_all_users

    meeting_url = config.TEST_MEETING_URL
    if not meeting_url:
        raise HTTPException(400, "TEST_MEETING_URL not set in Railway Variables")
    if live_assistant and not config.LIVE_ASSISTANT_ENABLED:
        raise HTTPException(
            409,
            "LIVE_ASSISTANT_ENABLED is off; enable the master flag before this isolated E2E",
        )
    if live_assistant and not config.LIVE_ASSISTANT_SPEAK:
        raise HTTPException(
            409,
            "LIVE_ASSISTANT_SPEAK is off; enable voice before the live-assistant E2E",
        )

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

    # The smoke runner polls for status=recording and then calls
    # POST /test/launch-speaker. Starting only after the recorder has joined
    # avoids losing the beginning of the WAV and makes the second participant
    # visible to the recorder's meeting-end detector.

    # Look up the meeting record so we can return its ID to the smoke test
    meeting = await models.get_meeting_by_url(meeting_url)
    meeting_id = str(meeting["id"]) if meeting else None
    if live_assistant and meeting_id:
        await models.set_meeting_assistant_enabled(meeting_id, True)

    return {
        "status": "started",
        "meeting_url": meeting_url,
        "meeting_id": meeting_id,
        "calendar_event_id": event_id,
        "calendar_id": cal["google_calendar_id"],
        "live_assistant": live_assistant,
        "message": "Calendar event created for +3 min; Test Speaker will join when recording starts",
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
    meeting_url: str = Field(min_length=10, max_length=2000)
    duration_minutes: int = Field(default=5, ge=1, le=15)
    audio_profile: Literal["standard", "live_assistant"] = "standard"


_speaker_jobs: dict[str, dict] = {}
_MAX_SPEAKER_JOBS = 20


class FinishE2ERequest(BaseModel):
    meeting_id: str = Field(min_length=8, max_length=64)


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
    live_audio_file = os.path.abspath(
        os.path.join(
            os.path.dirname(__file__),
            "..",
            "tests",
            "e2e",
            "live_assistant_test_audio.wav",
        )
    )
    result = {
        "speaker_script_exists": os.path.exists(speaker_script),
        "speaker_script_path": speaker_script,
        "audio_exists": os.path.exists(audio_file),
        "audio_size": os.path.getsize(audio_file) if os.path.exists(audio_file) else 0,
        "audio_path": audio_file,
        "live_audio_exists": os.path.exists(live_audio_file),
        "live_audio_size": (
            os.path.getsize(live_audio_file)
            if os.path.exists(live_audio_file)
            else 0
        ),
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
    Returns a job ID; the smoke test must wait for status=speaking so a muted or
    failed browser cannot produce a false-positive E2E result.
    """
    finished = [
        job_id for job_id, job in _speaker_jobs.items()
        if job.get("status") in ("completed", "failed")
    ]
    while len(_speaker_jobs) >= _MAX_SPEAKER_JOBS and finished:
        _speaker_jobs.pop(finished.pop(0), None)
    if len(_speaker_jobs) >= _MAX_SPEAKER_JOBS:
        raise HTTPException(503, "Too many active Test Speaker jobs")

    job_id = uuid.uuid4().hex
    _speaker_jobs[job_id] = {
        "job_id": job_id,
        "status": "starting",
        "ready": False,
        "returncode": None,
        "error": None,
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    asyncio.create_task(
        _launch_speaker(
            job_id,
            req.meeting_url,
            req.duration_minutes,
            req.audio_profile,
        ),
        name=f"e2e-speaker-{job_id[:8]}",
    )
    return {
        "ok": True,
        "job_id": job_id,
        "status": "starting",
        "message": f"Test Speaker launching for {req.duration_minutes} min",
    }


@router.get("/test/speaker-status/{job_id}")
async def test_speaker_status(job_id: str, caller: TestOrAdminUser):
    job = _speaker_jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Test Speaker job not found")
    return job


@router.get("/test/speaker-jobs")
async def test_speaker_jobs(caller: TestOrAdminUser):
    """Recent jobs for diagnostics when a local smoke runner was interrupted."""
    return {
        "jobs": sorted(
            _speaker_jobs.values(),
            key=lambda job: job.get("created_at", 0),
            reverse=True,
        )
    }


@router.post("/test/finish-e2e-recording")
async def finish_e2e_recording(req: FinishE2ERequest, caller: TestOrAdminUser):
    """Gracefully finish recording after the verified Test Speaker has exited."""
    meeting = await models.get_meeting(req.meeting_id)
    if not meeting:
        raise HTTPException(404, "Meeting not found")
    if meeting.get("status") != "recording":
        raise HTTPException(409, f"Meeting is not recording (status={meeting.get('status')})")

    from services.recorder import request_e2e_finish
    if not request_e2e_finish(req.meeting_id):
        raise HTTPException(409, "Recording task is not active in this process")
    return {"ok": True, "meeting_id": req.meeting_id}


async def _launch_speaker(
    job_id: str,
    meeting_url: str,
    duration_minutes: int,
    audio_profile: str = "standard",
) -> None:
    """Launch test_speaker.py as a subprocess — no delay."""
    job = _speaker_jobs[job_id]
    speaker_script = os.path.join(
        os.path.dirname(__file__), "..", "tests", "e2e", "test_speaker.py"
    )
    speaker_script = os.path.abspath(speaker_script)

    if not os.path.exists(speaker_script):
        logger.error("test_speaker.py not found at %s", speaker_script)
        job.update(
            status="failed",
            error=f"test_speaker.py not found at {speaker_script}",
            updated_at=time.time(),
        )
        return

    logger.info(
        "Launching Test Speaker: %s --url %s --duration %d --audio-profile %s",
        speaker_script,
        meeting_url,
        duration_minutes,
        audio_profile,
    )
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, speaker_script,
            "--url", meeting_url,
            "--duration", str(duration_minutes),
            "--audio-profile", audio_profile,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        job.update(status="joining", pid=proc.pid, updated_at=time.time())

        async def _read_stream(stream, lines: list[str], *, is_error: bool) -> None:
            while True:
                raw = await stream.readline()
                if not raw:
                    return
                line = raw.decode(errors="replace").rstrip()
                lines.append(line)
                del lines[:-100]
                if "E2E_SPEAKER_READY" in line:
                    job.update(status="speaking", ready=True, updated_at=time.time())
                if is_error:
                    logger.info("Test Speaker: %s", line)
                else:
                    logger.info("Test Speaker stdout: %s", line)

        await asyncio.gather(
            _read_stream(proc.stdout, stdout_lines, is_error=False),
            _read_stream(proc.stderr, stderr_lines, is_error=True),
            proc.wait(),
        )
        job.update(
            status="completed" if proc.returncode == 0 and job["ready"] else "failed",
            returncode=proc.returncode,
            error=None if proc.returncode == 0 and job["ready"] else (
                "\n".join(stderr_lines[-10:])[-2000:]
                or "Test Speaker exited before confirming microphone readiness"
            ),
            stdout="\n".join(stdout_lines[-20:])[-3000:],
            stderr="\n".join(stderr_lines[-20:])[-3000:],
            updated_at=time.time(),
        )
        logger.info("Test Speaker finished with exit code %d", proc.returncode)
    except Exception as exc:
        logger.exception("Test Speaker job %s failed", job_id[:8])
        job.update(
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            updated_at=time.time(),
        )


@router.delete("/test/calendar-event/{event_id}")
async def delete_test_calendar_event(event_id: str, calendar_id: str, caller: TestOrAdminUser):
    """Delete the test calendar event created by start_e2e_test (cleanup)."""
    from services.calendar_sync import _delete_event_sync

    user_id = caller["user_id"]
    if user_id == 0:
        write_token = await models.get_any_admin_write_token()
        if not write_token:
            raise HTTPException(400, "No admin has Calendar write access")
        user_id, token_row = write_token
    else:
        token_row = await models.get_google_write_token(user_id)
        if not token_row:
            raise HTTPException(400, "Admin token lacks Calendar write access")

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _delete_event_sync, token_row, calendar_id, event_id)
    return {"ok": True, "deleted_event_id": event_id}


@router.delete("/test/calendar-event/by-meeting/{meeting_id}")
async def delete_test_calendar_event_by_meeting(meeting_id: str, caller: TestOrAdminUser):
    """Safely delete an E2E Calendar event when only the meeting ID is known."""
    from services.calendar_sync import _delete_event_sync

    meeting = await models.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(404, "Meeting not found")
    if not (meeting.get("title") or "").startswith("[E2E Test]"):
        raise HTTPException(400, "Only E2E test meetings can be cleaned up")

    link = await models.get_calendar_link_for_meeting(meeting_id)
    if not link:
        raise HTTPException(404, "No Calendar event linked to this meeting")

    token_row = await models.get_google_write_token(link["user_id"])
    if not token_row:
        raise HTTPException(400, "Event owner lacks Calendar write access")

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        _delete_event_sync,
        token_row,
        link["calendar_id"],
        link["google_event_id"],
    )
    return {"ok": True, "deleted_event_id": link["google_event_id"]}
