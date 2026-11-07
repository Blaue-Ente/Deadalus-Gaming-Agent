"""Local HTTP API of the play worker: 127.0.0.1 only, bearer token, honest typed errors."""
from __future__ import annotations

import hmac
from contextlib import asynccontextmanager
import re
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import engines
from .errors import PlayError
from .manager import PlayManager

_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
_STATUS = {"not_found": 404, "session_limit": 409, "bad_request": 400, "unsupported_engine": 400,
           "engine_missing": 424, "tool_missing": 424, "display_unavailable": 424,
           "session_stopped": 410, "window_missing": 502, "start_failed": 502, "frame_unreadable": 502}


class StartBody(BaseModel):
    slug: str = Field(..., min_length=1, max_length=81)
    engine: str = "godot"
    width: int = 640
    height: int = 480


class FrameBody(BaseModel):
    source: str = "window"


class MouseBody(BaseModel):
    kind: str = "click"
    x: int = 0
    y: int = 0
    button: str = "left"


class InputBody(BaseModel):
    keys: list[str] = Field(default_factory=list, max_length=32)
    mouse: MouseBody | None = None
    hold_ms: int = 80
    via: str = "os"
    probe: bool = False       # frame -> input -> frame and report the observed change
    settle_ms: int = 400


class WaitBody(BaseModel):
    frames: int = 0
    ms: int = 0


def project_dir_for(slug: str) -> Path:
    if not _SLUG_RE.match(slug or ""):
        raise PlayError("bad_request", f"invalid project slug: {slug!r}")
    from ..context import projects_dir
    path = projects_dir() / slug
    if not path.is_dir():
        raise PlayError("not_found", f"no project {slug!r} in the Playground")
    return path


def create_app(token: str, manager: PlayManager | None = None) -> FastAPI:
    if not token or len(token) < 16:
        raise ValueError("the play worker needs a token of at least 16 characters")
    mgr = manager or PlayManager()
    @asynccontextmanager
    async def lifespan(_app):
        yield
        mgr.stop_all()           # a stopping worker leaves no game or display behind

    app = FastAPI(title="Daedalus play worker", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.manager = mgr

    def auth(authorization: str = Header(default="")) -> None:
        given = authorization[7:] if authorization.lower().startswith("bearer ") else ""
        if not hmac.compare_digest(given.encode(), token.encode()):
            raise HTTPException(status_code=401, detail="token required")

    @app.exception_handler(PlayError)
    async def _play_error(_req, exc: PlayError):
        return JSONResponse(exc.payload(), status_code=_STATUS.get(exc.code, 500))

    @app.get("/health")
    def health() -> dict[str, Any]:     # no token: liveness only, no paths, nothing about sessions
        return {"ok": True, "service": "daedalus-play-worker"}

    @app.get("/v1/status", dependencies=[Depends(auth)])
    def status() -> dict[str, Any]:
        return {"ok": True, "limit": mgr.limit, "sessions": mgr.list(), **engines.tools_status()}

    @app.post("/v1/sessions", dependencies=[Depends(auth)])
    def start(body: StartBody) -> dict[str, Any]:
        return mgr.start(body.slug, project_dir_for(body.slug), engine=body.engine, size=(body.width, body.height))

    @app.get("/v1/sessions/{sid}", dependencies=[Depends(auth)])
    def one(sid: str) -> dict[str, Any]:
        return mgr.get(sid).status()

    @app.post("/v1/sessions/{sid}/frame", dependencies=[Depends(auth)])
    def frame(sid: str, body: FrameBody) -> dict[str, Any]:
        return mgr.get(sid).frame(body.source)

    @app.post("/v1/sessions/{sid}/input", dependencies=[Depends(auth)])
    def send(sid: str, body: InputBody) -> dict[str, Any]:
        sess = mgr.get(sid)
        mouse = body.mouse.model_dump() if body.mouse else None
        if body.probe:
            return sess.probe_input(keys=body.keys, mouse=mouse, via=body.via, settle_ms=body.settle_ms, hold_ms=body.hold_ms)
        return sess.send(keys=body.keys, mouse=mouse, via=body.via, hold_ms=body.hold_ms)

    @app.post("/v1/sessions/{sid}/wait", dependencies=[Depends(auth)])
    def wait(sid: str, body: WaitBody) -> dict[str, Any]:
        return mgr.get(sid).wait(frames=body.frames, ms=body.ms)

    @app.get("/v1/sessions/{sid}/tree", dependencies=[Depends(auth)])
    def tree(sid: str) -> dict[str, Any]:
        return mgr.get(sid).tree()

    @app.get("/v1/sessions/{sid}/errors", dependencies=[Depends(auth)])
    def errors(sid: str) -> dict[str, Any]:
        return mgr.get(sid).errors()

    @app.get("/v1/sessions/{sid}/fps", dependencies=[Depends(auth)])
    def fps(sid: str) -> dict[str, Any]:
        return mgr.get(sid).fps()

    @app.delete("/v1/sessions/{sid}", dependencies=[Depends(auth)])
    def stop(sid: str) -> dict[str, Any]:
        return mgr.stop(sid)

    return app
