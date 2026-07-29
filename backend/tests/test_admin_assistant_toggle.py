"""Safety policy for the admin-only per-meeting live-assistant toggle."""

import pytest

from services.assistant_toggle import (
    AssistantToggleError,
    validate_assistant_toggle,
)


PENDING_MEETING = {
    "id": "meeting-1",
    "status": "pending",
    "meeting_url": "https://telemost.example/j/1",
}


def validate(meeting=PENDING_MEETING, enabled=True, **overrides):
    flags = {
        "live_assistant_enabled": True,
        "live_assistant_speak": True,
        "live_assistant_all_meetings": False,
        **overrides,
    }
    return validate_assistant_toggle(meeting, enabled, **flags)


def test_pending_meeting_can_be_enabled():
    assert validate() is None


def test_pending_meeting_can_be_disabled_even_if_voice_master_flag_is_off():
    assert validate(
        enabled=False,
        live_assistant_enabled=False,
        live_assistant_speak=False,
    ) is None


@pytest.mark.parametrize(
    ("meeting", "overrides", "status_code"),
    [
        (None, {}, 404),
        ({**PENDING_MEETING, "status": "recording"}, {}, 409),
        (PENDING_MEETING, {"live_assistant_all_meetings": True}, 409),
        (PENDING_MEETING, {"live_assistant_enabled": False}, 409),
        (PENDING_MEETING, {"live_assistant_speak": False}, 409),
        ({**PENDING_MEETING, "meeting_url": None}, {}, 409),
    ],
)
def test_unsafe_toggle_is_rejected(meeting, overrides, status_code):
    with pytest.raises(AssistantToggleError) as exc:
        validate(meeting, **overrides)

    assert exc.value.status_code == status_code
    assert exc.value.detail
