"""Routing and request-isolation tests for optional public web answers."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services import public_info


def test_classifier_routes_obvious_public_questions():
    assert public_info.classify_public_question(
        "Какая завтра погода в Ереване?"
    ) == "public"
    assert public_info.classify_public_question(
        "Какой сегодня курс доллара к драму?"
    ) == "public"


def test_classifier_keeps_company_questions_in_corporate_search():
    assert public_info.classify_public_question(
        "Что обсуждали на встрече по проекту ГПБМ?"
    ) == "corporate"


def test_classifier_requests_clarification_for_mixed_question():
    assert public_info.classify_public_question(
        "Какая погода была во время встречи с клиентом?"
    ) == "ambiguous"
    assert public_info.classify_public_question(
        "Найди в интернете информацию о нашем проекте ГПБМ"
    ) == "ambiguous"


def test_public_request_contains_only_standalone_question(monkeypatch):
    captured = {}

    class FakeResponse:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {
                "output": [{
                    "type": "message",
                    "content": [{
                        "type": "output_text",
                        "text": "В Ереване сейчас 25 градусов.",
                        "annotations": [{
                            "type": "url_citation",
                            "url": "https://weather.example/yerevan",
                            "title": "Прогноз",
                        }],
                    }],
                }],
            }

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["request"] = kwargs
            return FakeResponse()

    monkeypatch.setattr(public_info.httpx, "AsyncClient", FakeClient)
    question = "Какая сейчас погода в Ереване?"

    answer, details = asyncio.run(public_info.answer_public_question(question))

    assert answer == "В Ереване сейчас 25 градусов."
    assert captured["request"]["json"]["input"] == question
    assert "transcript" not in captured["request"]["json"]
    assert "meeting" not in captured["request"]["json"]
    assert details[0]["snippet"] == "https://weather.example/yerevan"
