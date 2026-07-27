"""
telemost-web backend
FastAPI app serving REST API + static React frontend.
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from auth.deps import get_admin_user
from config import config
from database.connection import init_db, close_db
from utils.http import RequestBodyLimitMiddleware
from utils.paths import confined_file

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────
    await init_db(config.DATABASE_URL)

    # Ensure the network-backed audio dir exists without blocking the event loop.
    from services import fsio
    await fsio.mkdir_p(Path(config.AUDIO_DIR))

    # Recover meetings that were interrupted by the previous deploy
    from services.recorder import (
        recover_interrupted_meetings,
        run_recording_scheduler,
        request_shutdown,
        wait_for_idle,
    )
    await recover_interrupted_meetings()

    # Background tasks
    from services.calendar_sync import run_sync_loop
    from services.mattermost_sync import run_mm_sync_loop
    from services.gmail_sync import run_gmail_sync_loop

    sync_task = asyncio.create_task(run_sync_loop(), name="calendar-sync")
    scheduler_task = asyncio.create_task(run_recording_scheduler(), name="rec-scheduler")
    mm_task = asyncio.create_task(run_mm_sync_loop(), name="mm-sync")
    gmail_task = asyncio.create_task(run_gmail_sync_loop(), name="gmail-sync")
    app.state.background_tasks = {
        task.get_name(): task
        for task in (sync_task, scheduler_task, mm_task, gmail_task)
    }

    # Force a clean restart if the event loop ever wedges (instead of hanging
    # forever until a manual redeploy — see 2026-07-22).
    from services.watchdog import start_watchdog
    start_watchdog()

    logger.info("telemost-web started")
    yield

    # ── Shutdown ─────────────────────────────────────────────────────────
    # 1. Tell the scheduler to stop launching new recordings
    request_shutdown()
    sync_task.cancel()
    scheduler_task.cancel()
    mm_task.cancel()
    gmail_task.cancel()

    # 2. Wait for any in-progress recordings to finish gracefully.
    #    railway.toml stopSec must be >= this timeout + a small buffer.
    #    55 min covers: longest meeting (45 min) + transcription (10 min).
    await wait_for_idle(timeout=3300)

    await close_db()
    logger.info("telemost-web stopped")


app = FastAPI(title="Sifox Meetings", lifespan=lifespan)


# Reject obviously oversized legacy multipart uploads before Starlette parses
# and spools their complete bodies. Chunked extension uploads use a separate
# endpoint with an 8 MB per-chunk cap.
_LEGACY_UPLOAD_BODY_LIMIT = 502 * 1024 * 1024
_LEGACY_UPLOAD_PATHS = {
    "/api/extension/upload",
    "/api/admin/recordings/upload",
}


app.add_middleware(
    RequestBodyLimitMiddleware,
    paths=_LEGACY_UPLOAD_PATHS,
    max_bytes=_LEGACY_UPLOAD_BODY_LIMIT,
)
app.add_middleware(
    RequestBodyLimitMiddleware,
    paths={"/api/extension/upload/start"},
    max_bytes=32 * 1024,
)

# CORS (dev only — in prod frontend is served from same origin)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[config.BASE_URL, "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API routes ────────────────────────────────────────────────────────────────
from api.auth import router as auth_router
from api.meetings import router as meetings_router
from api.chat import router as chat_router
from api.admin import router as admin_router
from api.extension import router as extension_router
from api.communications import router as communications_router
from api.share import router as share_router
from api.calls import router as calls_router, admin_router as megafon_admin_router

app.include_router(auth_router)
app.include_router(meetings_router)
app.include_router(chat_router)
app.include_router(admin_router)
app.include_router(extension_router)
app.include_router(communications_router)
app.include_router(share_router)
app.include_router(calls_router)
app.include_router(megafon_admin_router)


@app.get("/live")
async def live():
    """Process/event-loop liveness; does not depend on external services."""
    return {"status": "ok"}


@app.get("/health")
async def health():
    """Readiness check used by Railway before routing traffic."""
    from database.connection import get_pool
    from services import fsio
    from services.recorder import _active, _shutdown_requested

    failed_tasks = []
    for name, task in getattr(app.state, "background_tasks", {}).items():
        if task.done() and not task.cancelled():
            failed_tasks.append(name)
            try:
                exc = task.exception()
            except asyncio.CancelledError:
                exc = None
            if exc:
                logger.error("Background task %s stopped: %s", name, exc)
    if failed_tasks:
        raise HTTPException(503, detail=f"Background tasks stopped: {', '.join(failed_tasks)}")
    if fsio.circuit_is_open():
        raise HTTPException(503, detail="Storage unavailable")

    try:
        pool = await get_pool()
        await asyncio.wait_for(pool.fetchval("SELECT 1"), timeout=2)
    except Exception as e:
        logger.error("Readiness database check failed: %s", e)
        raise HTTPException(503, detail="Database unavailable") from e

    return {
        "status": "ok",
        "database": "ok",
        "active_recordings": len(_active),
        "shutdown_requested": _shutdown_requested,
    }


# Debug screenshots can contain meeting titles and participant names.
@app.get("/debug/screenshot/{name}")
async def debug_screenshot(name: str, _admin=Depends(get_admin_user)):
    import re
    if not re.match(r'^[\w.-]+\.png$', name):
        return {"error": "invalid name"}
    path = f"/tmp/recorder-debug/{name}"
    if not os.path.exists(path):
        return {"error": "not found", "available": os.listdir("/tmp/recorder-debug") if os.path.exists("/tmp/recorder-debug") else []}
    return FileResponse(path)


# ── Serve React frontend ──────────────────────────────────────────────────────
_frontend_dist = (Path(__file__).resolve().parent / ".." / "frontend" / "dist").resolve()

if os.path.exists(_frontend_dist):
    app.mount("/assets", StaticFiles(directory=_frontend_dist / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # Serve static files from dist root (SVGs, favicon, etc.) before falling back to SPA
        if full_path:
            candidate = confined_file(_frontend_dist, full_path)
            if candidate is not None:
                return FileResponse(candidate)
        index = _frontend_dist / "index.html"
        return FileResponse(index)
else:
    @app.get("/")
    async def dev_root():
        return {"message": "API running. Frontend not built yet — run `npm run build` in /frontend."}


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=False,
    )
