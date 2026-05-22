"""Meeting routes: list, detail, transcript, audio stream."""
import os
import logging
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse, StreamingResponse
from typing import Annotated

from auth.deps import get_current_user
from config import config
from database import models

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/meetings", tags=["meetings"])

CurrentUser = Annotated[dict, Depends(get_current_user)]


@router.get("")
async def list_meetings(
    user: CurrentUser,
    limit: int = 20,
    offset: int = 0,
):
    if user["is_admin"]:
        meetings = await models.get_all_meetings(limit=limit, offset=offset)
    else:
        meetings = await models.get_meetings_for_user(
            user["user_id"], limit=limit, offset=offset
        )
    return {"meetings": meetings, "limit": limit, "offset": offset}


@router.get("/{meeting_id}")
async def get_meeting(meeting_id: str, user: CurrentUser):
    meeting = await _get_accessible_meeting(meeting_id, user)
    participants = await models.get_participants(meeting_id)
    return {**meeting, "participants": participants}


@router.get("/{meeting_id}/transcript")
async def get_transcript(meeting_id: str, user: CurrentUser):
    meeting = await _get_accessible_meeting(meeting_id, user)
    if not meeting.get("transcript"):
        raise HTTPException(404, "Transcript not available yet")
    return {"transcript": meeting["transcript"]}


@router.get("/{meeting_id}/audio")
async def get_audio(meeting_id: str, user: CurrentUser):
    meeting = await _get_accessible_meeting(meeting_id, user)
    if not meeting.get("audio_path"):
        raise HTTPException(404, "Audio not available")

    full_path = os.path.join(config.AUDIO_DIR, meeting["audio_path"])
    if not os.path.exists(full_path):
        raise HTTPException(404, "Audio file not found on disk")

    return FileResponse(
        full_path,
        media_type="audio/wav",
        filename=f"meeting-{meeting_id[:8]}.wav",
        headers={"Content-Disposition": f'attachment; filename="meeting-{meeting_id[:8]}.wav"'},
    )


# ── helpers ───────────────────────────────────────────────────────────────────

async def _get_accessible_meeting(meeting_id: str, user: dict) -> dict:
    """Get meeting, raise 403/404 if not accessible."""
    meeting = await models.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(404, "Meeting not found")

    if user["is_admin"]:
        return meeting

    # Check user has access
    user_id = user["user_id"]
    email = user["email"]

    accessible = await models.get_meetings_for_user(user_id, limit=1000)
    ids = {str(m["id"]) for m in accessible}
    if meeting_id not in ids:
        raise HTTPException(403, "Access denied")

    return meeting
