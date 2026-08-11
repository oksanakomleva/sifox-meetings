import os
from pydantic_settings import BaseSettings


class Config(BaseSettings):
    # ── App ───────────────────────────────────────────────────────────────
    SECRET_KEY: str                          # random 32-byte hex
    BASE_URL: str = "http://localhost:8000"
    ALLOWED_DOMAIN: str = "sifox.com"
    ADMIN_EMAILS: str = "oksana.komleva@sifox.com"

    # ── Database ──────────────────────────────────────────────────────────
    DATABASE_URL: str                        # postgresql://...

    # ── Google OAuth (web login) ──────────────────────────────────────────
    GOOGLE_CLIENT_ID: str
    GOOGLE_CLIENT_SECRET: str

    @property
    def GOOGLE_REDIRECT_URI(self) -> str:
        return f"{self.BASE_URL}/api/auth/callback"

    # ── Google Calendar OAuth (for calendar sync, separate consent) ───────
    # Uses same GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET

    # ── OpenAI ────────────────────────────────────────────────────────────
    OPENAI_API_KEY: str
    OPENAI_MODEL: str = "gpt-4o"             # used by analyzer (protocols, tags)
    # Chat feeds full transcripts into context, so it needs a big window.
    CHAT_MODEL: str = "gpt-4.1"              # ~1M-token context window
    CHAT_CONTEXT_DAYS: int = 90              # global chat: how far back to pull transcripts
    CHAT_MAX_CONTEXT_CHARS: int = 1_200_000  # safety cap on assembled transcript context

    # ── Whisper ───────────────────────────────────────────────────────────
    WHISPER_MODEL: str = "medium"

    # ── Storage ───────────────────────────────────────────────────────────
    AUDIO_DIR: str = "/audio"
    USER_UPLOAD_QUOTA_GB: int = 5

    # ── Encryption ────────────────────────────────────────────────────────
    ENCRYPTION_KEY: str                      # Fernet key (base64)

    # ── Recording ─────────────────────────────────────────────────────────
    JOIN_BEFORE_MINUTES: int = 1
    # Telemost can publish a guest to other participants before its own page
    # finishes switching from pre-join to the in-call toolbar. Keep the browser
    # alive long enough to cover that lag and, when applicable, organizer admit.
    TELEMOST_JOIN_TIMEOUT_SEC: int = 90
    TELEMOST_JOIN_GRACE_AFTER_START_SEC: int = 90
    TELEMOST_JOIN_MAX_WAIT_SEC: int = 300
    TELEMOST_JOIN_RETRY_SEC: int = 10
    PARTICIPANT_POLL_INTERVAL: int = 30      # seconds between polls
    EMPTY_POLLS_TO_END: int = 3             # ~90s after everyone leaves
    MAX_RECORDING_HOURS: int = 4
    # Keep Chromium sandboxed by default. Set only as an emergency compatibility
    # fallback on a platform that cannot provide user namespaces.
    CHROMIUM_DISABLE_SANDBOX: bool = False

    # ── Live in-meeting assistant ("Протоколлер, …") ──────────────────────
    # All gated behind LIVE_ASSISTANT_ENABLED (default off) — when off, the
    # recorder behaves exactly as before.
    LIVE_ASSISTANT_ENABLED: bool = False
    # Keep per-meeting opt-in as the default rollout mode. Set this only after a
    # successful pilot if every recorded meeting should get the assistant.
    LIVE_ASSISTANT_ALL_MEETINGS: bool = False
    LIVE_WAKE_WORD: str = "протоколлер"      # stem-matched (see live_assistant)
    # Short wake-word windows need the same recognition quality as the final
    # transcript. The tiny local model repeatedly missed "Протоколлер" in real
    # Telemost audio, even though the post-meeting model heard the whole phrase.
    LIVE_WAKE_STT: str = "openai"             # "openai" | "local"
    LIVE_WAKE_STT_MODEL: str = "whisper-1"
    LIVE_WAKE_MODEL: str = "tiny"            # cheap continuous wake-word STT
    LIVE_QUESTION_MODEL: str = "small"       # accurate STT for the question only
    LIVE_WINDOW_SEC: int = 5                 # rolling window length per STT pass
    LIVE_POLL_SEC: int = 2                   # overlapping wake-word checks
    LIVE_MIN_RMS: int = 120                  # skip near-silent PCM before cloud STT
    LIVE_QUESTION_MAX_SEC: int = 12          # max audio transcribed as the question
    LIVE_QUESTION_SILENCE_SEC: float = 0.9   # stop capture after this much silence
    LIVE_QUESTION_MIN_WAIT_SEC: float = 0.8  # keep a little audio after wake window
    LIVE_QUESTION_WAKE_ONLY_WAIT_SEC: float = 2.5  # allow «Протоколлер» + pause
    LIVE_BUFFER_MIN: int = 10                # rolling live-transcript memory
    LIVE_CONTEXT_AUDIO_SEC: int = 180        # accurate re-STT cap for meeting-only Q&A
    LIVE_STT_TIMEOUT_SEC: int = 45           # kill/restart a wedged native worker
    LIVE_STT_QUEUE_TIMEOUT_SEC: int = 5      # shed load instead of building stale audio
    LIVE_QUESTION_STT: str = "openai"        # "openai" | "local"
    LIVE_QUESTION_STT_MODEL: str = "whisper-1"
    # Phase 3 — speak the answer into the meeting (TTS). SEPARATE flag: the risky
    # mic/voice path only engages when BOTH this and LIVE_ASSISTANT_ENABLED are on,
    # so deploying it doesn't change the working listen+text behaviour.
    LIVE_ASSISTANT_SPEAK: bool = False
    LIVE_TTS: str = "openai"                  # "openai" | "espeak"
    LIVE_TTS_MODEL: str = "tts-1"
    LIVE_TTS_VOICE: str = "alloy"
    LIVE_PUBLIC_INFO_ENABLED: bool = True
    LIVE_PUBLIC_INFO_MODEL: str = "gpt-4.1"
    LIVE_PUBLIC_INFO_TIMEOUT_SEC: int = 25
    # Live answers must be FAST — keep the LLM context small (FTS-ranked email/MM
    # survive; the huge meeting-transcript dump is trimmed). Far below the 1.2M
    # used for the web chat, which made live answers take ~minute.
    LIVE_CONTEXT_MAX_CHARS: int = 24_000
    LIVE_MEETINGS_LIMIT: int = 20             # most-recent meetings considered live

    # ── Session ───────────────────────────────────────────────────────────
    SESSION_TTL_DAYS: int = 30

    # ── Communications ingestion (Mattermost + Gmail) ─────────────────────
    # All optional — if unset, the corresponding sync loop is a no-op.
    MM_TOKEN: str | None = None              # Mattermost bot/personal access token
    MM_SERVER_URL: str | None = None         # e.g. https://mattermost.company.com
    GOOGLE_SERVICE_ACCOUNT_JSON: str | None = None  # SA key JSON (inline or file path) for Gmail DWD
    GMAIL_BOOTSTRAP_DAYS: int = 30           # first-run window per user
    MM_SYNC_MINUTES: int = 15
    GMAIL_SYNC_MINUTES: int = 30

    # ── MegaFon call import (rec.megafon.ru → demo "Звонки") ──────────────
    # Off by default. Phone may be set here or entered per-import in the admin UI.
    MEGAFON_ENABLED: bool = False
    MEGAFON_PHONE: str | None = None
    MEGAFON_API_BASE: str = "https://openapi.megafon.ru/api/product/rec/v1"
    MEGAFON_KEYCLOAK_URL: str = "https://account.megafon.ru/auth"
    MEGAFON_REALM: str = "Subscribers"
    MEGAFON_CLIENT_ID: str = "rec"
    MEGAFON_REDIRECT_URI: str = "https://rec.megafon.ru"
    # Stereo call recordings have one party per channel. Which channel is "Вы"
    # (0=left, 1=right). Flip via env if speakers come out swapped.
    MEGAFON_YOU_CHANNEL: int = 0

    # ── E2E Testing ───────────────────────────────────────────────────────
    TEST_API_KEY: str | None = None          # static key for automated E2E tests
    TEST_MEETING_URL: str | None = None      # permanent Telemost room URL

    @property
    def admin_email_list(self) -> list[str]:
        return [e.strip().lower() for e in self.ADMIN_EMAILS.split(",")]

    model_config = {"env_file": ".env", "extra": "ignore"}


config = Config()
