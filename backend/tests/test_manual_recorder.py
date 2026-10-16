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
ZOOM_URL = "https://us06web.zoom.us/j/12345678901?pwd=abc123"
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
        manual_meetings.normalize_meeting_url(bad)


def test_normalizes_room_url_without_query_or_fragment():
    assert manual_meetings.normalize_meeting_url(URL + "/?utm=1#foo") == URL


def test_zoom_link_keeps_passcode_but_drops_tracking():
    assert manual_meetings.normalize_meeting_url(
        "https://us06web.zoom.us/wc/join/12345678901?utm=1&pwd=abc123#section"
    ) == ZOOM_URL


@pytest.mark.parametrize("bad", [
    "https://zoom.us.evil.example/j/12345678901",
    "https://zoom.us@evil.example/j/12345678901",
    "http://zoom.us/j/12345678901",
    "https://zoom.us:444/j/12345678901",
    "https://zoom.us/meeting/12345678901",
])
def test_rejects_unsafe_zoom_urls(bad):
    with pytest.raises(ValueError):
        manual_meetings.normalize_meeting_url(bad)


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


def test_authenticated_user_can_send_zoom_link(client):
    http, _, launch = client
    response = http.post("/api/meetings/manual", json={"meeting_url": ZOOM_URL})
    assert response.status_code == 202
    launch.assert_awaited_once_with(ZOOM_URL, "", 7)


def test_preview_user_cannot_launch(client):
    http, state, launch = client
    state["is_preview"] = True
    assert http.post("/api/meetings/manual", json={"meeting_url": URL}).status_code == 403
    launch.assert_not_awaited()


def test_manual_service_starts_only_pending_row(monkeypatch):
    row = {"id": MID, "status": "pending"}
    create = AsyncMock(return_value=(row, False))
    start = AsyncMock(return_value=True)
    monkeypatch.setattr(manual_meetings.models, "create_manual_meeting", create, raising=False)
    monkeypatch.setattr(manual_meetings, "normalize_meeting_url", lambda url: URL)
    from services import recorder
    monkeypatch.setattr(recorder, "_shutdown_requested", False)
    monkeypatch.setattr(recorder, "start_recording", start)
    result, existing = asyncio.run(manual_meetings.launch_manual_meeting(URL, "", 7))
    assert result == row and not existing
    create.assert_awaited_once_with(URL, "Встреча по ссылке", 7)
    start.assert_awaited_once_with(MID)


def test_manual_service_reports_failed_claim(monkeypatch):
    from services import recorder
    monkeypatch.setattr(recorder, "_shutdown_requested", False)
    monkeypatch.setattr(manual_meetings.models, "create_manual_meeting",
                        AsyncMock(return_value=({"id": MID, "status": "pending"}, False)), raising=False)
    monkeypatch.setattr(manual_meetings.models, "get_meeting", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(recorder, "start_recording", AsyncMock(return_value=False))
    with pytest.raises(manual_meetings.MeetingAlreadyInProgress):
        asyncio.run(manual_meetings.launch_manual_meeting(URL, None, 7))


def test_manual_service_accepts_a_claim_by_another_worker(monkeypatch):
    from services import recorder
    monkeypatch.setattr(recorder, "_shutdown_requested", False)
    monkeypatch.setattr(manual_meetings.models, "create_manual_meeting",
                        AsyncMock(return_value=({"id": MID, "status": "pending"}, False)), raising=False)
    monkeypatch.setattr(manual_meetings.models, "get_meeting",
                        AsyncMock(return_value={"id": MID, "status": "joining"}), raising=False)
    monkeypatch.setattr(recorder, "start_recording", AsyncMock(return_value=False))
    row, existing = asyncio.run(manual_meetings.launch_manual_meeting(URL, None, 7))
    assert row["status"] == "joining" and existing


def test_navigation_retries_only_when_page_did_not_render():
    from services import recorder
    page = MagicMock()
    page.url = "about:blank"
    page.goto = AsyncMock(side_effect=[TimeoutError("slow"), None])
    page.locator.return_value.count = AsyncMock(return_value=0)
    asyncio.run(recorder._navigate_to_meeting(page, URL))
    assert page.goto.await_count == 2
    assert page.goto.await_args_list[0].kwargs["wait_until"] == "domcontentloaded"
    assert page.goto.await_args_list[1].kwargs["wait_until"] == "commit"


def test_navigation_accepts_rendered_page_after_load_timeout():
    from services import recorder
    page = MagicMock()
    page.url = URL
    page.goto = AsyncMock(side_effect=TimeoutError("slow asset"))
    page.locator.return_value.count = AsyncMock(return_value=1)
    asyncio.run(recorder._navigate_to_meeting(page, URL))
    page.goto.assert_awaited_once()


def test_zoom_uses_its_own_join_flow(monkeypatch):
    from services import recorder
    join = AsyncMock(return_value=set())
    monkeypatch.setattr(recorder, "_join_zoom_meeting", join)
    page = MagicMock()
    assert asyncio.run(recorder._join_meeting(page, ZOOM_URL)) == set()
    join.assert_awaited_once_with(page, ZOOM_URL)


def test_zoom_guest_flow_preserves_passcode_and_confirms_admission(monkeypatch):
    from services import recorder
    navigate = AsyncMock()
    click = AsyncMock(side_effect=[False, True, True, False])
    monkeypatch.setattr(recorder, "_navigate_to_meeting", navigate)
    monkeypatch.setattr(recorder, "_zoom_click_visible", click)
    monkeypatch.setattr(recorder, "_zoom_dismiss_prejoin_prompts", AsyncMock(return_value=0))
    monkeypatch.setattr(recorder, "_zoom_media_prompt_visible", AsyncMock(return_value=False))
    page = MagicMock()
    page.url = ZOOM_URL
    page.frames = []
    page.locator.return_value.first.is_visible = AsyncMock(return_value=True)
    page.locator.return_value.first.fill = AsyncMock()
    assert asyncio.run(recorder._join_zoom_meeting(page, ZOOM_URL)) == set()
    navigate.assert_awaited_once_with(page, ZOOM_URL)
    page.locator.return_value.first.fill.assert_awaited_once_with("Protocaller")
    assert click.await_args_list[1].args[1] == recorder._ZOOM_BROWSER_JOIN_SELECTORS
    assert "button.preview-join-button" in click.await_args_list[2].args[1]


def test_zoom_finds_guest_form_inside_webclient_frame():
    from services import recorder
    page = MagicMock()
    frame = MagicMock()
    frame.url = "https://app.zoom.us/wc/12345678901/join"
    page.frames = [frame]
    assert recorder._zoom_join_surfaces(page) == [frame, page]


def test_zoom_submits_guest_form_inside_webclient_frame(monkeypatch):
    from services import recorder
    monkeypatch.setattr(recorder, "_navigate_to_meeting", AsyncMock())
    click = AsyncMock(side_effect=[False, True, False])
    monkeypatch.setattr(recorder, "_zoom_click_visible", click)
    monkeypatch.setattr(recorder, "_zoom_dismiss_prejoin_prompts", AsyncMock(return_value=0))
    monkeypatch.setattr(recorder, "_zoom_media_prompt_visible", AsyncMock(return_value=False))
    page = MagicMock()
    page.url = ZOOM_URL
    frame = MagicMock()
    frame.url = "https://app.zoom.us/wc/12345678901/join"
    page.frames = [frame]
    frame.locator.return_value.first.is_visible = AsyncMock(return_value=True)
    frame.locator.return_value.first.fill = AsyncMock()
    assert asyncio.run(recorder._join_zoom_meeting(page, ZOOM_URL)) == set()
    frame.locator.return_value.first.fill.assert_awaited_once_with("Protocaller")
    assert click.await_args_list[1].args[0] is frame
    assert "button.preview-join-button" in click.await_args_list[1].args[1]


def test_zoom_retries_browser_entry_when_launcher_button_renders_late(monkeypatch):
    from services import recorder
    monkeypatch.setattr(recorder, "_navigate_to_meeting", AsyncMock())
    monkeypatch.setattr(recorder, "_zoom_dismiss_prejoin_prompts", AsyncMock(return_value=0))
    monkeypatch.setattr(recorder, "_zoom_media_prompt_visible", AsyncMock(return_value=False))
    browser_attempts = 0

    async def click(_surface, selectors):
        nonlocal browser_attempts
        if selectors == recorder._ZOOM_BROWSER_JOIN_SELECTORS:
            browser_attempts += 1
            return browser_attempts >= 2
        return selectors == recorder._ZOOM_GUEST_JOIN_SELECTORS

    monkeypatch.setattr(recorder, "_zoom_click_visible", click)
    page = MagicMock()
    page.url = ZOOM_URL
    page.frames = []
    page.wait_for_timeout = AsyncMock()
    name_field = page.locator.return_value.first
    name_field.is_visible = AsyncMock(side_effect=lambda **_kwargs: browser_attempts >= 2)
    name_field.fill = AsyncMock()

    assert asyncio.run(recorder._join_zoom_meeting(page, ZOOM_URL)) == set()
    assert browser_attempts == 2
    name_field.fill.assert_awaited_once_with("Protocaller")


def test_zoom_missing_form_reports_webclient_without_leaking_passcode():
    from services import recorder
    page = MagicMock()
    page.url = ZOOM_URL
    frame = MagicMock()
    frame.url = "https://app.zoom.us/wc/12345678901/join?pwd=secret"
    page.frames = [frame]
    frame.locator.return_value.inner_text = AsyncMock(return_value="Continue without audio and video")
    page.locator.return_value.inner_text = AsyncMock(return_value="Zoom")

    detail = asyncio.run(recorder._zoom_missing_guest_form_detail(page))
    assert "веб-клиент Zoom открылся" in detail
    assert "pwd" not in detail and "secret" not in detail


def test_zoom_direct_webclient_fallback_keeps_passcode_and_rejects_other_hosts():
    from services import recorder
    invitation = "https://us04web.zoom.us/j/75332086484?pwd=secret-token"
    assert recorder._zoom_direct_webclient_url(invitation) == (
        "https://us04web.zoom.us/wc/75332086484/join?pwd=secret-token"
    )
    assert recorder._zoom_direct_webclient_url(
        "https://evilzoom.us/j/75332086484?pwd=secret-token"
    ) is None
    assert recorder._zoom_direct_webclient_url(
        "https://us04web.zoom.us/signin?pwd=secret-token"
    ) is None


def test_zoom_guest_submit_cannot_match_account_signin_button():
    from services import recorder
    selectors = recorder._ZOOM_GUEST_JOIN_SELECTORS
    assert "button.preview-join-button" in selectors
    assert "button:text-is('Войти')" in selectors
    assert not any(":has-text('Войти')" in selector for selector in selectors)


def test_zoom_detects_account_signin_redirect_without_query():
    from services import recorder
    page = MagicMock()
    page.url = "https://app.zoom.us/signin?pwd=secret"
    assert recorder._zoom_on_signin_page(page)
    page.url = "https://app.zoom.us/wc/12345678901/join?pwd=secret"
    assert not recorder._zoom_on_signin_page(page)
    page.url = "https://evilzoom.us/signin"
    assert not recorder._zoom_on_signin_page(page)


def test_zoom_skips_both_media_prompts_before_join(monkeypatch):
    from services import recorder
    click = AsyncMock(side_effect=[True, True, False])
    monkeypatch.setattr(recorder, "_zoom_click_visible", click)
    page = MagicMock()
    page.wait_for_timeout = AsyncMock()
    assert asyncio.run(recorder._zoom_dismiss_prejoin_prompts(page)) == 2
    assert click.await_count == 3
    assert "без микрофона и камеры" in click.await_args_list[0].args[1][0]
    selectors = click.await_args_list[0].args[1]
    assert "a:text-is('Продолжить без микрофона и камеры')" in selectors
    assert ":text-is('Продолжить без микрофона и камеры')" in selectors


def test_zoom_does_not_submit_while_media_prompt_covers_join(monkeypatch):
    from services import recorder
    monkeypatch.setattr(recorder, "_navigate_to_meeting", AsyncMock())
    monkeypatch.setattr(recorder, "_zoom_click_visible", AsyncMock(return_value=False))
    monkeypatch.setattr(recorder, "_zoom_dismiss_prejoin_prompts", AsyncMock(return_value=0))
    monkeypatch.setattr(recorder, "_zoom_media_prompt_visible", AsyncMock(return_value=True))
    page = MagicMock()
    page.url = ZOOM_URL
    page.frames = []
    field = page.locator.return_value.first
    field.is_visible = AsyncMock(return_value=True)
    field.fill = AsyncMock()

    with pytest.raises(RuntimeError, match="не удалось закрыть окна выбора микрофона и камеры"):
        asyncio.run(recorder._join_zoom_meeting(page, ZOOM_URL))
    field.fill.assert_not_awaited()


def test_zoom_retries_stalled_guest_form_after_dismissing_prompt(monkeypatch):
    from services import recorder
    dismiss = AsyncMock(return_value=1)
    click = AsyncMock(return_value=True)
    monkeypatch.setattr(recorder, "_zoom_dismiss_prejoin_prompts", dismiss)
    monkeypatch.setattr(recorder, "_zoom_click_visible", click)
    field = MagicMock()
    field.is_visible = AsyncMock(return_value=True)
    field.input_value = AsyncMock(return_value="")
    field.fill = AsyncMock()
    assert asyncio.run(recorder._zoom_retry_guest_form(MagicMock(), field, 1)) is True
    field.fill.assert_awaited_once_with("Protocaller")
    click.assert_awaited_once()
    assert click.await_args.args[1] == recorder._ZOOM_GUEST_JOIN_SELECTORS


def test_zoom_retry_uses_enter_only_on_still_visible_named_form(monkeypatch):
    from services import recorder
    monkeypatch.setattr(recorder, "_zoom_dismiss_prejoin_prompts", AsyncMock(return_value=0))
    field = MagicMock()
    field.is_visible = AsyncMock(return_value=True)
    field.input_value = AsyncMock(return_value="Protocaller")
    field.fill = AsyncMock()
    field.press = AsyncMock()
    assert asyncio.run(recorder._zoom_retry_guest_form(MagicMock(), field, 2)) is True
    field.fill.assert_not_awaited()
    field.press.assert_awaited_once_with("Enter", timeout=3_000)


def test_zoom_never_resubmits_after_guest_form_disappears(monkeypatch):
    from services import recorder
    dismiss = AsyncMock()
    click = AsyncMock()
    monkeypatch.setattr(recorder, "_zoom_dismiss_prejoin_prompts", dismiss)
    monkeypatch.setattr(recorder, "_zoom_click_visible", click)
    field = MagicMock()
    field.is_visible = AsyncMock(return_value=False)
    assert asyncio.run(recorder._zoom_retry_guest_form(MagicMock(), field, 1)) is False
    dismiss.assert_not_awaited()
    click.assert_not_awaited()


def test_zoom_timeout_reports_open_name_form_not_unproven_waiting_room(monkeypatch):
    from services import recorder
    monkeypatch.setattr(recorder, "_zoom_media_prompt_visible", AsyncMock(return_value=False))
    page = MagicMock()
    page.locator.return_value.inner_text = AsyncMock(return_value="Введите информацию о встрече")
    name_field = MagicMock()
    name_field.is_visible = AsyncMock(return_value=True)
    assert asyncio.run(recorder._zoom_join_failure_detail(page, name_field)) == (
        "форма ввода имени осталась открытой после нажатия «Войти»"
    )


def test_zoom_timeout_reports_media_overlay_instead_of_open_form(monkeypatch):
    from services import recorder
    monkeypatch.setattr(recorder, "_zoom_media_prompt_visible", AsyncMock(return_value=True))
    page = MagicMock()
    page.locator.return_value.inner_text = AsyncMock(return_value="Введите информацию о встрече")
    name_field = MagicMock()
    name_field.is_visible = AsyncMock(return_value=True)
    assert asyncio.run(recorder._zoom_join_failure_detail(page, name_field)) == (
        "окно выбора микрофона и камеры осталось поверх формы входа"
    )


def test_zoom_timeout_explains_authentication_without_leaking_join_query():
    from services import recorder
    page = MagicMock()
    page.url = ZOOM_URL
    page.locator.return_value.inner_text = AsyncMock(
        return_value="Only authenticated users can join this meeting"
    )
    name_field = MagicMock()
    name_field.is_visible = AsyncMock(return_value=False)
    detail = asyncio.run(recorder._zoom_join_failure_detail(page, name_field))
    assert "авторизации" in detail
    assert "pwd" not in detail and "abc123" not in detail


def test_zoom_authentication_requirement_is_not_a_waiting_room():
    from services import recorder
    assert recorder._zoom_requires_authentication(
        "only authenticated users can join this meeting"
    )
    assert not recorder._zoom_requires_authentication(
        "please wait for the host to let you in from the waiting room"
    )
    assert not recorder._zoom_requires_authentication("Sign in to join, or enter your name as a guest")


def test_zoom_count_is_unknown_without_a_labelled_counter():
    from services import recorder
    page = MagicMock()
    page.locator.return_value.all = AsyncMock(return_value=[])
    assert asyncio.run(recorder._zoom_participant_count(page)) is None


def test_zoom_recording_stops_after_participants_leave(monkeypatch):
    from services import recorder
    page = MagicMock()
    page.url = ZOOM_URL.replace("/j/", "/wc/join/")
    page.is_closed.return_value = False
    page.locator.return_value.inner_text = AsyncMock(return_value="Meeting")
    counts = iter([2, 1, 1, 1])
    monkeypatch.setattr(recorder, "_zoom_participant_count",
                        AsyncMock(side_effect=lambda _page: next(counts)))
    monkeypatch.setattr(recorder.asyncio, "sleep", AsyncMock())
    ended = []
    result = asyncio.run(recorder._wait_for_meeting_end(
        page, set(), meeting_id=MID, on_end=lambda reason, present: ended.append((reason, present)),
        service="zoom",
    ))
    assert result is True
    assert ended == [("participants_left", True)]


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
