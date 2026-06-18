"""Unified question-answering over meetings + Mattermost + email.

Used by the live in-meeting assistant. Two access scopes:
- "full": archive of past meetings + Mattermost + email (internal meetings).
- "meeting_only": ONLY the current meeting's live transcript (external meetings)
  — nothing from the archive/email/MM leaks to outside guests.

Pure helpers (wake-word match, scope selection, context packing) are separated
out so they can be unit-tested without a DB or audio.
"""
import logging
import re
from datetime import datetime, timedelta, timezone

from config import config
from database import models

logger = logging.getLogger(__name__)

_NON_LETTER = re.compile(r"[^0-9a-zа-яё]+", re.IGNORECASE)


def _normalize(text: str) -> str:
    return _NON_LETTER.sub(" ", (text or "").lower().replace("ё", "е")).strip()


def wake_stem(wake_word: str) -> str:
    """A tolerant stem of the wake word — ASR often mangles the ending
    («протоколлер» → «протокол лер» / «протоколер»). Match on the first letters."""
    w = _normalize(wake_word).replace(" ", "")
    return w[: max(5, len(w) - 3)] if w else ""


def contains_wake_word(text: str, wake_word: str) -> bool:
    return bool(wake_stem(wake_word)) and wake_stem(wake_word) in _normalize(text).replace(" ", "")


def strip_wake_word(text: str, wake_word: str) -> str:
    """Return the part of `text` AFTER the wake word (the actual question)."""
    stem = wake_stem(wake_word)
    norm = _normalize(text)
    idx = norm.replace(" ", "").find(stem) if stem else -1
    if idx < 0:
        return text.strip()
    # Map the collapsed-index back roughly: drop tokens up to the one containing the stem.
    tokens = norm.split()
    out, seen = [], ""
    cut = False
    for tok in tokens:
        if not cut:
            seen += tok
            if stem in seen:
                cut = True
            continue
        out.append(tok)
    return " ".join(out).strip() or text.strip()


def select_scope(attendee_emails: list[str], internal_domain: str, full_override: bool) -> str:
    """'full' if the host force-allowed it OR every known attendee is internal;
    otherwise 'meeting_only'. Unknown/empty attendee list → conservative
    'meeting_only'."""
    if full_override:
        return "full"
    emails = [e.strip().lower() for e in (attendee_emails or []) if e and "@" in e]
    if not emails:
        return "meeting_only"
    dom = "@" + internal_domain.lower().lstrip("@")
    return "full" if all(e.endswith(dom) for e in emails) else "meeting_only"


def pack_context(items: list[tuple[float, object, str]], budget: int) -> str:
    """Keep the most relevant lines under the char budget, present newest-first.
    `items` = (relevance_rank, datetime, formatted_line)."""
    items = sorted(items, key=lambda x: (x[0], x[1]), reverse=True)
    used, kept = 0, []
    for _rank, dtv, line in items:
        if used + len(line) > budget:
            break
        kept.append((dtv, line))
        used += len(line)
    kept.sort(key=lambda x: x[0], reverse=True)
    return "\n".join(line for _, line in kept)


def _fmt_dt(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if hasattr(dt, "strftime") else str(dt)[:16]


_SYSTEM_VOICE = (
    "Ты — ассистент на рабочей встрече. На вопрос отвечай ОЧЕНЬ кратко (1–2 "
    "предложения), это будет произнесено голосом. Опирайся СТРОГО на данные ниже; "
    "если ответа в них нет — коротко скажи, что не нашёл. На русском.\n\n"
    "ДАННЫЕ (новые сверху):\n{context}"
)


async def answer_question(
    question: str,
    *,
    scope: str,
    live_transcript: str = "",
    days: int = 90,
) -> tuple[str, list[str]]:
    """Returns (answer_text, sources_used)."""
    q = (question or "").strip()
    if not q:
        return "", []

    budget = config.CHAT_MAX_CONTEXT_CHARS
    sources_used: list[str] = []

    if scope == "meeting_only":
        context = (live_transcript or "").strip()[:budget]
        if context:
            sources_used.append("meeting")
        context = context or "Пока в этой встрече ничего не сказано."
    else:
        now = datetime.now(timezone.utc)
        df = now - timedelta(days=days)
        items: list[tuple[float, object, str]] = []
        # Meetings archive (no FTS — recency-ranked)
        try:
            for m in await models.get_recent_meetings_with_transcripts(days=days):
                if m.get("transcript"):
                    line = f"[ВСТРЕЧА] {_fmt_dt(m['start_time'])} «{m.get('title') or m.get('topic') or '—'}»: {m['transcript']}"
                    items.append((0.0, m["start_time"], line))
                    sources_used.append("meetings")
        except Exception as e:
            logger.warning("qa: meetings retrieval failed: %s", e)
        # Mattermost (FTS)
        try:
            for mm in await models.search_mm_messages(q, df, now, None, 200):
                line = f"[MM] {_fmt_dt(mm['created_at'])} @{mm.get('username') or '—'} в #{mm.get('channel_name') or mm['channel_id']}: {mm['message']}"
                items.append((float(mm.get("rank") or 0), mm["created_at"], line))
                sources_used.append("mattermost")
        except Exception as e:
            logger.warning("qa: mm search failed: %s", e)
        # Email (FTS)
        try:
            for em in await models.search_email_messages(q, df, now, None, 200):
                line = f"[EMAIL] {_fmt_dt(em['received_at'])} от {em.get('from_email') or '—'} / Тема: {em.get('subject') or ''} / {(em.get('body_text') or '')[:1000]}"
                items.append((float(em.get("rank") or 0), em["received_at"], line))
                sources_used.append("email")
        except Exception as e:
            logger.warning("qa: email search failed: %s", e)

        live = (live_transcript or "").strip()
        if live:
            items.append((1e9, now, f"[ТЕКУЩАЯ ВСТРЕЧА] {live}"))  # always keep
            sources_used.append("meeting")
        context = pack_context(items, budget) or "Нет данных."

    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    resp = await client.chat.completions.create(
        model=config.CHAT_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_VOICE.format(context=context)},
            {"role": "user", "content": q},
        ],
        max_tokens=220,
        temperature=0.3,
    )
    return (resp.choices[0].message.content or "").strip(), sorted(set(sources_used))
