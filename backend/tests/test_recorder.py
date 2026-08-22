"""Unit tests for recorder pure functions."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.recorder import (
    _is_real_name,
    _effective_speaker_timeline,
    _speaker_for_segment,
    _build_transcript,
    _confirm_audio_capture_started,
    _fmt_time,
    _find_pids_with_environment,
    _collect_runtime_snapshot,
)
from services import recorder


class TestRecorderProcessCleanup:
    def test_finds_only_process_with_exact_meeting_sink(self, tmp_path):
        proc = tmp_path / "proc"
        (proc / "101").mkdir(parents=True)
        (proc / "102").mkdir()
        (proc / "not-a-pid").mkdir()
        (proc / "101" / "environ").write_bytes(
            b"DISPLAY=:99\0PULSE_SINK=meet_target\0"
        )
        (proc / "102" / "environ").write_bytes(
            b"DISPLAY=:99\0PULSE_SINK=meet_other\0"
        )

        assert _find_pids_with_environment(
            "PULSE_SINK=meet_target", proc
        ) == [101]

    def test_runtime_snapshot_counts_browser_and_audio_processes(self, tmp_path):
        proc = tmp_path / "proc"
        proc.mkdir()
        (proc / "loadavg").write_text(
            "0.10 0.20 0.30 1/10 1\n", encoding="utf-8"
        )
        for pid, name in (("101", "chrome"), ("102", "chrome"), ("103", "ffmpeg")):
            (proc / pid).mkdir()
            (proc / pid / "comm").write_text(name, encoding="utf-8")

        result = _collect_runtime_snapshot(proc)

        assert "load=0.10 0.20 0.30 1/10 1" in result
        assert "chrome:2" in result
        assert "ffmpeg:1" in result


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


class _Proc:
    def __init__(self, returncode=None):
        self.returncode = returncode


class _Capture:
    def __init__(self, parec_returncode=None, ffmpeg_returncode=None):
        self.parec = _Proc(parec_returncode)
        self.ffmpeg = _Proc(ffmpeg_returncode)


class TestConfirmAudioCaptureStarted:
    def test_accepts_observed_file_growth_immediately(self, monkeypatch, tmp_path):
        sizes = AsyncMock(side_effect=[12_000, 12_000, 18_000])
        monkeypatch.setattr(recorder.fsio, "size", sizes)
        monkeypatch.setattr(recorder.asyncio, "sleep", AsyncMock())

        asyncio.run(
            _confirm_audio_capture_started(_Capture(), tmp_path / "audio.wav")
        )

        assert sizes.await_count == 3

    def test_accepts_live_capture_while_monitor_is_silent(self, monkeypatch, tmp_path):
        sizes = AsyncMock(return_value=0)
        monkeypatch.setattr(recorder.fsio, "size", sizes)
        monkeypatch.setattr(recorder.asyncio, "sleep", AsyncMock())

        asyncio.run(
            _confirm_audio_capture_started(_Capture(), tmp_path / "audio.wav")
        )

        assert sizes.await_count > 1

    @pytest.mark.parametrize(
        ("parec_code", "ffmpeg_code"),
        [(1, None), (None, 1)],
    )
    def test_rejects_dead_capture_process(
        self, monkeypatch, tmp_path, parec_code, ffmpeg_code
    ):
        monkeypatch.setattr(recorder.fsio, "size", AsyncMock(return_value=0))

        with pytest.raises(RuntimeError, match="процесс захвата завершился"):
            asyncio.run(
                _confirm_audio_capture_started(
                    _Capture(parec_code, ffmpeg_code), tmp_path / "audio.wav"
                )
            )


def test_claim_uses_joining_until_capture_is_confirmed():
    source = (Path(__file__).parent.parent / "database" / "models.py").read_text(
        encoding="utf-8"
    )
    claim = source.split("async def claim_meeting_for_recording", 1)[1].split(
        "async def mark_duplicate_if_sibling_active", 1
    )[0]

    assert "SET status = 'joining'" in claim
    assert "o.status IN ('joining', 'recording')" in claim
