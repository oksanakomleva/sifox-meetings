"""
Pytest configuration — adds backend/ to sys.path and stubs external deps so
unit tests can import pure functions without needing a live DB or Railway secrets.
"""
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── 1. Stub required env vars ─────────────────────────────────────────────────
# pydantic_settings Config() validates on instantiation — provide dummy values.
# Unit tests only exercise pure functions; these values are never used at runtime.
_STUB_ENV = {
    "SECRET_KEY": "test-secret-key-for-unit-tests",
    "DATABASE_URL": "postgresql://test:test@localhost/test",
    "GOOGLE_CLIENT_ID": "test-client-id.apps.googleusercontent.com",
    "GOOGLE_CLIENT_SECRET": "test-client-secret",
    "OPENAI_API_KEY": "sk-test-key",
    "ENCRYPTION_KEY": "dGVzdC1lbmNyeXB0aW9uLWtleS0zMi1ieXRlcy1sb25n",
}
for key, val in _STUB_ENV.items():
    os.environ.setdefault(key, val)

# ── 2. Stub heavy/unavailable packages ───────────────────────────────────────
# asyncpg, playwright, faster_whisper, openai are not installed locally.
# Services import them at module level → stub them before any service is imported.

def _stub_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod

for _heavy in [
    "asyncpg",
    "playwright", "playwright.async_api",
    "faster_whisper",
    "openai",
    "google", "google.oauth2", "google.oauth2.credentials",
    "google.auth", "google.auth.transport", "google.auth.transport.requests",
    "googleapiclient", "googleapiclient.discovery",
    "cryptography", "cryptography.fernet",
]:
    if _heavy not in sys.modules:
        _stub_module(_heavy)

# database.connection needs asyncpg → stub the whole database package
_db_conn = _stub_module("database.connection", get_pool=MagicMock())
_db_models = _stub_module("database.models")
_db_pkg = _stub_module("database", connection=_db_conn, models=_db_models)
_db_pkg.models = _db_models
