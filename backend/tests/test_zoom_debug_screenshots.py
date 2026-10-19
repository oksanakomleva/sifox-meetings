"""Staging Zoom join screenshots are private, bounded, and best-effort."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import admin
from auth.deps import get_test_or_admin_user
from config import config
from services import recorder


MID = "11111111-1111-4111-8111-111111111111"


def test_staging_snapshot_is_saved_without_url_in_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_ENV", "staging")
    monkeypatch.setattr(recorder, "ZOOM_DEBUG_ROOT", tmp_path)
    page = MagicMock()
    page.screenshot = AsyncMock(return_value=b"PNG")

    asyncio.run(recorder._zoom_debug_snapshot(page, MID, "signin"))

    assert (tmp_path / MID / "signin.png").read_bytes() == b"PNG"
    assert page.screenshot.await_args.kwargs == {"full_page": False, "timeout": 4000}


def test_snapshot_disabled_in_production_and_rejects_bad_identifiers(tmp_path, monkeypatch):
    monkeypatch.setattr(recorder, "ZOOM_DEBUG_ROOT", tmp_path)
    page = MagicMock()
    page.screenshot = AsyncMock(return_value=b"PNG")

    monkeypatch.setattr(config, "APP_ENV", "production")
    asyncio.run(recorder._zoom_debug_snapshot(page, MID, "signin"))
    monkeypatch.setattr(config, "APP_ENV", "staging")
    asyncio.run(recorder._zoom_debug_snapshot(page, "../other", "signin"))
    asyncio.run(recorder._zoom_debug_snapshot(page, MID, "../other"))

    page.screenshot.assert_not_awaited()
    assert list(tmp_path.iterdir()) == []


def test_snapshot_failure_does_not_interrupt_join(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_ENV", "staging")
    monkeypatch.setattr(recorder, "ZOOM_DEBUG_ROOT", tmp_path)
    page = MagicMock()
    page.screenshot = AsyncMock(side_effect=RuntimeError("browser closed"))

    asyncio.run(recorder._zoom_debug_snapshot(page, MID, "guest-form"))

    assert list(tmp_path.iterdir()) == []


def test_debug_api_requires_admin_and_staging(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "APP_ENV", "staging")
    monkeypatch.setattr(recorder, "ZOOM_DEBUG_ROOT", tmp_path)
    folder = tmp_path / MID
    folder.mkdir()
    (folder / "signin.png").write_bytes(b"PNG")
    app = FastAPI()
    app.include_router(admin.router)
    with TestClient(app) as client:
        denied = client.get(f"/api/admin/meetings/{MID}/zoom-debug")
        assert denied.status_code == 401
        app.dependency_overrides[get_test_or_admin_user] = lambda: {"is_admin": True}
        listing = client.get(f"/api/admin/meetings/{MID}/zoom-debug")
        assert listing.status_code == 200
        assert listing.json()["stages"] == ["signin"]
        image = client.get(f"/api/admin/meetings/{MID}/zoom-debug/signin")
        assert image.status_code == 200
        assert image.content == b"PNG"
        assert image.headers["cache-control"] == "private, no-store"
        assert client.get(f"/api/admin/meetings/{MID}/zoom-debug/../signin").status_code != 200
        monkeypatch.setattr(config, "APP_ENV", "production")
        assert client.get(f"/api/admin/meetings/{MID}/zoom-debug").status_code == 404
    app.dependency_overrides.clear()
