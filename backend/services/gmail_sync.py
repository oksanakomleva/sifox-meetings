"""Gmail ingestion via a Service Account with Domain-Wide Delegation.

For every email in `users`, impersonate that mailbox (read-only) and store the
text/plain bodies in `email_messages`. Incremental per user via
`sync_state['gmail:{email}']` (cursor = Gmail historyId). First run is bounded to
the last GMAIL_BOOTSTRAP_DAYS days.

No-op if GOOGLE_SERVICE_ACCOUNT_JSON is not configured. Requires a Workspace
super-admin to authorize the service account's client_id for scope
https://www.googleapis.com/auth/gmail.readonly.
"""
import asyncio
import base64
import json
import logging
from datetime import datetime, timezone

from config import config
from database import models

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
_BOOTSTRAP_MAX = 200  # messages on first run per user (bounded)


def _service_account_info() -> dict:
    raw = config.GOOGLE_SERVICE_ACCOUNT_JSON
    if raw.strip().startswith("{"):
        return json.loads(raw)
    with open(raw, "r", encoding="utf-8") as f:           # treat as file path
        return json.load(f)


def _build_service(email: str):
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    creds = service_account.Credentials.from_service_account_info(
        _service_account_info(), scopes=_SCOPES, subject=email,
    )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _decode_b64url(data: str) -> str:
    pad = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + pad).decode("utf-8", errors="replace")


def _extract_plain(payload: dict) -> str:
    """First text/plain part (recursively); no HTML, no attachments."""
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data")
        if data:
            return _decode_b64url(data)
    for part in payload.get("parts", []) or []:
        text = _extract_plain(part)
        if text:
            return text
    return ""


def _parse_message(msg: dict, user_email: str) -> dict:
    payload = msg.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    to_raw = headers.get("to", "") or ""
    to_emails = [a.strip() for a in to_raw.split(",") if a.strip()]
    internal = int(msg.get("internalDate", "0"))
    return {
        "id": msg["id"],
        "user_email": user_email,
        "from_email": headers.get("from"),
        "to_emails": to_emails,
        "subject": headers.get("subject"),
        "body_text": _extract_plain(payload),
        "received_at": datetime.fromtimestamp(internal / 1000, tz=timezone.utc),
    }


def _bootstrap(service, email: str) -> tuple[list[dict], str | None]:
    resp = service.users().messages().list(
        userId="me", q=f"newer_than:{config.GMAIL_BOOTSTRAP_DAYS}d", maxResults=_BOOTSTRAP_MAX,
    ).execute()
    ids = [m["id"] for m in resp.get("messages", [])]
    rows = []
    for mid in ids:
        full = service.users().messages().get(userId="me", id=mid, format="full").execute()
        rows.append(_parse_message(full, email))
    profile = service.users().getProfile(userId="me").execute()
    return rows, profile.get("historyId")


def _incremental(service, email: str, cursor: str) -> tuple[list[dict], str | None]:
    from googleapiclient.errors import HttpError
    msg_ids: set[str] = set()
    page_token = None
    history_id = cursor
    try:
        while True:
            resp = service.users().history().list(
                userId="me", startHistoryId=cursor,
                historyTypes=["messageAdded"], pageToken=page_token,
            ).execute()
            for h in resp.get("history", []):
                for ma in h.get("messagesAdded", []):
                    msg_ids.add(ma["message"]["id"])
            history_id = resp.get("historyId", history_id)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    except HttpError as e:
        if getattr(e, "resp", None) is not None and e.resp.status == 404:
            # historyId too old / expired → re-bootstrap.
            return _bootstrap(service, email)
        raise
    rows = []
    for mid in msg_ids:
        try:
            full = service.users().messages().get(userId="me", id=mid, format="full").execute()
            rows.append(_parse_message(full, email))
        except Exception as e:
            logger.warning("Gmail: failed to fetch message %s for %s: %s", mid, email, e)
    return rows, history_id


def _fetch_user_gmail(email: str, cursor: str | None) -> tuple[list[dict], str | None]:
    service = _build_service(email)
    if cursor:
        return _incremental(service, email, cursor)
    return _bootstrap(service, email)


async def sync_gmail() -> int:
    if not config.GOOGLE_SERVICE_ACCOUNT_JSON:
        return 0
    emails = await models.get_user_emails()
    loop = asyncio.get_running_loop()
    total = 0
    for email in emails:
        try:
            state = await models.get_sync_state(f"gmail:{email}")
            cursor = state["last_cursor"] if state else None
            rows, new_cursor = await loop.run_in_executor(None, _fetch_user_gmail, email, cursor)
            if rows:
                await models.insert_email_messages(rows)
                total += len(rows)
            await models.upsert_sync_state(f"gmail:{email}", datetime.now(timezone.utc), new_cursor)
        except Exception as e:
            logger.error("Gmail sync failed for %s: %s", email, e)
    logger.info("Gmail: ingested %d messages for %d users", total, len(emails))
    return total


async def run_gmail_sync_loop() -> None:
    interval = max(60, config.GMAIL_SYNC_MINUTES * 60)
    while True:
        try:
            await sync_gmail()
        except Exception as e:
            logger.error("Gmail sync loop error: %s", e)
        await asyncio.sleep(interval)
