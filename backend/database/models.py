"""All asyncpg queries for telemost-web."""
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from database.connection import get_pool
from utils.encryption import encrypt, decrypt

logger = logging.getLogger(__name__)


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


async def create_preview_session(user_id: int, ttl_hours: int = 1) -> str:
    """Short-lived session flagged as a preview (admin 'view as user'). The flag
    lets the public /auth/preview activator accept it while refusing real
    login sessions."""
    import secrets
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO sessions (id, user_id, expires_at, is_preview) VALUES ($1, $2, $3, TRUE)",
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
    if not row:
        return None
    session = dict(row)
    # A preview session always behaves as a regular (non-admin) user — that is the
    # whole point of "view as user", even when the underlying account is an admin.
    if session.get("is_preview"):
        session["is_admin"] = False
    return session


async def delete_session(token: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM sessions WHERE id = $1", token)


async def purge_expired_sessions() -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM sessions WHERE expires_at <= NOW()")


# ── Browser recorder (extension) ────────────────────────────────────────────────

async def set_meeting_recorder_user(meeting_id: str, user_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE meetings SET recorder_user_id = $2, updated_at = NOW() WHERE id = $1",
            meeting_id, user_id,
        )


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


async def get_calendar_status(user_id: int) -> dict:
    """Return whether the user has connected Google Calendar and enabled any calendar."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        has_token = await conn.fetchval(
            "SELECT 1 FROM google_tokens WHERE user_id = $1", user_id
        )
        has_enabled = await conn.fetchval(
            "SELECT 1 FROM calendars WHERE owner_user_id = $1 AND record_enabled = TRUE", user_id
        )
        cal_count = await conn.fetchval(
            "SELECT COUNT(*) FROM calendars WHERE owner_user_id = $1", user_id
        ) or 0
    return {
        "connected": bool(has_token),
        "has_enabled_calendar": bool(has_enabled),
        "calendar_count": int(cal_count),
    }


async def auto_enable_primary_calendar(user_id: int) -> bool:
    """
    Enable record_enabled on the user's primary calendar (or first calendar).
    Called automatically after a user connects their Google Calendar.
    Returns True if a calendar was enabled.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Enable primary first, fall back to first available
        row = await conn.fetchrow(
            """
            UPDATE calendars
               SET record_enabled = TRUE
             WHERE owner_user_id = $1
               AND id = (
                   SELECT id FROM calendars
                    WHERE owner_user_id = $1
                    ORDER BY is_primary DESC, id
                    LIMIT 1
               )
            RETURNING id, name, is_primary
            """,
            user_id,
        )
    return row is not None


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
    google_event_id: str | None = None,
) -> dict[str, Any]:
    """Insert or update a meeting.

    Calendar meetings are deduplicated by ``google_event_id`` — each calendar
    occurrence (unique via singleEvents=True) gets its own row, so recurring
    meetings and permanent Telemost rooms reused across events never overwrite
    each other. Manual meetings (no calendar event) just insert a fresh row.

    Returns the meeting row.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        if not google_event_id:
            # Manual / non-calendar meeting: no dedup key, always a new row.
            row = await conn.fetchrow(
                """
                INSERT INTO meetings (meeting_url, title, start_time)
                VALUES ($1, $2, $3)
                RETURNING *
                """,
                meeting_url, title, start_time,
            )
            return dict(row)

        async with conn.transaction():
            existing = await conn.fetchrow(
                "SELECT id, status, start_time FROM meetings "
                "WHERE google_event_id = $1 FOR UPDATE",
                google_event_id,
            )

            if existing is None:
                row = await conn.fetchrow(
                    """
                    INSERT INTO meetings (meeting_url, title, start_time, google_event_id)
                    VALUES ($1, $2, $3, $4)
                    RETURNING *
                    """,
                    meeting_url, title, start_time, google_event_id,
                )
                return dict(row)

            old_start = existing["start_time"]
            # A finished occurrence (done/error) whose event was moved to a new,
            # still-recordable time is a genuine reschedule: keep the old recording
            # and split off a fresh pending occurrence so the bot records the new
            # time (and it shows up in the upcoming list).
            rescheduled = (
                existing["status"] in ("done", "error")
                and old_start is not None
                and abs((start_time - old_start).total_seconds()) > 60
                and start_time >= datetime.now(timezone.utc) - timedelta(minutes=30)
            )

            if rescheduled:
                # Release the google_event_id from the archived recording (its
                # transcript/audio/summary stay intact; the partial unique index
                # allows NULL) so the new pending row can hold it.
                await conn.execute(
                    "UPDATE meetings SET google_event_id = NULL, updated_at = NOW() WHERE id = $1",
                    existing["id"],
                )
                row = await conn.fetchrow(
                    """
                    INSERT INTO meetings (meeting_url, title, start_time, google_event_id)
                    VALUES ($1, $2, $3, $4)
                    RETURNING *
                    """,
                    meeting_url, title, start_time, google_event_id,
                )
                logger.info(
                    "Event %s rescheduled (%s → %s): archived recording %s, new occurrence %s",
                    google_event_id, old_start, start_time,
                    str(existing["id"])[:8], str(row["id"])[:8],
                )
                return dict(row)

            # Normal re-sync: update in place. Keep start_time fresh only while
            # still pending; otherwise freeze it so re-syncing the same occurrence
            # never disturbs an in-flight or finished recording.
            row = await conn.fetchrow(
                """
                UPDATE meetings
                   SET title = COALESCE($2, title),
                       meeting_url = $1,
                       updated_at = NOW(),
                       start_time = CASE WHEN status = 'pending' THEN $3 ELSE start_time END
                 WHERE id = $4
                RETURNING *
                """,
                meeting_url, title, start_time, existing["id"],
            )
            return dict(row)


async def get_meeting_by_url(meeting_url: str) -> dict[str, Any] | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        # meeting_url is no longer unique (one Telemost room can back many
        # meetings), so return the most recently created match.
        row = await conn.fetchrow(
            """
            SELECT id, title, status, start_time FROM meetings
            WHERE meeting_url = $1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            meeting_url,
        )
    return dict(row) if row else None


async def claim_meeting_for_recording(meeting_id: str) -> bool:
    """
    Atomically claim a pending meeting for recording.
    Returns True if successfully claimed, False if already taken.

    Refuses the claim if another meeting sharing the same Telemost URL is
    already being recorded/processed (or was just recorded) within a 15-minute
    window. This prevents sending several protocallers into the same room when
    two calendar events point at one permanent Telemost link (e.g. a recurring
    series plus a manually duplicated event). Genuinely different meetings that
    reuse a room are normally scheduled far apart, so the window leaves them
    untouched.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE meetings SET status = 'recording', updated_at = NOW()
            WHERE id = $1 AND status = 'pending'
              AND NOT EXISTS (
                  SELECT 1 FROM meetings o
                  WHERE o.meeting_url = meetings.meeting_url
                    AND o.id <> meetings.id
                    AND o.status IN ('recording', 'transcribing', 'analyzing', 'done')
                    AND abs(extract(epoch FROM (o.start_time - meetings.start_time))) < 900
              )
            """,
            meeting_id,
        )
    return result == "UPDATE 1"


async def mark_duplicate_if_sibling_active(meeting_id: str) -> bool:
    """
    Mark a pending meeting as a duplicate (status='error') if another meeting
    with the same Telemost URL is already recording/processing/done within a
    15-minute window. Keeps duplicate calendar events for one room from
    lingering as pending and later sending a lone protocaller into an
    already-finished meeting. Returns True if it was marked.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE meetings m
            SET status = 'error',
                error_message = 'Дубль звонка: запись для этой ссылки уже идёт или сделана',
                updated_at = NOW()
            WHERE m.id = $1 AND m.status = 'pending'
              AND EXISTS (
                  SELECT 1 FROM meetings o
                  WHERE o.meeting_url = m.meeting_url
                    AND o.id <> m.id
                    AND o.status IN ('recording', 'transcribing', 'analyzing', 'done')
                    AND abs(extract(epoch FROM (o.start_time - m.start_time))) < 900
              )
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


async def update_meeting_tags(meeting_id: str, tags: list[str]) -> None:
    """Overwrite a meeting's tags (manual edit from the web UI)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE meetings SET tags = $2, updated_at = NOW() WHERE id = $1",
            meeting_id, tags,
        )


async def get_known_tags(limit: int = 200) -> list[str]:
    """Distinct tags ever used across meetings, most frequent first. Serves both
    the AI tagging step (reuse existing tags) and the web UI autocomplete/filter."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT tag, COUNT(*) AS cnt
            FROM meetings, unnest(tags) AS tag
            WHERE tags IS NOT NULL
            GROUP BY tag
            ORDER BY cnt DESC, tag ASC
            LIMIT $1
            """,
            limit,
        )
    return [r["tag"] for r in rows]


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
                OR
                -- the user who recorded/uploaded it (e.g. browser extension)
                m.recorder_user_id = $1
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


async def get_demo_meetings(limit: int = 200) -> list[dict]:
    """All completed meetings tagged "демо" (the curated demo set), newest first."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, title, start_time, end_time, status, summary, tags, topic,
                   meeting_type, audio_path, audio_size, created_at
            FROM meetings
            WHERE status = 'done'
              AND EXISTS (SELECT 1 FROM unnest(tags) t WHERE lower(t) = 'демо')
            ORDER BY start_time DESC NULLS LAST
            LIMIT $1
            """,
            limit,
        )
    return [dict(r) for r in rows]


async def get_recent_meetings_with_transcripts(
    days: int = 90,
    limit: int = 1000,
) -> list[dict]:
    """Admin global chat: completed meetings within the last `days` that have a
    transcript, newest first. Returns the FULL transcript text (not truncated)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, title, topic, start_time, end_time, transcript, tags
            FROM meetings
            WHERE status = 'done'
              AND transcript IS NOT NULL
              AND start_time >= NOW() - make_interval(days => $1::int)
            ORDER BY start_time DESC
            LIMIT $2
            """,
            days, limit,
        )
    return [dict(r) for r in rows]


async def get_recent_meetings_with_transcripts_for_user(
    user_id: int,
    days: int = 90,
    limit: int = 1000,
) -> list[dict]:
    """Non-admin global chat: same as above but restricted to meetings the user
    can access (participant / calendar attendee / explicit grant)."""
    pool = await get_pool()
    user = await get_user_by_id(user_id)
    if not user:
        return []
    email = user["email"]
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT m.id, m.title, m.topic, m.start_time, m.end_time, m.transcript, m.tags
            FROM meetings m
            WHERE m.status = 'done'
              AND m.transcript IS NOT NULL
              AND m.start_time >= NOW() - make_interval(days => $3::int)
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
            LIMIT $4
            """,
            user_id, email, days, limit,
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
              SET attendee_emails = EXCLUDED.attendee_emails,
                  meeting_id = EXCLUDED.meeting_id,
                  calendar_id = EXCLUDED.calendar_id
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
    # granted_by=0 means system/API-key — store as NULL to avoid FK violation
    granted_by_val = granted_by if granted_by > 0 else None
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO meeting_access_grants (user_id, meeting_id, granted_by)
            VALUES ($1, $2, $3)
            ON CONFLICT DO NOTHING
            """,
            user_id, meeting_id, granted_by_val,
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

async def get_upcoming_meetings_for_user(user_id: int, is_admin: bool) -> list[dict]:
    """Pending/active meetings visible to this user (for calendar tab)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if is_admin:
            rows = await conn.fetch(
                """
                SELECT id, title, topic, start_time, end_time, status, meeting_type
                FROM meetings
                WHERE status IN ('pending', 'recording', 'transcribing', 'analyzing')
                  AND start_time > NOW() - interval '2 hours'
                ORDER BY start_time ASC
                LIMIT 100
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
                       m.status, m.meeting_type
                FROM meetings m
                WHERE m.status IN ('pending', 'recording', 'transcribing', 'analyzing')
                  AND m.start_time > NOW() - interval '2 hours'
                  AND (
                    EXISTS (SELECT 1 FROM calendar_meeting_links cml
                            WHERE cml.meeting_id = m.id AND cml.user_id = $1)
                    OR EXISTS (SELECT 1 FROM calendar_meeting_links cml
                               WHERE cml.meeting_id = m.id AND $2 = ANY(cml.attendee_emails))
                    OR EXISTS (SELECT 1 FROM meeting_access_grants g
                               WHERE g.meeting_id = m.id AND g.user_id = $1)
                  )
                ORDER BY m.start_time ASC
                LIMIT 100
                """,
                user_id, email,
            )
    return [dict(r) for r in rows]


async def get_meetings_this_week(user_id: int, is_admin: bool) -> list[dict]:
    """Meetings from last 7 days (done + summary) accessible to this user."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if is_admin:
            rows = await conn.fetch(
                """
                SELECT id, title, topic, start_time, end_time, summary, tags,
                       meeting_type, updated_at, transcript
                FROM meetings
                WHERE status = 'done'
                  AND transcript IS NOT NULL
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
                       m.summary, m.tags, m.meeting_type, m.updated_at, m.transcript
                FROM meetings m
                WHERE m.status = 'done'
                  AND m.transcript IS NOT NULL
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


async def get_week_summary_cache(user_id: int) -> dict | None:
    """Cached "итоги недели" for a user, or None if never generated."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT signature, summary, meeting_count FROM week_summaries WHERE user_id = $1",
            user_id,
        )
    return dict(row) if row else None


async def upsert_week_summary_cache(
    user_id: int,
    signature: str,
    summary: str | None,
    meeting_count: int,
) -> None:
    """Store/refresh the cached weekly summary for a user."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO week_summaries (user_id, signature, summary, meeting_count, generated_at)
            VALUES ($1, $2, $3, $4, NOW())
            ON CONFLICT (user_id) DO UPDATE
            SET signature     = EXCLUDED.signature,
                summary       = EXCLUDED.summary,
                meeting_count = EXCLUDED.meeting_count,
                generated_at  = NOW()
            """,
            user_id, signature, summary, meeting_count,
        )


# ── Communications: Mattermost + Gmail ────────────────────────────────────────

async def get_user_emails() -> list[str]:
    """All non-null user emails (Gmail sync targets only these)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT email FROM users WHERE email IS NOT NULL")
    return [r["email"] for r in rows]


async def get_sync_state(source: str) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT source, last_synced_at, last_cursor FROM sync_state WHERE source = $1",
            source,
        )
    return dict(row) if row else None


async def upsert_sync_state(source: str, last_synced_at, last_cursor: str | None) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO sync_state (source, last_synced_at, last_cursor)
            VALUES ($1, $2, $3)
            ON CONFLICT (source) DO UPDATE
              SET last_synced_at = EXCLUDED.last_synced_at,
                  last_cursor    = EXCLUDED.last_cursor
            """,
            source, last_synced_at, last_cursor,
        )


async def insert_mm_messages(rows: list[dict]) -> int:
    """Bulk-insert Mattermost posts; skip duplicates. Returns count attempted."""
    if not rows:
        return 0
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO mm_messages
              (id, channel_id, channel_name, user_id, username, message, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (id) DO NOTHING
            """,
            [
                (r["id"], r["channel_id"], r.get("channel_name"), r.get("user_id"),
                 r.get("username"), r["message"], r["created_at"])
                for r in rows
            ],
        )
    return len(rows)


async def insert_email_messages(rows: list[dict]) -> int:
    if not rows:
        return 0
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO email_messages
              (id, user_email, from_email, to_emails, subject, body_text, received_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (id) DO NOTHING
            """,
            [
                (r["id"], r["user_email"], r.get("from_email"), r.get("to_emails") or [],
                 r.get("subject"), r.get("body_text"), r["received_at"])
                for r in rows
            ],
        )
    return len(rows)


async def query_mm_messages(
    channel_id: str | None = None,
    user_id: str | None = None,
    date_from=None,
    date_to=None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    conds, args = [], []
    def add(expr, val):
        args.append(val)
        conds.append(expr.format(len(args)))
    if channel_id: add("channel_id = ${}", channel_id)
    if user_id:    add("user_id = ${}", user_id)
    if date_from:  add("created_at >= ${}", date_from)
    if date_to:    add("created_at <= ${}", date_to)
    if q:          add("message ILIKE ${}", f"%{q}%")
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    args.append(limit); lim = len(args)
    args.append(offset); off = len(args)
    sql = f"SELECT * FROM mm_messages {where} ORDER BY created_at DESC LIMIT ${lim} OFFSET ${off}"
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return [dict(r) for r in rows]


async def query_email_messages(
    user_email: str | None = None,
    date_from=None,
    date_to=None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    conds, args = [], []
    def add(expr, val):
        args.append(val)
        conds.append(expr.format(len(args)))
    if user_email: add("user_email = ${}", user_email)
    if date_from:  add("received_at >= ${}", date_from)
    if date_to:    add("received_at <= ${}", date_to)
    if q:
        args.append(f"%{q}%")
        n = len(args)
        conds.append(f"(subject ILIKE ${n} OR body_text ILIKE ${n})")
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    args.append(limit); lim = len(args)
    args.append(offset); off = len(args)
    sql = f"SELECT * FROM email_messages {where} ORDER BY received_at DESC LIMIT ${lim} OFFSET ${off}"
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return [dict(r) for r in rows]


async def search_mm_messages(
    query_text: str,
    date_from=None,
    date_to=None,
    channel_id: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """Most relevant MM posts to `query_text` within the period (FTS, ts_rank)."""
    args = [query_text]
    extra = []
    def add(expr, val):
        args.append(val)
        extra.append(expr.format(len(args)))
    if date_from:  add("m.created_at >= ${}", date_from)
    if date_to:    add("m.created_at <= ${}", date_to)
    if channel_id: add("m.channel_id = ${}", channel_id)
    args.append(limit); lim = len(args)
    extra_sql = (" AND " + " AND ".join(extra)) if extra else ""
    # Broaden the query to OR semantics (any term) so a multi-word question still
    # matches; ts_rank then surfaces the messages matching the most/strongest terms.
    sql = f"""
        WITH q AS (SELECT replace(websearch_to_tsquery('russian', $1)::text, '&', '|')::tsquery AS tq)
        SELECT m.*, ts_rank(to_tsvector('russian', m.message), q.tq) AS rank
        FROM mm_messages m, q
        WHERE q.tq @@ to_tsvector('russian', m.message){extra_sql}
        ORDER BY rank DESC, m.created_at DESC
        LIMIT ${lim}
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return [dict(r) for r in rows]


async def search_email_messages(
    query_text: str,
    date_from=None,
    date_to=None,
    user_email: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """Most relevant emails (subject+body) to `query_text` within the period."""
    args = [query_text]
    extra = []
    def add(expr, val):
        args.append(val)
        extra.append(expr.format(len(args)))
    if date_from:   add("e.received_at >= ${}", date_from)
    if date_to:     add("e.received_at <= ${}", date_to)
    if user_email:  add("e.user_email = ${}", user_email)
    args.append(limit); lim = len(args)
    extra_sql = (" AND " + " AND ".join(extra)) if extra else ""
    sql = f"""
        WITH q AS (SELECT replace(websearch_to_tsquery('russian', $1)::text, '&', '|')::tsquery AS tq)
        SELECT e.*,
               ts_rank(to_tsvector('russian', coalesce(e.subject,'') || ' ' || coalesce(e.body_text,'')), q.tq) AS rank
        FROM email_messages e, q
        WHERE q.tq @@ to_tsvector('russian', coalesce(e.subject,'') || ' ' || coalesce(e.body_text,'')){extra_sql}
        ORDER BY rank DESC, e.received_at DESC
        LIMIT ${lim}
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
    return [dict(r) for r in rows]


async def distinct_mm_channels() -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT channel_id, MAX(channel_name) AS channel_name, COUNT(*) AS count
            FROM mm_messages
            GROUP BY channel_id
            ORDER BY channel_name NULLS LAST
            """
        )
    return [dict(r) for r in rows]


async def distinct_email_users() -> list[str]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT user_email FROM email_messages ORDER BY user_email"
        )
    return [r["user_email"] for r in rows]


async def comms_stats() -> dict:
    """Counts + sync cursors — for the comms debug endpoint."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        mm = await conn.fetchval("SELECT COUNT(*) FROM mm_messages")
        em = await conn.fetchval("SELECT COUNT(*) FROM email_messages")
        ss = await conn.fetch(
            "SELECT source, last_synced_at, last_cursor FROM sync_state ORDER BY source"
        )
    return {"mm_messages": mm, "email_messages": em, "sync_state": [dict(r) for r in ss]}
