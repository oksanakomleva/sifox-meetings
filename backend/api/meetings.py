"""Meeting routes: list, detail, transcript, audio stream."""
import os
import logging
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from typing import Annotated

from auth.deps import get_current_user
from config import config
from database import models

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/meetings", tags=["meetings"])

CurrentUser = Annotated[dict, Depends(get_current_user)]


@router.get("/calendar-status")
async def calendar_status(user: CurrentUser):
    """Return whether the current user has connected and enabled their Google Calendar."""
    return await models.get_calendar_status(user["user_id"])


# Skimmable-by-design weekly summary. Kept short on purpose: a one-glance recap
# plus one line per topic/meeting. Length is bounded by both this format and a
# small max_tokens below.
_WEEK_SYSTEM_PROMPT = (
    "Ты составляешь КОРОТКУЮ сводку по итогам рабочей недели на основе полных "
    "транскриптов встреч (каждая — со своим заголовком и текстом с разбивкой по "
    "участникам, формат реплики «[ВРЕМЯ] Имя: текст»).\n\n"
    "Сводку должно быть видно одним взглядом, почти без прокрутки. Формат строго такой:\n"
    "1) Первая строка — обзор недели в 1–2 предложениях.\n"
    "2) Дальше маркированный список, по одному пункту на тему/встречу. Каждый пункт — "
    "ОДНА короткая строка вида «‹заказчик/проект/тема› — суть и ключевое решение или итог».\n"
    "3) Если несколько встреч по одной теме — объедини их в один пункт.\n\n"
    "Ограничения: не больше 8 пунктов; никаких длинных абзацев, цитат и воды; "
    "опирайся только на транскрипты, ничего не выдумывай; пиши на русском языке."
)


def _week_signature(meetings: list[dict]) -> str:
    """Stable hash over (id, updated_at) of the in-window meetings, plus the
    summary prompt. Changes when a meeting is added, edited (updated_at moves),
    drops out of the 7-day window, OR the prompt/format itself changes — so a
    prompt tweak transparently invalidates all cached summaries."""
    import hashlib
    items = []
    for m in meetings:
        upd = m.get("updated_at")
        upd_str = upd.isoformat() if hasattr(upd, "isoformat") else str(upd)
        items.append(f"{m['id']}:{upd_str}")
    raw = "|".join(sorted(items)) + "||" + _WEEK_SYSTEM_PROMPT
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@router.get("/week-summary")
async def week_summary(user: CurrentUser):
    """AI-generated summary of the last 7 days for Dashboard.

    Cached per user: regenerated via OpenAI only when the set of in-window
    meetings changes (see _week_signature); otherwise served from week_summaries.
    """
    from openai import AsyncOpenAI
    meetings = await models.get_meetings_this_week(user["user_id"], user["is_admin"])
    if not meetings:
        return {"summary": None, "count": 0}

    signature = _week_signature(meetings)
    cached = await models.get_week_summary_cache(user["user_id"])
    if cached and cached["signature"] == signature and cached.get("summary"):
        return {"summary": cached["summary"], "count": cached["meeting_count"]}

    # Feed FULL transcripts (with speaker labels), newest first, up to the budget.
    max_chars = config.CHAT_MAX_CONTEXT_CHARS
    parts = []
    used = 0
    included = 0
    for m in meetings:
        transcript = m.get("transcript")
        if not transcript:
            continue
        name = m.get("topic") or m.get("title") or "Без названия"
        date_val = m.get("start_time")
        date_str = date_val.strftime("%d.%m") if hasattr(date_val, "strftime") else str(date_val)[:10]
        block = (
            f"### Встреча: {name} ({date_str})\n"
            f"Транскрипт (с разбивкой по участникам):\n{transcript}"
        )
        if used + len(block) <= max_chars:
            parts.append(block)
            used += len(block)
            included += 1
        elif included == 0:
            parts.append(block[:max_chars])
            included += 1
            break
        else:
            break
    if included < len(meetings):
        logger.info(
            "week-summary: %d/%d meetings fit the %d-char budget",
            included, len(meetings), max_chars,
        )

    context = "\n\n---\n\n".join(parts)
    client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    resp = await client.chat.completions.create(
        model=config.CHAT_MODEL,
        messages=[
            {"role": "system", "content": _WEEK_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Встречи за последние 7 дней (полные транскрипты):\n\n{context}\n\n"
                    "Составь короткую сводку по правилам выше."
                ),
            },
        ],
        max_tokens=600,
        temperature=0.3,
    )
    summary_text = resp.choices[0].message.content
    await models.upsert_week_summary_cache(
        user["user_id"], signature, summary_text, len(meetings)
    )
    return {"summary": summary_text, "count": len(meetings)}


class DemoCallIn(BaseModel):
    title: str
    datetime: str = ""
    transcript: str = ""


class DemoSummaryRequest(BaseModel):
    period: str = "week"            # 'day' | 'week'
    calls: list[DemoCallIn] = []


@router.post("/demo-summary")
async def demo_summary(body: DemoSummaryRequest, user: CurrentUser):
    """Demo-only day/week summary: SAME prompt as the real week summary, but the
    context is the "демо"-tagged meetings for the period + the fake demo calls
    sent from the client. Only available inside a preview session."""
    if not user.get("is_preview"):
        raise HTTPException(403, "Demo only")
    from openai import AsyncOpenAI

    # "демо"-tagged completed meetings with transcripts (admin-wide query — this
    # is the curated demo set, and the endpoint is preview-gated).
    meetings = await models.get_recent_meetings_with_transcripts(days=7)
    meetings = [
        m for m in meetings
        if any((t or "").lower() == "демо" for t in (m.get("tags") or []))
    ]
    if body.period == "day":
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).date()
        meetings = [
            m for m in meetings
            if m.get("start_time") and m["start_time"].date() == today
        ]

    max_chars = config.CHAT_MAX_CONTEXT_CHARS
    parts: list[str] = []
    used = 0
    for m in meetings:
        t = m.get("transcript")
        if not t:
            continue
        block = (
            f"### Встреча: {m.get('topic') or m.get('title') or 'Без названия'}\n"
            f"Транскрипт:\n{t}"
        )
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)
    for c in body.calls:
        block = f"### Звонок: {c.title} ({c.datetime})\nРасшифровка:\n{c.transcript}"
        if used + len(block) > max_chars:
            break
        parts.append(block)
        used += len(block)

    if not parts:
        return {"summary": None}

    period_word = "за сегодня" if body.period == "day" else "за последние 7 дней"
    context = "\n\n---\n\n".join(parts)
    client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    resp = await client.chat.completions.create(
        model=config.CHAT_MODEL,
        messages=[
            {"role": "system", "content": _WEEK_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Встречи и звонки {period_word} (полные расшифровки):\n\n{context}\n\n"
                    "Составь короткую сводку по правилам выше."
                ),
            },
        ],
        max_tokens=600,
        temperature=0.3,
    )
    return {"summary": resp.choices[0].message.content}


@router.get("/upcoming")
async def upcoming_meetings(user: CurrentUser):
    """Pending/active meetings for the user's own "Мои встречи" calendar tab.
    Always user-scoped (even for admins — they see ALL upcoming via /admin/upcoming)."""
    meetings = await models.get_upcoming_meetings_for_user(user["user_id"], False)
    return {"meetings": meetings}


@router.get("/week")
async def meetings_this_week(user: CurrentUser):
    """Last 7 days of completed meetings with summaries (for Dashboard)."""
    meetings = await models.get_meetings_this_week(user["user_id"], user["is_admin"])
    return {"meetings": meetings}


@router.get("/tags")
async def list_known_tags(user: CurrentUser):
    """All tags ever used, most frequent first — for autocomplete and filtering."""
    return {"tags": await models.get_known_tags()}


@router.get("")
async def list_meetings(
    user: CurrentUser,
    limit: int = 20,
    offset: int = 0,
):
    """The caller's OWN meetings ("Мои встречи"). Admins see all meetings via the
    separate /api/admin/meetings page, not here."""
    meetings = await models.get_meetings_for_user(
        user["user_id"], limit=limit, offset=offset
    )
    return {"meetings": meetings, "limit": limit, "offset": offset}


@router.get("/demo-list")
async def demo_list(user: CurrentUser):
    """All "демо"-tagged completed meetings (the curated demo set), regardless of
    the preview user's per-meeting access. Preview-gated."""
    if not user.get("is_preview"):
        raise HTTPException(403, "Demo only")
    meetings = await models.get_demo_meetings()
    return {"meetings": meetings}


@router.get("/{meeting_id}")
async def get_meeting(meeting_id: str, user: CurrentUser):
    meeting = await _get_accessible_meeting(meeting_id, user)
    participants = await models.get_participants(meeting_id)
    return {**meeting, "participants": participants}


class TagsUpdate(BaseModel):
    tags: list[str]


@router.put("/{meeting_id}/tags")
async def update_meeting_tags(meeting_id: str, body: TagsUpdate, user: CurrentUser):
    """Manually edit a meeting's tags. Anyone with access to the meeting can edit."""
    from services.analyzer import normalize_tags
    await _get_accessible_meeting(meeting_id, user)  # enforces 403/404
    tags = normalize_tags(body.tags)
    await models.update_meeting_tags(meeting_id, tags)
    return {"tags": tags}


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

    # New recordings are MP3; legacy ones may still be WAV.
    ext = os.path.splitext(meeting["audio_path"])[1].lower() or ".mp3"
    media_type = "audio/mpeg" if ext == ".mp3" else "audio/wav"
    download_name = f"meeting-{meeting_id[:8]}{ext}"
    return FileResponse(
        full_path,
        media_type=media_type,
        filename=download_name,
        headers={"Content-Disposition": f'attachment; filename="{download_name}"'},
    )


# ── helpers ───────────────────────────────────────────────────────────────────

async def _get_accessible_meeting(meeting_id: str, user: dict) -> dict:
    """Get meeting, raise 403/404 if not accessible."""
    meeting = await models.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(404, "Meeting not found")

    if user["is_admin"]:
        return meeting

    # Preview/demo: the curated "демо"-tagged meetings are accessible regardless
    # of the preview user's own per-meeting access.
    if user.get("is_preview") and any((t or "").lower() == "демо" for t in (meeting.get("tags") or [])):
        return meeting

    # Check user has access
    user_id = user["user_id"]
    email = user["email"]

    accessible = await models.get_meetings_for_user(user_id, limit=1000)
    ids = {str(m["id"]) for m in accessible}
    if meeting_id not in ids:
        raise HTTPException(403, "Access denied")

    return meeting
