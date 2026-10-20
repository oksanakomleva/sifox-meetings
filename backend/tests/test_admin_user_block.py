"""Blocking a user is an admin-session action, never an API-key action."""
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import admin
from auth.deps import get_admin_user


def test_admin_can_block_and_unblock_user(monkeypatch):
    update = AsyncMock(return_value=True)
    monkeypatch.setattr(admin.models, "set_user_active", update, raising=False)
    app = FastAPI()
    app.include_router(admin.router)
    app.dependency_overrides[get_admin_user] = lambda: {"user_id": 1, "is_admin": True}
    with TestClient(app) as client:
        assert client.patch("/api/admin/users/2/active", json={"is_active": False}).status_code == 200
        assert client.patch("/api/admin/users/2/active", json={"is_active": True}).status_code == 200
    assert update.await_args_list[0].args == (2, False, 1)
    assert update.await_args_list[1].args == (2, True, 1)


def test_block_requires_real_admin_session(monkeypatch):
    update = AsyncMock(return_value=True)
    monkeypatch.setattr(admin.models, "set_user_active", update, raising=False)
    app = FastAPI()
    app.include_router(admin.router)
    with TestClient(app) as client:
        assert client.patch("/api/admin/users/2/active", json={"is_active": False},
                            headers={"X-Test-Api-Key": "test"}).status_code == 401
    update.assert_not_awaited()


def test_admin_cannot_block_self(monkeypatch):
    update = AsyncMock(side_effect=ValueError("Cannot block your own account"))
    monkeypatch.setattr(admin.models, "set_user_active", update, raising=False)
    app = FastAPI()
    app.include_router(admin.router)
    app.dependency_overrides[get_admin_user] = lambda: {"user_id": 1, "is_admin": True}
    with TestClient(app) as client:
        assert client.patch("/api/admin/users/1/active", json={"is_active": False}).status_code == 400
