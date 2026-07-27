"""Shared 'save an uploaded recording → create meeting → run pipeline' helper.

Used by both the admin web uploader and the browser extension.
faster-whisper/ffmpeg decode any accepted container; transcribe_and_analyze
converts it to mp3 (dropping the source video).
"""
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile

from config import config
from database import models
from services import fsio

logger = logging.getLogger(__name__)

ALLOWED_AUDIO_SUFFIX = {".webm", ".ogg", ".opus", ".m4a", ".mp4", ".wav", ".mp3"}
MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # 500 MB
_UPLOAD_SAVE_TIMEOUT = 5 * 60
_upload_slots = asyncio.Semaphore(2)
_chunk_locks: dict[str, asyncio.Lock] = {}
_MAX_USER_UPLOAD_JOBS = 3
_MAX_GLOBAL_UPLOAD_JOBS = 20


async def _ensure_user_upload_capacity(recorder_user_id: int) -> None:
    if await models.count_all_active_upload_jobs() >= _MAX_GLOBAL_UPLOAD_JOBS:
        raise HTTPException(503, "Очередь обработки заполнена. Повторите загрузку позже.")
    if await models.count_active_upload_jobs(recorder_user_id) >= _MAX_USER_UPLOAD_JOBS:
        raise HTTPException(429, "У вас уже обрабатывается несколько записей. Дождитесь их завершения.")
    used = await models.get_user_uploaded_audio_bytes(recorder_user_id)
    quota = max(1, config.USER_UPLOAD_QUOTA_GB) * 1024 * 1024 * 1024
    if used >= quota:
        raise HTTPException(
            413,
            f"Достигнута квота записей ({config.USER_UPLOAD_QUOTA_GB} ГБ). Удалите старые записи.",
        )


def _parse_start_time(started_at: str | None) -> datetime:
    start_time = datetime.now(timezone.utc)
    if started_at:
        try:
            parsed = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            start_time = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return start_time


async def _create_upload_meeting(
    *,
    title: str | None,
    recorder_user_id: int,
    started_at: str | None,
    source_url: str,
    status: str,
) -> str:
    meeting = await models.upsert_meeting(
        meeting_url=source_url,
        title=(title or "Загруженная запись").strip()[:300],
        start_time=_parse_start_time(started_at),
        google_event_id=None,
    )
    meeting_id = str(meeting["id"])
    await models.set_meeting_recorder_user(meeting_id, recorder_user_id)
    await models.update_meeting_status(meeting_id, status)
    return meeting_id


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
    try:
        await _ensure_user_upload_capacity(recorder_user_id)
    except HTTPException:
        await file.close()
        raise
    try:
        await asyncio.wait_for(_upload_slots.acquire(), timeout=5)
    except asyncio.TimeoutError:
        await file.close()
        raise HTTPException(503, "Сервер уже сохраняет другие записи. Повторите загрузку через минуту.")
    try:
        return await _save_upload_and_process(
            file,
            title=title,
            recorder_user_id=recorder_user_id,
            started_at=started_at,
            source_url=source_url,
        )
    finally:
        _upload_slots.release()


async def _save_upload_and_process(
    file: UploadFile,
    *,
    title: str | None,
    recorder_user_id: int,
    started_at: str | None,
    source_url: str,
) -> str:
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_AUDIO_SUFFIX:
        raise HTTPException(400, f"Неподдерживаемый формат: {suffix or '?'}")

    meeting_id = await _create_upload_meeting(
        title=title,
        recorder_user_id=recorder_user_id,
        started_at=started_at,
        source_url=source_url,
        status="transcribing",
    )

    await fsio.mkdir_p(Path(config.AUDIO_DIR))
    dest = Path(config.AUDIO_DIR) / f"{meeting_id}{suffix}"
    written = 0
    try:
        # open/write/close on the /audio network volume run in a worker thread so
        # a storage stall can't freeze the event loop (see fsio.py).
        out = await fsio.run_io(open, dest, "wb")
        try:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(413, "Файл слишком большой (макс. 500 МБ). Загрузите извлечённое аудио.")
                await fsio.run_io(out.write, chunk, timeout=_UPLOAD_SAVE_TIMEOUT)
        finally:
            await fsio.run_io(out.close)
    except HTTPException:
        await fsio.unlink_quiet(dest)
        await models.update_meeting_status(meeting_id, "error", "Upload too large")
        raise
    except (asyncio.TimeoutError, OSError) as e:
        await fsio.unlink_quiet(dest)
        await models.update_meeting_status(meeting_id, "error", "Storage unavailable during upload")
        raise HTTPException(503, "Хранилище временно недоступно. Повторите загрузку позже.") from e
    finally:
        await file.close()

    if written < 1000:
        await fsio.unlink_quiet(dest)
        await models.update_meeting_status(meeting_id, "error", "Empty or tiny upload")
        raise HTTPException(400, "Файл пустой")

    from services.recorder import spawn_tracked
    if not spawn_tracked(
        meeting_id,
        _process_upload(meeting_id, dest),
        name=f"upload-{meeting_id[:8]}",
    ):
        await models.update_meeting_status(meeting_id, "error", "Duplicate upload processing task")
        raise HTTPException(409, "Эта запись уже обрабатывается")
    return meeting_id


async def start_chunked_upload(
    *,
    title: str | None,
    recorder_user_id: int,
    started_at: str | None,
    source_url: str,
) -> dict[str, Any]:
    """Create a resumable browser-extension upload backed by ``.webm.part``."""
    await _ensure_user_upload_capacity(recorder_user_id)
    meeting_id = await _create_upload_meeting(
        title=title or "Запись из браузера",
        recorder_user_id=recorder_user_id,
        started_at=started_at,
        source_url=source_url or "extension://browser-recording",
        status="uploading",
    )
    await fsio.mkdir_p(Path(config.AUDIO_DIR))
    part_path = Path(config.AUDIO_DIR) / f"{meeting_id}.webm.part"
    await fsio.run_io(lambda: part_path.touch(exist_ok=True))
    return {"meeting_id": meeting_id, "offset": await fsio.size(part_path)}


async def _owned_upload(meeting_id: str, recorder_user_id: int) -> dict:
    meeting = await models.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(404, "Upload not found")
    if meeting.get("recorder_user_id") != recorder_user_id:
        raise HTTPException(403, "Upload belongs to another user")
    if meeting.get("status") != "uploading":
        raise HTTPException(409, "Upload is no longer accepting chunks")
    return meeting


async def append_upload_chunk(
    meeting_id: str,
    *,
    recorder_user_id: int,
    offset: int,
    chunk: bytes,
) -> int:
    """Append one idempotent chunk and return the next expected byte offset."""
    if not chunk:
        raise HTTPException(400, "Empty chunk")
    if len(chunk) > 8 * 1024 * 1024:
        raise HTTPException(413, "Chunk too large")
    if offset < 0 or offset > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "Invalid chunk offset")
    await _owned_upload(meeting_id, recorder_user_id)

    lock = _chunk_locks.setdefault(meeting_id, asyncio.Lock())
    async with lock:
        part_path = Path(config.AUDIO_DIR) / f"{meeting_id}.webm.part"

        def _append() -> tuple[int, bool]:
            current = part_path.stat().st_size if part_path.exists() else 0
            # A retry after a lost HTTP response: the exact chunk is already
            # present, so acknowledge it without writing a duplicate.
            if current == offset + len(chunk):
                return current, False
            if current != offset:
                return current, False
            if current + len(chunk) > MAX_UPLOAD_BYTES:
                raise ValueError("upload too large")
            with part_path.open("ab") as out:
                out.write(chunk)
                out.flush()
            return current + len(chunk), True

        try:
            new_offset, written = await fsio.run_io(
                _append, timeout=_UPLOAD_SAVE_TIMEOUT
            )
        except ValueError as e:
            raise HTTPException(413, "Upload too large") from e
        except (asyncio.TimeoutError, OSError) as e:
            raise HTTPException(503, "Storage temporarily unavailable") from e

        if not written and new_offset not in (offset, offset + len(chunk)):
            raise HTTPException(
                409,
                detail={"message": "Offset mismatch", "expected_offset": new_offset},
            )
        return new_offset


async def finish_chunked_upload(
    meeting_id: str,
    *,
    recorder_user_id: int,
    total_bytes: int,
) -> None:
    """Atomically publish a completed part file and start processing it."""
    meeting = await models.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(404, "Upload not found")
    if meeting.get("recorder_user_id") != recorder_user_id:
        raise HTTPException(403, "Upload belongs to another user")
    # Idempotent retry after the client lost the successful finish response.
    if meeting.get("status") in ("transcribing", "analyzing", "done"):
        return
    if meeting.get("status") != "uploading":
        raise HTTPException(409, "Upload is no longer accepting chunks")
    lock = _chunk_locks.setdefault(meeting_id, asyncio.Lock())
    async with lock:
        part_path = Path(config.AUDIO_DIR) / f"{meeting_id}.webm.part"
        actual = await fsio.size(part_path)
        if actual != total_bytes:
            raise HTTPException(
                409,
                detail={"message": "Upload incomplete", "expected_offset": actual},
            )
        if actual < 1000:
            raise HTTPException(400, "Uploaded recording is empty")
        if actual > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Upload too large")

        final_path = Path(config.AUDIO_DIR) / f"{meeting_id}.webm"
        try:
            await fsio.run_io(part_path.replace, final_path)
        except (asyncio.TimeoutError, OSError) as e:
            raise HTTPException(503, "Storage temporarily unavailable") from e

        await models.update_meeting_status(meeting_id, "transcribing")
        from services.recorder import spawn_tracked
        if not spawn_tracked(
            meeting_id,
            _process_upload(meeting_id, final_path),
            name=f"upload-{meeting_id[:8]}",
        ):
            raise HTTPException(409, "This recording is already being processed")
        _chunk_locks.pop(meeting_id, None)


async def cancel_chunked_upload(
    meeting_id: str,
    *,
    recorder_user_id: int,
) -> None:
    """Discard an upload session when capture could not actually start."""
    await _owned_upload(meeting_id, recorder_user_id)
    lock = _chunk_locks.setdefault(meeting_id, asyncio.Lock())
    async with lock:
        part_path = Path(config.AUDIO_DIR) / f"{meeting_id}.webm.part"
        await fsio.unlink_quiet(part_path)
        await models.update_meeting_status(
            meeting_id,
            "error",
            "Browser recorder failed to start",
        )
    _chunk_locks.pop(meeting_id, None)
