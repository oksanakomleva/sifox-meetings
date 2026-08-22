"""Regression tests for semantic E2E assertions (no network required)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.e2e.smoke import (
    _contains_expected_live_answer,
    _note_is_adapted_and_integrated,
)


def test_listener_transcript_accepts_cyrillic_or_latin_project_name():
    assert _contains_expected_live_answer("Проект называется Мега.")
    assert _contains_expected_live_answer("Proekt nazyvaetsya Megaprotocoler.")


def test_note_semantics_accepts_natural_summary_paraphrase():
    assert _note_is_adapted_and_integrated(
        "## Итоги\nЗафиксировано сокращение времени ожидания ответа.",
        "что нам удалось сократить время ожидания ответа",
    )


def test_note_semantics_uses_the_text_stt_actually_captured():
    assert _note_is_adapted_and_integrated(
        '## Итоги\nУдалось сократить занятость по проекту "Мега".',
        "что нам удалось сократить занятость планета",
    )


def test_note_semantics_rejects_verbatim_or_unrelated_summary():
    note = "что нам удалось сократить задержку ответа"
    assert not _note_is_adapted_and_integrated(note, note)
    assert not _note_is_adapted_and_integrated("Обсудили название проекта.", note)
