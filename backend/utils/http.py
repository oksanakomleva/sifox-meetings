"""Small ASGI middleware used by the application."""
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException

ASGIApp = Callable[
    [dict[str, Any], Callable[[], Awaitable[dict[str, Any]]], Callable[[dict[str, Any]], Awaitable[None]]],
    Awaitable[None],
]


class RequestBodyLimitMiddleware:
    """Reject selected request bodies while they are still being received.

    Unlike a limit inside a FastAPI endpoint, this runs before multipart parsing,
    so a chunked request cannot fill the temporary disk first.
    """

    def __init__(self, app: ASGIApp, *, paths: set[str], max_bytes: int):
        self.app = app
        self.paths = paths
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path") not in self.paths:
            await self.app(scope, receive, send)
            return

        total = 0

        async def limited_receive():
            nonlocal total
            message = await receive()
            if message.get("type") == "http.request":
                total += len(message.get("body", b""))
                if total > self.max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail="Upload too large (maximum 500 MB)",
                    )
            return message

        await self.app(scope, limited_receive, send)
