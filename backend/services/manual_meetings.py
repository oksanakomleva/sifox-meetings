"""Shared, authenticated launch path for web and Mattermost requests."""
import re
from urllib.parse import parse_qsl, urlencode, urlsplit

from database import models


class MeetingAlreadyInProgress(Exception):
    """The same room belongs to an active/recent other recording."""


class RecorderUnavailable(Exception):
    """The recorder is shutting down and must not accept new work."""


def normalize_meeting_url(raw: str) -> str:
    """Allow only supported meeting rooms, never an arbitrary browser URL."""
    value = raw.strip()
    try:
        parts = urlsplit(value)
        if (
            parts.scheme.lower() != "https"
            or parts.username is not None
            or parts.password is not None
            or parts.port is not None
        ):
            raise ValueError
        host = (parts.hostname or "").lower()
        if host == "telemost.yandex.ru" and re.fullmatch(
            r"/j/[A-Za-z0-9_-]{5,100}/?", parts.path
        ):
            return f"https://telemost.yandex.ru{parts.path.rstrip('/')}"
        if (host == "zoom.us" or host.endswith(".zoom.us")) and re.fullmatch(
            r"/(?:j|wc/join)/[0-9]{8,15}/?", parts.path
        ):
            meeting_id = parts.path.rstrip("/").split("/")[-1]
            # Only the meeting passcode belongs in a shareable join URL. Drop
            # tracking and auth tokens rather than persisting them in our DB.
            passwords = [v for k, v in parse_qsl(parts.query, keep_blank_values=True)
                         if k == "pwd" and 0 < len(v) <= 200]
            query = f"?{urlencode({'pwd': passwords[0]})}" if passwords else ""
            return f"https://{host}/j/{meeting_id}{query}"
    except ValueError as exc:
        raise ValueError("Нужна ссылка на встречу Телемоста или Zoom") from exc
    raise ValueError("Нужна ссылка на встречу Телемоста или Zoom")


def meeting_title(raw: str | None) -> str:
    clean = " ".join((raw or "").split())[:120]
    return clean or "Встреча по ссылке"


async def launch_manual_meeting(url: str, title: str | None, user_id: int) -> tuple[dict, bool]:
    from services import recorder

    if recorder._shutdown_requested:
        raise RecorderUnavailable()
    normalized = normalize_meeting_url(url)
    meeting, existing = await models.create_manual_meeting(
        normalized, meeting_title(title), user_id,
    )
    if meeting is None:
        raise MeetingAlreadyInProgress()
    if meeting["status"] == "pending":
        if not await recorder.start_recording(str(meeting["id"])):
            # The 30-second scheduler (or another API worker) may have claimed
            # this exact row between creation and our call. That is success,
            # not a duplicate-room error.
            current = await models.get_meeting(str(meeting["id"]))
            if current and current["status"] in ("joining", "recording"):
                return current, True
            raise MeetingAlreadyInProgress()
    return meeting, existing
