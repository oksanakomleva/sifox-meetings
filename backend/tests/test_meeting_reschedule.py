"""Regression tests for calendar meetings moved after a finished time slot."""
import importlib.util
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def real_models():
    # conftest stubs the DB package; load the real, pure status helper directly.
    encryption = types.ModuleType("utils.encryption")
    encryption.encrypt = encryption.decrypt = lambda value: value
    previous = sys.modules.get("utils.encryption")
    sys.modules["utils.encryption"] = encryption
    try:
        path = Path(__file__).parents[1] / "database" / "models.py"
        spec = importlib.util.spec_from_file_location("reschedule_models_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if previous is None:
            sys.modules.pop("utils.encryption", None)
        else:
            sys.modules["utils.encryption"] = previous


@pytest.mark.parametrize("status", ["done", "error", "no_show"])
def test_finished_occurrence_moved_to_future_is_rescheduled(real_models, status):
    now = datetime(2026, 9, 25, 14, 0, tzinfo=timezone.utc)
    assert real_models._is_rescheduled_occurrence(
        status,
        now - timedelta(hours=2),
        now + timedelta(hours=1),
        now=now,
    )


@pytest.mark.parametrize("status", ["pending", "joining", "recording", "transcribing", "analyzing"])
def test_active_occurrence_is_never_split(real_models, status):
    now = datetime(2026, 9, 25, 14, 0, tzinfo=timezone.utc)
    assert not real_models._is_rescheduled_occurrence(
        status,
        now - timedelta(hours=2),
        now + timedelta(hours=1),
        now=now,
    )


def test_small_calendar_time_correction_does_not_duplicate(real_models):
    now = datetime(2026, 9, 25, 14, 0, tzinfo=timezone.utc)
    assert not real_models._is_rescheduled_occurrence(
        "no_show", now, now + timedelta(seconds=60), now=now,
    )


def test_old_moved_slot_is_not_requeued(real_models):
    now = datetime(2026, 9, 25, 14, 0, tzinfo=timezone.utc)
    assert not real_models._is_rescheduled_occurrence(
        "no_show",
        now - timedelta(hours=3),
        now - timedelta(minutes=31),
        now=now,
    )
