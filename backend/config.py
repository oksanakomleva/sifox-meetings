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
    OPENAI_MODEL: str = "gpt-4o"

    # ── Whisper ───────────────────────────────────────────────────────────
    WHISPER_MODEL: str = "medium"

    # ── Storage ───────────────────────────────────────────────────────────
    AUDIO_DIR: str = "/audio"

    # ── Encryption ────────────────────────────────────────────────────────
    ENCRYPTION_KEY: str                      # Fernet key (base64)

    # ── Recording ─────────────────────────────────────────────────────────
    JOIN_BEFORE_MINUTES: int = 1
    PARTICIPANT_POLL_INTERVAL: int = 30      # seconds between polls
    EMPTY_POLLS_TO_END: int = 3             # ~90s after everyone leaves
    MAX_RECORDING_HOURS: int = 4

    # ── Session ───────────────────────────────────────────────────────────
    SESSION_TTL_DAYS: int = 30

    # ── E2E Testing ───────────────────────────────────────────────────────
    TEST_API_KEY: str | None = None          # static key for automated E2E tests
    TEST_MEETING_URL: str | None = None      # permanent Telemost room URL

    @property
    def admin_email_list(self) -> list[str]:
        return [e.strip().lower() for e in self.ADMIN_EMAILS.split(",")]

    model_config = {"env_file": ".env", "extra": "ignore"}


config = Config()
