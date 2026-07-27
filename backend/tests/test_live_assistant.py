"""Pure/unit coverage for live-assistant audio and UI-state helpers."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.live_assistant import RollingPCMBuffer, merge_live_transcript
from services.recorder import _mic_control_state


def test_rolling_pcm_buffer_keeps_only_configured_history():
    buffer = RollingPCMBuffer(seconds=2)
    one_second = b"\x01\x02" * 16_000
    buffer.append(one_second)
    buffer.append(b"\x03\x04" * 16_000)
    buffer.append(b"\x05\x06" * 16_000)

    assert len(buffer) == len(one_second) * 2
    assert buffer.tail(1) == b"\x05\x06" * 16_000


def test_live_transcript_merges_overlapping_windows():
    assert (
        merge_live_transcript(
            "кодовое название проекта маяк",
            "проекта Маяк протоколлер как называется проект",
        )
        == "кодовое название проекта маяк протоколлер как называется проект"
    )


def test_mic_action_labels_map_to_current_state():
    assert _mic_control_state("Включить микрофон") == "off"
    assert _mic_control_state("Unmute microphone") == "off"
    assert _mic_control_state("Выключить микрофон") == "on"
    assert _mic_control_state("Mute microphone") == "on"
    assert _mic_control_state("mic-button opaque") == "unknown"
