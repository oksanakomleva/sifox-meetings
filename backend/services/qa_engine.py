"""Unified question-answering over meetings + Mattermost + email.

Used by the live in-meeting assistant. Two access scopes:
- "full": archive of past meetings + Mattermost + email (internal meetings).
- "meeting_only": ONLY the current meeting's live transcript (external meetings)
  — nothing from the archive/email/MM leaks to outside guests.

Pure helpers (wake-word match, scope selection, context packing) are separated
out so they can be unit-tested without a DB or audio.
"""
import asyncio
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
    """Normalized wake word kept for backwards-compatible helper imports."""
    return _normalize(wake_word).replace(" ", "")


def _wake_variants(wake_word: str) -> tuple[str, ...]:
    """Known ASR-safe variants without matching the ordinary word «протокол»."""
    exact = wake_stem(wake_word)
    if not exact:
        return ()
    variants = {exact}
    # Whisper often collapses a doubled consonant: протоколлер → протоколер.
    for doubled in ("лл", "нн", "сс", "тт"):
        variants.add(exact.replace(doubled, doubled[0]))
    return tuple(sorted((v for v in variants if v), key=len, reverse=True))


def contains_wake_word(text: str, wake_word: str) -> bool:
    collapsed = _normalize(text).replace(" ", "")
    return any(variant in collapsed for variant in _wake_variants(wake_word))


def strip_wake_word(text: str, wake_word: str) -> str:
    """Return the part of `text` AFTER the wake word (the actual question)."""
    variants = _wake_variants(wake_word)
    norm = _normalize(text)
    collapsed = norm.replace(" ", "")
    if not any(variant in collapsed for variant in variants):
        return text.strip()
    # Map the collapsed-index back roughly: drop tokens up to the one containing the stem.
    tokens = norm.split()
    out, seen = [], ""
    cut = False
    for tok in tokens:
        if not cut:
            seen += tok
            if any(variant in seen for variant in variants):
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


_SEARCH_FILLERS = {
    "скажи", "подскажи", "расскажи", "покажи", "найди", "пожалуйста",
    "можешь", "можно", "мне", "нам", "информацию", "известно",
    "какого", "какая", "какой", "что", "кто", "где", "когда", "как",
    "до", "ли", "про", "о", "об", "это", "эта", "этой",
    "говорили", "обсуждали", "протоколлер",
}


def build_search_query(question: str) -> str:
    """Remove conversational filler while preserving names and subject terms."""
    normalized = _normalize(question)
    meaningful = [
        token
        for token in normalized.split()
        if len(token) > 1 and token not in _SEARCH_FILLERS
    ]
    return " ".join(meaningful) or normalized


def make_search_snippet(text: str, query: str, max_chars: int = 900) -> str:
    """Return a compact excerpt around a query token, not just the text prefix."""
    compact = re.sub(r"\s+", " ", text or "").strip()
    if len(compact) <= max_chars:
        return compact

    lowered = compact.lower().replace("ё", "е")
    positions: list[int] = []
    for token in build_search_query(query).split():
        # Prefixes also match common Russian inflections:
        # Клевицкий → Клевицкого, Сергей → Сергея.
        needle = token[: max(4, min(len(token), 7))]
        if len(needle) < 4:
            continue
        position = lowered.find(needle)
        if position >= 0:
            positions.append(position)
    center = min(positions) if positions else 0
    start = max(0, center - max_chars // 3)
    end = min(len(compact), start + max_chars)
    start = max(0, end - max_chars)
    snippet = compact[start:end].strip()
    return f"{'…' if start else ''}{snippet}{'…' if end < len(compact) else ''}"


def format_meeting_metadata(
    meeting: dict | None,
    attendee_emails: list[str],
) -> str:
    """Stable facts about the current meeting that live STT cannot know."""
    if not meeting:
        return ""
    parts = [
        f"Название: {meeting.get('title') or meeting.get('topic') or 'не указано'}",
        f"Начало: {_fmt_dt(meeting.get('start_time'))}",
    ]
    attendees = sorted({
        email.strip().lower()
        for email in attendee_emails
        if email and "@" in email
    })
    if attendees:
        parts.append(f"Участники: {', '.join(attendees)}")
    return "; ".join(parts)


def pack_context(items: list[tuple[float, object, str]], budget: int) -> str:
    """Keep the most relevant lines under the char budget, present newest-first.
    `items` = (relevance_rank, datetime, formatted_line)."""
    items = sorted(items, key=lambda x: (x[0], x[1]), reverse=True)
    used, kept = 0, []
    for _rank, dtv, line in items:
        if used + len(line) > budget:
            continue
        kept.append((dtv, line))
        used += len(line)
    kept.sort(key=lambda x: x[0], reverse=True)
    return "\n".join(line for _, line in kept)


def _fmt_dt(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if hasattr(dt, "strftime") else str(dt)[:16]


_SYSTEM_VOICE = (
    "Ты — ассистент на рабочей встрече. На вопрос отвечай ОЧЕНЬ кратко (1–2 "
    "предложения), это будет произнесено голосом. Опирайся СТРОГО на данные ниже; "
    "если ответа в них нет — коротко скажи, что не нашёл. СНАЧАЛА ищи прямой "
    "ответ в блоке [ТЕКУЩАЯ ВСТРЕЧА]. Если он там есть, используй его и игнорируй "
    "противоречащие сведения из старых встреч, почты и Mattermost. К архиву "
    "обращайся только если в текущей встрече ответа нет. «Протоколлер» — имя "
    "ассистента и ключевое слово обращения, оно никогда не является частью "
    "названия проекта или другого соседнего факта. На русском.\n\n"
    "ДАННЫЕ (новые сверху):\n{context}"
)


async def answer_question(
    question: str,
    *,
    scope: str,
    live_transcript: str = "",
    meeting_metadata: str = "",
    days: int = 90,
    budget: int | None = None,
) -> tuple[str, list[str], list[dict], str]:
    """Return answer, source names, admin-visible excerpts and search query."""
    q = (question or "").strip()
    if not q:
        return "", [], [], ""

    budget = budget or config.CHAT_MAX_CONTEXT_CHARS
    search_query = build_search_query(q)
    sources_used: list[str] = []
    source_details: list[dict] = []
    metadata = (meeting_metadata or "").strip()

    if scope == "meeting_only":
        context_parts = []
        if metadata:
            context_parts.append(f"[ТЕКУЩАЯ ВСТРЕЧА: МЕТАДАННЫЕ] {metadata}")
            sources_used.append("meeting")
            source_details.append({
                "source": "meeting",
                "label": "Текущая встреча",
                "snippet": metadata,
            })
        live = (live_transcript or "").strip()
        if live:
            live_snippet = make_search_snippet(
                live,
                search_query,
                min(4_000, budget),
            )
            context_parts.append(
                f"[ТЕКУЩАЯ ВСТРЕЧА: РАЗГОВОР] {live_snippet}"
            )
            if "meeting" not in sources_used:
                sources_used.append("meeting")
            source_details.append({
                "source": "meeting",
                "label": "Разговор текущей встречи",
                "snippet": make_search_snippet(live, search_query, 500),
            })
        context = "\n".join(context_parts)[:budget]
        context = context or "Пока в этой встрече ничего не сказано."
    else:
        now = datetime.now(timezone.utc)
        df = now - timedelta(days=days)
        items: list[tuple[float, object, str]] = []
        meetings_result, mm_result, email_result = await asyncio.gather(
            models.search_meeting_transcripts(search_query, df, now, 30),
            models.search_mm_messages(search_query, df, now, None, 60),
            models.search_email_messages(search_query, df, now, None, 60),
            return_exceptions=True,
        )
        # Query-centred excerpts avoid dropping a whole long transcript when it
        # exceeds the live context budget.
        if isinstance(meetings_result, Exception):
            logger.warning("qa: meetings retrieval failed: %s", meetings_result)
        else:
            for m in meetings_result[: config.LIVE_MEETINGS_LIMIT]:
                if m.get("transcript"):
                    snippet = make_search_snippet(
                        m["transcript"],
                        search_query,
                        1_200,
                    )
                    title = m.get("title") or m.get("topic") or "—"
                    line = (
                        f"[ВСТРЕЧА] {_fmt_dt(m['start_time'])} "
                        f"«{title}»: {snippet}"
                    )
                    items.append((float(m.get("rank") or 0), m["start_time"], line))
                    sources_used.append("meetings")
                    if sum(
                        d["source"] == "meetings" for d in source_details
                    ) < 3:
                        source_details.append({
                            "source": "meetings",
                            "label": f"Встреча «{title}»",
                            "snippet": make_search_snippet(
                                snippet,
                                search_query,
                                500,
                            ),
                            "timestamp": str(m.get("start_time") or ""),
                        })
        # Mattermost (FTS)
        if isinstance(mm_result, Exception):
            logger.warning("qa: mm search failed: %s", mm_result)
        else:
            for mm in mm_result:
                snippet = make_search_snippet(
                    mm.get("message") or "",
                    search_query,
                    900,
                )
                channel = mm.get("channel_name") or mm["channel_id"]
                username = mm.get("username") or "—"
                line = (
                    f"[MM] {_fmt_dt(mm['created_at'])} "
                    f"@{username} в #{channel}: {snippet}"
                )
                items.append((float(mm.get("rank") or 0), mm["created_at"], line))
                sources_used.append("mattermost")
                if sum(
                    d["source"] == "mattermost" for d in source_details
                ) < 3:
                    source_details.append({
                        "source": "mattermost",
                        "label": f"Mattermost #{channel} · @{username}",
                        "snippet": make_search_snippet(
                            snippet,
                            search_query,
                            500,
                        ),
                        "timestamp": str(mm.get("created_at") or ""),
                    })
        # Email (FTS)
        if isinstance(email_result, Exception):
            logger.warning("qa: email search failed: %s", email_result)
        else:
            for em in email_result:
                searchable = (
                    f"{em.get('subject') or ''}\n{em.get('body_text') or ''}"
                )
                snippet = make_search_snippet(
                    searchable,
                    search_query,
                    1_000,
                )
                sender = em.get("from_email") or "—"
                subject = em.get("subject") or "Без темы"
                line = (
                    f"[EMAIL] {_fmt_dt(em['received_at'])} от {sender} / "
                    f"Тема: {subject} / {snippet}"
                )
                items.append((float(em.get("rank") or 0), em["received_at"], line))
                sources_used.append("email")
                if sum(d["source"] == "email" for d in source_details) < 3:
                    source_details.append({
                        "source": "email",
                        "label": f"Письмо «{subject}» · {sender}",
                        "snippet": make_search_snippet(
                            snippet,
                            search_query,
                            500,
                        ),
                        "timestamp": str(em.get("received_at") or ""),
                    })

        live = (live_transcript or "").strip()
        if metadata:
            items.append((
                1e10,
                now,
                f"[ТЕКУЩАЯ ВСТРЕЧА: МЕТАДАННЫЕ] {metadata}",
            ))
            sources_used.append("meeting")
            source_details.insert(0, {
                "source": "meeting",
                "label": "Текущая встреча",
                "snippet": metadata,
            })
        if live:
            items.append((
                1e9,
                now,
                f"[ТЕКУЩАЯ ВСТРЕЧА: РАЗГОВОР] "
                f"{make_search_snippet(live, search_query, 4_000)}",
            ))
            sources_used.append("meeting")
            source_details.insert(1 if metadata else 0, {
                "source": "meeting",
                "label": "Разговор текущей встречи",
                "snippet": make_search_snippet(live, search_query, 500),
            })
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
    return (
        (resp.choices[0].message.content or "").strip(),
        sorted(set(sources_used)),
        source_details[:10],
        search_query,
    )
