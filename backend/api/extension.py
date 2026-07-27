"""Browser-extension routes: audio upload + extension download.

The Chrome extension records any in-browser meeting (tab audio + mic) and
uploads the audio here. It authenticates by reading the user's existing web
login (Google) session cookie and sending it as X-Session-Token. Upload kicks
off the same transcribe→analyze pipeline used for live Telemost recordings.
"""
import io
import zipfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request, UploadFile, File
from fastapi.responses import Response
from pydantic import BaseModel, Field

from auth.deps import get_current_user, get_extension_user
router = APIRouter(prefix="/api/extension", tags=["extension"])

CurrentUser = Annotated[dict, Depends(get_current_user)]
ExtensionUser = Annotated[dict, Depends(get_extension_user)]

# Repo root holds the `extension/` source folder (see Dockerfile COPY).
_EXTENSION_DIR = Path(__file__).resolve().parents[2] / "extension"


@router.get("/version")
async def extension_version():
    """Latest extension version (from the deployed manifest) so installed copies
    can detect when an update is available. Public — no auth needed."""
    import json
    try:
        data = json.loads((_EXTENSION_DIR / "manifest.json").read_text(encoding="utf-8"))
        return {"version": data.get("version", "0.0.0")}
    except Exception:
        return {"version": "0.0.0"}


@router.get("/download")
async def download_extension(user: CurrentUser):
    """Zip the extension source on the fly so teammates can install it
    (chrome://extensions → Load unpacked). Always reflects the deployed version."""
    if not _EXTENSION_DIR.is_dir():
        raise HTTPException(404, "Extension source not found on server")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(_EXTENSION_DIR.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(_EXTENSION_DIR))
    buf.seek(0)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="sifox-recorder-extension.zip"'},
    )


# ── Extension-authenticated routes ────────────────────────────────────────────

@router.get("/me")
async def whoami(user: ExtensionUser):
    """Verify a token from the extension popup."""
    return {"email": user["email"], "name": user.get("name")}


@router.post("/upload", status_code=202)
async def upload_recording(
    user: ExtensionUser,
    file: UploadFile = File(...),
    title: str | None = Form(None),
    started_at: str | None = Form(None),
    source_url: str | None = Form(None),
):
    """Accept a recorded audio blob and process it in the background.

    Returns 202 + {meeting_id} immediately; transcription/analysis run async.
    """
    from services.uploads import save_upload_and_process
    meeting_id = await save_upload_and_process(
        file,
        title=title or "Запись из браузера",
        recorder_user_id=user["user_id"],
        started_at=started_at,
        source_url=source_url or "extension://browser-recording",
    )
    return {"meeting_id": meeting_id, "status": "processing"}


class StartUploadRequest(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    started_at: str | None = Field(default=None, max_length=64)
    source_url: str | None = Field(default=None, max_length=2000)


class FinishUploadRequest(BaseModel):
    total_bytes: int = Field(ge=0, le=500 * 1024 * 1024)


@router.post("/upload/start", status_code=201)
async def start_resumable_upload(body: StartUploadRequest, user: ExtensionUser):
    from services.uploads import start_chunked_upload
    return await start_chunked_upload(
        title=body.title,
        recorder_user_id=user["user_id"],
        started_at=body.started_at,
        source_url=body.source_url or "extension://browser-recording",
    )


@router.post("/upload/{meeting_id}/chunk")
async def upload_chunk(
    meeting_id: str,
    offset: int,
    request: Request,
    user: ExtensionUser,
):
    declared = request.headers.get("content-length")
    try:
        if declared and int(declared) > 8 * 1024 * 1024:
            raise HTTPException(413, "Chunk too large")
    except ValueError as e:
        raise HTTPException(400, "Invalid Content-Length") from e
    parts: list[bytes] = []
    received = 0
    async for part in request.stream():
        received += len(part)
        if received > 8 * 1024 * 1024:
            raise HTTPException(413, "Chunk too large")
        parts.append(part)
    chunk = b"".join(parts)
    from services.uploads import append_upload_chunk
    new_offset = await append_upload_chunk(
        meeting_id,
        recorder_user_id=user["user_id"],
        offset=offset,
        chunk=chunk,
    )
    return {"offset": new_offset}


@router.post("/upload/{meeting_id}/finish", status_code=202)
async def finish_resumable_upload(
    meeting_id: str,
    body: FinishUploadRequest,
    user: ExtensionUser,
):
    from services.uploads import finish_chunked_upload
    await finish_chunked_upload(
        meeting_id,
        recorder_user_id=user["user_id"],
        total_bytes=body.total_bytes,
    )
    return {"meeting_id": meeting_id, "status": "processing"}


@router.post("/upload/{meeting_id}/cancel", status_code=204)
async def cancel_resumable_upload(meeting_id: str, user: ExtensionUser):
    """Discard an upload that was created but whose recorder failed to start."""
    from services.uploads import cancel_chunked_upload
    await cancel_chunked_upload(
        meeting_id,
        recorder_user_id=user["user_id"],
    )
    return Response(status_code=204)
