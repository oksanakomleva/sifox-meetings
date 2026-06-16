"""Admin: view ingested Mattermost/Gmail data + AI chat over that data.

All endpoints are admin-only. The AI chat reuses the existing OpenAI infra
(AsyncOpenAI + config.CHAT_MODEL) and is non-streaming with multi-turn history.
"""
import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from auth.deps import get_admin_user, get_test_or_admin_user
from config import config
from database import models

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["communications"])

AdminUser = Annotated[dict, Depends(get_admin_user)]
TestOrAdmin = Annotated[dict, Depends(get_test_or_admin_user)]


@router.get("/comms/debug")
async def comms_debug(user: TestOrAdmin, run: int = 0, gmail_user: str | None = None):
    """Diagnostics (accepts X-Test-Api-Key). ?run=1 runs the MM sync inline and
    returns the count or the captured error."""
    import os
    out: dict = {
        "mm_configured": bool(config.MM_TOKEN and config.MM_SERVER_URL),
        "mm_server_url": config.MM_SERVER_URL,
        "gmail_configured": bool(config.GOOGLE_SERVICE_ACCOUNT_JSON),
        # Env introspection (names + lengths only, never values) to pinpoint
        # missing/misnamed/empty variables on the running container.
        "env_mm_keys": sorted([k for k in os.environ if "MM" in k.upper()]),
        "env_MM_TOKEN_len": len(os.environ.get("MM_TOKEN", "")),
        "env_MM_SERVER_URL_present": "MM_SERVER_URL" in os.environ,
        "config_MM_TOKEN_len": len(config.MM_TOKEN or ""),
    }
    # Live probe: which channels can the token see?
    if config.MM_TOKEN and config.MM_SERVER_URL:
        try:
            import httpx
            from services.mattermost_sync import _bot_channels
            async with httpx.AsyncClient(
                base_url=config.MM_SERVER_URL.rstrip("/"),
                headers={"Authorization": f"Bearer {config.MM_TOKEN}"},
                timeout=20.0,
            ) as client:
                chans = await _bot_channels(client)
                out["mm_channels_visible"] = [
                    {"id": c["id"], "name": c.get("display_name") or c.get("name")} for c in chans
                ]
        except Exception as e:
            out["mm_probe_error"] = repr(e)
    # Live Gmail probe: build the service for one user (DWD impersonation) and
    # read their profile — surfaces JSON / DWD-authorization / API-enabled errors.
    if config.GOOGLE_SERVICE_ACCOUNT_JSON:
        try:
            import asyncio as _asyncio
            from services.gmail_sync import _build_service
            emails = await models.get_user_emails()
            out["gmail_user_count"] = len(emails)
            probe = gmail_user or (emails[0] if emails else None)
            if probe:
                loop = _asyncio.get_running_loop()
                prof = await loop.run_in_executor(
                    None, lambda: _build_service(probe).users().getProfile(userId="me").execute()
                )
                out["gmail_probe_user"] = probe
                out["gmail_probe_ok"] = True
                out["gmail_history_id"] = prof.get("historyId")
                out["gmail_messages_total"] = prof.get("messagesTotal")
        except Exception as e:
            out["gmail_probe_error"] = repr(e)

    if run:
        try:
            from services.mattermost_sync import sync_mattermost
            out["mm_ingested_now"] = await sync_mattermost()
        except Exception as e:
            out["mm_sync_error"] = repr(e)
        try:
            from services.gmail_sync import sync_gmail
            out["gmail_ingested_now"] = await sync_gmail()
        except Exception as e:
            out["gmail_sync_error"] = repr(e)
    out["stats"] = await models.comms_stats()
    return out


def _parse_date(s: str | None, end: bool = False):
    if not s:
        return None
    try:
        if len(s) == 10:  # 'YYYY-MM-DD' → start/end of day
            s = s + ("T23:59:59" if end else "T00:00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


# ── Manual sync trigger (for testing / on demand) ─────────────────────────────

@router.post("/comms/sync")
async def trigger_comms_sync(admin: AdminUser):
    """Run Mattermost + Gmail ingestion now (background). Returns immediately."""
    import asyncio
    from services.mattermost_sync import sync_mattermost
    from services.gmail_sync import sync_gmail
    asyncio.create_task(sync_mattermost())
    asyncio.create_task(sync_gmail())
    return {"ok": True, "message": "Sync started"}


# ── Data browse endpoints ─────────────────────────────────────────────────────

@router.get("/mm/messages")
async def mm_messages(
    admin: AdminUser,
    channel_id: str | None = None,
    user_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    rows = await models.query_mm_messages(
        channel_id=channel_id, user_id=user_id,
        date_from=_parse_date(date_from), date_to=_parse_date(date_to, end=True),
        q=q, limit=min(limit, 200), offset=offset,
    )
    return {"messages": rows, "limit": limit, "offset": offset}


@router.get("/mm/channels")
async def mm_channels(admin: AdminUser):
    return {"channels": await models.distinct_mm_channels()}


@router.get("/email/messages")
async def email_messages(
    admin: AdminUser,
    user_email: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    rows = await models.query_email_messages(
        user_email=user_email,
        date_from=_parse_date(date_from), date_to=_parse_date(date_to, end=True),
        q=q, limit=min(limit, 200), offset=offset,
    )
    return {"messages": rows, "limit": limit, "offset": offset}


@router.get("/email/users")
async def email_users(admin: AdminUser):
    return {"users": await models.distinct_email_users()}


# ── AI chat over the ingested data ────────────────────────────────────────────

class ContextFilters(BaseModel):
    sources: list[str] = ["mattermost", "gmail"]
    date_from: str | None = None
    date_to: str | None = None
    channel_id: str | None = None
    user_email: str | None = None


class AiChatRequest(BaseModel):
    question: str
    context_filters: ContextFilters = ContextFilters()
    conversation_history: list[dict] = []


def _fmt_dt(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if hasattr(dt, "strftime") else str(dt)[:16]


@router.post("/ai/chat")
async def ai_chat(req: AiChatRequest, admin: AdminUser):
    f = req.context_filters
    df, dt = _parse_date(f.date_from), _parse_date(f.date_to, end=True)

    def mm_line(m):
        return (f"[MM] {_fmt_dt(m['created_at'])} @{m.get('username') or '—'} "
                f"в #{m.get('channel_name') or m['channel_id']}: {m['message']}")

    def em_line(e):
        return (f"[EMAIL] {_fmt_dt(e['received_at'])} от {e.get('from_email') or '—'} "
                f"кому {', '.join(e.get('to_emails') or [])} / Тема: {e.get('subject') or ''} / "
                f"{(e.get('body_text') or '')[:1000]}")

    q_text = (req.question or "").strip()
    PER_SOURCE = 200
    items: list[tuple[float, object, str]] = []  # (relevance, datetime, line)

    if "mattermost" in f.sources:
        # Relevance-ranked over the whole period; fall back to recent if the
        # question yields no lexical match (e.g. generic "что обсуждали?").
        rows = await models.search_mm_messages(q_text, df, dt, f.channel_id, PER_SOURCE) if q_text else []
        if not rows:
            rows = await models.query_mm_messages(channel_id=f.channel_id, date_from=df, date_to=dt, limit=PER_SOURCE)
        for m in rows:
            items.append((float(m.get("rank") or 0), m["created_at"], mm_line(m)))
    if "gmail" in f.sources:
        rows = await models.search_email_messages(q_text, df, dt, f.user_email, PER_SOURCE) if q_text else []
        if not rows:
            rows = await models.query_email_messages(user_email=f.user_email, date_from=df, date_to=dt, limit=PER_SOURCE)
        for e in rows:
            items.append((float(e.get("rank") or 0), e["received_at"], em_line(e)))

    if not items:
        return {"answer": "Нет данных за выбранный период."}

    # Keep the MOST RELEVANT under the char budget (so relevant older messages
    # survive instead of being dropped by recency), then present newest-first.
    items.sort(key=lambda x: (x[0], x[1]), reverse=True)
    budget = config.CHAT_MAX_CONTEXT_CHARS
    used, kept = 0, []
    for _rank, dtv, ln in items:
        if used + len(ln) > budget:
            break
        kept.append((dtv, ln))
        used += len(ln)
    kept.sort(key=lambda x: x[0], reverse=True)
    context = "\n".join(ln for _, ln in kept)

    system = (
        "Ты — аналитик коммуникаций компании. Отвечай на вопросы СТРОГО на основе "
        "предоставленных данных из Mattermost и почты. Если ответа в данных нет — так и "
        "скажи. Отвечай на русском языке, кратко и по делу.\n\n"
        f"ДАННЫЕ (новые сверху):\n{context}"
    )
    messages = [{"role": "system", "content": system}]
    for m in req.conversation_history[-10:]:
        if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str):
            messages.append({"role": m["role"], "content": m["content"]})
    messages.append({"role": "user", "content": req.question})

    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    try:
        resp = await client.chat.completions.create(
            model=config.CHAT_MODEL, messages=messages, max_tokens=1200, temperature=0.3,
        )
        return {"answer": resp.choices[0].message.content}
    except Exception as e:
        logger.error("comms ai chat error: %s", e)
        return {"answer": None, "error": str(e)}
