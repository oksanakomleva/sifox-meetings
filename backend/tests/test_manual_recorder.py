import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import meetings
from auth.deps import get_current_user
from services import manual_meetings, mattermost_recorder


USER = {"user_id": 7, "email": "user@sifox.com", "is_active": True, "is_preview": False}
URL = "https://telemost.yandex.ru/j/123456789"
MID = "11111111-1111-4111-8111-111111111111"


@pytest.mark.parametrize("bad", [
    "http://telemost.yandex.ru/j/12345",
    "https://evil.example/j/12345",
    "https://telemost.yandex.ru.evil.example/j/12345",
    "https://telemost.yandex.ru@evil.example/j/12345",
    "https://telemost.yandex.ru/",
    "https://telemost.yandex.ru/j/12345/extra",
    "https://telemost.yandex.ru:444/j/12345",
])
def test_rejects_non_room_urls(bad):
    with pytest.raises(ValueError):
        manual_meetings.normalize_telemost_url(bad)


def test_normalizes_room_url_without_query_or_fragment():
    assert manual_meetings.normalize_telemost_url(URL + "/?utm=1#foo") == URL


def test_mm_only_recognizes_plain_link_or_explicit_command():
    assert mattermost_recorder.parse_command(URL) == ("record", URL, None)
    assert mattermost_recorder.parse_command("протоколлер, запиши " + URL) == ("record", URL, None)
    assert mattermost_recorder.parse_command("привязать abcDEF123456") == ("link", "abcDEF123456", None)
    assert mattermost_recorder.parse_command("Встреча была здесь: " + URL) is None
    assert mattermost_recorder.parse_command("https://evil.example/j/12345")[0] == "invalid_url"


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(meetings.router)
    state = dict(USER)
    app.dependency_overrides[get_current_user] = lambda: state
    launch = AsyncMock(return_value=({"id": MID, "status": "pending"}, False))
    monkeypatch.setattr(manual_meetings, "launch_manual_meeting", launch)
    with TestClient(app) as test_client:
        yield test_client, state, launch


def test_authenticated_user_can_launch_from_interface(client):
    http, _, launch = client
    response = http.post("/api/meetings/manual", json={"meeting_url": URL, "title": "Обсуждение"})
    assert response.status_code == 202
    assert response.json()["meeting_id"] == MID
    launch.assert_awaited_once_with(URL, "Обсуждение", 7)


def test_preview_user_cannot_launch(client):
    http, state, launch = client
    state["is_preview"] = True
    assert http.post("/api/meetings/manual", json={"meeting_url": URL}).status_code == 403
    launch.assert_not_awaited()


def test_manual_service_starts_only_pending_row(monkeypatch):
    row = {"id": MID, "status": "pending"}
    create = AsyncMock(return_value=(row, False))
    start = AsyncMock()
    monkeypatch.setattr(manual_meetings.models, "create_manual_telemost_meeting", create, raising=False)
    monkeypatch.setattr(manual_meetings, "normalize_telemost_url", lambda url: URL)
    from services import recorder
    monkeypatch.setattr(recorder, "_shutdown_requested", False)
    monkeypatch.setattr(recorder, "start_recording", start)
    result, existing = asyncio.run(manual_meetings.launch_manual_meeting(URL, "", 7))
    assert result == row and not existing
    create.assert_awaited_once_with(URL, "Встреча по ссылке", 7)
    start.assert_awaited_once_with(MID)


def test_mm_unlinked_sender_cannot_start_recording(monkeypatch):
    sender = AsyncMock(return_value=None)
    launch = AsyncMock()
    reply = AsyncMock()
    monkeypatch.setattr(mattermost_recorder.models, "get_mm_recorder_user", sender, raising=False)
    monkeypatch.setattr(mattermost_recorder, "launch_manual_meeting", launch)
    monkeypatch.setattr(mattermost_recorder, "_reply", reply)
    asyncio.run(mattermost_recorder._handle_command(
        MagicMock(), "direct-channel", "unknown-user", ("record", URL, None),
    ))
    launch.assert_not_awaited()
    reply.assert_awaited_once()


def test_first_post_in_new_direct_message_is_processed(monkeypatch):
    bootstrap = int(time.time() * 1000) - 2000
    post = {"id": "post-new", "user_id": "person", "create_at": bootstrap + 1000,
            "type": "", "message": URL}
    response = MagicMock()
    response.json.return_value = {"posts": {"post-new": post}}
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    handle = AsyncMock()
    monkeypatch.setattr(mattermost_recorder.models, "get_sync_state", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(mattermost_recorder.models, "upsert_sync_state", AsyncMock(), raising=False)
    monkeypatch.setattr(mattermost_recorder.models, "is_mm_recorder_post_processed", AsyncMock(return_value=False), raising=False)
    monkeypatch.setattr(mattermost_recorder.models, "mark_mm_recorder_post_processed", AsyncMock(), raising=False)
    monkeypatch.setattr(mattermost_recorder, "_handle_command", handle)
    asyncio.run(mattermost_recorder._poll_direct_channel(
        client, {"id": "new-direct", "last_post_at": bootstrap + 1000}, "bot", bootstrap,
    ))
    handle.assert_awaited_once_with(client, "new-direct", "person", ("record", URL, None))


def test_stale_mm_link_does_not_launch(monkeypatch):
    now_ms = int(time.time() * 1000)
    old = now_ms - 10 * 60 * 1000
    post = {"id": "post-old", "user_id": "person", "create_at": old,
            "type": "", "message": URL}
    response = MagicMock()
    response.json.return_value = {"posts": {"post-old": post}}
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    handle = AsyncMock()
    reply = AsyncMock()
    monkeypatch.setattr(mattermost_recorder.models, "get_sync_state", AsyncMock(return_value={"last_cursor": str(old - 1)}), raising=False)
    monkeypatch.setattr(mattermost_recorder.models, "upsert_sync_state", AsyncMock(), raising=False)
    monkeypatch.setattr(mattermost_recorder.models, "is_mm_recorder_post_processed", AsyncMock(return_value=False), raising=False)
    monkeypatch.setattr(mattermost_recorder.models, "mark_mm_recorder_post_processed", AsyncMock(), raising=False)
    monkeypatch.setattr(mattermost_recorder, "_handle_command", handle)
    monkeypatch.setattr(mattermost_recorder, "_reply", reply)
    asyncio.run(mattermost_recorder._poll_direct_channel(
        client, {"id": "direct", "last_post_at": old}, "bot", old - 1,
    ))
    handle.assert_not_awaited()
    reply.assert_awaited_once()
