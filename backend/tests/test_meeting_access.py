"""Meeting viewers can share; unrelated and preview users cannot."""
import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI
from fastapi.testclient import TestClient
from api import meetings
from auth.deps import get_current_user

MID = "11111111-1111-4111-8111-111111111111"
USER = {"user_id": 7, "email": "viewer@sifox.com", "is_admin": False}


@pytest.fixture
def api_client(monkeypatch):
    state = dict(USER)
    mocks = {}
    defaults = {
        "get_meeting": {"id": MID, "status": "done", "visible_to_all": False},
        "user_can_access_meeting": True,
        "get_meeting_access_users": [{"id": 8, "name": "Colleague", "email": "c@sifox.com", "explicit_grant": True, "has_access": True}],
        "grant_meeting_access_many": None,
        "create_meeting_share": None,
        "list_meeting_shares": [],
        "get_meeting_share": {"meeting_id": MID},
        "delete_meeting_share": True,
        "set_meeting_visible_to_all": None,
    }
    for name, result in defaults.items():
        mocks[name] = AsyncMock(return_value=result)
        monkeypatch.setattr(meetings.models, name, mocks[name], raising=False)
    app = FastAPI()
    app.include_router(meetings.router)
    app.dependency_overrides[get_current_user] = lambda: state
    with TestClient(app) as client:
        yield client, state, mocks


def test_nonadmin_reads_existing_access(api_client):
    client, _, mocks = api_client
    response = client.get(f"/api/meetings/{MID}/access")
    assert response.status_code == 200
    assert response.json()["users"][0]["explicit_grant"]
    mocks["user_can_access_meeting"].assert_awaited_once_with(7, USER["email"], MID)


def test_nonadmin_grants_to_multiple_colleagues(api_client):
    client, _, mocks = api_client
    response = client.post(f"/api/meetings/{MID}/access", json={"user_ids": [8, 9, 8]})
    assert response.status_code == 200
    mocks["grant_meeting_access_many"].assert_awaited_once_with([8, 9], MID, 7)


@pytest.mark.parametrize("ids", [[], [True], ["8"], [-1], [0], [2**64], list(range(1, 102))])
def test_invalid_recipients_rejected(api_client, ids):
    client, _, mocks = api_client
    assert client.post(f"/api/meetings/{MID}/access", json={"user_ids": ids}).status_code == 422
    mocks["grant_meeting_access_many"].assert_not_awaited()


def test_inactive_recipient_reports_actionable_error(api_client):
    client, _, mocks = api_client
    mocks["grant_meeting_access_many"].side_effect = ValueError()
    assert client.post(f"/api/meetings/{MID}/access", json={"user_ids": [8, 9]}).status_code == 400


OPERATIONS = [
    ("GET", "access", None),
    ("POST", "access", {"user_ids": [8]}),
    ("POST", "share", {"password": "test-password"}),
    ("GET", "shares", None),
    ("DELETE", "shares/test-token", None),
    ("POST", "visible-to-all", {"value": True}),
]


@pytest.mark.parametrize("reason,status", [("unrelated", 403), ("preview", 403), ("missing", 404)])
@pytest.mark.parametrize("method,path,body", OPERATIONS)
def test_all_sharing_actions_enforce_meeting_access(api_client, reason, status, method, path, body):
    client, state, mocks = api_client
    if reason == "unrelated":
        mocks["user_can_access_meeting"].return_value = False
    elif reason == "preview":
        state.update(is_preview=True, is_admin=True)
    else:
        mocks["get_meeting"].return_value = None
    response = client.request(method, f"/api/meetings/{MID}/{path}", json=body)
    assert response.status_code == status
    for name in ("grant_meeting_access_many", "create_meeting_share", "delete_meeting_share", "set_meeting_visible_to_all", "get_meeting_access_users", "list_meeting_shares"):
        mocks[name].assert_not_awaited()


def test_nonadmin_publishes_password_protected_link(api_client):
    from services.share import verify_password
    client, _, mocks = api_client
    response = client.post(f"/api/meetings/{MID}/share", json={"password": "test-password"})
    assert response.status_code == 200
    token, mid, hashed, actor, expires = mocks["create_meeting_share"].call_args.args
    assert mid == MID and actor == 7 and expires is None
    assert verify_password("test-password", hashed)
    assert response.json()["url"].endswith(f"/share/{token}")


@pytest.mark.parametrize("value", [True, False])
def test_nonadmin_controls_company_visibility(api_client, value):
    client, _, mocks = api_client
    assert client.post(f"/api/meetings/{MID}/visible-to-all", json={"value": value}).status_code == 200
    mocks["set_meeting_visible_to_all"].assert_awaited_once_with(MID, value)


def test_cannot_revoke_another_meetings_link(api_client):
    client, _, mocks = api_client
    mocks["get_meeting_share"].return_value = {"meeting_id": "other-meeting"}
    assert client.delete(f"/api/meetings/{MID}/shares/token").status_code == 404
    mocks["delete_meeting_share"].assert_not_awaited()


def test_nonadmin_can_revoke_link_for_accessible_meeting(api_client):
    client, _, mocks = api_client
    assert client.delete(f"/api/meetings/{MID}/shares/token").status_code == 200
    mocks["delete_meeting_share"].assert_awaited_once_with("token")


def test_unauthenticated_request_is_rejected(api_client, monkeypatch):
    client, _, _ = api_client
    client.app.dependency_overrides.clear()
    monkeypatch.setattr(meetings.models, "get_session", AsyncMock(return_value=None), raising=False)
    assert client.get(f"/api/meetings/{MID}/access").status_code == 401


@pytest.fixture
def real_models(monkeypatch):
    # The shared conftest replaces database.models; load its actual SQL helpers
    # under an isolated name and provide a transaction-capable connection double.
    encryption = types.ModuleType("utils.encryption")
    encryption.encrypt = encryption.decrypt = lambda value: value
    monkeypatch.setitem(sys.modules, "utils.encryption", encryption)
    spec = importlib.util.spec_from_file_location("access_models_test", Path(__file__).parents[1] / "database" / "models.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    conn = MagicMock()
    conn.fetch = AsyncMock()
    conn.execute = AsyncMock()
    conn.transaction.return_value.__aenter__ = AsyncMock()
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(module, "get_pool", AsyncMock(return_value=pool))
    return module, conn


def test_batch_is_validated_before_any_insert(real_models):
    module, conn = real_models
    conn.fetch.return_value = [{"id": 8}]
    with pytest.raises(ValueError):
        asyncio.run(module.grant_meeting_access_many([8, 9], MID, 7))
    conn.execute.assert_not_awaited()
    assert conn.transaction.return_value.__aexit__.call_args.args[0] is ValueError


def test_batch_uses_one_idempotent_insert_and_records_actor(real_models):
    module, conn = real_models
    conn.fetch.return_value = [{"id": 8}, {"id": 9}]
    asyncio.run(module.grant_meeting_access_many([9, 8, 8], MID, 7))
    sql, ids, mid, actor = conn.execute.call_args.args
    assert ids == [8, 9] and mid == MID and actor == 7
    assert "ON CONFLICT (user_id, meeting_id) DO NOTHING" in sql
    conn.execute.assert_awaited_once()
