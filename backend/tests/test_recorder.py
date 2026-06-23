"""Unit tests for recorder pure functions."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.recorder import (
    _is_real_name,
    _effective_speaker_timeline,
    _speaker_for_segment,
    _build_transcript,
    _fmt_time,
)


class _Seg:
    """Minimal stand-in for a faster-whisper segment (has start/end/text)."""
    def __init__(self, start, end, text):
        self.start = start
        self.end = end
        self.text = text


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


class TestSpeakerForSegment:
    def test_majority_overlap_wins(self):
        # 8–14s: 2s under A (8–10), 4s under B (10–14) → B
        tl = [(0.0, "Alice"), (10.0, "Bob")]
        assert _speaker_for_segment(8.0, 14.0, tl) == "Bob"

    def test_fully_inside_one_speaker(self):
        tl = [(0.0, "Alice"), (10.0, "Bob")]
        assert _speaker_for_segment(1.0, 4.0, tl) == "Alice"

    def test_before_first_event_is_unknown(self):
        assert _speaker_for_segment(0.0, 2.0, [(5.0, "Alice")]) == "Участник"

    def test_empty_timeline_is_unknown(self):
        assert _speaker_for_segment(0.0, 5.0, []) == "Участник"


class TestBuildTranscript:
    def test_same_speaker_short_gap_merges(self):
        # Same speaker, gap 1s (<4s) → merged into a single block.
        segments = [_Seg(0.0, 1.0, "Hello"), _Seg(2.0, 3.0, "World")]
        result = _build_transcript(segments, [(0.0, "Alice")])
        assert "Alice:" in result
        assert "Hello" in result and "World" in result
        assert result.count("Alice:") == 1

    def test_speaker_change(self):
        segments = [_Seg(0.0, 1.0, "Hi"), _Seg(10.0, 11.0, "Reply")]
        tl = [(0.0, "Alice"), (5.0, "Bob")]
        result = _build_transcript(segments, tl)
        assert "Alice: Hi" in result
        assert "Bob: Reply" in result

    def test_long_monologue_splits_into_paragraphs(self):
        # Single speaker (e.g. an upload with no timeline), continuous speech with
        # tiny gaps over ~150s → must break into multiple blocks via the length cap,
        # not collapse into one wall of text.
        segments = [_Seg(float(i) * 3, float(i) * 3 + 2.5, f"s{i}") for i in range(50)]
        result = _build_transcript(segments, [])  # no timeline → all "Участник"
        assert result.count("Участник:") > 1
        # Every segment's text is still present.
        assert "s0" in result and "s49" in result


class TestFmtTime:
    def test_seconds_only(self):
        assert _fmt_time(45) == "00:45"

    def test_minutes(self):
        assert _fmt_time(125) == "02:05"

    def test_hours(self):
        assert _fmt_time(3725) == "01:02:05"
