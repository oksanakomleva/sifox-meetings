"""Pure/unit coverage for live-assistant audio and UI-state helpers."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import config
from services import live_assistant
from services.live_assistant import (
    RollingPCMBuffer,
    _handle_question,
    capture_question_audio,
    _diagnostic_update,
    get_live_diagnostic,
    merge_live_transcript,
    pcm_rms,
    trailing_pcm_is_silent,
    transcribe_wake_window,
)
from services.recorder import (
    _create_pulse_source,
    _is_join_confirmed,
    _mic_control_state,
)


def test_rolling_pcm_buffer_keeps_only_configured_history():
    buffer = RollingPCMBuffer(seconds=2)
    one_second = b"\x01\x02" * 16_000
    buffer.append(one_second)
    buffer.append(b"\x03\x04" * 16_000)
    buffer.append(b"\x05\x06" * 16_000)

    assert len(buffer) == len(one_second) * 2
    assert buffer.tail(1) == b"\x05\x06" * 16_000
    assert buffer.total_bytes == len(one_second) * 3


def test_rolling_pcm_buffer_reads_absolute_contiguous_range():
    buffer = RollingPCMBuffer(seconds=2)
    one_second = b"\x01\x02" * 16_000
    buffer.append(one_second)
    buffer.append(b"\x03\x04" * 16_000)
    buffer.append(b"\x05\x06" * 16_000)

    # The first second has rolled off. Absolute offset one_second starts at the
    # beginning of the retained second and never joins chunks across a gap.
    assert buffer.range(len(one_second), len(one_second)) == b"\x03\x04" * 16_000


def test_live_transcript_merges_overlapping_windows():
    assert (
        merge_live_transcript(
            "кодовое название проекта маяк",
            "проекта Маяк протоколлер как называется проект",
        )
        == "кодовое название проекта маяк протоколлер как называется проект"
    )


def test_pcm_rms_distinguishes_silence_from_speech():
    silence = b"\x00\x00" * 16_000
    speech = (1000).to_bytes(2, "little", signed=True) * 16_000

    assert pcm_rms(silence) == 0
    assert pcm_rms(speech) == 1000
    assert pcm_rms(speech) > config.LIVE_MIN_RMS


def test_trailing_silence_ends_question_capture_before_hard_limit():
    speech = (1000).to_bytes(2, "little", signed=True) * 16_000
    silence = b"\x00\x00" * 16_000
    rolling = RollingPCMBuffer(seconds=20)
    rolling.append(speech + speech + silence)

    class RunningReader:
        @staticmethod
        def done():
            return False

    captured = asyncio.run(
        capture_question_audio(
            rolling,
            RunningReader(),
            question_start=0,
            window_end_offset=len(speech),
            wake_text="Протоколлер, какая погода?",
            max_bytes=len(speech) * 12,
        )
    )

    assert captured == speech + speech + silence
    assert trailing_pcm_is_silent(
        captured,
        config.LIVE_QUESTION_SILENCE_SEC,
        config.LIVE_MIN_RMS,
    )


def test_note_command_is_saved_and_acknowledged(monkeypatch):
    transcribe = AsyncMock(
        return_value="Протоколлер, запиши отправить договор до пятницы"
    )
    save_note = AsyncMock()
    save_qa = AsyncMock()
    speak = AsyncMock()
    monkeypatch.setattr(live_assistant, "transcribe_question", transcribe)
    monkeypatch.setattr(
        live_assistant.models,
        "save_live_note",
        save_note,
        raising=False,
    )
    monkeypatch.setattr(
        live_assistant.models,
        "save_live_qa",
        save_qa,
        raising=False,
    )

    asyncio.run(
        _handle_question(
            "00000000-0000-0000-0000-000000000001",
            b"audio",
            "",
            b"",
            speak,
        )
    )

    save_note.assert_awaited_once_with(
        "00000000-0000-0000-0000-000000000001",
        "отправить договор до пятницы",
    )
    assert "попадёт в итоговый протокол" in speak.await_args.args[0]
    assert save_qa.await_args.args[3] == "note"


def test_mic_action_labels_map_to_current_state():
    assert _mic_control_state("Включить микрофон") == "off"
    assert _mic_control_state("Unmute microphone") == "off"
    assert _mic_control_state("Выключить микрофон") == "on"
    assert _mic_control_state("Mute microphone") == "on"
    assert _mic_control_state("mic-button opaque") == "unknown"


def test_virtual_mic_uses_remapped_source(monkeypatch):
    captured = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return b"42\n", b""

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    module_id = asyncio.run(
        _create_pulse_source("botmic_source_test", "botmic_test.monitor")
    )

    assert module_id == 42
    assert captured["args"][:3] == (
        "pactl",
        "load-module",
        "module-remap-source",
    )
    assert "master=botmic_test.monitor" in captured["args"]
    assert "source_name=botmic_source_test" in captured["args"]


def test_join_confirmation_rejects_prejoin_form():
    assert not _is_join_confirmed(
        {
            "has_leave": False,
            "has_mic": True,
            "has_join": True,
            "has_name_input": True,
        }
    )
    assert _is_join_confirmed(
        {
            "has_leave": False,
            "has_mic": True,
            "has_join": False,
            "has_name_input": False,
        }
    )
    assert _is_join_confirmed({"has_leave": True})


def test_live_diagnostic_is_copied_and_updated():
    meeting_id = "diagnostic-test"
    _diagnostic_update(meeting_id, status="listening", bytes_received=123)

    diagnostic = get_live_diagnostic(meeting_id)
    assert diagnostic["status"] == "listening"
    assert diagnostic["bytes_received"] == 123

    diagnostic["status"] = "tampered"
    assert get_live_diagnostic(meeting_id)["status"] == "listening"


def test_wake_window_uses_accurate_cloud_stt(monkeypatch):
    cloud = AsyncMock(return_value="Протоколлер, как называется проект?")
    local = AsyncMock(return_value="")
    monkeypatch.setattr(live_assistant, "transcribe_openai_pcm", cloud)
    monkeypatch.setattr(live_assistant, "transcribe_pcm", local)
    monkeypatch.setattr(config, "LIVE_WAKE_STT", "openai")

    text = asyncio.run(transcribe_wake_window(b"\x00\x00" * 16_000))

    assert "Протоколлер" in text
    cloud.assert_awaited_once()
    local.assert_not_awaited()


def test_wake_window_falls_back_to_isolated_local_stt(monkeypatch):
    cloud = AsyncMock(side_effect=TimeoutError("cloud unavailable"))
    local = AsyncMock(return_value="протоколер, что решили?")
    monkeypatch.setattr(live_assistant, "transcribe_openai_pcm", cloud)
    monkeypatch.setattr(live_assistant, "transcribe_pcm", local)
    monkeypatch.setattr(config, "LIVE_WAKE_STT", "openai")

    text = asyncio.run(transcribe_wake_window(b"\x00\x00" * 16_000))

    assert text == "протоколер, что решили?"
    local.assert_awaited_once_with(
        b"\x00\x00" * 16_000,
        config.LIVE_WAKE_MODEL,
        beam_size=1,
    )
