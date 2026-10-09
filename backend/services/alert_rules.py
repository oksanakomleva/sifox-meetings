"""Pure incident detection for recorder monitoring; no network or notifications."""
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Incident:
    key: str
    kind: str
    meeting_id: str | None = None


def _utc(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def detect_incidents(
    meetings: list[dict],
    active_recording_ids: set[str],
    worker_ages: dict[str, float],
    worker_stamps: dict[str, int],
    *,
    now: datetime | None = None,
    shutdown_requested: bool = False,
) -> list[Incident]:
    """Find actionable failures; no-show and normal participant exit are not errors."""
    if shutdown_requested:
        return []
    now = now or datetime.now(timezone.utc)
    incidents: list[Incident] = []

    for name, threshold in (("recording_scheduler", 120), ("calendar_sync", 1200)):
        if worker_ages.get(name, 0) > threshold:
            incidents.append(Incident(f"worker:{name}:{worker_stamps.get(name, 0)}", f"{name}_stalled"))

    for meeting in meetings:
        meeting_id = str(meeting["id"])
        if (meeting.get("title") or "").startswith("[E2E Test]"):
            continue
        status = meeting.get("status")
        if status == "no_show":
            continue
        start = _utc(meeting.get("start_time"))
        updated = _utc(meeting.get("updated_at"))
        scheduled_end = _utc(meeting.get("end_time"))
        ended = _utc(meeting.get("recording_ended_at"))
        eligible = bool(meeting.get("record_eligible"))

        def add(kind: str, event_time: datetime | None) -> None:
            stamp = int(event_time.timestamp()) if event_time else 0
            incidents.append(Incident(f"meeting:{meeting_id}:{kind}:{stamp}", kind, meeting_id))

        if status == "error" and updated and 0 <= (now - updated).total_seconds() <= 1800:
            add("meeting_error", updated)
        elif status == "pending" and eligible and start:
            delay = (now - start).total_seconds()
            if 7 * 60 <= delay <= 30 * 60:
                add("pending_overdue", start)
        elif status == "joining" and updated and (now - updated).total_seconds() > 8 * 60:
            add("joining_stalled", updated)
        elif status == "recording" and meeting_id not in active_recording_ids:
            if updated and (now - updated).total_seconds() > 2 * 60:
                add("recording_process_missing", updated)
        elif status == "transcribing" and updated and (now - updated).total_seconds() > 130 * 60:
            add("transcribing_stalled", updated)
        elif status == "analyzing" and updated and (now - updated).total_seconds() > 30 * 60:
            add("analyzing_stalled", updated)

        reason = meeting.get("recording_end_reason")
        if ended and meeting.get("recording_had_participants"):
            recent = 0 <= (now - ended).total_seconds() <= 45 * 60
            if recent and reason == "page_crashed":
                add("page_crashed", ended)
            elif recent and reason == "url_changed" and scheduled_end:
                if (scheduled_end - ended).total_seconds() > 15 * 60:
                    add("left_early", ended)
    return incidents
