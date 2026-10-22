import asyncio
from copy import deepcopy

import pytest
from cryptography.fernet import Fernet, InvalidToken

from config import config
from rotate_encryption_key import _main, rotate_stored_tokens
from utils import encryption


def _keys(monkeypatch):
    old = Fernet.generate_key().decode()
    new = Fernet.generate_key().decode()
    monkeypatch.setattr(config, "ENCRYPTION_KEY", old)
    monkeypatch.setattr(config, "ENCRYPTION_KEY_NEXT", new)
    monkeypatch.setattr(config, "ENCRYPTION_WRITE_NEXT", False)
    return Fernet(old.encode()), Fernet(new.encode())


def test_dual_key_reads_both_but_keeps_old_writes_until_switched(monkeypatch):
    old, new = _keys(monkeypatch)
    old_token = old.encrypt(b"calendar-token").decode()
    assert encryption.decrypt(old_token) == "calendar-token"
    old_write = encryption.encrypt("before-switch")
    assert old.decrypt(old_write.encode()) == b"before-switch"
    monkeypatch.setattr(config, "ENCRYPTION_WRITE_NEXT", True)
    new_token = encryption.encrypt("new-token")
    assert new.decrypt(new_token.encode()) == b"new-token"
    assert encryption.decrypt(new_token) == "new-token"
    with pytest.raises(InvalidToken):
        old.decrypt(new_token.encode())


def test_rotation_is_idempotent_and_rejects_unreadable_tokens(monkeypatch):
    old, new = _keys(monkeypatch)
    old_token = old.encrypt(b"refresh-token").decode()
    rotated, changed = encryption.rotate_encrypted_token(old_token)
    assert changed
    assert new.decrypt(rotated.encode()) == b"refresh-token"
    assert encryption.rotate_encrypted_token(rotated) == (rotated, False)
    with pytest.raises(InvalidToken):
        encryption.rotate_encrypted_token("not-a-token")


def test_rotation_requires_distinct_valid_keys(monkeypatch):
    old, _ = _keys(monkeypatch)
    monkeypatch.setattr(config, "ENCRYPTION_KEY_NEXT", config.ENCRYPTION_KEY)
    with pytest.raises(ValueError, match="must differ"):
        encryption.validate_encryption_keys()
    monkeypatch.setattr(config, "ENCRYPTION_KEY_NEXT", "invalid")
    with pytest.raises(ValueError):
        encryption.validate_encryption_keys()
    monkeypatch.setattr(config, "ENCRYPTION_KEY_NEXT", "")
    monkeypatch.setattr(config, "ENCRYPTION_WRITE_NEXT", True)
    with pytest.raises(ValueError, match="requires"):
        encryption.validate_encryption_keys()
    monkeypatch.setattr(config, "ENCRYPTION_WRITE_NEXT", False)
    token = encryption.encrypt("original")
    assert old.decrypt(token.encode()) == b"original"
    with pytest.raises(RuntimeError, match="required"):
        encryption.rotate_encrypted_token(token)


class _Transaction:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        self.before = deepcopy(self.connection.tables)
        return self

    async def __aexit__(self, error_type, *_):
        if error_type is not None:
            self.connection.tables = self.before
        return False


class _Connection:
    def __init__(self, tables):
        self.tables = tables
        self.updates = []

    def transaction(self):
        return _Transaction(self)

    async def fetch(self, query):
        table = next(name for name in self.tables if f"FROM {name}" in query)
        return [row.copy() for row in self.tables[table]]

    async def execute(self, query, access, refresh, user_id):
        table = next(name for name in self.tables if f"UPDATE {name}" in query)
        row = next(row for row in self.tables[table] if row["user_id"] == user_id)
        row.update(access_token=access, refresh_token=refresh)
        self.updates.append((table, user_id))


def test_database_rotation_checks_then_updates_both_tables(monkeypatch):
    old, new = _keys(monkeypatch)
    connection = _Connection({
        "google_tokens": [{"user_id": 1, "access_token": old.encrypt(b"a").decode(),
                           "refresh_token": old.encrypt(b"r").decode()}],
        "gmail_send_tokens": [{"user_id": 2, "access_token": new.encrypt(b"mail").decode(),
                               "refresh_token": None}],
    })
    assert asyncio.run(rotate_stored_tokens(connection, apply=False)) == {
        "google_tokens": (1, 2), "gmail_send_tokens": (1, 0),
    }
    assert connection.updates == []
    assert asyncio.run(rotate_stored_tokens(connection, apply=True)) == {
        "google_tokens": (1, 2), "gmail_send_tokens": (1, 0),
    }
    assert connection.updates == [("google_tokens", 1)]
    assert asyncio.run(rotate_stored_tokens(connection, apply=False)) == {
        "google_tokens": (1, 0), "gmail_send_tokens": (1, 0),
    }


def test_rotation_command_refuses_production(monkeypatch):
    monkeypatch.setattr(config, "APP_ENV", "production")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")
    with pytest.raises(RuntimeError, match="only run inside"):
        asyncio.run(_main(apply=True))


def test_bad_ciphertext_rolls_back_prior_updates(monkeypatch):
    old, _ = _keys(monkeypatch)
    connection = _Connection({
        "google_tokens": [{"user_id": 1, "access_token": old.encrypt(b"a").decode(),
                           "refresh_token": None}],
        "gmail_send_tokens": [{"user_id": 2, "access_token": "broken",
                               "refresh_token": None}],
    })
    before = deepcopy(connection.tables)
    with pytest.raises(InvalidToken):
        asyncio.run(rotate_stored_tokens(connection, apply=True))
    assert connection.tables == before


def test_rotation_command_refuses_writes_before_switch(monkeypatch):
    _keys(monkeypatch)
    monkeypatch.setattr(config, "APP_ENV", "staging")
    monkeypatch.setattr(config, "DATABASE_URL", "postgresql://test@postgres.railway.internal/db")
    monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "staging")
    with pytest.raises(RuntimeError, match="Enable ENCRYPTION_WRITE_NEXT"):
        asyncio.run(_main(apply=True))
