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
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

-- ── Google Calendar tokens (per user, for calendar sync) ─────────────────────
CREATE TABLE IF NOT EXISTS google_tokens (
    user_id       BIGINT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    access_token  TEXT NOT NULL,         -- Fernet-encrypted
    refresh_token TEXT,                  -- Fernet-encrypted
    token_expiry  TIMESTAMP,
    calendar_sync_enabled BOOLEAN DEFAULT TRUE,
    updated_at    TIMESTAMP DEFAULT NOW()
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
    -- pending → recording → transcribing → analyzing → done | error
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

-- ── Access grants (admin can grant access to specific meetings) ───────────────
CREATE TABLE IF NOT EXISTS meeting_access_grants (
    user_id    BIGINT REFERENCES users(id) ON DELETE CASCADE,
    meeting_id UUID REFERENCES meetings(id) ON DELETE CASCADE,
    granted_by BIGINT REFERENCES users(id),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (user_id, meeting_id)
);

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
