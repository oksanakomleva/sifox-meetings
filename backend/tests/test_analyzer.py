"""Unit tests for analyzer — mainly checks prompt sanity."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.analyzer import _TAGGING_PROMPT, _PROTOCOL_PROMPTS


class TestTaggingPrompt:
    def test_format_works(self):
        """The classic crash: unescaped {"type"} in prompt → KeyError on .format()."""
        result = _TAGGING_PROMPT.format(transcript="hello world")
        assert "hello world" in result
        assert '"type"' in result  # JSON example must survive .format()

    def test_format_with_special_chars(self):
        result = _TAGGING_PROMPT.format(transcript='text with {curly} and "quotes"')
        assert "curly" in result


class TestProtocolPrompts:
    def test_all_types_present(self):
        required = {"sales", "internal", "planning", "review", "interview", "partner", "other"}
        assert required.issubset(set(_PROTOCOL_PROMPTS.keys()))

    def test_all_have_structure(self):
        for meeting_type, prompt in _PROTOCOL_PROMPTS.items():
            assert "Структура:" in prompt or "##" in prompt, f"{meeting_type} missing structure"
            assert "Участники" in prompt, f"{meeting_type} missing Участники section"
