"""Regression tests for semantic E2E assertions (no network required)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.e2e.smoke import (
    _contains_expected_live_answer,
    _contains_expected_note_meaning,
)


def test_listener_transcript_accepts_cyrillic_or_latin_project_name():
    assert _contains_expected_live_answer("Проект называется Мега.")
    assert _contains_expected_live_answer("Proekt nazyvaetsya Megaprotocoler.")


def test_note_semantics_accepts_natural_summary_paraphrases():
    assert _contains_expected_note_meaning("Удалось сократить задержку ответа.")
    assert _contains_expected_note_meaning(
        "Зафиксировано сокращение времени ожидания ответа."
    )


def test_note_semantics_rejects_missing_result():
    assert not _contains_expected_note_meaning("Обсудили задержку ответа.")
    assert not _contains_expected_note_meaning("Удалось сократить расходы.")
