"""Send a meeting protocol by e-mail from the user's own mailbox.

Uses the sender's PERSONAL OAuth token (scope gmail.send), stored in
gmail_send_tokens — NOT the service account. No Workspace super-admin needed,
and it works for any Google account (including personal gmail).
"""
import asyncio
import base64
import html as _html
import logging
import re
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import config
from database import models

logger = logging.getLogger(__name__)


# ── Markdown → HTML ─────────────────────────────────────────────────────────────

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _inline(text: str) -> str:
    """Escape HTML, then apply **bold**."""
    escaped = _html.escape(text)
    return _BOLD_RE.sub(r"<strong>\1</strong>", escaped)


def markdown_to_html(md: str) -> str:
    """Convert the protocol's simple Markdown to email-friendly HTML.

    Mirrors the frontend MarkdownRenderer: ## / ### headings, - / * bullets,
    `N.` numbered lists, **bold**, blank-line-separated paragraphs.
    """
    lines = (md or "").replace("\r\n", "\n").split("\n")
    html_parts: list[str] = []
    list_type: str | None = None  # 'ul' | 'ol'

    def close_list() -> None:
        nonlocal list_type
        if list_type:
            html_parts.append(f"</{list_type}>")
            list_type = None

    for raw in lines:
        line = raw.strip()
        if not line:
            close_list()
            continue
        if line.startswith("### "):
            close_list()
            html_parts.append(f"<h3>{_inline(line[4:])}</h3>")
        elif line.startswith("## "):
            close_list()
            html_parts.append(f"<h2>{_inline(line[3:])}</h2>")
        elif line.startswith("# "):
            close_list()
            html_parts.append(f"<h2>{_inline(line[2:])}</h2>")
        elif line.startswith("- ") or line.startswith("* "):
            if list_type != "ul":
                close_list()
                html_parts.append("<ul>")
                list_type = "ul"
            html_parts.append(f"<li>{_inline(line[2:])}</li>")
        elif re.match(r"^\d+\.\s", line):
            if list_type != "ol":
                close_list()
                html_parts.append("<ol>")
                list_type = "ol"
            html_parts.append(f"<li>{_inline(re.sub(r'^\d+\.\s', '', line))}</li>")
        else:
            close_list()
            html_parts.append(f"<p>{_inline(line)}</p>")

    close_list()
    body = "\n".join(html_parts)
    return (
        '<div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;'
        'font-size:15px;line-height:1.5;color:#1a1a1a">' + body + "</div>"
    )


# ── Sending ─────────────────────────────────────────────────────────────────────

def _naive_expiry(dt):
    """google-auth expects a naive UTC datetime for expiry."""
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _send_sync(token_row: dict, sender: str, recipients: list[str],
               subject: str, html_body: str, text_body: str) -> tuple[str, dict | None]:
    """Build the Gmail service from the user's token and send. Returns
    (message_id, refreshed_token | None)."""
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
    refreshed = None
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        refreshed = {"access_token": creds.token, "expiry": creds.expiry}

    service = build("gmail", "v1", credentials=creds, cache_discovery=False)

    msg = MIMEMultipart("alternative")
    msg["To"] = ", ".join(recipients)
    msg["From"] = sender
    msg["Subject"] = subject
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
    return sent["id"], refreshed


async def send_protocol_email(
    user_id: int,
    token_row: dict,
    sender: str,
    recipients: list[str],
    subject: str,
    body_markdown: str,
) -> dict:
    """Send the protocol e-mail. Persists a refreshed access token if Google
    rotated it. Returns {ok: True, message_id} or {ok: False, error}."""
    html_body = markdown_to_html(body_markdown)
    loop = asyncio.get_running_loop()
    try:
        message_id, refreshed = await loop.run_in_executor(
            None, _send_sync, token_row, sender, recipients, subject, html_body, body_markdown
        )
    except Exception as e:  # googleapiclient HttpError or auth failure
        logger.error("Protocol send failed for user %s: %s", user_id, e)
        return {"ok": False, "error": str(e)}

    if refreshed:
        try:
            await models.save_gmail_send_token(
                user_id, refreshed["access_token"], None, refreshed["expiry"]
            )
        except Exception as e:
            logger.warning("Could not persist refreshed gmail-send token: %s", e)

    logger.info("Protocol e-mailed (msg %s) to %d recipient(s)", message_id, len(recipients))
    return {"ok": True, "message_id": message_id}
