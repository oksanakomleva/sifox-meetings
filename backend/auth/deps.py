"""FastAPI dependencies for authentication."""
from fastapi import Cookie, Header, HTTPException, Request, status
from typing import Annotated

from database import models


async def get_current_user(
    session_token: Annotated[str | None, Cookie(alias="session")] = None,
) -> dict:
    if not session_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    session = await models.get_session(session_token)
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    if not session.get("is_active", True):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled")
    return session


async def get_admin_user(
    session_token: Annotated[str | None, Cookie(alias="session")] = None,
) -> dict:
    if not session_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    session = await models.get_session(session_token)
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    if not session.get("is_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    return session


async def get_test_or_admin_user(
    session_token: Annotated[str | None, Cookie(alias="session")] = None,
    x_test_api_key: Annotated[str | None, Header(alias="X-Test-Api-Key")] = None,
) -> dict:
    """
    Auth dependency for E2E test endpoints.
    Accepts either:
      - a valid admin session cookie (browser login), OR
      - X-Test-Api-Key header matching TEST_API_KEY env var (automated tests)
    """
    from config import config

    # Test API key path — for automated E2E without browser login
    if x_test_api_key is not None:
        if not config.TEST_API_KEY:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="TEST_API_KEY not configured on server")
        if x_test_api_key != config.TEST_API_KEY:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid test API key")
        # Return a synthetic admin user dict
        return {"user_id": 0, "email": "e2e-test@automated", "is_admin": True, "name": "E2E Test"}

    # Normal session cookie path
    if not session_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    session = await models.get_session(session_token)
    if not session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    if not session.get("is_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin only")
    return session
