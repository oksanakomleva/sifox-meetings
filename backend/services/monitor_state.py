"""Cheap in-process progress markers for internal incident detection."""
import time

_started = time.monotonic()
_last_completed: dict[str, float] = {
    "recording_scheduler": _started,
    "calendar_sync": _started,
}
_last_completed_epoch: dict[str, float] = {name: time.time() for name in _last_completed}


def completed(name: str) -> None:
    _last_completed[name] = time.monotonic()
    _last_completed_epoch[name] = time.time()


def ages() -> dict[str, float]:
    now = time.monotonic()
    return {name: round(now - stamp, 1) for name, stamp in _last_completed.items()}


def stamps() -> dict[str, int]:
    return {name: int(stamp) for name, stamp in _last_completed_epoch.items()}
