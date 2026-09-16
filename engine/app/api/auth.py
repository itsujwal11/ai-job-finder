"""Authentication: bearer token for n8n, optional HTTP Basic for the dashboard."""
from __future__ import annotations

import base64
import binascii
import hmac

from fastapi import Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from ..config import get_env

MIN_TOKEN_LENGTH = 24


def require_pipeline_token(authorization: str | None = Header(default=None)) -> None:
    token = get_env().engine_api_token
    if not token or len(token) < MIN_TOKEN_LENGTH or token.startswith("change-me"):
        raise HTTPException(status_code=503, detail="ENGINE_API_TOKEN is not configured (use 24+ random characters)")
    supplied = authorization[7:] if authorization and authorization.startswith("Bearer ") else ""
    if not hmac.compare_digest(supplied.encode(), token.encode()):
        raise HTTPException(status_code=401, detail="Invalid or missing bearer token")


def _basic_ok(header: str, password: str) -> bool:
    if not header.startswith("Basic "):
        return False
    try:
        user, _, supplied = base64.b64decode(header[6:]).decode("utf-8").partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return False
    return hmac.compare_digest(user.encode(), b"admin") and hmac.compare_digest(supplied.encode(), password.encode())


async def dashboard_auth_middleware(request: Request, call_next) -> Response:
    password = get_env().dashboard_password
    path = request.url.path
    if password and not path.startswith(("/api/pipeline", "/api/health")):
        if not _basic_ok(request.headers.get("authorization", ""), password):
            return JSONResponse(
                {"detail": "Authentication required"}, status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Job Discovery"'},
            )
    return await call_next(request)
