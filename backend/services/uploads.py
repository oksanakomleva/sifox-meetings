"""Shared 'save an uploaded recording → create meeting → run pipeline' helper.

Used by the admin web uploader (Zoom mp4, etc.). The browser extension has its
own copy of this flow in api/extension.py. faster-whisper/ffmpeg decode any
container; transcribe_and_analyze converts to mp3 (dropping the source video).
"""
import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException, UploadFile

from config import config
from database import models

logger = logging.getLogger(__name__)

ALLOWED_AUDIO_SUFFIX = {".webm", ".ogg", ".opus", ".m4a", ".mp4", ".wav", ".mp3"}
MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB


async def _process_upload(meeting_id: str, audio_path: Path) -> None:
    from services.recorder import transcribe_and_analyze
    try:
        await transcribe_and_analyze(meeting_id, audio_path, end_time=datetime.now(timezone.utc))
    except Exception as e:  # noqa: BLE001
        logger.error("Upload processing %s failed: %s", meeting_id[:8], e, exc_info=True)
        await models.update_meeting_status(meeting_id, "error", str(e)[:500])


async def save_upload_and_process(
    file: UploadFile,
    *,
    title: str | None,
    recorder_user_id: int,
    started_at: str | None = None,
    source_url: str = "upload://manual",
) -> str:
    """Stream the upload to disk, create a meeting, kick off transcribe→analyze.
    Returns the new meeting_id. Raises HTTPException on bad/oversized input."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_AUDIO_SUFFIX:
        raise HTTPException(400, f"Неподдерживаемый формат: {suffix or '?'}")

    start_time = datetime.now(timezone.utc)
    if started_at:
        try:
            parsed = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            start_time = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass

    meeting = await models.upsert_meeting(
        meeting_url=source_url,
        title=(title or "Загруженная запись").strip()[:300],
        start_time=start_time,
        google_event_id=None,
    )
    meeting_id = str(meeting["id"])
    await models.set_meeting_recorder_user(meeting_id, recorder_user_id)
    await models.update_meeting_status(meeting_id, "transcribing")

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
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "Файл слишком большой (макс. 500 МБ). Загрузите извлечённое аудио.")
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
        raise HTTPException(400, "Файл пустой")

    asyncio.create_task(_process_upload(meeting_id, dest))
    return meeting_id
