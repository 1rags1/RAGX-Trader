"""
HTTP + WebSocket access gate for RAGX-Trader.

Validates a shared access code, then issues an HttpOnly session cookie (HMAC-signed).
/api/health stays public for Railway probes.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from pathlib import Path

from fastapi import APIRouter, Request, Response, WebSocket
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT_DIR / "frontend"

COOKIE_NAME = "ragx_trader_session"
SESSION_PAYLOAD = b"ragx-trader-authenticated-v1"
DEFAULT_ACCESS_CODE = "RAGS07"

router = APIRouter(tags=["gate"])


def gate_enabled() -> bool:
    raw = os.getenv("RAGX_GATE_ENABLED", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def access_code() -> str:
    return (os.getenv("RAGX_ACCESS_CODE") or DEFAULT_ACCESS_CODE).strip()


def _signing_secret() -> bytes:
    secret = (os.getenv("RAGX_GATE_SECRET") or "").strip()
    if not secret:
        secret = f"{access_code()}-ragx-trader-gate-v1"
        logger.warning(
            "RAGX_GATE_SECRET is not set; using a derived secret. Set RAGX_GATE_SECRET in production."
        )
    return secret.encode("utf-8")


def session_cookie_value() -> str:
    return hmac.new(_signing_secret(), SESSION_PAYLOAD, hashlib.sha256).hexdigest()


def _cookie_from_request(request: Request) -> str | None:
    return request.cookies.get(COOKIE_NAME)


def is_authenticated_request(request: Request) -> bool:
    if not gate_enabled():
        return True
    token = _cookie_from_request(request)
    if not token:
        return False
    expected = session_cookie_value()
    return secrets.compare_digest(token, expected)


def is_authenticated_websocket(websocket: WebSocket) -> bool:
    if not gate_enabled():
        return True
    token = websocket.cookies.get(COOKIE_NAME)
    if not token:
        return False
    expected = session_cookie_value()
    return secrets.compare_digest(token, expected)


def _cookie_secure(request: Request) -> bool:
    forced = os.getenv("RAGX_COOKIE_SECURE", "").strip().lower()
    if forced in ("1", "true", "yes"):
        return True
    if forced in ("0", "false", "no"):
        return False
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if forwarded == "https":
        return True
    return request.url.scheme == "https"


def _set_session_cookie(response: Response, request: Request) -> None:
    max_age = int(os.getenv("RAGX_SESSION_MAX_AGE", str(60 * 60 * 24 * 7)))
    response.set_cookie(
        key=COOKIE_NAME,
        value=session_cookie_value(),
        max_age=max_age,
        httponly=True,
        secure=_cookie_secure(request),
        samesite="lax",
        path="/",
    )


def _clear_session_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(
        key=COOKIE_NAME,
        path="/",
        httponly=True,
        secure=_cookie_secure(request),
        samesite="lax",
    )


def is_public_path(path: str, method: str) -> bool:
    if not gate_enabled():
        return True

    normalized = path.rstrip("/") or "/"

    if normalized == "/api/health":
        return True
    if normalized == "/gate":
        return True
    if normalized in ("/css/gate.css", "/js/gate.js"):
        return True
    if normalized.startswith("/api/gate"):
        return True
    return False


def _wants_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return "text/html" in accept or "*/*" in accept or not accept


class AccessGateMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        if is_public_path(request.url.path, request.method):
            return await call_next(request)

        if is_authenticated_request(request):
            return await call_next(request)

        path = request.url.path
        if path.startswith("/api/") or path.startswith("/ws/"):
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized", "detail": "Access code required."},
            )

        if _wants_html(request):
            return RedirectResponse(url="/gate", status_code=302)

        return JSONResponse(
            status_code=401,
            content={"error": "unauthorized", "detail": "Access code required."},
        )


class LoginBody(BaseModel):
    code: str = Field(..., min_length=1, max_length=64)


@router.get("/gate")
async def gate_page(request: Request):
    if is_authenticated_request(request):
        return RedirectResponse(url="/", status_code=302)
    gate_file = FRONTEND_DIR / "gate.html"
    if not gate_file.is_file():
        return JSONResponse(status_code=500, content={"error": "gate page missing"})
    return FileResponse(gate_file, media_type="text/html; charset=utf-8")


@router.get("/api/gate/status")
async def gate_status(request: Request):
    return {"authenticated": is_authenticated_request(request), "gate_enabled": gate_enabled()}


@router.post("/api/gate/login")
async def gate_login(body: LoginBody, request: Request, response: Response):
    if not gate_enabled():
        return {"ok": True, "gate_enabled": False}

    submitted = body.code.strip()
    expected = access_code()
    if not submitted or not secrets.compare_digest(submitted, expected):
        return JSONResponse(
            status_code=401,
            content={"error": "invalid_code", "detail": "Incorrect access code."},
        )

    _set_session_cookie(response, request)
    return {"ok": True}


@router.post("/api/gate/logout")
async def gate_logout(request: Request, response: Response):
    _clear_session_cookie(response, request)
    return {"ok": True}


def configure_access_gate(app) -> None:
    app.include_router(router)
    if gate_enabled():
        app.add_middleware(AccessGateMiddleware)
        logger.info("Access gate enabled (public: /api/health, /gate, /api/gate/*).")
    else:
        logger.info("Access gate disabled (RAGX_GATE_ENABLED=0).")
