"""Calls: public (preview-gated) viewing of imported rec.megafon.ru calls, plus
admin endpoints to drive the interactive MegaFon import (phone → OTP → sync)."""
import logging
import os
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from auth.deps import get_current_user, get_test_or_admin_user
from config import config
from database import models

logger = logging.getLogger(__name__)

# ── Viewing (demo "Звонки" — preview session only) ────────────────────────────
router = APIRouter(prefix="/api/calls", tags=["calls"])
CurrentUser = Annotated[dict, Depends(get_current_user)]


def _require_preview(user: dict) -> None:
    if not user.get("is_preview"):
        raise HTTPException(403, "Demo only")


@router.get("")
async def list_calls(user: CurrentUser):
    _require_preview(user)
    return {"calls": await models.get_calls()}


@router.get("/{call_id}")
async def get_call(call_id: str, user: CurrentUser):
    _require_preview(user)
    call = await models.get_call(call_id)
    if not call:
        raise HTTPException(404, "Call not found")
    return call


@router.get("/{call_id}/audio")
async def get_call_audio(call_id: str, user: CurrentUser):
    _require_preview(user)
    call = await models.get_call(call_id)
    if not call or not call.get("audio_path"):
        raise HTTPException(404, "Audio not available")
    full_path = os.path.join(config.AUDIO_DIR, call["audio_path"])
    if not os.path.exists(full_path):
        raise HTTPException(404, "Audio file not found on disk")
    ext = os.path.splitext(call["audio_path"])[1].lower() or ".mp3"
    media_type = "audio/mpeg" if ext == ".mp3" else "audio/wav"
    return FileResponse(full_path, media_type=media_type)  # inline → seekable


# ── Admin: MegaFon interactive import ─────────────────────────────────────────
admin_router = APIRouter(prefix="/api/admin/megafon", tags=["admin", "calls"])
AdminUser = Annotated[dict, Depends(get_test_or_admin_user)]


class StartImportRequest(BaseModel):
    phone: str | None = None


class OtpRequest(BaseModel):
    job_id: str
    code: str


@admin_router.post("/start")
async def megafon_start(body: StartImportRequest, admin: AdminUser):
    from services import megafon_sync
    phone = (body.phone or config.MEGAFON_PHONE or "").strip()
    if not phone:
        raise HTTPException(400, "Укажите номер телефона")
    try:
        job_id = await megafon_sync.start_login(phone)
    except megafon_sync.MegafonError as e:
        raise HTTPException(400, str(e))
    return {"job_id": job_id, "status": "otp_required"}


@admin_router.post("/otp")
async def megafon_otp(body: OtpRequest, admin: AdminUser):
    from services import megafon_sync
    try:
        await megafon_sync.submit_otp(body.job_id, body.code)
    except megafon_sync.MegafonError as e:
        raise HTTPException(400, str(e))
    return {"job_id": body.job_id, "status": "importing"}


@admin_router.get("/status/{job_id}")
async def megafon_status(job_id: str, admin: AdminUser):
    from services import megafon_sync
    st = megafon_sync.get_status(job_id)
    if st is None:
        raise HTTPException(404, "Job not found")
    return st


@admin_router.post("/reset")
async def megafon_reset(admin: AdminUser):
    """Delete all imported calls + their audio (so the next import re-downloads
    and re-processes from scratch, e.g. to apply speaker diarization)."""
    paths = await models.delete_all_calls()
    from services import fsio
    removed = 0
    for p in paths:
        target = Path(config.AUDIO_DIR) / p
        existed = await fsio.exists(target)
        if existed:
            await fsio.unlink_quiet(target)
        if existed and not await fsio.exists(target):
            removed += 1
    return {"deleted_rows": len(paths), "removed_files": removed}
