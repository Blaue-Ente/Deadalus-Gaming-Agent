"""Standalone Daedalus FastAPI service — equal Iyveris mesh peer."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.admin_compat import router as admin_compat_router
from app.envload import load_env_file
from app.playground_api import router as playground_router
from app.harness_api import router as harness_router
from daedalus.playground_router import router as published_apps_router
from daedalus.paths import product_root, repo_root, chroma_dir
from daedalus.identity_standalone import DAEDALUS_STANDALONE_PREAMBLE
from daedalus.identity import build_persona

log = logging.getLogger("app.main")
_STATIC = Path(__file__).resolve().parent / "static"
_UI_STATIC = Path(__file__).resolve().parent.parent / "ui" / "static"


def _mesh_key() -> str:
    return os.environ.get("IYVERIS_MESH_KEY", "").strip()


def _check_mesh(x_iyveris_mesh_key: str | None) -> None:
    expected = _mesh_key()
    if not expected:
        return  # open in local dev
    if x_iyveris_mesh_key != expected:
        raise HTTPException(status_code=401, detail="invalid mesh key")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    load_env_file()
    # DAEDALUS_STANDALONE is set by CLI/Docker, not here — never flip it inside ADN.
    try:
        from daedalus.host import standalone_mode
        if standalone_mode():
            from daedalus.standalone_boot import startup
            await startup()
    except Exception:
        log.exception("standalone boot failed (non-fatal)")
    try:
        from daedalus.evolution import start_gardener
        start_gardener()
    except Exception:
        log.exception("evolution gardener failed to start (non-fatal)")
    try:
        yield
    finally:
        try:
            from daedalus.evolution import stop_gardener
            await stop_gardener()
        except Exception:
            log.debug("evolution gardener stop failed", exc_info=True)


app = FastAPI(
    title="Daedalus",
    description="Independent coding agent — games, apps, self-improvement.",
    version="0.3.3",
    lifespan=lifespan,
)
app.include_router(playground_router)
app.include_router(published_apps_router)
app.include_router(admin_compat_router)
app.include_router(harness_router)
if _UI_STATIC.is_dir():
    app.mount("/admin/static", StaticFiles(directory=_UI_STATIC), name="admin-static")
if _STATIC.is_dir():
    app.mount("/static", StaticFiles(directory=_STATIC), name="static")


class ChatBody(BaseModel):
    message: str = Field(..., min_length=1, max_length=20000)
    session_id: str = Field(default="default", max_length=128)
    effort: str = Field(default="auto", max_length=16)


class MeshBody(BaseModel):
    action: str = ""
    payload: dict = Field(default_factory=dict)
    from_service: str = ""


@app.get("/health")
async def health() -> dict:
    from daedalus.host import adn_available, standalone_mode
    from daedalus.store import db_path
    try:
        from daedalus.llm_config import get_config
        cfg = get_config()
        llm = {
            "provider": cfg.get("provider"),
            "model": cfg.get("model"),
            "has_key": bool(cfg.get("api_key")),
        }
    except Exception:
        llm = {"has_key": False}
    return {
        "ok": True,
        "service": "daedalus",
        "product_root": str(product_root()),
        "repo_root": str(repo_root()),
        "standalone": standalone_mode(),
        "adn_visible": adn_available(),
        "sqlite": str(db_path()) if standalone_mode() else None,
        "chroma": str(chroma_dir()) if standalone_mode() else None,
        "llm": llm,
    }


@app.get("/capabilities")
async def capabilities() -> dict:
    return {
        "ok": True,
        "name": "Daedalus",
        "capabilities": [
            "coding-agent", "games", "playground", "autopilot",
            "self-improve", "memory", "phaser", "unity-scaffold", "expo",
            "files", "shell-sandbox", "browser-verify", "git", "workers",
            "web-search", "mesh-peer", "local-llm", "shell-center",
            "native-tools", "image-gen", "preferences", "learned-skills",
            "agent-reach", "playtest", "mirofish-sidecar", "mcp",
            "browser-session", "subagents",
            "offline-kit", "llm-wardrobe", "addons", "capsule",
            "evolution", "windows-desktop", "live-publish",
            "effort-routing", "world-kit", "long-horizon",
        ],
        "mesh_endpoint": "/api/iyveris",
        "studio": "/",
    }


@app.get("/api/v1/llm/status")
async def llm_status() -> dict:
    try:
        from daedalus.llm_config import get_config
        cfg = get_config()
        return {
            "ok": True,
            "provider": cfg.get("provider"),
            "model": cfg.get("model"),
            "source": cfg.get("source"),
            "has_key": bool(cfg.get("api_key")),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


@app.post("/api/iyveris")
async def iyveris_mesh(
    body: MeshBody,
    x_iyveris_mesh_key: str | None = Header(default=None, alias="X-Iyveris-Mesh-Key"),
) -> dict:
    _check_mesh(x_iyveris_mesh_key)
    action = (body.action or "ping").strip().lower()
    if action in ("ping", "health"):
        return {"ok": True, "result": {"service": "daedalus", "status": "ok"}, "error": None}
    if action == "capabilities":
        caps = await capabilities()
        return {"ok": True, "result": caps, "error": None}
    if action in ("ask", "daedalus.ask", "chat"):
        msg = str((body.payload or {}).get("message") or (body.payload or {}).get("prompt") or "")
        if not msg:
            return {"ok": False, "result": None, "error": "payload.message required"}
        from daedalus.agent import get_daedalus
        reply = await get_daedalus().chat(message=msg, session_id="mesh")
        return {"ok": True, "result": reply, "error": None}
    return {"ok": False, "result": None, "error": f"unknown action: {action}"}


@app.post("/api/v1/daedalus/chat")
async def chat(body: ChatBody) -> dict:
        from daedalus.agent import get_daedalus
        return await get_daedalus().chat(
            message=body.message,
            session_id=body.session_id,
            effort=body.effort,
        )


@app.get("/api/v1/daedalus/identity")
async def identity_preview() -> dict:
    persona = build_persona()
    return {
        "ok": True,
        "standalone": True,
        "preamble_chars": len(DAEDALUS_STANDALONE_PREAMBLE),
        "persona_chars": len(persona),
        "games_first": "Games first" in DAEDALUS_STANDALONE_PREAMBLE,
    }


@app.get("/")
async def studio() -> FileResponse:
    page = _STATIC / "studio.html"
    if page.is_file():
        return FileResponse(page)
    return JSONResponse({
        "service": "Daedalus",
        "docs": "/docs",
        "health": "/health",
        "mesh": "/api/iyveris",
        "chat": "/api/v1/daedalus/chat",
    })


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    mark = _STATIC / "daedalus-mark.png"
    if mark.is_file():
        return FileResponse(mark)
    raise HTTPException(status_code=404, detail="no favicon")
