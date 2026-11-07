"""Standalone Daedalus FastAPI service — equal Iyveris mesh peer."""
from __future__ import annotations

import asyncio
import hmac
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.admin_compat import router as admin_compat_router
from app.envload import load_env_file
from app.playground_api import router as playground_router
from app.harness_api import router as harness_router
from app.harness_core_api import router as harness_core_router
from daedalus.playground_router import router as published_apps_router
from daedalus.skill_catalog import catalog_router
from daedalus.paths import product_root, repo_root, chroma_dir
from daedalus.identity_standalone import DAEDALUS_STANDALONE_PREAMBLE
from daedalus.identity import build_persona
from daedalus.owner_auth import OwnerAuthMiddleware, client_is_loopback_scope

log = logging.getLogger("app.main")
_STATIC = Path(__file__).resolve().parent / "static"
_UI_STATIC = Path(__file__).resolve().parent.parent / "ui" / "static"


def _health_llm_snapshot() -> dict:
    """Sync LLM slice for /health — run via asyncio.to_thread (blocks a worker thread, not the event loop)."""
    from daedalus.llm_config import get_brain_config, get_config
    brain = get_brain_config()
    cfg = get_config()
    return {
        "provider": cfg.get("provider"),
        "model": cfg.get("model"),
        "has_key": bool(cfg.get("api_key")),
        "brain_provider": brain.get("provider"),
        "brain_model": brain.get("model"),
    }


def _llm_status_payload() -> dict:
    """Sync body for GET /api/v1/llm/status — offloaded from async handler."""
    from daedalus.llm_config import get_brain_config, get_config
    brain = get_brain_config()
    cfg = get_config()
    overlays: dict = {}
    if isinstance(brain, dict) and isinstance(cfg, dict):
        if cfg.get("model") != brain.get("model") or cfg.get("provider") != brain.get("provider"):
            overlays["this_turn"] = {
                "provider": cfg.get("provider"),
                "model": cfg.get("model"),
                "source": cfg.get("source"),
            }
        if cfg.get("run_budget_tier"):
            overlays["run_budget_tier"] = cfg.get("run_budget_tier")
        if cfg.get("effort"):
            overlays["task_effort"] = cfg.get("effort")
    return {
        "ok": True,
        "provider": cfg.get("provider"),
        "model": cfg.get("model"),
        "source": cfg.get("source"),
        "has_key": bool(cfg.get("api_key")),
        "brain": {
            "provider": brain.get("provider"),
            "model": brain.get("model"),
            "source": brain.get("source"),
            "has_key": bool(brain.get("api_key")),
        },
        "overlays": overlays,
    }


def _mesh_key() -> str:
    return os.environ.get("IYVERIS_MESH_KEY", "").strip()


def _mesh_ok(provided: str | None, expected: str) -> bool:
    got = (provided or "").strip()
    if not expected or not got:
        return False
    if len(got) != len(expected):
        return False
    return hmac.compare_digest(got, expected)


def _check_mesh(
    x_iyveris_mesh_key: str | None,
    *,
    loopback: bool,
    require_key: bool = False,
) -> None:
    """Peer HTTP auth.

    Ping/health/capabilities: loopback may skip an unset key.
    Ask/chat always need a configured key (fail closed).
    """
    expected = _mesh_key()
    if require_key and not expected:
        raise HTTPException(status_code=401, detail="mesh key required")
    if expected:
        if not _mesh_ok(x_iyveris_mesh_key, expected):
            raise HTTPException(status_code=401, detail="invalid mesh key")
        return
    if not loopback:
        raise HTTPException(status_code=401, detail="mesh key required for non-loopback")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    load_env_file()
    # DAEDALUS_STANDALONE is set by CLI/Docker, not here — never flip it inside ADN.
    try:
        from daedalus.owner_auth import get_or_create_token, token_path
        get_or_create_token()
        log.info("owner token ready at %s", token_path())
    except Exception:
        log.exception("owner token bootstrap failed (non-fatal)")
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
        # No task owns a live run after a restart: mark them unknown (never success), then attach the
        # ledger to the gateway trace.
        from daedalus.harness import runs as _harness_runs
        _harness_runs.recover_orphans()
        _harness_runs.install()
    except Exception:
        log.exception("run ledger startup failed (non-fatal)")
    reaper = None
    try:
        from daedalus.playground_runner import start_idle_reaper
        reaper = start_idle_reaper()
    except Exception:
        log.exception("idle runner reaper failed to start (non-fatal)")
    try:
        yield
    finally:
        if reaper is not None:
            reaper.cancel()
            import contextlib
            with contextlib.suppress(asyncio.CancelledError):
                await reaper
        try:
            from daedalus.evolution import stop_gardener
            await stop_gardener()
        except Exception:
            log.debug("evolution gardener stop failed", exc_info=True)


app = FastAPI(
    title="Daedalus",
    description="Independent coding agent — games, apps, self-improvement.",
    version="0.3.8",
    lifespan=lifespan,
)
app.include_router(playground_router)
app.include_router(published_apps_router)
app.include_router(admin_compat_router)
app.include_router(harness_router)
app.include_router(harness_core_router)
app.include_router(catalog_router)
if _UI_STATIC.is_dir():
    app.mount("/admin/static", StaticFiles(directory=_UI_STATIC), name="admin-static")
if _STATIC.is_dir():
    app.mount("/static", StaticFiles(directory=_STATIC), name="static")
app.add_middleware(OwnerAuthMiddleware)


class ChatBody(BaseModel):
    message: str = Field(..., min_length=1, max_length=20000)
    session_id: str = Field(default="default", max_length=128)
    effort: str = Field(default="auto", max_length=16)
    skill_ids: list[str] = Field(default_factory=list)
    mcp_ids: list[str] = Field(default_factory=list)
    attachment_ids: list[str] = Field(default_factory=list)


class MeshBody(BaseModel):
    action: str = ""
    payload: dict = Field(default_factory=dict)
    from_service: str = ""


@app.get("/health")
async def health() -> dict:
    from daedalus.host import adn_available, standalone_mode
    from daedalus.store import db_path
    try:
        llm = await asyncio.to_thread(_health_llm_snapshot)
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
        "kv": _kv_backend(),
    }


def _kv_backend() -> dict:
    try:
        from daedalus import kv
        return kv.backend()
    except Exception as exc:  # health must never fail on this
        return {"requested": "unknown", "effective": "unknown", "note": str(exc)[:120]}


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
            "dissection", "play-lab", "smolvm-computer",
            "offline-kit", "llm-wardrobe", "addons", "capsule",
            "evolution", "windows-desktop", "windows-gui", "live-publish",
            "effort-routing", "world-kit", "world-runtime", "long-horizon",
            "owner-token", "typed-tools", "atomic-router", "durable-runs",
            "one-builder", "quality-gate", "engine-runners", "godot-runtime",
            "gameplay-bot", "asset-pipeline-v2",             "skill-memory", "production-layer",
            "unity-mcp", "game-agent-bench",
            "math3d", "game-lint", "profiler", "game-canon", "spatial",
            "tool-router", "session-context", "skill-catalog",
        ],
        "mesh_endpoint": "/api/iyveris",
        "studio": "/",
    }


@app.get("/api/v1/llm/status")
async def llm_status() -> dict:
    try:
        return await asyncio.to_thread(_llm_status_payload)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


@app.get("/api/v1/auth/bootstrap")
async def auth_bootstrap(request: Request) -> dict:
    from daedalus.owner_auth import get_or_create_token
    if not client_is_loopback_scope(request.scope):
        raise HTTPException(status_code=403, detail="bootstrap only from loopback")
    return {
        "ok": True,
        "token": get_or_create_token(),
        "header": "Authorization",
        "scheme": "Bearer",
    }


@app.post("/api/iyveris")
async def iyveris_mesh(
    request: Request,
    body: MeshBody,
    x_iyveris_mesh_key: str | None = Header(default=None, alias="X-Iyveris-Mesh-Key"),
) -> dict:
    action = (body.action or "ping").strip().lower()
    ask = action in ("ask", "daedalus.ask", "chat")
    _check_mesh(
        x_iyveris_mesh_key,
        loopback=client_is_loopback_scope(request.scope),
        require_key=ask,
    )
    if action in ("ping", "health"):
        return {"ok": True, "result": {"service": "daedalus", "status": "ok"}, "error": None}
    if action == "capabilities":
        caps = await capabilities()
        return {"ok": True, "result": caps, "error": None}
    if ask:
        msg = str((body.payload or {}).get("message") or (body.payload or {}).get("prompt") or "")
        if not msg:
            return {"ok": False, "result": None, "error": "payload.message required"}
        from daedalus.agent import get_daedalus
        reply = await get_daedalus().chat(message=msg, session_id="mesh")
        return {"ok": True, "result": reply, "error": None}
    return {"ok": False, "result": None, "error": f"unknown action: {action}"}


@app.post("/api/v1/daedalus/chat")
async def chat(body: ChatBody) -> dict:
    try:
        from daedalus.agent import get_daedalus
        return await get_daedalus().chat(
            message=body.message,
            session_id=body.session_id,
            effort=body.effort,
            skill_ids=body.skill_ids,
            mcp_ids=body.mcp_ids,
            attachment_ids=body.attachment_ids,
        )
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("blocking chat failed")
        raise HTTPException(status_code=500, detail=f"daedalus_failed: {exc}") from exc


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
    ico = _STATIC / "favicon.ico"
    if ico.is_file():
        return FileResponse(ico)
    mark = _STATIC / "daedalus-mark.png"
    if mark.is_file():
        return FileResponse(mark)
    raise HTTPException(status_code=404, detail="no favicon")
