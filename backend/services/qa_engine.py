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
    return " ".join(out).strip()


_NOTE_COMMAND = re.compile(
    r"^\s*запиши(?:\s*,?\s*пожалуйста)?(?:\s+(?:в\s+)?(?:заметки?|протокол))?"
    r"\s*[:,—-]?\s*(.*)$",
    re.IGNORECASE,
)


def parse_note_command(question: str) -> tuple[bool, str]:
    """Recognize «запиши …» and return the exact dictated note body."""
    match = _NOTE_COMMAND.match(question or "")
    if not match:
        return False, ""
    note = re.sub(r"\s+", " ", match.group(1)).strip(" \t,.;:—-")
    return True, note


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
    "до", "ли", "про", "о", "об", "это", "эта", "этой", "чем", "там",
    "за", "нас", "на", "по",
    "говорили", "обсуждали", "протоколлер",
}

_SEARCH_EXPANSIONS = {
    "гпб": ("гпбм", "газпромбанк"),
    "гпбм": ("гпб", "газпромбанк"),
    "оман": ("омантел",),
    "оману": ("омантел",),
    "омане": ("омантел",),
    "синк": ("встреча", "созвон"),
    "прошел": ("итоги", "результат"),
    "прошёл": ("итоги", "результат"),
    "прошла": ("итоги", "результат"),
    "обсудили": ("итоги", "договорились"),
}


def build_search_query(question: str) -> str:
    """Remove conversational filler and add a few domain search aliases."""
    normalized = _normalize(question)
    meaningful = [
        token
        for token in normalized.split()
        if len(token) > 1 and token not in _SEARCH_FILLERS
    ]
    expanded = list(meaningful)
    for token in meaningful:
        for alias in _SEARCH_EXPANSIONS.get(token, ()):
            if alias not in expanded:
                expanded.append(alias)
    meaningful = expanded
    return " ".join(meaningful) or normalized


_SEARCH_PRIORITY_GENERIC = {
    "встреча", "встрече", "встречу", "созвон", "синк",
    "обсудили", "обсуждали", "обсуждал", "договорились", "договаривались",
    "итоги", "результат", "результаты", "метрика", "метрики", "метрикам",
    "неделя", "неделе", "неделю", "прошлая", "прошлой", "текущая", "текущей",
    "прошел", "прошёл", "прошла", "отпуск", "отпуске", "уходит",
    "называется", "название", "личная", "личную",
}


def build_priority_search_query(question: str) -> str:
    """Pick subject/name terms that broad FTS must not bury under common words."""
    normalized = _normalize(question)
    base_tokens = [
        token
        for token in normalized.split()
        if (
            len(token) > 1
            and token not in _SEARCH_FILLERS
            and token not in _SEARCH_PRIORITY_GENERIC
        )
    ]
    if not base_tokens:
        return ""
    # Two trailing terms preserve full names ("Сергей Клевицкий"), while a
    # project acronym such as ГПБМ stays an exact one-term priority query.
    return " ".join(base_tokens[-2:])


def _query_needles(query: str) -> list[str]:
    needles = []
    for token in build_search_query(query).split():
        if len(token) < 3:
            continue
        # Prefix matching covers common Russian inflections while preserving
        # short project acronyms such as ГПБ.
        needle = token if len(token) <= 4 else token[: min(len(token), 7)]
        if needle not in needles:
            needles.append(needle)
    return needles


def _exact_query_needles(query: str) -> list[str]:
    needles = []
    for token in _normalize(query).split():
        if len(token) < 3:
            continue
        needle = token if len(token) <= 4 else token[: min(len(token), 7)]
        if needle not in needles:
            needles.append(needle)
    return needles


def make_search_snippet(text: str, query: str, max_chars: int = 900) -> str:
    """Return an excerpt around the most specific match, including acronyms."""
    compact = re.sub(r"\s+", " ", text or "").strip()
    if len(compact) <= max_chars:
        return compact

    lowered = compact.lower().replace("ё", "е")
    candidates: list[tuple[int, int, int]] = []
    for index, needle in enumerate(_query_needles(query)):
        matches = [m.start() for m in re.finditer(re.escape(needle), lowered)]
        if matches:
            # Prefer a rarer term. On ties prefer the later query term, which is
            # commonly the subject/name/acronym rather than conversational text.
            candidates.append((len(matches), -index, matches[0]))
    center = min(candidates)[2] if candidates else 0
    start = max(0, center - max_chars // 3)
    end = min(len(compact), start + max_chars)
    start = max(0, end - max_chars)
    snippet = compact[start:end].strip()
    return f"{'…' if start else ''}{snippet}{'…' if end < len(compact) else ''}"


def relevance_score(
    query: str,
    text: str,
    db_rank: float = 0.0,
    priority_query: str = "",
) -> float:
    """Comparable in-process score based on query-term coverage."""
    haystack = _normalize(text)
    needles = _query_needles(query)
    if not needles:
        return db_rank
    matches = sum(1 for needle in needles if needle in haystack)
    coverage = matches / len(needles)
    priority_needles = _exact_query_needles(priority_query)
    priority_match = bool(priority_needles) and all(
        needle in haystack for needle in priority_needles
    )
    return (
        (2_000 if priority_match else 0)
        + coverage * 1_000
        + matches * 25
        + float(db_rank or 0)
    )


_LIVE_NOISE_PATTERNS = (
    re.compile(r"\bпродолжение\s+следует\b[.…\s]*", re.IGNORECASE),
    re.compile(r"\bпродолжение\s+в\s+следующей\s+части\b[.…\s]*", re.IGNORECASE),
    re.compile(
        r"\bредактор\s+субтитров\s+(?:[а-яё]\.?\s*)?[а-яё-]{2,}\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bкорректор\s+(?:[а-яё]\.?\s*)?[а-яё-]{2,}\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bне\s+забудьте\s+(?:поставить\s+лайк\s+и\s+)?подписаться\s+"
        r"на\s+(?:канал|новые\s+видео)\b[.!…\s]*",
        re.IGNORECASE,
    ),
    re.compile(r"\bподписывайтесь\s+на\s+канал\b[.!…\s]*", re.IGNORECASE),
    re.compile(r"(?:\bwriting\s+with\s+apple\b[\s,;.!…]*){2,}", re.IGNORECASE),
)


def clean_live_transcript(text: str) -> str:
    """Remove recurring media/subtitle credits that drown useful live speech."""
    cleaned = text or ""
    for pattern in _LIVE_NOISE_PATTERNS:
        cleaned = pattern.sub(" ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


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


_CONTEXT_SOURCE_ORDER = ("mattermost", "email", "meetings")
_CONTEXT_SOURCE_SHARES = {
    "mattermost": 0.34,
    "email": 0.32,
    "meetings": 0.34,
}


def pack_context_with_quotas(
    current_items: list[dict],
    source_items: dict[str, list[dict]],
    budget: int,
) -> tuple[str, list[dict]]:
    """Keep current facts plus a guaranteed, balanced slice of every source."""
    selected: list[dict] = []
    selected_ids: set[int] = set()
    used = 0

    def add(item: dict) -> bool:
        nonlocal used
        line = item["line"]
        if id(item) in selected_ids or used + len(line) > budget:
            return False
        selected.append(item)
        selected_ids.add(id(item))
        used += len(line)
        return True

    for item in current_items:
        add(item)

    groups = {
        source: sorted(
            source_items.get(source, []),
            key=lambda item: (item["rank"], str(item["dt"])),
            reverse=True,
        )
        for source in _CONTEXT_SOURCE_ORDER
    }
    active = [source for source in _CONTEXT_SOURCE_ORDER if groups[source]]
    archive_budget = max(0, budget - used)
    source_used = {source: 0 for source in active}
    indexes = {source: 0 for source in active}

    # First guarantee at least one candidate from each available source.
    for source in active:
        item = groups[source][0]
        if add(item):
            source_used[source] += len(item["line"])
        indexes[source] = 1

    # Then fill a fair per-source quota. This prevents meeting transcripts from
    # evicting a short, exact Mattermost post or email.
    for source in active:
        quota = int(archive_budget * _CONTEXT_SOURCE_SHARES[source])
        group = groups[source]
        while indexes[source] < len(group):
            item = group[indexes[source]]
            if source_used[source] + len(item["line"]) > quota:
                break
            if add(item):
                source_used[source] += len(item["line"])
            indexes[source] += 1

    # Redistribute any unused quota round-robin without letting one source take
    # the entire remainder.
    made_progress = True
    while made_progress:
        made_progress = False
        for source in active:
            group = groups[source]
            while indexes[source] < len(group):
                item = group[indexes[source]]
                indexes[source] += 1
                if add(item):
                    made_progress = True
                    break

    return "\n".join(item["line"] for item in selected), selected


def _fmt_dt(dt) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if hasattr(dt, "strftime") else str(dt)[:16]


_SYSTEM_VOICE = (
    "Ты — ассистент на рабочей встрече. На вопрос отвечай ОЧЕНЬ кратко (1–2 "
    "предложения), это будет произнесено голосом. Опирайся СТРОГО на данные ниже; "
    "если ответа в них нет — коротко скажи, что не нашёл. Если вопрос прямо "
    "относится к текущей встрече, сначала используй блок [ТЕКУЩАЯ ВСТРЕЧА]. "
    "Если пользователь спрашивает о другой/прошлой встрече, проекте, Mattermost "
    "или почте, обязательно используй подходящие блоки [АРХИВ] и НЕ отвечай, "
    "что данных нет только потому, что их нет в текущей встрече. Точное сообщение "
    "с именем, датой или числом важнее общего похожего фрагмента. «Протоколлер» — имя "
    "ассистента и ключевое слово обращения, оно никогда не является частью "
    "названия проекта или другого соседнего факта. На русском.\n\n"
    "ДАННЫЕ (текущая встреча сначала, затем архив по релевантности):\n{context}"
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
    priority_query = build_priority_search_query(q)
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
                "used": True,
            })
        live = clean_live_transcript(live_transcript)
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
                "used": True,
            })
        context = "\n".join(context_parts)[:budget]
        context = context or "Пока в этой встрече ничего не сказано."
    else:
        now = datetime.now(timezone.utc)
        df = now - timedelta(days=days)
        source_items: dict[str, list[dict]] = {
            "meetings": [],
            "mattermost": [],
            "email": [],
        }
        current_items: list[dict] = []
        meetings_result, mm_result, email_result = await asyncio.gather(
            models.search_meeting_transcripts(
                search_query,
                df,
                now,
                30,
                priority_query=priority_query,
            ),
            models.search_mm_messages(
                search_query,
                df,
                now,
                None,
                60,
                priority_query=priority_query,
            ),
            models.search_email_messages(
                search_query,
                df,
                now,
                None,
                60,
                priority_query=priority_query,
            ),
            return_exceptions=True,
        )
        # Query-centred excerpts avoid dropping a whole long transcript when it
        # exceeds the live context budget.
        if isinstance(meetings_result, Exception):
            logger.warning("qa: meetings retrieval failed: %s", meetings_result)
        else:
            seen_meetings = set()
            for m in meetings_result[: config.LIVE_MEETINGS_LIMIT]:
                if m.get("transcript"):
                    meeting_key = m.get("id") or (
                        m.get("title"),
                        m.get("start_time"),
                    )
                    if meeting_key in seen_meetings:
                        continue
                    seen_meetings.add(meeting_key)
                    snippet = make_search_snippet(
                        m["transcript"],
                        search_query,
                        1_200,
                    )
                    title = m.get("title") or m.get("topic") or "—"
                    line = (
                        f"[АРХИВ: ВСТРЕЧА] {_fmt_dt(m['start_time'])} "
                        f"«{title}»: {snippet}"
                    )
                    detail = {
                        "source": "meetings",
                        "label": f"Встреча «{title}»",
                        "snippet": make_search_snippet(
                            snippet,
                            search_query,
                            500,
                        ),
                        "timestamp": str(m.get("start_time") or ""),
                        "used": False,
                    }
                    source_items["meetings"].append({
                        "source": "meetings",
                        "rank": relevance_score(
                            search_query,
                            f"{title} {snippet}",
                            float(m.get("rank") or 0),
                            priority_query,
                        ),
                        "dt": m["start_time"],
                        "line": line,
                        "detail": detail,
                    })
        # Mattermost (FTS)
        if isinstance(mm_result, Exception):
            logger.warning("qa: mm search failed: %s", mm_result)
        else:
            seen_mm = set()
            for mm in mm_result:
                raw_message = mm.get("message") or ""
                snippet = make_search_snippet(
                    raw_message,
                    search_query,
                    900,
                )
                channel = mm.get("channel_name") or mm["channel_id"]
                username = mm.get("username") or "—"
                mm_key = (
                    channel,
                    username,
                    _normalize(raw_message)[:1_000],
                )
                if mm_key in seen_mm:
                    continue
                seen_mm.add(mm_key)
                line = (
                    f"[АРХИВ: MATTERMOST] {_fmt_dt(mm['created_at'])} "
                    f"@{username} в #{channel}: {snippet}"
                )
                detail = {
                    "source": "mattermost",
                    "label": f"Mattermost #{channel} · @{username}",
                    "snippet": make_search_snippet(
                        snippet,
                        search_query,
                        500,
                    ),
                    "timestamp": str(mm.get("created_at") or ""),
                    "used": False,
                }
                source_items["mattermost"].append({
                    "source": "mattermost",
                    "rank": relevance_score(
                        search_query,
                        f"{channel} {username} {raw_message}",
                        float(mm.get("rank") or 0),
                        priority_query,
                    ),
                    "dt": mm["created_at"],
                    "line": line,
                    "detail": detail,
                })
        # Email (FTS)
        if isinstance(email_result, Exception):
            logger.warning("qa: email search failed: %s", email_result)
        else:
            seen_email = set()
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
                email_key = (
                    sender,
                    subject,
                    _normalize(searchable)[:2_000],
                )
                if email_key in seen_email:
                    continue
                seen_email.add(email_key)
                line = (
                    f"[АРХИВ: EMAIL] {_fmt_dt(em['received_at'])} от {sender} / "
                    f"Тема: {subject} / {snippet}"
                )
                detail = {
                    "source": "email",
                    "label": f"Письмо «{subject}» · {sender}",
                    "snippet": make_search_snippet(
                        snippet,
                        search_query,
                        500,
                    ),
                    "timestamp": str(em.get("received_at") or ""),
                    "used": False,
                }
                source_items["email"].append({
                    "source": "email",
                    "rank": relevance_score(
                        search_query,
                        searchable,
                        float(em.get("rank") or 0),
                        priority_query,
                    ),
                    "dt": em["received_at"],
                    "line": line,
                    "detail": detail,
                })

        live = clean_live_transcript(live_transcript)
        if metadata:
            detail = {
                "source": "meeting",
                "label": "Текущая встреча",
                "snippet": metadata,
                "used": False,
            }
            current_items.append({
                "source": "meeting",
                "rank": 1e10,
                "dt": now,
                "line": f"[ТЕКУЩАЯ ВСТРЕЧА: МЕТАДАННЫЕ] {metadata}",
                "detail": detail,
            })
        if live:
            detail = {
                "source": "meeting",
                "label": "Разговор текущей встречи",
                "snippet": make_search_snippet(live, search_query, 500),
                "used": False,
            }
            current_items.append({
                "source": "meeting",
                "rank": 1e9,
                "dt": now,
                "line": (
                    "[ТЕКУЩАЯ ВСТРЕЧА: РАЗГОВОР] "
                    f"{make_search_snippet(live, search_query, 4_000)}"
                ),
                "detail": detail,
            })

        context, selected = pack_context_with_quotas(
            current_items,
            source_items,
            budget,
        )
        context = context or "Нет данных."
        for item in selected:
            item["detail"]["used"] = True
        sources_used = sorted({item["source"] for item in selected})

        # Put actual model context first, then show a bounded set of retrieved
        # candidates that did not fit. This makes the admin diagnostic truthful.
        diagnostic_items = list(selected)
        diagnostic_items.extend(
            item for item in current_items if not item["detail"]["used"]
        )
        for source in _CONTEXT_SOURCE_ORDER:
            ordered = sorted(
                source_items[source],
                key=lambda item: (item["rank"], str(item["dt"])),
                reverse=True,
            )
            diagnostic_items.extend(
                item for item in ordered if not item["detail"]["used"]
            )
        seen_details = set()
        for item in diagnostic_items:
            detail = item["detail"]
            key = (
                detail["source"],
                detail["label"],
                detail["snippet"],
            )
            if key in seen_details:
                continue
            seen_details.add(key)
            source_details.append(detail)
            if len(source_details) >= 18:
                break

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
        source_details,
        search_query,
    )
