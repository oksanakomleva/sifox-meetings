"""Regression tests for resumable browser-extension upload recovery."""

import asyncio
import sys
import types
from unittest.mock import AsyncMock

import pytest

try:
    from fastapi import HTTPException
except ModuleNotFoundError:  # Minimal local test image does not install web deps.
    fastapi_stub = types.ModuleType("fastapi")

    class HTTPException(Exception):
        def __init__(self, status_code, detail=None):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    class UploadFile:  # pragma: no cover - only needed to import the service
        pass

    fastapi_stub.HTTPException = HTTPException
    fastapi_stub.UploadFile = UploadFile
    sys.modules["fastapi"] = fastapi_stub

from services import uploads

if "fastapi_stub" in globals():
    sys.modules.pop("fastapi", None)


def test_upload_status_reports_server_offset(monkeypatch):
    monkeypatch.setattr(
        uploads.models,
        "get_meeting",
        AsyncMock(return_value={
            "id": "meeting-1",
            "recorder_user_id": 7,
            "status": "uploading",
            "error_message": None,
        }),
        raising=False,
    )
    monkeypatch.setattr(uploads.fsio, "size", AsyncMock(return_value=12_345))

    result = asyncio.run(
        uploads.get_chunked_upload_status("meeting-1", recorder_user_id=7)
    )

    assert result["accepting_chunks"] is True
    assert result["completed"] is False
    assert result["offset"] == 12_345


def test_upload_status_can_recreate_a_missing_empty_part(monkeypatch):
    monkeypatch.setattr(
        uploads.models,
        "get_meeting",
        AsyncMock(return_value={
            "id": "meeting-1",
            "recorder_user_id": 7,
            "status": "uploading",
        }),
        raising=False,
    )
    size = AsyncMock(return_value=-1)
    monkeypatch.setattr(uploads.fsio, "size", size)

    result = asyncio.run(
        uploads.get_chunked_upload_status("meeting-1", recorder_user_id=7)
    )

    assert result["offset"] == 0
    size.assert_awaited_once()


def test_upload_status_recognizes_already_completed_session(monkeypatch):
    monkeypatch.setattr(
        uploads.models,
        "get_meeting",
        AsyncMock(return_value={
            "id": "meeting-1",
            "recorder_user_id": 7,
            "status": "transcribing",
        }),
        raising=False,
    )

    result = asyncio.run(
        uploads.get_chunked_upload_status("meeting-1", recorder_user_id=7)
    )

    assert result["accepting_chunks"] is False
    assert result["completed"] is True


def test_closed_upload_returns_machine_readable_conflict(monkeypatch):
    monkeypatch.setattr(
        uploads.models,
        "get_meeting",
        AsyncMock(return_value={
            "id": "meeting-1",
            "recorder_user_id": 7,
            "status": "error",
        }),
        raising=False,
    )

    with pytest.raises(HTTPException) as caught:
        asyncio.run(uploads._owned_upload("meeting-1", recorder_user_id=7))

    assert caught.value.status_code == 409
    assert caught.value.detail == {
        "code": "upload_closed",
        "message": "Upload is no longer accepting chunks",
        "status": "error",
    }


def test_upload_status_does_not_leak_another_users_session(monkeypatch):
    monkeypatch.setattr(
        uploads.models,
        "get_meeting",
        AsyncMock(return_value={
            "id": "meeting-1",
            "recorder_user_id": 99,
            "status": "uploading",
        }),
        raising=False,
    )

    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            uploads.get_chunked_upload_status("meeting-1", recorder_user_id=7)
        )

    assert caught.value.status_code == 403
