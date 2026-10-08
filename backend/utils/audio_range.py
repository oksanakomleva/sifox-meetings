"""Serve meeting audio with single byte ranges for browser seeking.

The pinned FastAPI version uses a Starlette FileResponse that predates Range
support. Streaming only the requested bytes also avoids loading a recording
into memory when someone seeks near the end of a long meeting.
"""

import os
import re
from collections.abc import Iterator

_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)\Z")
_CHUNK_SIZE = 64 * 1024


def parse_audio_range(header: str, size: int) -> tuple[int, int]:
    """Return inclusive start/end offsets for one HTTP byte range."""
    if len(header) > 128 or size <= 0:
        raise ValueError("Invalid range")
    match = _RANGE_RE.fullmatch(header.strip())
    if not match or not any(match.groups()):
        raise ValueError("Invalid range")

    start_text, end_text = match.groups()
    if not start_text:
        suffix = int(end_text)
        if suffix <= 0:
            raise ValueError("Invalid range")
        return max(0, size - suffix), size - 1

    start = int(start_text)
    end = min(int(end_text), size - 1) if end_text else size - 1
    if start >= size or end < start:
        raise ValueError("Unsatisfiable range")
    return start, end


def _read_audio_range(path: str, start: int, end: int) -> Iterator[bytes]:
    remaining = end - start + 1
    with open(path, "rb") as audio:
        audio.seek(start)
        while remaining:
            chunk = audio.read(min(_CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def audio_response(
    path: str,
    media_type: str,
    range_header: str | None,
    *,
    download_name: str | None = None,
):
    """Return a full file or a 206 response for a requested slice."""
    from fastapi import HTTPException
    from fastapi.responses import FileResponse, StreamingResponse

    headers = {"Accept-Ranges": "bytes"}
    if download_name:
        headers["Content-Disposition"] = f'attachment; filename="{download_name}"'
    if not range_header:
        return FileResponse(path, media_type=media_type, headers=headers)

    size = os.path.getsize(path)
    try:
        start, end = parse_audio_range(range_header, size)
    except ValueError as exc:
        raise HTTPException(
            416,
            "Requested audio range is not available",
            headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"},
        ) from exc

    headers.update({
        "Content-Range": f"bytes {start}-{end}/{size}",
        "Content-Length": str(end - start + 1),
    })
    return StreamingResponse(
        _read_audio_range(path, start, end),
        status_code=206,
        media_type=media_type,
        headers=headers,
    )
