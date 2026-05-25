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


@router.post("/stream")
async def chat_stream(req: ChatRequest, user: CurrentUser):
    """SSE streaming chat response."""
    user_id = user["user_id"]

    # Build context from meetings
    context_parts = []
    if req.meeting_id:
        meeting = await _get_accessible_meeting(req.meeting_id, user)
        if meeting.get("transcript"):
            context_parts.append(
                f"Встреча: {meeting.get('title', 'Без названия')}\n"
                f"Дата: {meeting.get('start_time', '')}\n"
                f"Транскрипт:\n{meeting['transcript'][:8000]}"
            )
        if meeting.get("summary"):
            context_parts.append(f"Протокол:\n{meeting['summary'][:3000]}")
    else:
        # Global chat — use summaries of all accessible meetings
        if user["is_admin"]:
            meetings = await models.get_all_meetings(limit=30)
        else:
            meetings = await models.get_meetings_for_user(user_id, limit=30)

        for m in meetings[:10]:
            if m.get("summary"):
                context_parts.append(
                    f"Встреча «{m.get('topic') or m.get('title', 'Без названия')}» "
                    f"({m.get('start_time', '')}):\n{m['summary'][:1000]}"
                )

    context = "\n\n---\n\n".join(context_parts) if context_parts else "Нет доступных данных о встречах."

    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%d %B %Y")

    history = await models.get_chat_history(user_id, req.meeting_id, limit=10)
    messages = [
        {
            "role": "system",
            "content": (
                "Ты помощник, отвечающий на вопросы о рабочих встречах компании Sifox. "
                "Отвечай на русском языке, кратко и по делу. "
                "Используй только информацию из предоставленного контекста.\n\n"
                f"Сегодня: {today}.\n\n"
                f"Контекст встреч:\n{context}"
            ),
        }
    ]
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": req.message})

    await models.save_chat_message(user_id, "user", req.message, req.meeting_id)

    return StreamingResponse(
        _stream_openai(messages, user_id, req.meeting_id),
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
) -> AsyncGenerator[str, None]:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    full_response = []

    try:
        stream = await client.chat.completions.create(
            model=config.OPENAI_MODEL,
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

        # Save assistant reply
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
