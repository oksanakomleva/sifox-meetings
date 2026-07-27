-- ── Users (authenticated via Google OAuth) ──────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id          BIGSERIAL PRIMARY KEY,
    email       TEXT UNIQUE NOT NULL,
    name        TEXT,
    avatar_url  TEXT,
    google_id   TEXT UNIQUE,
    is_admin    BOOLEAN DEFAULT FALSE,
    is_active   BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMP DEFAULT NOW(),
    last_login  TIMESTAMP
);

-- ── Web sessions ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,         -- random token
    user_id    BIGINT REFERENCES users(id) ON DELETE CASCADE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    is_preview BOOLEAN NOT NULL DEFAULT FALSE  -- admin "view as user" impersonation
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);
-- Migration: add is_preview to existing sessions tables
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS is_preview BOOLEAN NOT NULL DEFAULT FALSE;

-- ── Google Calendar tokens (per user, for calendar sync) ─────────────────────
CREATE TABLE IF NOT EXISTS google_tokens (
    user_id       BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    access_token  TEXT NOT NULL,         -- Fernet-encrypted
    refresh_token TEXT,                  -- Fernet-encrypted
    token_expiry  TIMESTAMP,
    calendar_sync_enabled BOOLEAN DEFAULT TRUE,
    has_write_scope BOOLEAN DEFAULT FALSE,  -- TRUE only for E2E test admin (calendar write)
    updated_at    TIMESTAMP DEFAULT NOW()
);
-- Migration: add has_write_scope if missing
ALTER TABLE google_tokens ADD COLUMN IF NOT EXISTS has_write_scope BOOLEAN DEFAULT FALSE;

-- ── Gmail send tokens (per user, personal OAuth gmail.send) ──────────────────
-- Kept SEPARATE from google_tokens so granting send access never clobbers the
-- calendar token and a personal gmail used only for sending isn't pulled into
-- calendar sync.
CREATE TABLE IF NOT EXISTS gmail_send_tokens (
    user_id       BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    access_token  TEXT NOT NULL,         -- Fernet-encrypted
    refresh_token TEXT,                  -- Fernet-encrypted
    token_expiry  TIMESTAMPTZ,
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- ── Calendars available for recording ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS calendars (
    id                  BIGSERIAL PRIMARY KEY,
    owner_user_id       BIGINT REFERENCES users(id) ON DELETE CASCADE,
    google_calendar_id  TEXT NOT NULL,
    name                TEXT,
    is_primary          BOOLEAN DEFAULT FALSE,
    record_enabled      BOOLEAN DEFAULT FALSE,  -- admin toggles this
    created_at          TIMESTAMP DEFAULT NOW(),
    UNIQUE (owner_user_id, google_calendar_id)
);

-- ── Meetings (deduplicated by telemost URL) ───────────────────────────────────
CREATE TABLE IF NOT EXISTS meetings (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    meeting_url   TEXT UNIQUE NOT NULL,
    title         TEXT,
    start_time    TIMESTAMP WITH TIME ZONE,
    end_time      TIMESTAMP WITH TIME ZONE,
    status        TEXT DEFAULT 'pending',
    -- pending → recording → transcribing → analyzing → done | error | no_show
    -- no_show: bot joined but nobody came (not a failure)
    transcript    TEXT,
    summary       TEXT,
    tags          TEXT[],
    topic         TEXT,
    meeting_type  TEXT,
    audio_path    TEXT,                  -- relative to AUDIO_DIR
    audio_size    BIGINT,                -- bytes
    error_message TEXT,
    recorder_user_id BIGINT REFERENCES users(id),  -- who triggered recording
    created_at    TIMESTAMP DEFAULT NOW(),
    updated_at    TIMESTAMP DEFAULT NOW()
);
-- Migration: timestamp of when the protocol was last e-mailed to participants
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS protocol_sent_at TIMESTAMPTZ;
-- Backfill: meetings that failed only because nobody showed up were marked
-- 'error'/'Empty transcription' — reclassify them as the benign 'no_show'.
UPDATE meetings SET status='no_show', error_message=NULL
 WHERE status='error' AND error_message='Empty transcription';
-- Live assistant: host opt-in to FULL data access on a meeting with external
-- guests (NULL/false = auto scope by attendee domains).
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS assistant_full_access BOOLEAN DEFAULT FALSE;
-- Per-meeting live-assistant opt-in. The global environment flag remains the
-- emergency kill switch; this field prevents a pilot from affecting all calls.
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS assistant_enabled BOOLEAN DEFAULT FALSE;
-- Make a meeting visible in EVERY user's "Мои встречи" (for company-wide /
-- uploaded shared recordings). Per-user grants live in meeting_access_grants.
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS visible_to_all BOOLEAN DEFAULT FALSE;

-- ── Public share links (view a meeting by direct link + password, no login) ──
CREATE TABLE IF NOT EXISTS meeting_shares (
    token         TEXT PRIMARY KEY,         -- secrets.token_urlsafe(24)
    meeting_id    UUID NOT NULL REFERENCES meetings(id) ON DELETE CASCADE,
    password_hash TEXT NOT NULL,            -- pbkdf2: 'iterations$salt_hex$hash_hex'
    created_by    BIGINT REFERENCES users(id),
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    expires_at    TIMESTAMPTZ               -- NULL = never expires
);
CREATE INDEX IF NOT EXISTS idx_meeting_shares_meeting ON meeting_shares(meeting_id);

-- ── Live in-meeting assistant: Q&A audit log ─────────────────────────────────
CREATE TABLE IF NOT EXISTS live_qa (
    id          BIGSERIAL PRIMARY KEY,
    meeting_id  UUID REFERENCES meetings(id) ON DELETE CASCADE,
    asked_at    TIMESTAMPTZ DEFAULT NOW(),
    question    TEXT NOT NULL,
    answer      TEXT,
    scope       TEXT,                    -- 'full' | 'meeting_only'
    sources     TEXT[]                   -- which sources fed the answer
);
ALTER TABLE live_qa ADD COLUMN IF NOT EXISTS spoken BOOLEAN DEFAULT FALSE;
ALTER TABLE live_qa ADD COLUMN IF NOT EXISTS latency_ms INTEGER;
ALTER TABLE live_qa ADD COLUMN IF NOT EXISTS error TEXT;
CREATE INDEX IF NOT EXISTS idx_live_qa_meeting ON live_qa(meeting_id, asked_at);
CREATE INDEX IF NOT EXISTS idx_meetings_status    ON meetings(status);
CREATE INDEX IF NOT EXISTS idx_meetings_start     ON meetings(start_time);
CREATE INDEX IF NOT EXISTS idx_meetings_url       ON meetings(meeting_url);

-- ── Meeting participants ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS meeting_participants (
    meeting_id  UUID REFERENCES meetings(id) ON DELETE CASCADE,
    email       TEXT,                    -- NULL if email not resolved yet
    name        TEXT NOT NULL,
    user_id     BIGINT REFERENCES users(id),  -- resolved after matching
    PRIMARY KEY (meeting_id, name)
);
CREATE INDEX IF NOT EXISTS idx_participants_email   ON meeting_participants(email);
CREATE INDEX IF NOT EXISTS idx_participants_user_id ON meeting_participants(user_id);

-- ── Calendar events that triggered meeting recording ─────────────────────────
CREATE TABLE IF NOT EXISTS calendar_meeting_links (
    google_event_id TEXT NOT NULL,
    user_id         BIGINT REFERENCES users(id) ON DELETE CASCADE,
    meeting_id      UUID REFERENCES meetings(id) ON DELETE CASCADE,
    calendar_id     TEXT,
    attendee_emails TEXT[],             -- all attendees from this event
    created_at      TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (google_event_id, user_id)
);

-- ── AI chat messages ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chat_messages (
    id         BIGSERIAL PRIMARY KEY,
    user_id    BIGINT REFERENCES users(id) ON DELETE CASCADE,
    meeting_id UUID REFERENCES meetings(id) ON DELETE CASCADE,  -- NULL = global chat
    role       TEXT NOT NULL,           -- 'user' | 'assistant'
    content    TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_chat_user_meeting ON chat_messages(user_id, meeting_id);

-- ── Weekly summary cache ──────────────────────────────────────────────────────
-- "Итоги недели" used to be regenerated via OpenAI on every Dashboard load.
-- We now cache one row per user and only regenerate when the set of in-window
-- meetings changes. `signature` is a hash over (id, updated_at) of the meetings
-- that fed the summary, so new/edited/expired meetings all invalidate the cache.
CREATE TABLE IF NOT EXISTS week_summaries (
    user_id       BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    signature     TEXT NOT NULL,
    summary       TEXT,
    meeting_count INT NOT NULL DEFAULT 0,
    generated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ── Access grants (admin can grant access to specific meetings) ───────────────
CREATE TABLE IF NOT EXISTS meeting_access_grants (
    user_id    BIGINT REFERENCES users(id) ON DELETE CASCADE,
    meeting_id UUID REFERENCES meetings(id) ON DELETE CASCADE,
    granted_by BIGINT REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (user_id, meeting_id)
);

-- ── Invitations (admin invites users by email) ───────────────────────────────
CREATE TABLE IF NOT EXISTS invitations (
    id          BIGSERIAL PRIMARY KEY,
    token       TEXT UNIQUE NOT NULL,
    email       TEXT NOT NULL,
    created_by  BIGINT REFERENCES users(id) ON DELETE SET NULL,
    expires_at  TIMESTAMPTZ NOT NULL,
    accepted_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_invitations_token ON invitations(token);
CREATE INDEX IF NOT EXISTS idx_invitations_email ON invitations(email);

-- ── Migrations: convert TIMESTAMP → TIMESTAMPTZ for existing tables ───────────
DO $$
DECLARE
    col RECORD;
    cols TEXT[][] := ARRAY[
        ARRAY['sessions',              'expires_at'],
        ARRAY['sessions',              'created_at'],
        ARRAY['users',                 'created_at'],
        ARRAY['users',                 'last_login'],
        ARRAY['google_tokens',         'token_expiry'],
        ARRAY['google_tokens',         'updated_at'],
        ARRAY['calendars',             'created_at'],
        ARRAY['meetings',              'created_at'],
        ARRAY['meetings',              'updated_at'],
        ARRAY['calendar_meeting_links','created_at'],
        ARRAY['chat_messages',         'created_at'],
        ARRAY['meeting_access_grants', 'created_at']
    ];
    i INT;
BEGIN
    FOR i IN 1..array_length(cols, 1) LOOP
        SELECT data_type INTO col
        FROM information_schema.columns
        WHERE table_name = cols[i][1] AND column_name = cols[i][2];
        IF col.data_type = 'timestamp without time zone' THEN
            EXECUTE format(
                'ALTER TABLE %I ALTER COLUMN %I TYPE TIMESTAMPTZ USING %I AT TIME ZONE ''UTC''',
                cols[i][1], cols[i][2], cols[i][2]
            );
        END IF;
    END LOOP;
END $$;

-- ── Migration: per-occurrence meetings keyed by google_event_id ───────────────
-- Previously meetings were deduped by meeting_url (UNIQUE). That broke recurring
-- meetings and any case where several distinct events reuse one permanent Telemost
-- room: occurrences collapsed into a single row and overwrote each other's
-- recording/transcript/summary. We now key calendar meetings by google_event_id
-- (each occurrence is a unique event via singleEvents=True), so every occurrence is
-- its own row and nothing is overwritten.

-- 1. Add the column (nullable: manual/non-calendar meetings keep it NULL).
ALTER TABLE meetings ADD COLUMN IF NOT EXISTS google_event_id TEXT;

-- 2. Backfill existing rows from their calendar link. A recurring meeting that was
--    collapsed into one row has many links; take the most recent one so the row
--    stays attached to its latest synced occurrence and is not duplicated on the
--    next sync. Same google_event_id always maps to one meeting (old URL-dedup
--    invariant), so this cannot create duplicate google_event_id values.
UPDATE meetings m
SET google_event_id = sub.google_event_id
FROM (
    SELECT DISTINCT ON (l.meeting_id) l.meeting_id, l.google_event_id
    FROM calendar_meeting_links l
    ORDER BY l.meeting_id, l.created_at DESC
) sub
WHERE sub.meeting_id = m.id AND m.google_event_id IS NULL;

-- 3. Drop the UNIQUE constraint on meeting_url (auto-named meetings_meeting_url_key).
--    A permanent Telemost room may now back many meetings. The non-unique
--    idx_meetings_url index is kept for lookups.
ALTER TABLE meetings DROP CONSTRAINT IF EXISTS meetings_meeting_url_key;

-- 4. New dedup key: partial unique index on google_event_id (ON CONFLICT target).
CREATE UNIQUE INDEX IF NOT EXISTS idx_meetings_google_event_id
    ON meetings(google_event_id) WHERE google_event_id IS NOT NULL;

-- Note: the browser extension authenticates by reusing the user's web login
-- (Google) session cookie via X-Session-Token — no separate token table needed.

-- ── Communications: Mattermost messages + Gmail emails ────────────────────────
CREATE TABLE IF NOT EXISTS mm_messages (
    id           TEXT PRIMARY KEY,          -- post id from Mattermost
    channel_id   TEXT NOT NULL,
    channel_name TEXT,
    user_id      TEXT,
    username     TEXT,
    message      TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL,
    fetched_at   TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mm_channel_time ON mm_messages(channel_id, created_at DESC);

CREATE TABLE IF NOT EXISTS email_messages (
    id          TEXT PRIMARY KEY,           -- gmail message id
    user_email  TEXT NOT NULL REFERENCES users(email) ON DELETE CASCADE,
    from_email  TEXT,
    to_emails   TEXT[],
    subject     TEXT,
    body_text   TEXT,
    received_at TIMESTAMPTZ NOT NULL,
    fetched_at  TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_email_user_time ON email_messages(user_email, received_at DESC);

-- Full-text search (relevance ranking for the AI context). 'russian' config.
CREATE INDEX IF NOT EXISTS idx_mm_fts ON mm_messages
    USING gin (to_tsvector('russian', message));
CREATE INDEX IF NOT EXISTS idx_email_fts ON email_messages
    USING gin (to_tsvector('russian', coalesce(subject, '') || ' ' || coalesce(body_text, '')));

-- Incremental sync cursors. source: 'mattermost:{channel_id}' | 'gmail:{email}'
CREATE TABLE IF NOT EXISTS sync_state (
    source         TEXT PRIMARY KEY,
    last_synced_at TIMESTAMPTZ,
    last_cursor    TEXT                       -- MM: last post create_at (ms); Gmail: historyId
);

-- ── Imported phone calls (rec.megafon.ru → demo "Звонки" section) ─────────────
-- Separate from meetings: call-specific metadata + full transcribe/analyze
-- pipeline. Dedup/incremental import keyed by external_id (MegaFon's call id).
CREATE TABLE IF NOT EXISTS calls (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_id   TEXT UNIQUE NOT NULL,      -- MegaFon call id → dedup / incremental
    title         TEXT,                      -- AI-generated short call title
    phone         TEXT,
    direction     TEXT,                      -- 'in' / 'out'
    started_at    TIMESTAMPTZ,
    duration_sec  INTEGER,
    audio_path    TEXT,                      -- relative to AUDIO_DIR (mp3)
    audio_size    BIGINT,
    status        TEXT DEFAULT 'pending',    -- pending → transcribing → analyzing → done | error
    transcript    TEXT,                      -- timecoded + speaker-labelled (как _build_transcript)
    summary       TEXT,
    tasks         JSONB,                     -- for the "Задачи" UI block
    reminders     JSONB,                     -- for the "Напоминания" UI block
    tags          TEXT[],
    analysis      JSONB,                     -- optional scores (переговоры/коммуникация/собеседник)
    error_message TEXT,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_calls_started ON calls(started_at DESC);
