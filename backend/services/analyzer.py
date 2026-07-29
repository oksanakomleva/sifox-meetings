"""OpenAI analysis: meeting type, tags, structured protocol."""
import asyncio
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor

from config import config

logger = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="analyzer")

def normalize_tags(tags) -> list[str]:
    """Canonical form for tags from any source (AI or manual user input):
    lowercase, trimmed, '#' stripped, de-duplicated, empties dropped. Internal
    spaces are kept so multi-word customer/project names survive."""
    seen: set[str] = set()
    out: list[str] = []
    for t in tags or []:
        if not isinstance(t, str):
            continue
        norm = t.strip().lstrip("#").strip().lower()
        norm = " ".join(norm.split())  # collapse internal whitespace
        if norm and norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out[:15]


def _build_tagging_prompt(transcript: str, title: str | None, known_tags: list[str]) -> str:
    title_str = (title or "").strip() or "(без названия)"
    known_block = ", ".join(known_tags) if known_tags else "(пока нет)"
    return (
        "Ты — AI-ассистент для анализа рабочих встреч компании Sifox.\n\n"
        "На основе НАЗВАНИЯ встречи и транскрипта определи:\n"
        "1. Тип встречи (одно из: sales / internal / planning / review / interview / partner / demo / other).\n"
        "   demo — презентация/демонстрация: демо-дни, показ проектов или продукта аудитории, "
        "когда выступающие по очереди представляют свои работы.\n"
        "2. Краткую тему (1 строка)\n"
        "3. Теги (1–5 штук).\n\n"
        "Правила для тегов:\n"
        "- Тег — это В ПЕРВУЮ ОЧЕРЕДЬ заказчик/клиент, проект или продукт, о котором идёт речь. "
        "Не добавляй мелкие детали, общие слова и обозначения процесса (например «звонок», «обсуждение», «работа»).\n"
        "- Если заказчик/проект/продукт указан в НАЗВАНИИ встречи, но не назван вслух — всё равно поставь его тегом.\n"
        "- Теги строчными буквами.\n"
        f"- Уже использованные ранее теги: {known_block}.\n"
        "  Если какой-то из них подходит по смыслу — используй ИМЕННО его (тот же текст), а не синоним. "
        "Новый тег создавай, только если ни один существующий не подходит.\n\n"
        f"Название встречи: {title_str}\n\n"
        'Формат ответа — строго JSON: {"type": "...", "topic": "...", "tags": ["...", "..."]}\n\n'
        f"Транскрипт:\n{transcript}"
    )

_PROTOCOL_PROMPTS = {
    "sales": """Ты — AI-ассистент. Составь протокол встречи с клиентом/партнёром по продажам.

Структура:
## Участники
## Контекст и цели встречи
## Ключевые обсуждённые темы
## Договорённости и следующие шаги (список с ответственными и сроками)
## Риски и вопросы""",

    "internal": """Ты — AI-ассистент. Составь протокол внутренней рабочей встречи.

Структура:
## Участники
## Повестка
## Принятые решения
## Задачи (кто, что, когда)
## Открытые вопросы""",

    "planning": """Ты — AI-ассистент. Составь протокол встречи по планированию.

Структура:
## Участники
## Цели планирования
## Принятые решения по приоритетам
## Распределение задач
## Риски и зависимости
## Следующие шаги""",

    "review": """Ты — AI-ассистент. Составь протокол встречи-ревью.

Структура:
## Участники
## Что разбиралось
## Найденные проблемы
## Что одобрено / отклонено
## Действия по итогам""",

    "interview": """Ты — AI-ассистент. Составь протокол интервью/собеседования.

Структура:
## Участники
## Позиция / контекст
## Ключевые темы разговора
## Впечатления и оценки
## Следующий шаг в процессе""",

    "partner": """Ты — AI-ассистент. Составь протокол встречи с партнёром.

Структура:
## Участники
## Цель встречи
## Обсуждённые темы
## Договорённости
## Следующие шаги""",

    "demo": """Ты — AI-ассистент. Составь протокол демо-дня / встречи-презентации, на которой по очереди представляли НЕСКОЛЬКО проектов.

Структура:
## Участники
## Обзор (что за демо-день, сколько проектов представлено)
## Проекты
Для КАЖДОГО представленного проекта — отдельный подраздел:
### <Название проекта или команды>
- Что демонстрировали / суть
- Ключевые идеи и решения
- Вопросы и обратная связь от аудитории
- Дальнейшие шаги / договорённости
## Общие итоги и решения

Важно: выдели каждый проект отдельным подразделом `###`, не сливай всё в один список. Если название проекта не прозвучало — назови его по теме демонстрации.""",

    "other": """Ты — AI-ассистент. Составь краткий протокол встречи.

Структура:
## Участники
## Тема встречи
## Ключевые моменты
## Решения и действия""",
}


def append_dictated_notes(summary: str, notes: list[dict]) -> str:
    """Append explicit voice notes verbatim so the model cannot omit them."""
    lines: list[str] = []
    for item in notes or []:
        text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
        if text:
            lines.append(f"- {text}")
    if not lines:
        return (summary or "").strip()
    return (
        f"{(summary or '').strip()}\n\n"
        "## Продиктованные заметки\n"
        + "\n".join(lines)
    ).strip()


def _analyze_sync(
    transcript: str,
    title: str | None,
    known_tags: list[str],
    force_type: str | None = None,
    dictated_notes: list[dict] | None = None,
) -> dict:
    from openai import OpenAI

    client = OpenAI(api_key=config.OPENAI_API_KEY)

    # Cap transcript for both steps as a safety bound on the context window.
    capped = transcript
    if len(capped) > config.CHAT_MAX_CONTEXT_CHARS:
        capped = capped[:config.CHAT_MAX_CONTEXT_CHARS]

    # Step 1: tagging — sees the full transcript + the meeting title + the
    # vocabulary of already-used tags (so it reuses them instead of inventing
    # synonyms). Uses CHAT_MODEL for the large context window.
    tag_resp = client.chat.completions.create(
        model=config.CHAT_MODEL,
        messages=[{
            "role": "user",
            "content": _build_tagging_prompt(capped, title, known_tags),
        }],
        response_format={"type": "json_object"},
        max_tokens=300,
        temperature=0.2,
    )
    meta = json.loads(tag_resp.choices[0].message.content)
    meeting_type = meta.get("type", "other")
    topic = meta.get("topic", "")
    tags = normalize_tags(meta.get("tags", []))

    # Optional manual override: pin the protocol structure (and stored type) to a
    # specific kind regardless of what the classifier picked.
    if force_type and force_type in _PROTOCOL_PROMPTS:
        meeting_type = force_type

    if len(transcript) > config.CHAT_MAX_CONTEXT_CHARS:
        logger.warning(
            "analyzer: transcript %d chars > cap %d, truncating",
            len(transcript), config.CHAT_MAX_CONTEXT_CHARS,
        )

    # Step 2: protocol — feed the FULL transcript (with speaker labels) via
    # CHAT_MODEL (large context window) so long meetings fit.
    system_prompt = _PROTOCOL_PROMPTS.get(meeting_type, _PROTOCOL_PROMPTS["other"])
    proto_resp = client.chat.completions.create(
        model=config.CHAT_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Транскрипт встречи:\n{capped}"},
        ],
        max_tokens=2500,
        temperature=0.3,
    )
    summary = append_dictated_notes(
        proto_resp.choices[0].message.content.strip(),
        dictated_notes or [],
    )

    return {
        "meeting_type": meeting_type,
        "topic": topic,
        "tags": tags,
        "summary": summary,
    }


async def analyze_meeting(
    transcript: str,
    title: str | None = None,
    *,
    force_type: str | None = None,
    meeting_id: str | None = None,
) -> dict:
    # Fetch the existing tag vocabulary here (async) so the model can reuse tags.
    from database import models
    known_tags = await models.get_known_tags()
    dictated_notes = (
        await models.get_live_notes(meeting_id)
        if meeting_id
        else []
    )
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _executor,
        _analyze_sync,
        transcript,
        title,
        known_tags,
        force_type,
        dictated_notes,
    )


# ── Phone-call analysis (imported rec.megafon.ru calls → demo "Звонки") ────────

_CALL_ANALYSIS_PROMPT = """Ты — AI-ассистент, анализируешь транскрипт ТЕЛЕФОННОГО РАЗГОВОРА.
Собеседники: «Вы» (владелец номера) и «Собеседник». Верни СТРОГО JSON:
{
  "title": "короткий заголовок звонка (о чём он), 3-7 слов",
  "summary": "сжатый протокол разговора в 2-5 предложениях",
  "tasks": [{"assignee": "Вы" | "Собеседник", "items": ["конкретная задача/договорённость", "..."]}],
  "reminders": ["напоминание о сроке/перезвоне/событии", "..."],
  "tags": ["клиент/тема/продукт", "..."]
}
Правила: tasks — только реальные договорённости и действия (если нет — пустой массив).
reminders — то, о чём стоит не забыть (даты, перезвоны). tags — 1-5 коротких строчных тегов.
Если разговор пустой/без содержания — summary опиши кратко, остальные массивы пустые."""


def _analyze_call_sync(transcript: str) -> dict:
    from openai import OpenAI

    client = OpenAI(api_key=config.OPENAI_API_KEY)
    capped = transcript[: config.CHAT_MAX_CONTEXT_CHARS]
    resp = client.chat.completions.create(
        model=config.CHAT_MODEL,
        messages=[
            {"role": "system", "content": _CALL_ANALYSIS_PROMPT},
            {"role": "user", "content": f"Транскрипт звонка:\n{capped}"},
        ],
        response_format={"type": "json_object"},
        max_tokens=1500,
        temperature=0.3,
    )
    data = json.loads(resp.choices[0].message.content)
    return {
        "title": (data.get("title") or "").strip(),
        "summary": (data.get("summary") or "").strip(),
        "tasks": data.get("tasks") or [],
        "reminders": data.get("reminders") or [],
        "tags": normalize_tags(data.get("tags", [])),
    }


async def analyze_call(transcript: str) -> dict:
    """Analyze a phone-call transcript → {title, summary, tasks, reminders, tags}."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, _analyze_call_sync, transcript)
