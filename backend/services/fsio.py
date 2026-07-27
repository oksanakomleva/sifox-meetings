"""Off-loop filesystem helpers for the network-attached AUDIO_DIR volume.

The service runs a single asyncio event loop (uvicorn --workers 1) that serves
the API AND every background loop. A synchronous filesystem syscall (stat /
unlink / mkdir / open+write) on the REMOTE /audio volume, run directly on that
loop, freezes the WHOLE service for the full network-I/O timeout if the volume
stalls — that is what took recording down on 2026-07-17 (p50 > 20 s, 0 % errors,
CPU idle, even calendar_sync went silent).

These helpers run each blocking op in a worker thread (so the loop stays free)
AND cap it with a timeout (so a stalled mount surfaces as a catchable error,
letting the caller mark the item 'error' instead of hanging forever).
"""
import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

logger = logging.getLogger(__name__)

# A volume metadata op should return in well under a second; if the mount stalls
# we fail fast rather than keep a loop worker parked for minutes.
FS_TIMEOUT = 30.0
# Keep potentially wedged network-volume calls away from asyncio's shared
# default executor (OAuth/calendar and other unrelated work use that pool).
_storage_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="storage-io")
_consecutive_failures = 0
_circuit_open_until = 0.0
_FAILURES_TO_OPEN = 3
_CIRCUIT_SECONDS = 60.0


class StorageUnavailableError(OSError):
    pass


def circuit_is_open() -> bool:
    return time.monotonic() < _circuit_open_until


async def run_io(func, *args, timeout: float = FS_TIMEOUT):
    """Run blocking storage I/O in the dedicated bounded executor.

    A Python timeout cannot kill a thread stuck in a kernel filesystem call, but
    isolation prevents those calls from exhausting asyncio's default executor.
    """
    global _consecutive_failures, _circuit_open_until
    if circuit_is_open():
        raise StorageUnavailableError("storage circuit breaker is open")

    loop = asyncio.get_running_loop()
    future = loop.run_in_executor(_storage_executor, func, *args)
    try:
        result = await asyncio.wait_for(future, timeout=timeout)
    except (asyncio.TimeoutError, OSError):
        _consecutive_failures += 1
        if _consecutive_failures >= _FAILURES_TO_OPEN:
            _circuit_open_until = time.monotonic() + _CIRCUIT_SECONDS
            logger.error(
                "Storage circuit opened for %.0fs after %d consecutive failures",
                _CIRCUIT_SECONDS,
                _consecutive_failures,
            )
        raise
    else:
        _consecutive_failures = 0
        return result


async def size(p: Path) -> int:
    """File size in bytes, or -1 if it does not exist. Combines exists()+stat()
    into a single offloaded call (a missing file is `< any threshold`)."""
    def _size() -> int:
        try:
            return p.stat().st_size
        except FileNotFoundError:
            return -1
    return await run_io(_size)


async def exists(p: Path) -> bool:
    return await run_io(p.exists)


async def unlink_quiet(p: Path) -> None:
    """Best-effort delete: never raises (missing file, or a volume stall/timeout
    are all swallowed). For cleanup deletes where failure must not break flow."""
    def _unlink() -> None:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    try:
        await run_io(_unlink)
    except (asyncio.TimeoutError, OSError) as e:  # noqa: BLE001 — best effort
        logger.warning("unlink %s failed/timed out: %s", getattr(p, "name", p), e)


async def mkdir_p(p: Path) -> None:
    await run_io(lambda: p.mkdir(parents=True, exist_ok=True))
