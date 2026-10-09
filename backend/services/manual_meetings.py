"""Shared, authenticated launch path for web and Mattermost requests."""
import re
from urllib.parse import urlsplit

from database import models


class MeetingAlreadyInProgress(Exception):
    """The same Telemost room belongs to an active/recent other recording."""


class RecorderUnavailable(Exception):
    """The recorder is shutting down and must not accept new work."""


def normalize_telemost_url(raw: str) -> str:
    """Only a real Telemost room, never an arbitrary browser URL."""
    value = raw.strip()
    try:
        parts = urlsplit(value)
        if (
            parts.scheme.lower() != "https"
            or parts.hostname != "telemost.yandex.ru"
            or parts.username is not None
            or parts.password is not None
            or parts.port is not None
            or not re.fullmatch(r"/j/[A-Za-z0-9_-]{5,100}/?", parts.path)
        ):
            raise ValueError
    except ValueError as exc:
        raise ValueError("Нужна ссылка на встречу вида https://telemost.yandex.ru/j/...") from exc
    return f"https://telemost.yandex.ru{parts.path.rstrip('/')}"


def meeting_title(raw: str | None) -> str:
    clean = " ".join((raw or "").split())[:120]
    return clean or "Встреча по ссылке"


async def launch_manual_meeting(url: str, title: str | None, user_id: int) -> tuple[dict, bool]:
    from services import recorder

    if recorder._shutdown_requested:
        raise RecorderUnavailable()
    normalized = normalize_telemost_url(url)
    meeting, existing = await models.create_manual_telemost_meeting(
        normalized, meeting_title(title), user_id,
    )
    if meeting is None:
        raise MeetingAlreadyInProgress()
    if meeting["status"] == "pending":
        await recorder.start_recording(str(meeting["id"]))
    return meeting, existing
