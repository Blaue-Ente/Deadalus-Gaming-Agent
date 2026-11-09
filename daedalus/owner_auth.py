"""Owner token for Studio / Command Center HTTP APIs.

Loopback Studio bootstraps a token from ``GET /api/v1/auth/bootstrap``.
Public bind (not 127.0.0.1) refuses to start unless a token already exists
in ``DAEDALUS_TOKEN`` or ``$DAEDALUS_DATA/owner.token``.

This is not a multi-user ACL. One owner, one secret. Cookies are not used
(CSRF). Send ``Authorization: Bearer`` or ``X-Daedalus-Token``. GET streams
and file downloads may pass ``?daedalus_token=`` because EventSource cannot
set headers.
"""
from __future__ import annotations

import hmac
import logging
import os
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from starlette.responses import JSONResponse

from .paths import data_root

log = logging.getLogger(__name__)

TOKEN_ENV = "DAEDALUS_TOKEN"
TOKEN_HEADER = "x-daedalus-token"
QUERY_PARAM = "daedalus_token"
DEFAULT_BIND_HOST = "127.0.0.1"

_LOOPBACK_HOSTS = frozenset({
    "127.0.0.1",
    "::1",
    "localhost",
    "testclient",  # Starlette TestClient
})

_PUBLIC_EXACT = frozenset({
    "/",
    "/health",
    "/capabilities",
    "/favicon.ico",
    "/docs",
    "/docs/",
    "/redoc",
    "/redoc/",
    "/openapi.json",
    "/admin",
    "/admin/",
    "/admin/daedalus",
    "/admin/daedalus/",
    "/admin/daedalus/hub",
    "/admin/daedalus/playground",
    "/admin/daedalus/game-studio",
    "/admin/daedalus/connectors",
    "/admin/daedalus/proposals",
    "/admin/daedalus/skills",
    "/admin/daedalus/bundles",
    "/admin/llm-router",
    "/api/v1/auth/bootstrap",
})


def default_serve_host() -> str:
    return (os.environ.get("HOST") or DEFAULT_BIND_HOST).strip() or DEFAULT_BIND_HOST


def is_loopback_host(host: str | None) -> bool:
    h = (host or "").strip().lower()
    if not h:
        return False
    if h in _LOOPBACK_HOSTS:
        return True
    if h.startswith("127."):
        return True
    if h.startswith("::ffff:127."):
        return True
    return False


def bind_host() -> str:
    return (
        os.environ.get("DAEDALUS_BIND_HOST")
        or os.environ.get("HOST")
        or DEFAULT_BIND_HOST
    ).strip() or DEFAULT_BIND_HOST


def bind_is_loopback() -> bool:
    return is_loopback_host(bind_host())


def token_path() -> Path:
    return data_root() / "owner.token"


def configured_token() -> str:
    env = (os.environ.get(TOKEN_ENV) or "").strip()
    if env:
        return env
    path = token_path()
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return ""


def get_or_create_token() -> str:
    existing = configured_token()
    if existing:
        return existing
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(path, flags, 0o600)
    except FileExistsError:
        return path.read_text(encoding="utf-8").strip()
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(token + "\n")
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return token


def token_ok(provided: str) -> bool:
    expected = get_or_create_token()
    got = (provided or "").strip()
    if not expected or not got:
        return False
    if len(got) != len(expected):
        return False
    return hmac.compare_digest(got, expected)


def assert_bind_allowed(host: str) -> None:
    """Refuse a public bind when no owner token is configured yet."""
    if is_loopback_host(host):
        return
    if configured_token():
        return
    raise SystemExit(
        f"Refusing to bind {host} without an owner token. "
        "Use --host 127.0.0.1, or set DAEDALUS_TOKEN, or place a token in "
        f"{token_path()}."
    )


def _apps_db_item_path(path: str) -> bool:
    """True for ``/apps/{slug}/db/{key}`` with a non-empty key (not clear-all)."""
    parts = [seg for seg in (path or "").split("/") if seg]
    if len(parts) < 4 or parts[0] != "apps" or parts[2] != "db":
        return False
    return bool("/".join(parts[3:]).strip())


def is_public_request(method: str, path: str) -> bool:
    m = (method or "GET").upper()
    if m == "OPTIONS":
        return True
    p = path.split("?", 1)[0]
    if p.startswith("/apps/"):
        if m in ("GET", "HEAD"):
            return True
        # Single-key PlaygroundDB writes from a published game stay public.
        # Clear-all DELETE /apps/{slug}/db/ and /apps/api/* need the owner token.
        if m in ("POST", "DELETE") and _apps_db_item_path(p):
            return True
        return False
    if m not in ("GET", "HEAD"):
        return False
    if p in _PUBLIC_EXACT:
        return True
    if p.startswith("/static/") or p.startswith("/admin/static/"):
        return True
    if p.startswith("/docs") or p.startswith("/redoc"):
        return True
    return False


def extract_token(headers: dict[str, str], query: dict[str, list[str]], method: str) -> str:
    auth = headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    header = (headers.get(TOKEN_HEADER) or "").strip()
    if header:
        return header
    if (method or "GET").upper() in ("GET", "HEAD"):
        vals = query.get(QUERY_PARAM) or []
        if vals:
            return (vals[0] or "").strip()
    return ""


def _headers_from_scope(scope: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, val in scope.get("headers") or []:
        out[key.decode("latin-1").lower()] = val.decode("latin-1")
    return out


def client_host_from_scope(scope: dict[str, Any]) -> str:
    client = scope.get("client")
    if not client:
        return ""
    return str(client[0] or "")


def client_is_loopback_scope(scope: dict[str, Any]) -> bool:
    return is_loopback_host(client_host_from_scope(scope))


class OwnerAuthMiddleware:
    """Pure ASGI wrapper so SSE / StreamingResponse is not buffered."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        method = str(scope.get("method") or "GET").upper()
        path = str(scope.get("path") or "")
        if is_public_request(method, path):
            await self.app(scope, receive, send)
            return
        qs = parse_qs(scope.get("query_string", b"").decode("latin-1"), keep_blank_values=False)
        provided = extract_token(_headers_from_scope(scope), qs, method)
        if token_ok(provided):
            await self.app(scope, receive, send)
            return
        response = JSONResponse(
            {"ok": False, "error": "owner token required"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
        await response(scope, receive, send)
