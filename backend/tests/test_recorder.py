"""Unit tests for recorder pure functions."""
import asyncio
import sys
import wave
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
    _close_browser_runtime,
    _create_pulse_sink,
    _click_visible_join_button,
    _dismiss_join_overlays,
    _fill_guest_name,
    _ensure_silent_fake_audio_file,
    _is_join_confirmed,
    _telemost_call_state,
    _SYNTHETIC_CAMERA_INIT_SCRIPT,
)
from services import recorder
from tests.e2e.test_speaker import _wait_for_join_control


def test_synthetic_camera_preserves_real_audio_capture():
    assert "kind === 'videoinput'" in _SYNTHETIC_CAMERA_INIT_SCRIPT
    assert "nativeGetUserMedia({ audio: constraints.audio, video: false })" in (
        _SYNTHETIC_CAMERA_INIT_SCRIPT
    )
    assert "--use-fake-device-for-media-stream" not in _SYNTHETIC_CAMERA_INIT_SCRIPT


def test_silent_fake_audio_file_is_valid_and_reused(tmp_path):
    path = tmp_path / "silence.wav"

    created = _ensure_silent_fake_audio_file(path)
    original_size = created.stat().st_size
    reused = _ensure_silent_fake_audio_file(path)

    assert reused == created
    assert reused.stat().st_size == original_size
    with wave.open(str(created), "rb") as source:
        assert source.getnchannels() == 1
        assert source.getsampwidth() == 2
        assert source.getframerate() == 16_000
        assert source.getnframes() == 80_000


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

    def test_successful_playwright_close_still_sweeps_detached_children(
        self, monkeypatch
    ):
        browser = AsyncMock()
        playwright = AsyncMock()
        sweep = AsyncMock()
        monkeypatch.setattr(recorder, "_terminate_stuck_browser", sweep)

        asyncio.run(
            _close_browser_runtime(browser, playwright, "meet_target")
        )

        browser.close.assert_awaited_once()
        playwright.stop.assert_awaited_once()
        sweep.assert_awaited_once_with("meet_target")

    def test_failed_playwright_close_still_sweeps_detached_children(
        self, monkeypatch
    ):
        browser = AsyncMock()
        browser.close.side_effect = RuntimeError("close failed")
        sweep = AsyncMock()
        monkeypatch.setattr(recorder, "_terminate_stuck_browser", sweep)

        asyncio.run(_close_browser_runtime(browser, None, "meet_target"))

        sweep.assert_awaited_once_with("meet_target")

    def test_chromium_crash_reporting_is_disabled(self):
        source = Path(recorder.__file__).read_text(encoding="utf-8")

        assert '"--disable-breakpad"' in source
        assert '"--disable-crash-reporter"' in source


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


class _JoinElement:
    def __init__(self, *, visible=True, hide_on_click=False):
        self.visible = visible
        self.hide_on_click = hide_on_click
        self.clicked = False
        self.value = None

    async def is_visible(self, **kwargs):
        return self.visible

    async def click(self, **kwargs):
        self.clicked = True
        if self.hide_on_click:
            self.visible = False

    async def is_enabled(self):
        return True

    async def fill(self, value, **kwargs):
        self.value = value


class _JoinLocator:
    def __init__(self, elements=()):
        self.elements = list(elements)

    async def count(self):
        return len(self.elements)

    def nth(self, index):
        return self.elements[index]


class _JoinSurface:
    def __init__(self, url, *, selector_elements=None, state=None):
        self.url = url
        self.selector_elements = selector_elements or {}
        self.state = state or {}

    def locator(self, selector):
        return _JoinLocator(self.selector_elements.get(selector, ()))

    async def evaluate(self, script):
        return self.state


class _JoinPage(_JoinSurface):
    def __init__(self, *, frames=(), selector_elements=None, state=None):
        super().__init__(
            "https://telemost.yandex.ru/j/test",
            selector_elements=selector_elements,
            state=state,
        )
        self.frames = list(frames)
        self.wait_for_timeout = AsyncMock()


class TestTelemostThreeJoin:
    def test_fills_name_and_clicks_join_inside_private_join_iframe(self):
        name = _JoinElement()
        join = _JoinElement()
        frame = _JoinSurface(
            "https://telemost.yandex.ru/private-join/test",
            selector_elements={
                "input[type='text']": [name],
                "button:has-text('Подключиться')": [join],
            },
        )
        page = _JoinPage(frames=[frame])

        filled_via = asyncio.run(_fill_guest_name(page))
        clicked_via = asyncio.run(_click_visible_join_button(page))

        assert filled_via.startswith("private-join iframe")
        assert clicked_via.startswith("private-join iframe")
        assert name.value == "Protocaller"
        assert join.clicked

    def test_dismisses_new_onboarding_overlay(self):
        button = _JoinElement(hide_on_click=True)
        page = _JoinPage(
            selector_elements={"button:has-text('Звучит отлично')": [button]}
        )

        dismissed = asyncio.run(_dismiss_join_overlays(page))

        assert dismissed
        assert button.clicked

    def test_dismisses_entire_chain_of_join_overlays(self):
        welcome = _JoinElement(hide_on_click=True)
        understood_first = _JoinElement(hide_on_click=True)
        understood_second = _JoinElement(hide_on_click=True)
        page = _JoinPage(
            selector_elements={
                "button:has-text('Звучит отлично')": [welcome],
                "button:has-text('Понятно')": [
                    understood_first,
                    understood_second,
                ],
            }
        )

        dismissed = asyncio.run(_dismiss_join_overlays(page))

        assert dismissed
        assert welcome.clicked
        assert understood_first.clicked
        assert understood_second.clicked

    def test_prejoin_iframe_wins_over_background_end_call_control(self):
        frame = _JoinSurface(
            "https://telemost.yandex.ru/private-join/test",
            state={
                "has_leave": False,
                "has_mic": True,
                "has_join": True,
                "has_name_input": True,
                "has_waiting_room": False,
                "in_call_signal_count": 0,
                "labels": ["подключиться"],
            },
        )
        page = _JoinPage(
            frames=[frame],
            state={
                "has_leave": True,
                "has_mic": True,
                "has_join": False,
                "has_name_input": False,
                "has_waiting_room": False,
                "in_call_signal_count": 0,
                "labels": ["завершить звонок"],
            },
        )

        state = asyncio.run(_telemost_call_state(page))

        assert state["has_leave"]
        assert state["has_visible_prejoin"]
        assert not _is_join_confirmed(state)

    def test_new_call_shell_is_confirmed_after_prejoin_iframe_disappears(self):
        page = _JoinPage(
            state={
                "has_leave": True,
                "has_mic": True,
                "has_join": False,
                "has_name_input": False,
                "has_waiting_room": False,
                "in_call_signal_count": 0,
                "labels": ["завершить звонок"],
            }
        )

        state = asyncio.run(_telemost_call_state(page))

        assert not state["has_visible_prejoin"]
        assert _is_join_confirmed(state)

    def test_e2e_speaker_uses_same_private_join_iframe(self):
        join = _JoinElement()
        frame = _JoinSurface(
            "https://telemost.yandex.ru/private-join/test",
            selector_elements={"button:has-text('Подключиться')": [join]},
        )
        page = _JoinPage(frames=[frame])

        surface, _, selector, found = asyncio.run(
            _wait_for_join_control(
                page,
                ("button:has-text('Подключиться')",),
                timeout_ms=100,
            )
        )

        assert surface == "private-join iframe"
        assert selector == "button:has-text('Подключиться')"
        assert found is join


class _PulseModuleProc:
    def __init__(self, returncode, stdout=b"", stderr=b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self):
        return self._stdout, self._stderr


class TestCreatePulseSink:
    def test_returns_module_id(self, monkeypatch):
        create = AsyncMock(return_value=_PulseModuleProc(0, b"42\n"))
        monkeypatch.setattr(recorder.asyncio, "create_subprocess_exec", create)

        assert asyncio.run(_create_pulse_sink("meet_test")) == 42

    def test_surfaces_pactl_failure(self, monkeypatch):
        create = AsyncMock(
            return_value=_PulseModuleProc(1, stderr=b"Connection refused")
        )
        monkeypatch.setattr(recorder.asyncio, "create_subprocess_exec", create)

        with pytest.raises(RuntimeError, match="Connection refused"):
            asyncio.run(_create_pulse_sink("meet_test"))


def test_claim_uses_joining_until_capture_is_confirmed():
    source = (Path(__file__).parent.parent / "database" / "models.py").read_text(
        encoding="utf-8"
    )
    claim = source.split("async def claim_meeting_for_recording", 1)[1].split(
        "async def mark_duplicate_if_sibling_active", 1
    )[0]

    assert "SET status = 'joining'" in claim
    assert "o.status IN ('joining', 'recording')" in claim
