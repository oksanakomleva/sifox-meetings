"""All asyncpg queries for telemost-web."""
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from database.connection import get_pool
from utils.encryption import encrypt, decrypt


# ── Users ─────────────────────────────────────────────────────────────────────

async def upsert_user(
    google_id: str,
    email: str,
    name: str,
    avatar_url: str | None,
    is_admin_default: bool = False,
) -> dict[str, Any]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO users (google_id, email, name, avatar_url, is_admin, last_login)
            VALUES ($1, $2, $3, $4, $5, NOW())
            ON CONFLICT (google_id) DO UPDATE
              SET email = EXCLUDED.email,
                  name = EXCLUDED.name,
                  avatar_url = EXCLUDED.avatar_url,
                  last_login = NOW()
            RETURNING *
            """,
            google_id, email, name, avatar_url, is_admin_default,
        )
    return dict(row)


async def get_user_by_id(user_id: int) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE id = $1", user_id)
    return dict(row) if row else None


async def get_user_by_email(email: str) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM users WHERE email = $1", email.lower())
    return dict(row) if row else None


async def set_user_admin(user_id: int, is_admin: bool) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET is_admin = $1 WHERE id = $2", is_admin, user_id
        )


async def list_users(limit: int = 100) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, email, name, avatar_url, is_admin, is_active, created_at, last_login "
            "FROM users ORDER BY created_at DESC LIMIT $1",
            limit,
        )
    return [dict(r) for r in rows]


# ── Sessions ──────────────────────────────────────────────────────────────────

async def create_session(user_id: int, ttl_days: int = 30) -> str:
    import secrets
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=ttl_days)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO sessions (id, user_id, expires_at) VALUES ($1, $2, $3)",
            token, user_id, expires_at,
        )
    return token


async def get_session(token: str) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT s.*, u.email, u.name, u.avatar_url, u.is_admin, u.is_active
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.id = $1 AND s.expires_at > NOW()
            """,
            token,
        )
    return dict(row) if row else None


async def delete_session(token: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM sessions WHERE id = $1", token)


async def purge_expired_sessions() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM sessions WHERE expires_at <= NOW()")


# ── Google tokens ──────────────────────────────────────────────────────────────

async def save_google_token(
    user_id: int,
    access_token: str,
    refresh_token: str | None,
    token_expiry: datetime | None,
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO google_tokens
              (user_id, access_token, refresh_token, token_expiry, updated_at)
            VALUES ($1, $2, $3, $4, NOW())
            ON CONFLICT (user_id) DO UPDATE
              SET access_token = EXCLUDED.access_token,
                  refresh_token = COALESCE(EXCLUDED.refresh_token, google_tokens.refresh_token),
                  token_expiry = EXCLUDED.token_expiry,
                  updated_at = NOW()
            """,
            user_id,
            encrypt(access_token),
            encrypt(refresh_token) if refresh_token else None,
            token_expiry,
        )


async def get_google_token(user_id: int) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM google_tokens WHERE user_id = $1", user_id
        )
    if not row:
        return None
    result = dict(row)
    result["access_token"] = decrypt(result["access_token"])
    if result.get("refresh_token"):
        result["refresh_token"] = decrypt(result["refresh_token"])
    return result


async def save_google_write_token(
    user_id: int,
    access_token: str,
    refresh_token: str | None,
    token_expiry: datetime | None,
) -> None:
    """Save a Google token with full calendar write scope (E2E test admin only)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO google_tokens
              (user_id, access_token, refresh_token, token_expiry, has_write_scope, updated_at)
            VALUES ($1, $2, $3, $4, TRUE, NOW())
            ON CONFLICT (user_id) DO UPDATE
              SET access_token    = EXCLUDED.access_token,
                  refresh_token   = COALESCE(EXCLUDED.refresh_token, google_tokens.refresh_token),
                  token_expiry    = EXCLUDED.token_expiry,
                  has_write_scope = TRUE,
                  updated_at      = NOW()
            """,
            user_id,
            encrypt(access_token),
            encrypt(refresh_token) if refresh_token else None,
            token_expiry,
        )


async def get_google_write_token(user_id: int) -> dict[str, Any] | None:
    """Return Google token only if it has calendar write scope (for E2E test creation)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM google_tokens WHERE user_id = $1 AND has_write_scope = TRUE",
            user_id,
        )
    if not row:
        return None
    result = dict(row)
    result["access_token"] = decrypt(result["access_token"])
    if result.get("refresh_token"):
        result["refresh_token"] = decrypt(result["refresh_token"])
    return result


async def get_any_admin_write_token() -> tuple[int, dict] | None:
    """Return (user_id, token) for any admin user who has calendar write scope."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT gt.*, u.id AS uid
            FROM google_tokens gt
            JOIN users u ON u.id = gt.user_id
            WHERE u.is_admin = TRUE AND gt.has_write_scope = TRUE AND u.is_active = TRUE
            LIMIT 1
            """
        )
    if not row:
        return None
    result = dict(row)
    uid = result.pop("uid")
    result["access_token"] = decrypt(result["access_token"])
    if result.get("refresh_token"):
        result["refresh_token"] = decrypt(result["refresh_token"])
    return uid, result


async def delete_google_token(user_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM google_tokens WHERE user_id = $1", user_id)


async def get_all_users_with_tokens() -> list[dict]:
    """Users who have connected Google Calendar."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT u.id, u.email, u.name, u.is_admin, gt.calendar_sync_enabled
            FROM users u
            JOIN google_tokens gt ON gt.user_id = u.id
            WHERE u.is_active = TRUE AND gt.calendar_sync_enabled = TRUE
            """
        )
    return [dict(r) for r in rows]


# ── Calendars ──────────────────────────────────────────────────────────────────

async def upsert_calendar(
    owner_user_id: int,
    google_calendar_id: str,
    name: str,
    is_primary: bool,
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO calendars
              (owner_user_id, google_calendar_id, name, is_primary)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (owner_user_id, google_calendar_id) DO UPDATE
              SET name = EXCLUDED.name,
                  is_primary = EXCLUDED.is_primary
            """,
            owner_user_id, google_calendar_id, name, is_primary,
        )


async def get_calendars(owner_user_id: int | None = None) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if owner_user_id:
            rows = await conn.fetch(
                "SELECT * FROM calendars WHERE owner_user_id = $1 ORDER BY is_primary DESC, name",
                owner_user_id,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT c.*, u.email AS owner_email, u.name AS owner_name
                FROM calendars c
                JOIN users u ON u.id = c.owner_user_id
                ORDER BY u.email, c.is_primary DESC, c.name
                """
            )
    return [dict(r) for r in rows]


async def set_calendar_record_enabled(calendar_id: int, enabled: bool) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE calendars SET record_enabled = $1 WHERE id = $2",
            enabled, calendar_id,
        )


async def get_enabled_calendars() -> list[dict]:
    """All calendars with recording enabled (for sync worker)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT c.*, u.id AS user_id
            FROM calendars c
            JOIN users u ON u.id = c.owner_user_id
            WHERE c.record_enabled = TRUE AND u.is_active = TRUE
            """
        )
    return [dict(r) for r in rows]


# ── Meetings ──────────────────────────────────────────────────────────────────

async def upsert_meeting(
    meeting_url: str,
    title: str,
    start_time: datetime,
) -> dict[str, Any]:
    """Insert or get existing meeting by URL. Returns meeting row."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO meetings (meeting_url, title, start_time)
            VALUES ($1, $2, $3)
            ON CONFLICT (meeting_url) DO UPDATE
              SET title = COALESCE(EXCLUDED.title, meetings.title),
                  updated_at = NOW(),
                  -- Always update start_time for pending meetings so the bot picks up
                  -- the new occurrence immediately (e.g. E2E test reusing a permanent URL).
                  -- For recording/transcribing/analyzing, keep start_time as-is.
                  start_time = CASE
                    WHEN meetings.status IN ('pending', 'error', 'done')
                    THEN EXCLUDED.start_time
                    ELSE meetings.start_time
                  END,
                  -- Reset error/done meetings to pending for new/current occurrences.
                  -- Also reset pending meetings whose start_time changed to a new day
                  -- (handles permanent Telemost rooms reused across calendar events).
                  status = CASE
                    WHEN meetings.status IN ('error', 'done')
                         AND (
                           EXCLUDED.start_time::date != meetings.start_time::date
                           OR EXCLUDED.start_time > NOW()
                         )
                    THEN 'pending'
                    ELSE meetings.status
                  END,
                  error_message = CASE
                    WHEN meetings.status IN ('error', 'done')
                         AND (
                           EXCLUDED.start_time::date != meetings.start_time::date
                           OR EXCLUDED.start_time > NOW()
                         )
                    THEN NULL
                    ELSE meetings.error_message
                  END
            RETURNING *
            """,
            meeting_url, title, start_time,
        )
    return dict(row)


async def get_meeting_by_url(meeting_url: str) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, title, status, start_time FROM meetings WHERE meeting_url = $1",
            meeting_url,
        )
    return dict(row) if row else None


async def claim_meeting_for_recording(meeting_id: str) -> bool:
    """
    Atomically claim a pending meeting for recording.
    Returns True if successfully claimed, False if already taken.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE meetings SET status = 'recording', updated_at = NOW()
            WHERE id = $1 AND status = 'pending'
            """,
            meeting_id,
        )
    return result == "UPDATE 1"


async def update_meeting_status(
    meeting_id: str,
    status: str,
    error_message: str | None = None,
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE meetings
            SET status = $1,
                error_message = COALESCE($2, error_message),
                updated_at = NOW()
            WHERE id = $3
            """,
            status, error_message, meeting_id,
        )


async def save_meeting_audio(
    meeting_id: str, audio_path: str, audio_size: int
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE meetings SET audio_path = $1, audio_size = $2, updated_at = NOW() WHERE id = $3",
            audio_path, audio_size, meeting_id,
        )


async def save_transcript(meeting_id: str, transcript: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE meetings SET transcript = $1, updated_at = NOW() WHERE id = $2",
            transcript, meeting_id,
        )


async def save_analysis(
    meeting_id: str,
    summary: str,
    tags: list[str],
    topic: str,
    meeting_type: str,
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE meetings
            SET summary = $1, tags = $2, topic = $3, meeting_type = $4,
                status = 'done', updated_at = NOW()
            WHERE id = $5
            """,
            summary, tags, topic, meeting_type, meeting_id,
        )


async def get_meeting(meeting_id: str) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM meetings WHERE id = $1", meeting_id)
    return dict(row) if row else None


async def get_meetings_for_user(
    user_id: int,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """
    Return meetings where:
    - user is in meeting_participants (matched by user_id), OR
    - user was a calendar attendee (calendar_meeting_links.attendee_emails contains their email), OR
    - admin access grant exists
    """
    pool = await get_pool()
    user = await get_user_by_id(user_id)
    if not user:
        return []
    email = user["email"]
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT m.id, m.title, m.start_time, m.end_time,
                   m.status, m.summary, m.tags, m.topic, m.meeting_type,
                   m.audio_path, m.audio_size, m.created_at
            FROM meetings m
            WHERE m.status = 'done'
              AND (
                -- participant matched to user account
                EXISTS (
                    SELECT 1 FROM meeting_participants mp
                    WHERE mp.meeting_id = m.id AND mp.user_id = $1
                )
                OR
                -- attendee email in calendar event
                EXISTS (
                    SELECT 1 FROM calendar_meeting_links cml
                    WHERE cml.meeting_id = m.id AND $2 = ANY(cml.attendee_emails)
                )
                OR
                -- explicit access grant
                EXISTS (
                    SELECT 1 FROM meeting_access_grants g
                    WHERE g.meeting_id = m.id AND g.user_id = $1
                )
              )
            ORDER BY m.start_time DESC
            LIMIT $3 OFFSET $4
            """,
            user_id, email, limit, offset,
        )
    return [dict(r) for r in rows]


async def get_all_meetings(limit: int = 100, offset: int = 0) -> list[dict]:
    """Admin: all meetings."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, title, start_time, end_time, status, summary, tags, topic,
                   meeting_type, audio_path, audio_size, error_message, created_at,
                   char_length(transcript) AS transcript_length
            FROM meetings
            ORDER BY start_time DESC NULLS LAST
            LIMIT $1 OFFSET $2
            """,
            limit, offset,
        )
    return [dict(r) for r in rows]


async def get_pending_meetings_to_start(within_minutes: int = 2) -> list[dict]:
    """Meetings that should start recording soon."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM meetings
            WHERE status = 'pending'
              AND start_time <= NOW() + ($1 * interval '1 minute')
              AND start_time >= NOW() - interval '30 minutes'
            ORDER BY start_time
            """,
            within_minutes,
        )
    return [dict(r) for r in rows]


async def reset_stuck_meetings() -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE meetings
            SET status = 'error',
                error_message = 'Прервано из-за перезапуска сервиса',
                updated_at = NOW()
            WHERE status IN ('recording', 'transcribing', 'analyzing')
            """
        )
    return int(result.split()[-1])


# ── Participants ───────────────────────────────────────────────────────────────

async def upsert_participant(
    meeting_id: str, name: str, email: str | None = None
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Try to resolve user_id by email
        user_id = None
        if email:
            row = await conn.fetchrow(
                "SELECT id FROM users WHERE email = $1", email.lower()
            )
            if row:
                user_id = row["id"]
        await conn.execute(
            """
            INSERT INTO meeting_participants (meeting_id, name, email, user_id)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (meeting_id, name) DO UPDATE
              SET email = COALESCE(EXCLUDED.email, meeting_participants.email),
                  user_id = COALESCE(EXCLUDED.user_id, meeting_participants.user_id)
            """,
            meeting_id, name, email, user_id,
        )


async def resolve_participants_by_email(meeting_id: str) -> None:
    """Try to match participant names to users by email after recording."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE meeting_participants mp
            SET user_id = u.id, email = u.email
            FROM users u
            WHERE mp.meeting_id = $1
              AND mp.user_id IS NULL
              AND mp.email IS NOT NULL
              AND lower(mp.email) = u.email
            """,
            meeting_id,
        )


async def get_participants(meeting_id: str) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT name, email, user_id FROM meeting_participants WHERE meeting_id = $1",
            meeting_id,
        )
    return [dict(r) for r in rows]


# ── Calendar meeting links ────────────────────────────────────────────────────

async def link_calendar_event_to_meeting(
    google_event_id: str,
    user_id: int,
    meeting_id: str,
    calendar_id: str,
    attendee_emails: list[str],
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO calendar_meeting_links
              (google_event_id, user_id, meeting_id, calendar_id, attendee_emails)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (google_event_id, user_id) DO UPDATE
              SET attendee_emails = EXCLUDED.attendee_emails
            """,
            google_event_id, user_id, meeting_id, calendar_id, attendee_emails,
        )


# ── Chat ──────────────────────────────────────────────────────────────────────

async def save_chat_message(
    user_id: int,
    role: str,
    content: str,
    meeting_id: str | None = None,
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO chat_messages (user_id, meeting_id, role, content)
            VALUES ($1, $2, $3, $4)
            """,
            user_id, meeting_id, role, content,
        )


async def get_chat_history(
    user_id: int,
    meeting_id: str | None = None,
    limit: int = 20,
) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if meeting_id:
            rows = await conn.fetch(
                """
                SELECT role, content, created_at FROM chat_messages
                WHERE user_id = $1 AND meeting_id = $2
                ORDER BY created_at DESC LIMIT $3
                """,
                user_id, meeting_id, limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT role, content, created_at FROM chat_messages
                WHERE user_id = $1 AND meeting_id IS NULL
                ORDER BY created_at DESC LIMIT $2
                """,
                user_id, limit,
            )
    return [dict(r) for r in reversed(rows)]


# ── Access grants ─────────────────────────────────────────────────────────────

async def grant_meeting_access(
    user_id: int, meeting_id: str, granted_by: int
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO meeting_access_grants (user_id, meeting_id, granted_by)
            VALUES ($1, $2, $3)
            ON CONFLICT DO NOTHING
            """,
            user_id, meeting_id, granted_by,
        )


async def revoke_meeting_access(user_id: int, meeting_id: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM meeting_access_grants WHERE user_id = $1 AND meeting_id = $2",
            user_id, meeting_id,
        )


# ── Invitations ───────────────────────────────────────────────────────────────

async def create_invitation(email: str, created_by: int, ttl_days: int = 7) -> dict[str, Any]:
    import secrets
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(days=ttl_days)
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO invitations (token, email, created_by, expires_at)
            VALUES ($1, $2, $3, $4)
            RETURNING *
            """,
            token, email.lower().strip(), created_by, expires_at,
        )
    return dict(row)


async def get_invitation_by_token(token: str) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM invitations WHERE token = $1 AND expires_at > NOW()",
            token,
        )
    return dict(row) if row else None


async def accept_invitation(token: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE invitations SET accepted_at = NOW() WHERE token = $1",
            token,
        )


async def list_invitations(limit: int = 100) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT i.id, i.token, i.email, i.expires_at, i.accepted_at, i.created_at,
                   u.name AS created_by_name
            FROM invitations i
            LEFT JOIN users u ON u.id = i.created_by
            ORDER BY i.created_at DESC
            LIMIT $1
            """,
            limit,
        )
    return [dict(r) for r in rows]


async def delete_invitation(invitation_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM invitations WHERE id = $1", invitation_id)


# ── Weekly meetings (for Dashboard) ──────────────────────────────────────────

async def get_meetings_this_week(user_id: int, is_admin: bool) -> list[dict]:
    """Meetings from last 7 days (done + summary) accessible to this user."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if is_admin:
            rows = await conn.fetch(
                """
                SELECT id, title, topic, start_time, end_time, summary, tags, meeting_type
                FROM meetings
                WHERE status = 'done'
                  AND summary IS NOT NULL
                  AND start_time >= NOW() - interval '7 days'
                ORDER BY start_time DESC
                LIMIT 20
                """,
            )
        else:
            user = await get_user_by_id(user_id)
            if not user:
                return []
            email = user["email"]
            rows = await conn.fetch(
                """
                SELECT DISTINCT m.id, m.title, m.topic, m.start_time, m.end_time,
                       m.summary, m.tags, m.meeting_type
                FROM meetings m
                WHERE m.status = 'done'
                  AND m.summary IS NOT NULL
                  AND m.start_time >= NOW() - interval '7 days'
                  AND (
                    EXISTS (
                        SELECT 1 FROM meeting_participants mp
                        WHERE mp.meeting_id = m.id AND mp.user_id = $1
                    )
                    OR EXISTS (
                        SELECT 1 FROM calendar_meeting_links cml
                        WHERE cml.meeting_id = m.id AND $2 = ANY(cml.attendee_emails)
                    )
                    OR EXISTS (
                        SELECT 1 FROM meeting_access_grants g
                        WHERE g.meeting_id = m.id AND g.user_id = $1
                    )
                  )
                ORDER BY m.start_time DESC
                LIMIT 20
                """,
                user_id, email,
            )
    return [dict(r) for r in rows]
