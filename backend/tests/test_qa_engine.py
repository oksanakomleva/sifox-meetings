"""Unit tests for the live-assistant pure helpers (no DB / audio / OpenAI)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.qa_engine import (
    _SYSTEM_VOICE,
    contains_wake_word,
    strip_wake_word,
    select_scope,
    pack_context,
)

WAKE = "протоколлер"


class TestWakeWord:
    def test_exact(self):
        assert contains_wake_word("Протоколлер, когда отправили документы?", WAKE)

    def test_mangled_ending(self):
        # ASR often splits/garbles the tail.
        assert contains_wake_word("протокол лер когда отправили", WAKE)
        assert contains_wake_word("протоколер а что по срокам", WAKE)

    def test_case_and_yo(self):
        assert contains_wake_word("ПРОТОКОЛЛЕР привет", WAKE)

    def test_absent(self):
        assert not contains_wake_word("когда отправили документы", WAKE)

    def test_unrelated(self):
        assert not contains_wake_word("обсудим бюджет на квартал", WAKE)

    def test_ordinary_protocol_does_not_trigger(self):
        assert not contains_wake_word("протокол встречи готов", WAKE)


def test_voice_prompt_prioritizes_current_meeting_and_excludes_wake_name():
    assert "[ТЕКУЩАЯ ВСТРЕЧА]" in _SYSTEM_VOICE
    assert "игнорируй противоречащие сведения" in _SYSTEM_VOICE
    assert "никогда не является частью названия проекта" in _SYSTEM_VOICE


class TestStripWakeWord:
    def test_strips_prefix(self):
        q = strip_wake_word("Протоколлер когда отправили документы", WAKE)
        assert "отправили документы" in q
        assert "протокол" not in q.lower()

    def test_no_wake_returns_text(self):
        assert strip_wake_word("когда дедлайн", WAKE) == "когда дедлайн"


class TestSelectScope:
    def test_all_internal_is_full(self):
        assert select_scope(["a@sifox.com", "b@sifox.com"], "sifox.com", False) == "full"

    def test_external_is_meeting_only(self):
        assert select_scope(["a@sifox.com", "guest@gmail.com"], "sifox.com", False) == "meeting_only"

    def test_host_override_forces_full(self):
        assert select_scope(["guest@gmail.com"], "sifox.com", True) == "full"

    def test_unknown_attendees_conservative(self):
        assert select_scope([], "sifox.com", False) == "meeting_only"


class TestPackContext:
    def test_orders_newest_first_within_budget(self):
        items = [
            (0.1, "2026-01-01", "old low"),
            (9.0, "2026-02-01", "feb high"),
            (9.0, "2026-03-01", "mar high"),
        ]
        out = pack_context(items, budget=1000)
        # Both high-rank kept; presented newest-first.
        assert out.index("mar high") < out.index("feb high")

    def test_budget_drops_least_relevant(self):
        items = [
            (9.0, "2026-03-01", "X" * 50),
            (0.1, "2026-01-01", "Y" * 50),
        ]
        out = pack_context(items, budget=60)  # only one line fits
        assert "X" * 50 in out
        assert "Y" * 50 not in out

    def test_oversized_item_does_not_hide_smaller_item(self):
        items = [
            (10.0, "2026-03-01", "X" * 200),
            (9.0, "2026-02-01", "useful"),
        ]
        assert pack_context(items, budget=50) == "useful"
