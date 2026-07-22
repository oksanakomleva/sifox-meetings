"""Event-loop watchdog — force-restart the process if the loop wedges.

The service is a single asyncio event loop. If it ever stops ticking (a blocking
call that never returns, a deadlock), the process HANGS but does not EXIT — so
Railway's restartPolicy=on_failure never kicks in and the service stays down
until a human redeploys (2026-07-22). This watchdog turns that permanent hang
into a clean auto-restart: an async heartbeat stamps the time each interval, and
a separate daemon thread force-exits the process if the heartbeat goes stale.
Railway then restarts us and entrypoint.sh brings the display up clean.

Caveat: the monitor thread needs the GIL to act, so it cannot rescue a freeze
where a C extension holds the GIL forever. That specific case (whisper) is now
prevented by running transcription in a subprocess; this covers the rest.
"""
import asyncio
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

_BEAT_INTERVAL = 5.0     # how often the loop stamps that it is alive
_STALL_LIMIT = 90.0      # loop silent this long → force restart
_last_beat = time.monotonic()


async def _heartbeat() -> None:
    global _last_beat
    while True:
        _last_beat = time.monotonic()
        await asyncio.sleep(_BEAT_INTERVAL)


def _monitor() -> None:
    while True:
        time.sleep(_BEAT_INTERVAL)
        stalled = time.monotonic() - _last_beat
        if stalled > _STALL_LIMIT:
            # os.write/_exit avoid the Python-level machinery a wedged interpreter
            # may not be able to run; non-zero exit → Railway on_failure restart.
            os.write(
                2,
                f"WATCHDOG: event loop stalled {stalled:.0f}s (> {_STALL_LIMIT:.0f}s) "
                f"— forcing exit for a clean restart\n".encode(),
            )
            os._exit(1)


def start_watchdog() -> None:
    """Start the heartbeat task (on the running loop) and the monitor thread."""
    global _last_beat
    _last_beat = time.monotonic()
    asyncio.create_task(_heartbeat(), name="loop-heartbeat")
    threading.Thread(target=_monitor, name="loop-watchdog", daemon=True).start()
    logger.info("Event-loop watchdog started (stall limit %.0fs)", _STALL_LIMIT)
