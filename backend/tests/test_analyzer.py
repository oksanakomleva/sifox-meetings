"""Unit tests for analyzer — mainly checks prompt sanity."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.analyzer import _TAGGING_PROMPT, _PROTOCOL_PROMPTS


class TestTaggingPrompt:
    def test_replace_works(self):
        """Prompt substitution uses str.replace(), not .format() — immune to {} in transcript."""
        result = _TAGGING_PROMPT.replace("{transcript}", "hello world")
        assert "hello world" in result
        assert '"type"' in result  # JSON example must survive substitution

    def test_replace_with_curly_braces_in_transcript(self):
        """Transcript containing {} (e.g. JSON, code) must not crash substitution."""
        tricky = 'output was {"type": "error", "code": 500}'
        result = _TAGGING_PROMPT.replace("{transcript}", tricky)
        assert '"type": "error"' in result  # braces in transcript preserved as-is

    def test_replace_with_format_chars(self):
        """All special format characters in transcript must pass through safely."""
        result = _TAGGING_PROMPT.replace("{transcript}", 'text with {curly} and "quotes" and %s')
        assert "curly" in result
        assert "%s" in result


class TestProtocolPrompts:
    def test_all_types_present(self):
        required = {"sales", "internal", "planning", "review", "interview", "partner", "other"}
        assert required.issubset(set(_PROTOCOL_PROMPTS.keys()))

    def test_all_have_structure(self):
        for meeting_type, prompt in _PROTOCOL_PROMPTS.items():
            assert "Структура:" in prompt or "##" in prompt, f"{meeting_type} missing structure"
            assert "Участники" in prompt, f"{meeting_type} missing Участники section"
