"""Browser-extension routes: audio upload + extension download.

The Chrome extension records any in-browser meeting (tab audio + mic) and
uploads the audio here. It authenticates by reading the user's existing web
login (Google) session cookie and sending it as X-Session-Token. Upload kicks
off the same transcribe→analyze pipeline used for live Telemost recordings.
"""
import asyncio
import io
import logging
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, File
from fastapi.responses import Response

from auth.deps import get_current_user, get_extension_user
from config import config
from database import models

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/extension", tags=["extension"])

CurrentUser = Annotated[dict, Depends(get_current_user)]
ExtensionUser = Annotated[dict, Depends(get_extension_user)]

# Accept common browser MediaRecorder containers; ffmpeg decodes all of them.
_ALLOWED_AUDIO_SUFFIX = {".webm", ".ogg", ".opus", ".m4a", ".mp4", ".wav", ".mp3"}
_MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB (~16h of opus) — generous cap


# Repo root holds the `extension/` source folder (see Dockerfile COPY).
_EXTENSION_DIR = Path(__file__).resolve().parents[2] / "extension"


@router.get("/download")
async def download_extension(user: CurrentUser):
    """Zip the extension source on the fly so teammates can install it
    (chrome://extensions → Load unpacked). Always reflects the deployed version."""
    if not _EXTENSION_DIR.is_dir():
        raise HTTPException(404, "Extension source not found on server")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(_EXTENSION_DIR.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(_EXTENSION_DIR))
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="sifox-recorder-extension.zip"'},
    )


# ── Extension-authenticated routes ────────────────────────────────────────────

@router.get("/me")
async def whoami(user: ExtensionUser):
    """Verify a token from the extension popup."""
    return {"email": user["email"], "name": user.get("name")}


@router.post("/upload", status_code=202)
async def upload_recording(
    user: ExtensionUser,
    file: UploadFile = File(...),
    title: str | None = Form(None),
    started_at: str | None = Form(None),
    source_url: str | None = Form(None),
):
    """Accept a recorded audio blob and process it in the background.

    Returns 202 + {meeting_id} immediately; transcription/analysis run async.
    """
    suffix = Path(file.filename or "").suffix.lower() or ".webm"
    if suffix not in _ALLOWED_AUDIO_SUFFIX:
        raise HTTPException(400, f"Unsupported audio type: {suffix}")

    # Parse optional start time (ISO 8601); fall back to now.
    start_time = datetime.now(timezone.utc)
    if started_at:
        try:
            parsed = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            start_time = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    # Create the meeting row (manual — no calendar event).
    meeting = await models.upsert_meeting(
        meeting_url=source_url or "extension://browser-recording",
        title=(title or "Запись из браузера").strip()[:300],
        start_time=start_time,
        google_event_id=None,
    )
    meeting_id = str(meeting["id"])
    await models.set_meeting_recorder_user(meeting_id, user["user_id"])
    await models.update_meeting_status(meeting_id, "transcribing")

    # Stream the upload to disk in chunks (never load the whole blob in memory).
    os.makedirs(config.AUDIO_DIR, exist_ok=True)
    dest = Path(config.AUDIO_DIR) / f"{meeting_id}{suffix}"
    written = 0
    try:
        with open(dest, "wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > _MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "Upload too large")
                out.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        await models.update_meeting_status(meeting_id, "error", "Upload too large")
        raise
    finally:
        await file.close()

    if written < 1000:
        dest.unlink(missing_ok=True)
        await models.update_meeting_status(meeting_id, "error", "Empty or tiny upload")
        raise HTTPException(400, "Uploaded file is empty")

    asyncio.create_task(_process_upload(meeting_id, dest))
    return {"meeting_id": meeting_id, "status": "processing"}


async def _process_upload(meeting_id: str, audio_path: Path) -> None:
    """Background: run the shared transcribe→analyze pipeline, mark error on failure."""
    from services.recorder import transcribe_and_analyze
    try:
        await transcribe_and_analyze(
            meeting_id, audio_path, end_time=datetime.now(timezone.utc)
        )
    except Exception as e:  # noqa: BLE001
        logger.error("Upload processing %s failed: %s", meeting_id[:8], e, exc_info=True)
        await models.update_meeting_status(meeting_id, "error", str(e)[:500])
