"""Public, password-protected meeting view — NO login required.

Anyone with the link + password can unlock a meeting and see its protocol,
transcript, and play the mp3. No session/cookie involved; audio is gated by a
short-lived signed token issued after the password check.
"""
import asyncio
import logging
import os
import re
import time
from collections import OrderedDict
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from config import config
from database import models
from services import share as share_svc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/share", tags=["share"])

# Bounded in-memory brute-force throttle. A shared Redis-backed limiter would be
# preferable if the service ever runs with multiple workers/instances.
_attempts: OrderedDict[str, tuple[float, int]] = OrderedDict()
_MAX_ATTEMPTS = 8
_WINDOW = 300  # 5 min
_MAX_TRACKED_ATTEMPTS = 10_000
_SHARE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{24,64}$")


def _too_many_attempts(key: str) -> bool:
    now = time.monotonic()
    while _attempts:
        oldest_key, (oldest_start, _) = next(iter(_attempts.items()))
        if now - oldest_start <= _WINDOW:
            break
        _attempts.pop(oldest_key, None)

    start, n = _attempts.pop(key, (now, 0))
    if now - start > _WINDOW:
        start, n = now, 0
    while len(_attempts) >= _MAX_TRACKED_ATTEMPTS:
        _attempts.popitem(last=False)
    _attempts[key] = (start, n + 1)
    return n + 1 > _MAX_ATTEMPTS


class UnlockRequest(BaseModel):
    password: str


def _share_or_404(share: dict | None) -> dict:
    if not share:
        raise HTTPException(404, "Ссылка не найдена")
    exp = share.get("expires_at")
    if exp and exp < datetime.now(timezone.utc):
        raise HTTPException(404, "Срок действия ссылки истёк")
    return share


@router.post("/{token}/unlock")
async def unlock_share(token: str, body: UnlockRequest, request: Request):
    """Verify the password; return the meeting view + a signed audio URL."""
    if not _SHARE_TOKEN_RE.fullmatch(token):
        raise HTTPException(404, "Ссылка не найдена")
    share = _share_or_404(await models.get_meeting_share(token))
    client_ip = request.client.host if request.client else "unknown"
    attempt_key = f"{token}:{client_ip}"
    if _too_many_attempts(attempt_key):
        raise HTTPException(429, "Слишком много попыток, попробуйте позже")
    if not await asyncio.to_thread(
        share_svc.verify_password, body.password, share["password_hash"]
    ):
        raise HTTPException(401, "Неверный пароль")
    _attempts.pop(attempt_key, None)

    meeting = await models.get_meeting(share["meeting_id"])
    if not meeting:
        raise HTTPException(404, "Встреча не найдена")

    has_audio = bool(meeting.get("audio_path"))
    audio_url = None
    if has_audio:
        audio_url = f"/api/share/{token}/audio?t={share_svc.make_audio_token(token)}"
    return {
        "title": meeting.get("title"),
        "start_time": meeting.get("start_time").isoformat() if meeting.get("start_time") else None,
        "end_time": meeting.get("end_time").isoformat() if meeting.get("end_time") else None,
        "summary": meeting.get("summary"),
        "transcript": meeting.get("transcript"),
        "has_audio": has_audio,
        "audio_url": audio_url,
    }


@router.get("/{token}/audio")
async def share_audio(token: str, t: str):
    """Stream the meeting mp3 for a public share. Gated by the signed token `t`
    handed out by /unlock (password already verified there)."""
    if not share_svc.verify_audio_token(token, t):
        raise HTTPException(403, "Недействительный или просроченный токен")
    share = _share_or_404(await models.get_meeting_share(token))
    meeting = await models.get_meeting(share["meeting_id"])
    if not meeting or not meeting.get("audio_path"):
        raise HTTPException(404, "Аудио недоступно")

    full_path = os.path.join(config.AUDIO_DIR, meeting["audio_path"])
    if not os.path.exists(full_path):
        raise HTTPException(404, "Аудиофайл не найден")
    ext = os.path.splitext(meeting["audio_path"])[1].lower() or ".mp3"
    media_type = "audio/mpeg" if ext == ".mp3" else "audio/wav"
    return FileResponse(full_path, media_type=media_type)
