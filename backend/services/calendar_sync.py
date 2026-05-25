"""
Calendar sync service.
Polls Google Calendar for all users with tokens,
finds Telemost meetings, deduplicates by URL.
"""
import asyncio
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any

from config import config
from database import models

logger = logging.getLogger(__name__)

_TELEMOST_RE = re.compile(r"https?://telemost\.yandex\.ru/\S+")


def _extract_telemost_url(event: dict) -> str | None:
    for field in [event.get("location", ""), event.get("description", "")]:
        if field:
            m = _TELEMOST_RE.search(field)
            if m:
                return m.group(0).rstrip(".,;)")
    for ep in event.get("conferenceData", {}).get("entryPoints", []):
        uri = ep.get("uri", "")
        if "telemost" in uri:
            return uri
    return None


def _get_attendee_emails(event: dict) -> list[str]:
    emails = []
    for att in event.get("attendees", []):
        email = att.get("email", "").lower()
        if email and not att.get("resource", False):
            emails.append(email)
    return emails


def _naive_expiry(dt) -> "datetime | None":
    """Strip timezone from expiry datetime — google-auth expects naive UTC."""
    if dt is None:
        return None
    if hasattr(dt, "tzinfo") and dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _fetch_events_sync(token_row: dict, calendar_ids: list[str], days: int = 7) -> list[dict[str, Any]]:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = Credentials(
        token=token_row["access_token"],
        refresh_token=token_row.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=config.GOOGLE_CLIENT_ID,
        client_secret=config.GOOGLE_CLIENT_SECRET,
        expiry=_naive_expiry(token_row.get("token_expiry")),
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_row["_refreshed"] = {
            "access_token": creds.token,
            "expiry": creds.expiry,
        }

    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    now = datetime.now(timezone.utc)
    # Look 30 min into the past so recurring meetings that started recently
    # are still captured (Google Calendar filters by start_time with singleEvents=True)
    time_min = now - timedelta(minutes=30)
    time_max = now + timedelta(days=days)

    events = []
    for cal_id in calendar_ids:
        try:
            result = service.events().list(
                calendarId=cal_id,
                timeMin=time_min.isoformat(),
                timeMax=time_max.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=50,
            ).execute()
        except Exception as e:
            logger.warning("Failed to fetch from calendar %s: %s", cal_id, e)
            continue

        for item in result.get("items", []):
            telemost_url = _extract_telemost_url(item)
            if not telemost_url:
                continue

            start_raw = item["start"].get("dateTime") or item["start"].get("date")
            try:
                start_dt = datetime.fromisoformat(start_raw)
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=timezone.utc)
            except Exception:
                continue

            events.append({
                "google_id": item["id"],
                "title": item.get("summary", "Встреча"),
                "start": start_dt,
                "url": telemost_url,
                "calendar_id": cal_id,
                "attendee_emails": _get_attendee_emails(item),
            })

    return events


def _fetch_calendar_list_sync(token_row: dict) -> list[dict]:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = Credentials(
        token=token_row["access_token"],
        refresh_token=token_row.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=config.GOOGLE_CLIENT_ID,
        client_secret=config.GOOGLE_CLIENT_SECRET,
        expiry=_naive_expiry(token_row.get("token_expiry")),
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())

    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    result = service.calendarList().list(showHidden=False).execute()
    return [
        {
            "id": c["id"],
            "name": c.get("summary", c["id"]),
            "primary": c.get("primary", False),
        }
        for c in result.get("items", [])
        if not c.get("deleted") and c.get("accessRole") in ("owner", "writer", "reader")
    ]


def _create_test_event_sync(
    token_row: dict,
    calendar_id: str,
    meeting_url: str,
    title: str = "[E2E Test] Тестовая встреча",
    start_minutes_from_now: int = 3,
    duration_minutes: int = 10,
) -> str:
    """Create a Google Calendar event for E2E testing. Returns event ID."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = Credentials(
        token=token_row["access_token"],
        refresh_token=token_row.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=config.GOOGLE_CLIENT_ID,
        client_secret=config.GOOGLE_CLIENT_SECRET,
        expiry=_naive_expiry(token_row.get("token_expiry")),
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())

    service = build("calendar", "v3", credentials=creds, cache_discovery=False)

    start = datetime.now(timezone.utc) + timedelta(minutes=start_minutes_from_now)
    end = start + timedelta(minutes=duration_minutes)

    event = service.events().insert(
        calendarId=calendar_id,
        body={
            "summary": title,
            "description": f"Автоматический E2E тест системы записи.\n{meeting_url}",
            "location": meeting_url,
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": end.isoformat()},
        },
    ).execute()

    logger.info("Created test calendar event %s at %s", event["id"], start.isoformat())
    return event["id"]


def _delete_event_sync(token_row: dict, calendar_id: str, event_id: str) -> None:
    """Delete a Google Calendar event (cleanup after E2E test)."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = Credentials(
        token=token_row["access_token"],
        refresh_token=token_row.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=config.GOOGLE_CLIENT_ID,
        client_secret=config.GOOGLE_CLIENT_SECRET,
        expiry=_naive_expiry(token_row.get("token_expiry")),
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())

    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
    logger.info("Deleted test calendar event %s", event_id)


async def sync_user_calendars(user_id: int) -> None:
    """Sync calendar list for a user (for admin display)."""
    token_row = await models.get_google_token(user_id)
    if not token_row:
        return

    loop = asyncio.get_running_loop()
    calendars = await loop.run_in_executor(None, _fetch_calendar_list_sync, token_row)

    for cal in calendars:
        await models.upsert_calendar(user_id, cal["id"], cal["name"], cal["primary"])

    logger.info("Synced %d calendars for user %d", len(calendars), user_id)


async def sync_user_events(user_id: int) -> None:
    """Sync events for user's enabled calendars and create pending meetings."""
    token_row = await models.get_google_token(user_id)
    if not token_row:
        return

    # Get enabled calendars for this user
    user_cals = await models.get_calendars(owner_user_id=user_id)
    enabled_cal_ids = [c["google_calendar_id"] for c in user_cals if c["record_enabled"]]
    if not enabled_cal_ids:
        return

    loop = asyncio.get_running_loop()
    events = await loop.run_in_executor(
        None, _fetch_events_sync, token_row, enabled_cal_ids
    )

    # Refresh token if needed
    if token_row.get("_refreshed"):
        r = token_row["_refreshed"]
        await models.save_google_token(user_id, r["access_token"], None, r["expiry"])

    for ev in events:
        # Upsert meeting (dedup by URL)
        meeting = await models.upsert_meeting(
            meeting_url=ev["url"],
            title=ev["title"],
            start_time=ev["start"],
        )
        # Link calendar event to meeting (with attendees)
        await models.link_calendar_event_to_meeting(
            google_event_id=ev["google_id"],
            user_id=user_id,
            meeting_id=str(meeting["id"]),
            calendar_id=ev["calendar_id"],
            attendee_emails=ev["attendee_emails"],
        )

    logger.info("Synced %d events for user %d", len(events), user_id)


async def sync_all_users() -> None:
    """Sync events for all users with connected calendars."""
    users = await models.get_all_users_with_tokens()
    logger.info("Syncing calendars for %d users", len(users))
    for user in users:
        try:
            await sync_user_events(user["id"])
        except Exception as e:
            logger.error("Sync failed for user %d: %s", user["id"], e)


async def run_sync_loop() -> None:
    """Background loop: sync every 5 minutes."""
    while True:
        try:
            await sync_all_users()
        except Exception as e:
            logger.error("Sync loop error: %s", e)
        await asyncio.sleep(300)  # 5 minutes
