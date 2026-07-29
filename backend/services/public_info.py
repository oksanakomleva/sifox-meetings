"""Safe routing and answering for public, current-information questions.

Only the standalone spoken question is sent to OpenAI web search. Meeting
transcripts, Mattermost messages and email are never included in this request.
"""
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from config import config


_EXPLICIT_PUBLIC = re.compile(
    r"\b(?:найди|поищи|посмотри)\s+(?:в\s+)?интернет(?:е|у)?\b",
    re.IGNORECASE,
)
_WEATHER = re.compile(
    r"\b(?:погод[аыуе]|прогноз\s+погоды|температур[аыуе]|осадк[иов]|"
    r"дожд[ья]|снег[а]?|ветер|жарко|холодно)\b",
    re.IGNORECASE,
)
_CURRENCY = re.compile(
    r"\b(?:курс(?:\s+валют)?|доллар[а-я]*|евро|рубл[ьяею]*|драм[а-я]*|"
    r"юан[ьяею]*|тенге|usd|eur|rub|amd|cny|kzt)\b",
    re.IGNORECASE,
)
_PUBLIC_FACT = re.compile(
    r"\b(?:столиц[аыу]|населени[ея]|который\s+час|сколько\s+времени|"
    r"где\s+находится|когда\s+родил(?:ся|ась)|кто\s+написал|"
    r"кто\s+(?:такой|такая)|новост[ьи]|результат\s+матча|сч[её]т\s+матча)\b",
    re.IGNORECASE,
)
_CORPORATE = re.compile(
    r"\b(?:встреч[аеиу]|созвон|проект[а-я]*|клиент[а-я]*|письм[а-я]*|"
    r"почт[а-я]*|mattermost|чат[а-я]*|договорил[а-я]*|обсуждал[а-я]*|"
    r"коллег[а-я]*|задач[а-я]*|дедлайн[а-я]*|отпуск[а-я]*)\b",
    re.IGNORECASE,
)


def classify_public_question(question: str) -> str:
    """Return public, corporate or ambiguous without exposing any context."""
    text = (question or "").strip()
    if not text:
        return "corporate"

    public_signal = bool(
        _EXPLICIT_PUBLIC.search(text)
        or _WEATHER.search(text)
        or _CURRENCY.search(text)
        or _PUBLIC_FACT.search(text)
    )
    corporate_signal = bool(_CORPORATE.search(text))
    if public_signal and corporate_signal:
        return "ambiguous"
    return "public" if public_signal else "corporate"


def _extract_response(payload: dict) -> tuple[str, list[dict]]:
    text_parts: list[str] = []
    details: list[dict] = []
    seen_urls: set[str] = set()

    for item in payload.get("output") or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if content.get("type") != "output_text":
                continue
            if content.get("text"):
                text_parts.append(content["text"])
            for annotation in content.get("annotations") or []:
                if annotation.get("type") != "url_citation":
                    continue
                url = annotation.get("url")
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                title = annotation.get("title") or urlparse(url).netloc or "Веб-источник"
                details.append({
                    "source": "web",
                    "label": title,
                    "snippet": url,
                    "used": True,
                })

    return " ".join(text_parts).strip(), details[:5]


async def answer_public_question(question: str) -> tuple[str, list[dict]]:
    """Answer from current public web data using only the standalone question."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    payload = {
        "model": config.LIVE_PUBLIC_INFO_MODEL,
        "tools": [{"type": "web_search", "search_context_size": "low"}],
        "instructions": (
            "Ответь на русском очень кратко, 1–2 предложениями: ответ будет "
            "озвучен на встрече. Используй актуальные публичные данные и web search. "
            "Для погоды укажи место и период, для валют — валютную пару, значение "
            "и дату данных. Не упоминай внутренние встречи, проекты, почту или чаты. "
            f"Текущая дата UTC: {today}."
        ),
        "input": question,
        "max_output_tokens": 220,
        "store": False,
    }
    headers = {
        "Authorization": f"Bearer {config.OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    timeout = httpx.Timeout(config.LIVE_PUBLIC_INFO_TIMEOUT_SEC)
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            "https://api.openai.com/v1/responses",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()

    answer, details = _extract_response(response.json())
    if not answer:
        raise RuntimeError("Public web search returned an empty answer")
    return answer, details
