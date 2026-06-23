"""
telemost-web backend
FastAPI app serving REST API + static React frontend.
"""
import asyncio
import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from config import config
from database.connection import init_db, close_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────
    await init_db(config.DATABASE_URL)

    # Ensure audio dir exists
    os.makedirs(config.AUDIO_DIR, exist_ok=True)

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

app.include_router(auth_router)
app.include_router(meetings_router)
app.include_router(chat_router)
app.include_router(admin_router)
app.include_router(extension_router)
app.include_router(communications_router)
app.include_router(share_router)


@app.get("/health")
async def health():
    from services.recorder import _active, _shutdown_requested
    return {
        "status": "ok",
        "active_recordings": len(_active),
        "shutdown_requested": _shutdown_requested,
    }


# Debug screenshots from recorder (admin only via existing protected static files endpoint
# would be ideal — but for now anyone can view, no PII)
@app.get("/debug/screenshot/{name}")
async def debug_screenshot(name: str):
    import re
    if not re.match(r'^[\w.-]+\.png$', name):
        return {"error": "invalid name"}
    path = f"/tmp/recorder-debug/{name}"
    if not os.path.exists(path):
        return {"error": "not found", "available": os.listdir("/tmp/recorder-debug") if os.path.exists("/tmp/recorder-debug") else []}
    return FileResponse(path)


# ── Serve React frontend ──────────────────────────────────────────────────────
_frontend_dist = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")

if os.path.exists(_frontend_dist):
    app.mount("/assets", StaticFiles(directory=os.path.join(_frontend_dist, "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # Serve static files from dist root (SVGs, favicon, etc.) before falling back to SPA
        if full_path:
            candidate = os.path.join(_frontend_dist, full_path)
            if os.path.isfile(candidate):
                return FileResponse(candidate)
        index = os.path.join(_frontend_dist, "index.html")
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
