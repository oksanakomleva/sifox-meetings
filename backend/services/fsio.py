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
from pathlib import Path

logger = logging.getLogger(__name__)

# A volume metadata op should return in well under a second; if the mount stalls
# we fail fast rather than keep a loop worker parked for minutes.
FS_TIMEOUT = 30.0


async def _run(func, *args):
    return await asyncio.wait_for(asyncio.to_thread(func, *args), timeout=FS_TIMEOUT)


async def size(p: Path) -> int:
    """File size in bytes, or -1 if it does not exist. Combines exists()+stat()
    into a single offloaded call (a missing file is `< any threshold`)."""
    def _size() -> int:
        try:
            return p.stat().st_size
        except FileNotFoundError:
            return -1
    return await _run(_size)


async def exists(p: Path) -> bool:
    return await _run(p.exists)


async def unlink_quiet(p: Path) -> None:
    """Best-effort delete: never raises (missing file, or a volume stall/timeout
    are all swallowed). For cleanup deletes where failure must not break flow."""
    def _unlink() -> None:
        try:
            p.unlink()
        except FileNotFoundError:
            pass
    try:
        await _run(_unlink)
    except (asyncio.TimeoutError, OSError) as e:  # noqa: BLE001 — best effort
        logger.warning("unlink %s failed/timed out: %s", getattr(p, "name", p), e)


async def mkdir_p(p: Path) -> None:
    await _run(lambda: p.mkdir(parents=True, exist_ok=True))
