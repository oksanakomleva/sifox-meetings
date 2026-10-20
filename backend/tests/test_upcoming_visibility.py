"""Regression checks for active meetings that outlive the two-hour calendar window."""

import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def upcoming_models(monkeypatch):
    encryption = types.ModuleType("utils.encryption")
    encryption.encrypt = encryption.decrypt = lambda value: value
    monkeypatch.setitem(sys.modules, "utils.encryption", encryption)
    spec = importlib.util.spec_from_file_location(
        "upcoming_models_test", Path(__file__).parents[1] / "database" / "models.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(module, "get_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(module, "get_user_by_id", AsyncMock(return_value={"email": "person@sifox.com"}))
    return module, conn


@pytest.mark.parametrize("is_admin", [True, False])
def test_active_meetings_remain_visible_after_two_hours(upcoming_models, is_admin):
    module, conn = upcoming_models
    asyncio.run(module.get_upcoming_meetings_for_user(7, is_admin))
    sql = " ".join(conn.fetch.call_args.args[0].split())
    prefix = "" if is_admin else "m."
    assert (
        f"{prefix}status IN ('joining', 'recording', 'transcribing', 'analyzing') "
        f"OR ({prefix}status = 'pending' AND {prefix}start_time > NOW() - interval '2 hours')"
    ) in sql
    if not is_admin:
        assert ") AND ( -- the user is an actual attendee" in sql


def test_scheduled_attendee_can_open_card_but_public_visibility_waits_for_completion(upcoming_models):
    module, conn = upcoming_models
    conn.fetchval = AsyncMock(return_value=True)
    assert asyncio.run(module.user_can_access_meeting(7, "person@sifox.com", "meeting-1"))
    sql = " ".join(conn.fetchval.call_args.args[0].split())
    assert "m.status IN ('pending', 'joining', 'recording', 'transcribing', 'analyzing', 'done')" in sql
    assert "m.status = 'done' AND m.visible_to_all = TRUE" in sql
    assert "cml.meeting_id = m.id AND $2 = ANY(cml.attendee_emails)" in sql
