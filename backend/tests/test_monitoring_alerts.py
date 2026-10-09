import asyncio
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import httpx

from services import alerts, recorder
from services.alert_rules import detect_incidents
from services.alerts import format_incident
from services.mattermost_alerts import send_direct_message


NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


def meeting(**changes):
    row = {
        "id": "39725f04-a4a6-4c8c-91eb-213d2a0d29d9",
        "title": "Команда @channel",
        "status": "recording",
        "start_time": NOW - timedelta(minutes=20),
        "end_time": NOW + timedelta(minutes=40),
        "updated_at": NOW - timedelta(minutes=1),
        "record_eligible": True,
        "recording_end_reason": None,
        "recording_ended_at": None,
        "recording_had_participants": None,
    }
    row.update(changes)
    return row


def incidents(row, active=True):
    return detect_incidents(
        [row], {str(row["id"])} if active else set(), {}, {}, now=NOW,
    )


def test_normal_meeting_and_no_show_are_not_incidents():
    assert incidents(meeting()) == []
    assert incidents(meeting(status="no_show", updated_at=NOW)) == []


def test_failed_and_lost_meeting_are_detected():
    assert [i.kind for i in incidents(meeting(status="error", updated_at=NOW))] == ["meeting_error"]
    assert [i.kind for i in incidents(meeting(), active=False)] == []
    assert [i.kind for i in incidents(meeting(updated_at=NOW - timedelta(minutes=3)), active=False)] == [
        "recording_process_missing"
    ]


def test_unexpected_departure_detected_even_if_transcription_succeeds():
    row = meeting(status="done", recording_end_reason="page_crashed",
                  recording_ended_at=NOW - timedelta(minutes=2),
                  recording_had_participants=True)
    assert [i.kind for i in incidents(row)] == ["page_crashed"]
    row["recording_end_reason"] = "participants_left"
    assert incidents(row) == []
    row["recording_end_reason"] = "url_changed"
    assert [i.kind for i in incidents(row)] == ["left_early"]


def test_overdue_only_when_calendar_recording_enabled():
    row = meeting(status="pending", start_time=NOW - timedelta(minutes=8))
    assert [i.kind for i in incidents(row)] == ["pending_overdue"]
    row["record_eligible"] = False
    assert incidents(row) == []


def test_stalled_workers_and_shutdown():
    args = ([meeting()], {str(meeting()["id"])},
            {"recording_scheduler": 121, "calendar_sync": 1201},
            {"recording_scheduler": 100, "calendar_sync": 200})
    assert {i.kind for i in detect_incidents(*args, now=NOW)} == {
        "recording_scheduler_stalled", "calendar_sync_stalled",
    }
    assert detect_incidents(*args, now=NOW, shutdown_requested=True) == []


def test_alert_title_cannot_mention_entire_channel():
    from services.alert_rules import Incident
    msg = format_incident(
        Incident("key", "page_crashed", str(meeting()["id"])),
        meeting(title="Bad @channel\n[link](bad)"),
        "https://sifox-meetings.up.railway.app",
    )
    assert "@channel" not in msg
    assert "[link](bad)" not in msg
    assert "https://sifox-meetings.up.railway.app/meetings/" in msg


def test_end_reason_callback_is_recorded_without_live_participant_probe(monkeypatch):
    class Page:
        url = "https://telemost.yandex.ru/"

        def is_closed(self):
            return False

    monkeypatch.setattr(recorder.asyncio, "sleep", AsyncMock())
    observed = []
    assert asyncio.run(recorder._wait_for_meeting_end(
        Page(), {"Анна"}, on_end=lambda reason, present: observed.append((reason, present)),
    ))
    assert observed == [("url_changed", True)]


def test_closed_page_detected_before_participant_probe(monkeypatch):
    class Page:
        def is_closed(self):
            return True

    monkeypatch.setattr(recorder.asyncio, "sleep", AsyncMock())
    probe = AsyncMock()
    monkeypatch.setattr(recorder, "_participant_snapshot", probe)
    observed = []
    assert asyncio.run(recorder._wait_for_meeting_end(
        Page(), {"Анна"}, on_end=lambda reason, present: observed.append(reason),
    ))
    assert observed == ["page_crashed"]
    probe.assert_not_awaited()


def test_failed_delivery_keeps_alert_for_retry(monkeypatch):
    row = meeting(status="error", updated_at=datetime.now(timezone.utc))
    enqueued = []
    marked = []
    monkeypatch.setattr(alerts.models, "get_monitor_meetings", AsyncMock(return_value=[row]), raising=False)
    monkeypatch.setattr(alerts.models, "enqueue_monitor_alert", AsyncMock(side_effect=lambda *args: enqueued.append(args)), raising=False)
    monkeypatch.setattr(alerts.models, "get_unsent_monitor_alerts", AsyncMock(return_value=[
        {"alert_key": "alert-1", "message": "alert"},
    ]), raising=False)
    monkeypatch.setattr(alerts.models, "mark_monitor_alert_sent", AsyncMock(side_effect=lambda key: marked.append(key)), raising=False)
    monkeypatch.setattr(alerts, "send_direct_message", AsyncMock(side_effect=RuntimeError("offline")))
    monkeypatch.setattr(alerts.config, "MM_SERVER_URL", "https://mm.example")
    monkeypatch.setattr(alerts.config, "MM_TOKEN", "secret")
    monkeypatch.setattr(alerts.config, "ALERT_MM_USERNAME", "oksana.komleva")
    asyncio.run(alerts.check_and_send_alerts())
    assert enqueued
    assert marked == []


def test_mattermost_dm_uses_only_private_direct_channel(monkeypatch):
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path, request.content))
        path = request.url.path
        if path.endswith("/users/me"):
            return httpx.Response(200, json={"id": "bot"})
        if path.endswith("/users/username/oksana.komleva"):
            return httpx.Response(200, json={"id": "oksana"})
        if path.endswith("/channels/direct"):
            return httpx.Response(201, json={"id": "private-dm"})
        if path.endswith("/posts"):
            return httpx.Response(201, json={"id": "post"})
        return httpx.Response(404)

    real_client = httpx.AsyncClient

    def client_factory(*args, **kwargs):
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("services.mattermost_alerts.httpx.AsyncClient", client_factory)
    asyncio.run(send_direct_message(
        "https://mm.example", "secret", "oksana.komleva", "test alert",
    ))
    assert [path for _, path, _ in calls] == [
        "/api/v4/users/me", "/api/v4/users/username/oksana.komleva",
        "/api/v4/channels/direct", "/api/v4/posts",
    ]
    assert json.loads(calls[-1][2])["channel_id"] == "private-dm"


def test_external_health_state_deduplicates_down_and_recovery():
    path = Path(__file__).parents[2] / "monitoring" / "health_watch.py"
    spec = importlib.util.spec_from_file_location("health_watch", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = module.HealthState()
    assert [state.observe(False) for _ in range(4)] == [None, None, "down", None]
    assert [state.observe(True) for _ in range(3)] == [None, "recovered", None]
