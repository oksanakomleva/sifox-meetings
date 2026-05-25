"""Unit tests for recorder pure functions."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.recorder import (
    _is_real_name,
    _effective_speaker_timeline,
    _build_transcript,
    _fmt_time,
)


class TestIsRealName:
    def test_normal_name(self):
        assert _is_real_name("Oksana Komleva")
        assert _is_real_name("Иван Петров")

    def test_rejects_ui_noise(self):
        assert not _is_real_name("Подключиться")
        assert not _is_real_name("Ваше имя на встрече")
        assert not _is_real_name("Protocaller")
        assert not _is_real_name("Включить камеру")

    def test_rejects_too_short(self):
        assert not _is_real_name("A")
        assert not _is_real_name("")

    def test_rejects_too_long(self):
        assert not _is_real_name("X" * 61)

    def test_rejects_digits_only(self):
        assert not _is_real_name("12345")
        assert not _is_real_name("---")


class TestEffectiveSpeakerTimeline:
    def test_existing_timeline_kept(self):
        tl = [(0.0, "Alice"), (5.0, "Bob")]
        assert _effective_speaker_timeline(tl, {"Alice", "Bob"}) == tl

    def test_single_participant_fallback(self):
        result = _effective_speaker_timeline([], {"Alice", "Protocaller"})
        assert result == [(0.0, "Alice")]

    def test_empty_when_multiple_no_timeline(self):
        result = _effective_speaker_timeline([], {"Alice", "Bob"})
        assert result == []


class TestBuildTranscript:
    def test_basic(self):
        class Seg:
            def __init__(self, start, text):
                self.start = start
                self.text = text
        segments = [Seg(0.0, "Hello"), Seg(2.0, "World")]
        tl = [(0.0, "Alice")]
        result = _build_transcript(segments, tl)
        assert "Alice: Hello" in result
        assert "Alice: World" in result

    def test_speaker_change(self):
        class Seg:
            def __init__(self, start, text):
                self.start = start
                self.text = text
        segments = [Seg(0.0, "Hi"), Seg(10.0, "Reply")]
        tl = [(0.0, "Alice"), (5.0, "Bob")]
        result = _build_transcript(segments, tl)
        assert "Alice: Hi" in result
        assert "Bob: Reply" in result


class TestFmtTime:
    def test_seconds_only(self):
        assert _fmt_time(45) == "00:45"

    def test_minutes(self):
        assert _fmt_time(125) == "02:05"

    def test_hours(self):
        assert _fmt_time(3725) == "01:02:05"
