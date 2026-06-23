"""Unit tests for analyzer — prompt sanity + tag normalization."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.analyzer import _build_tagging_prompt, _PROTOCOL_PROMPTS, normalize_tags


class TestTaggingPrompt:
    def test_includes_transcript_title_and_json_example(self):
        result = _build_tagging_prompt("hello world", "Созвон по проекту", ["acme"])
        assert "hello world" in result          # transcript inlined
        assert '"type"' in result               # JSON example present
        assert "Созвон по проекту" in result    # meeting title included
        assert "acme" in result                 # known tags included

    def test_curly_braces_in_transcript_preserved(self):
        """Transcript with {} (JSON/code) is interpolated as a variable, not parsed."""
        tricky = 'output was {"type": "error", "code": 500}'
        result = _build_tagging_prompt(tricky, None, [])
        assert '"type": "error"' in result

    def test_format_chars_pass_through(self):
        result = _build_tagging_prompt('text with {curly} and "quotes" and %s', None, [])
        assert "curly" in result
        assert "%s" in result

    def test_no_known_tags_placeholder(self):
        result = _build_tagging_prompt("x", None, [])
        assert "(пока нет)" in result


class TestNormalizeTags:
    def test_lowercase_strip_hash_dedupe(self):
        assert normalize_tags(["#Acme", "acme ", "ACME", "  Project X "]) == ["acme", "project x"]

    def test_drops_empty_and_non_strings(self):
        assert normalize_tags(["", "   ", None, 5, "ok"]) == ["ok"]

    def test_caps_at_15(self):
        assert len(normalize_tags([f"t{i}" for i in range(30)])) == 15


class TestProtocolPrompts:
    def test_all_types_present(self):
        required = {"sales", "internal", "planning", "review", "interview", "partner", "demo", "other"}
        assert required.issubset(set(_PROTOCOL_PROMPTS.keys()))

    def test_all_have_structure(self):
        for meeting_type, prompt in _PROTOCOL_PROMPTS.items():
            assert "Структура:" in prompt or "##" in prompt, f"{meeting_type} missing structure"
            assert "Участники" in prompt, f"{meeting_type} missing Участники section"
