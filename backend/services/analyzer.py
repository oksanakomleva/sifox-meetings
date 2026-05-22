"""OpenAI analysis: meeting type, tags, structured protocol."""
import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor

from config import config

logger = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="analyzer")

_TAGGING_PROMPT = """Ты — AI-ассистент для анализа рабочих встреч компании Sifox.

Определи:
1. Тип встречи (одно из: sales / internal / planning / review / interview / partner / other)
2. Краткую тему (1 строка)
3. Список тегов (3–7 штук, строчными буквами, без # и пробелов, через запятую)

Формат ответа — строго JSON:
{"type": "...", "topic": "...", "tags": ["...", "..."]}

Транскрипт:
{transcript}"""

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

    "other": """Ты — AI-ассистент. Составь краткий протокол встречи.

Структура:
## Участники
## Тема встречи
## Ключевые моменты
## Решения и действия""",
}


def _analyze_sync(transcript: str) -> dict:
    from openai import OpenAI

    client = OpenAI(api_key=config.OPENAI_API_KEY)

    # Step 1: tagging
    tag_resp = client.chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[{
            "role": "user",
            "content": _TAGGING_PROMPT.format(transcript=transcript[:4000]),
        }],
        response_format={"type": "json_object"},
        max_tokens=200,
        temperature=0.2,
    )
    meta = json.loads(tag_resp.choices[0].message.content)
    meeting_type = meta.get("type", "other")
    topic = meta.get("topic", "")
    tags = meta.get("tags", [])

    # Step 2: protocol
    system_prompt = _PROTOCOL_PROMPTS.get(meeting_type, _PROTOCOL_PROMPTS["other"])
    proto_resp = client.chat.completions.create(
        model=config.OPENAI_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Транскрипт встречи:\n{transcript[:8000]}"},
        ],
        max_tokens=2000,
        temperature=0.3,
    )
    summary = proto_resp.choices[0].message.content.strip()

    return {
        "meeting_type": meeting_type,
        "topic": topic,
        "tags": tags,
        "summary": summary,
    }


async def analyze_meeting(transcript: str) -> dict:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, _analyze_sync, transcript)
