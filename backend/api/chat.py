"""AI chat route with SSE streaming."""
import logging
import json
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Annotated, AsyncGenerator

from auth.deps import get_current_user
from config import config
from database import models
from api.meetings import _get_accessible_meeting

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])

CurrentUser = Annotated[dict, Depends(get_current_user)]


class ChatRequest(BaseModel):
    message: str
    meeting_id: str | None = None  # None = ask about all accessible meetings
    demo: bool = False             # demo mode: scope to "демо" meetings, don't persist


_DEMO_TAG = "демо"


@router.post("/stream")
async def chat_stream(req: ChatRequest, user: CurrentUser):
    """SSE streaming chat response."""
    user_id = user["user_id"]
    # Demo only takes effect inside a preview session (admin-only to create).
    demo_mode = bool(req.demo and user.get("is_preview"))

    # Build context from FULL transcripts (with speaker labels), no protocols.
    # Bounded by a character budget so we never blow the model's context window.
    max_chars = config.CHAT_MAX_CONTEXT_CHARS
    context_parts = []
    notice = ""

    if req.meeting_id:
        # Per-meeting chat: the whole transcript; truncate only if it doesn't fit.
        meeting = await _get_accessible_meeting(req.meeting_id, user)
        transcript = meeting.get("transcript")
        if transcript:
            if len(transcript) > max_chars:
                transcript = transcript[:max_chars]
                notice = " (Транскрипт очень длинный — включена только его часть.)"
            context_parts.append(
                f"Встреча: {meeting.get('title', 'Без названия')}\n"
                f"Дата: {meeting.get('start_time', '')}\n"
                f"Транскрипт (с разбивкой по участникам):\n{transcript}"
            )
    else:
        # Global chat: full transcripts of all accessible meetings in the last
        # CHAT_CONTEXT_DAYS days, newest first, packed up to the char budget.
        if user["is_admin"]:
            meetings = await models.get_recent_meetings_with_transcripts(
                days=config.CHAT_CONTEXT_DAYS
            )
        else:
            meetings = await models.get_recent_meetings_with_transcripts_for_user(
                user_id, days=config.CHAT_CONTEXT_DAYS
            )

        if demo_mode:
            # Only reason over the curated "демо" meetings.
            meetings = [
                m for m in meetings
                if any((t or "").lower() == _DEMO_TAG for t in (m.get("tags") or []))
            ]

        used = 0
        included = 0
        for m in meetings:
            transcript = m.get("transcript")
            if not transcript:
                continue
            block = (
                f"Встреча «{m.get('topic') or m.get('title') or 'Без названия'}» "
                f"({m.get('start_time', '')}):\n{transcript}"
            )
            if used + len(block) <= max_chars:
                context_parts.append(block)
                used += len(block)
                included += 1
            elif included == 0:
                # Even the most recent meeting alone exceeds the budget — truncate it.
                context_parts.append(block[:max_chars])
                included += 1
                break
            else:
                break  # newest-first; remaining meetings are older, stop here

        dropped = len(meetings) - included
        if dropped > 0:
            notice = (
                f" (За {config.CHAT_CONTEXT_DAYS} дн. транскриптов больше, чем помещается "
                f"в контекст; {dropped} самых старых встреч не включено.)"
            )

    context = "\n\n---\n\n".join(context_parts) if context_parts else "Нет доступных данных о встречах."

    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%d %B %Y")

    # Demo chat is ephemeral: no past history fed in, nothing saved.
    history = [] if demo_mode else await models.get_chat_history(user_id, req.meeting_id, limit=10)
    messages = [
        {
            "role": "system",
            "content": (
                "Ты помощник, отвечающий на вопросы о рабочих встречах компании Sifox. "
                "В контексте — полные транскрипты встреч с разбивкой по участникам "
                "(каждая реплика в формате «[ВРЕМЯ] Имя: текст»). "
                "Отвечай на русском языке, кратко и по делу, опираясь только на этот "
                "контекст. Если ответа в нём нет — так и скажи.\n\n"
                f"Сегодня: {today}.\n\n"
                f"Контекст встреч:{notice}\n{context}"
            ),
        }
    ]
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": req.message})

    if not demo_mode:
        await models.save_chat_message(user_id, "user", req.message, req.meeting_id)

    return StreamingResponse(
        _stream_openai(messages, user_id, req.meeting_id, persist=not demo_mode),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _stream_openai(
    messages: list[dict],
    user_id: int,
    meeting_id: str | None,
    persist: bool = True,
) -> AsyncGenerator[str, None]:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    full_response = []

    try:
        stream = await client.chat.completions.create(
            model=config.CHAT_MODEL,
            messages=messages,
            stream=True,
            max_tokens=1500,
            temperature=0.3,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            if delta:
                full_response.append(delta)
                yield f"data: {json.dumps({'delta': delta})}\n\n"

        # Save assistant reply (skipped in demo — chat is ephemeral)
        if persist:
            await models.save_chat_message(
                user_id, "assistant", "".join(full_response), meeting_id
            )
        yield "data: [DONE]\n\n"

    except Exception as e:
        logger.error("OpenAI stream error: %s", e)
        yield f"data: {json.dumps({'error': str(e)})}\n\n"


@router.get("/history")
async def get_history(user: CurrentUser, meeting_id: str | None = None, limit: int = 20):
    history = await models.get_chat_history(user["user_id"], meeting_id, limit)
    return {"messages": history}
