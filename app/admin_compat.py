"""Serve the copied ADN Daedalus UI from standalone FastAPI.

ADN ``admin.py`` / templates stay in ArgosDataNexus. This router reimplements
the ``/admin/daedalus/*`` URL contract the copied JS expects, delegating to
``daedalus.*`` (SQLite playground, kernel autopilot, chat, Game Studio).
"""
from __future__ import annotations

import json
import logging
import mimetypes
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates

log = logging.getLogger(__name__)

_UI = Path(__file__).resolve().parent.parent / "ui"
_TEMPLATES_DIR = _UI / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter(prefix="/admin", tags=["admin-compat"])

_FALLBACK_SUGGESTIONS = [
    "Добави high-score таблица с localStorage",
    "Направи фона анимиран с particle ефект",
    "Добави звук при победа и загуба",
    "Подобри мобилния layout и touch controls",
    "Добави екран с инструкции преди старт",
]


def _jsonable(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    out: dict[str, Any] = {}
    for k, v in row.items():
        out[k] = v.isoformat() if hasattr(v, "isoformat") else v
    return out


async def _body(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _page(request: Request, name: str, extra: dict[str, Any] | None = None) -> HTMLResponse:
    return templates.TemplateResponse(request, name, extra or {})


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _autopilot_sse_response(job, from_offset: int = 0):
    from daedalus import autopilot_jobs as aj

    async def _gen():
        yield _sse("job", {
            "job_id": job.id, "slug": job.slug,
            "status": job.status, "offset": from_offset,
            "event_count": len(job.events),
        })
        async for out in aj.subscribe(job, from_offset):
            if out["event"] == "_ping":
                yield ": ping\n\n"
            else:
                yield _sse(out["event"], out.get("data", {}))

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _guided_sse_response(job, from_offset: int = 0):
    from daedalus import guided_jobs as gj

    async def _gen():
        yield _sse("job", {
            "job_id": job.id, "slug": job.slug,
            "status": getattr(job, "status", "running"),
            "offset": from_offset,
        })
        subscribe = getattr(gj, "subscribe", None)
        if subscribe is None:
            yield _sse("error", {"message": "guided subscribe unavailable"})
            return
        async for out in subscribe(job, from_offset):
            if out.get("event") == "_ping":
                yield ": ping\n\n"
            else:
                yield _sse(out.get("event") or "log", out.get("data") or {})

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _extract_json_obj(text: str) -> dict[str, Any] | None:
    text = (text or "").strip()
    if not text:
        return None
    try:
        val = json.loads(text)
        return val if isinstance(val, dict) else None
    except Exception:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        val = json.loads(m.group(0))
        return val if isinstance(val, dict) else None
    except Exception:
        return None


def _extract_json_list(text: str) -> list | None:
    text = (text or "").strip()
    m = re.search(r"\[.*\]", text, re.DOTALL)
    raw = m.group(0) if m else text
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else None
    except Exception:
        return None


# ── HTML ────────────────────────────────────────────────────────────────────

@router.get("/daedalus/hub", response_class=HTMLResponse)
async def hub_page(request: Request):
    return _page(request, "daedalus_hub.html")


@router.get("/daedalus", response_class=HTMLResponse)
async def chat_page(request: Request):
    return _page(request, "daedalus.html")


@router.get("/daedalus/playground", response_class=HTMLResponse)
async def playground_page(request: Request):
    return _page(request, "daedalus_playground.html")


@router.get("/daedalus/game-studio", response_class=HTMLResponse)
async def game_studio_page(request: Request):
    return _page(request, "daedalus_game_studio.html")


@router.get("/daedalus/connectors", response_class=HTMLResponse)
async def connectors_page(request: Request):
    return _page(request, "daedalus_connectors.html")


@router.get("/daedalus/proposals", response_class=HTMLResponse)
async def proposals_page(request: Request):
    return _page(request, "proposals.html")


@router.get("/daedalus/skills", response_class=HTMLResponse)
async def skills_page(request: Request):
    return _page(request, "skills.html")


@router.get("/daedalus/bundles", response_class=HTMLResponse)
async def bundles_page(request: Request):
    return _page(request, "bundles.html")


@router.get("/llm-router", response_class=HTMLResponse)
async def llm_router_page(request: Request):
    ctx = {"provider": None, "model": None, "source": None, "has_key": False}
    try:
        from daedalus.llm_config import get_config
        cfg = get_config()
        ctx.update({
            "provider": cfg.get("provider"),
            "model": cfg.get("model"),
            "source": cfg.get("source"),
            "has_key": bool(cfg.get("api_key")),
        })
    except Exception as exc:
        log.debug("llm status page: %s", exc)
    return _page(request, "llm_router.html", ctx)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def admin_root(request: Request):
    return _page(request, "daedalus_hub.html")


# ── Agent chat / live / mode ────────────────────────────────────────────────

@router.post("/daedalus/chat")
async def daedalus_chat(request: Request):
    body = await _body(request)
    message = str(body.get("message", "")).strip()
    if not message:
        return JSONResponse({"error": "message required"}, status_code=400)
    attached_text = str(body.get("attached_text", "")).strip()
    attached_name = str(body.get("attached_name", "документ")).strip()
    if attached_text:
        message = (
            f"━━━ ПРИКАЧЕН ДОКУМЕНТ: {attached_name} ━━━\n"
            f"{attached_text}\n"
            f"━━━ КРАЙ НА ДОКУМЕНТА ━━━\n\n"
            f"{message}"
        )
    try:
        from daedalus.agent import get_daedalus
        return JSONResponse(await get_daedalus().chat(
            message=message,
            session_id=body.get("session_id"),
            for_self=bool(body.get("for_self", False)),
        ))
    except Exception as exc:
        log.exception("standalone chat failed")
        return JSONResponse({"error": f"daedalus_failed: {exc}"}, status_code=500)


@router.get("/daedalus/live")
async def daedalus_live(request: Request):
    from daedalus.stream import get_live
    sid = (request.query_params.get("session_id") or "").strip() or None
    if sid:
        entry = get_live(sid)
        return JSONResponse({"ok": True, "live": {sid: entry} if entry else {}})
    return JSONResponse({"ok": True, "live": get_live()})


@router.get("/daedalus/mode")
async def daedalus_get_mode():
    from daedalus import mode as _md
    m = _md.get_mode()
    return JSONResponse({"mode": m, "label": _md.label(m), "valid": list(_md.VALID_MODES)})


@router.post("/daedalus/mode")
async def daedalus_set_mode(request: Request):
    from daedalus import mode as _md
    body = await _body(request)
    try:
        m = _md.set_mode(str(body.get("mode", "")))
        return JSONResponse({"ok": True, "mode": m, "label": _md.label(m)})
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@router.get("/daedalus/sessions")
async def daedalus_list_sessions(limit: int = 60):
    try:
        from daedalus.agent import get_daedalus
        return JSONResponse({"sessions": get_daedalus().memory.list_sessions(limit=limit)})
    except Exception as exc:
        log.warning("sessions list unavailable: %s", exc)
        return JSONResponse({"sessions": [], "error": str(exc)})


@router.get("/daedalus/sessions/{session_id}")
async def daedalus_get_session(session_id: str):
    try:
        from daedalus.agent import get_daedalus
        turns = get_daedalus().memory.session_history(session_id, limit=500)
        return JSONResponse({"session_id": session_id, "turns": turns})
    except Exception as exc:
        log.warning("session history unavailable: %s", exc)
        return JSONResponse({"session_id": session_id, "turns": [], "error": str(exc)})


@router.get("/daedalus/progress")
async def daedalus_progress():
    from daedalus.outbox import get_progress
    return JSONResponse({"ok": True, "progress": get_progress()})


@router.post("/daedalus/progress/clear")
async def daedalus_progress_clear():
    from daedalus.outbox import clear_progress
    clear_progress()
    return JSONResponse({"ok": True})


@router.get("/daedalus/permissions")
async def daedalus_permissions_list():
    from daedalus.outbox import list_permissions, permission_catalog
    return JSONResponse({
        "ok": True,
        "permissions": list_permissions(),
        "catalog": permission_catalog(),
    })


@router.post("/daedalus/permissions/grant")
async def daedalus_permissions_grant(request: Request):
    body = await _body(request)
    perm_class = str(body.get("perm_class", "")).strip()
    hours = float(body.get("hours", 24))
    if not perm_class:
        return JSONResponse({"ok": False, "error": "perm_class required"}, status_code=400)
    from daedalus.outbox import grant_permission
    result = grant_permission(perm_class=perm_class, hours=hours, granted_by="owner")
    return JSONResponse({"ok": True, "perm_class": perm_class, "hours": hours, "record": result})


@router.post("/daedalus/permissions/{perm_class}/revoke")
async def daedalus_permissions_revoke(perm_class: str):
    from daedalus.outbox import revoke_permission
    ok = revoke_permission(perm_class)
    return JSONResponse({"ok": ok, "perm_class": perm_class})


@router.post("/daedalus/permissions/grant-all")
async def daedalus_permissions_grant_all(request: Request):
    body = await _body(request)
    hours = float(body.get("hours", 24))
    from daedalus.outbox import grant_all_known_permissions
    result = grant_all_known_permissions(hours=hours, granted_by="owner")
    return JSONResponse({"ok": True, **result})


@router.post("/daedalus/permissions/renew-expired")
async def daedalus_permissions_renew(request: Request):
    body = await _body(request)
    hours = float(body.get("hours", 24))
    from daedalus.outbox import renew_expired_permissions
    return JSONResponse(renew_expired_permissions(hours=hours, granted_by="owner"))


@router.get("/daedalus/outbox/api")
async def daedalus_outbox_list(status: str | None = None):
    from daedalus.outbox import list_outbox, pending_count
    items = list_outbox(status=status, limit=100)
    return JSONResponse({"ok": True, "items": items, "pending": pending_count()})


@router.post("/daedalus/outbox/{item_id}/apply")
async def daedalus_outbox_apply(item_id: str):
    from daedalus.outbox import apply_data_batch
    return JSONResponse(await apply_data_batch(item_id))


@router.post("/daedalus/outbox/{item_id}/dismiss")
async def daedalus_outbox_dismiss(item_id: str):
    from daedalus.outbox import update_outbox_item, get_outbox_item
    item = get_outbox_item(item_id)
    if not item:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    updated = update_outbox_item(item_id, status="dismissed")
    return JSONResponse({"ok": True, "item": updated})


@router.get("/daedalus/llm-config")
async def get_llm_config():
    from daedalus.llm_config import describe_effective_config, list_preset_models
    diag = describe_effective_config()
    eff = diag.get("effective") or {}
    out = dict(eff)
    out.pop("api_key", None)
    out["presets"] = list_preset_models()
    out["db_raw"] = diag.get("db_raw")
    out["warnings"] = diag.get("warnings", [])
    out["aligned"] = diag.get("aligned", True)
    return JSONResponse(out)


@router.get("/daedalus/navigator/stats")
async def navigator_stats():
    from daedalus.navigator import get_navigator_stats
    return JSONResponse(get_navigator_stats())


@router.post("/daedalus/navigator/reset")
async def navigator_reset():
    from daedalus.navigator import reset_navigator_stats
    reset_navigator_stats()
    return JSONResponse({"ok": True})


@router.post("/daedalus/web-search")
async def daedalus_web_search(request: Request):
    body = await _body(request)
    query = str(body.get("query") or body.get("q") or "").strip()
    if not query:
        return JSONResponse({"ok": False, "error": "query required"}, status_code=400)
    try:
        from daedalus.agent import get_daedalus
        return JSONResponse(await get_daedalus().web_search(
            query=query,
            limit=int(body.get("limit") or 5),
            for_self=bool(body.get("for_self", False)),
        ))
    except Exception as extra:
        log.warning("agent web_search failed (%s); standalone fallback", extra)
        from daedalus.web_search_standalone import web_search
        return JSONResponse(await web_search(query, limit=int(body.get("limit") or 5)))


@router.post("/daedalus/github-search")
async def daedalus_github_search(request: Request):
    body = await _body(request)
    query = str(body.get("query") or body.get("q") or "").strip()
    if not query:
        return JSONResponse({"ok": False, "error": "query required"}, status_code=400)
    try:
        from daedalus.agent import get_daedalus
        return JSONResponse(await get_daedalus().github_search(
            query=query,
            language=body.get("language"),
            limit=int(body.get("limit") or 5),
            min_stars=int(body.get("min_stars") or 0),
            for_self=bool(body.get("for_self", False)),
        ))
    except Exception as extra:
        return JSONResponse({"ok": False, "error": str(extra)}, status_code=500)


# ── Playground CRUD ─────────────────────────────────────────────────────────

@router.get("/daedalus/playground/api/projects")
async def playground_list():
    from daedalus import playground as pg
    from daedalus import playground_runner as runner
    from daedalus import playground_workspace_backup as wsbackup
    try:
        projects = await pg.list_projects()
        running = {r["slug"]: r for r in runner.list_running()}
        out = []
        for p in projects:
            row = _jsonable(p)
            fc = wsbackup.workspace_file_count(p["slug"])
            row["workspace_files"] = fc
            row["workspace_empty"] = fc == 0
            warn = wsbackup.workspace_warning(p["slug"])
            if warn:
                row["workspace_warning"] = warn
            row["running"] = running.get(p["slug"])
            out.append(row)
        return JSONResponse({"projects": out, "count": len(out)})
    except Exception as exc:
        log.exception("list projects")
        return JSONResponse({"projects": [], "count": 0, "error": str(exc)})


@router.post("/daedalus/playground/api/projects")
async def playground_create(request: Request):
    from daedalus import playground as pg
    body = await _body(request)
    name = (body.get("name") or "").strip()
    if not name:
        return JSONResponse({"ok": False, "error": "name required"}, status_code=400)
    coding_mode = (body.get("coding_mode") or "lite").strip()
    if coding_mode not in ("lite", "pro", "premium"):
        coding_mode = "lite"
    try:
        proj = await pg.create_project(
            name, kind=body.get("kind", ""), source=body.get("source", "built"),
            description=body.get("description", ""), slug=body.get("slug", ""),
            coding_mode=coding_mode, effort=body.get("effort", "auto"),
        )
        files = body.get("files") or {}
        if files:
            pg.write_files(proj["slug"], files)
            proj = await pg.get_project(proj["slug"])
        return JSONResponse({"ok": True, "project": _jsonable(proj)})
    except Exception as exc:
        log.exception("create project")
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@router.get("/daedalus/playground/api/projects/{slug}")
async def playground_detail(slug: str):
    from daedalus import playground as pg
    from daedalus import playground_runner as runner
    proj = await pg.get_project(slug)
    if not proj:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    out = _jsonable(proj)
    out["tree"] = pg.read_tree(proj["slug"])
    out["running"] = runner.status(proj["slug"])
    return JSONResponse({"ok": True, "project": out})


@router.get("/daedalus/playground/api/projects/{slug}/file")
async def playground_file(slug: str, path: str):
    from daedalus import playground as pg
    try:
        return JSONResponse({"ok": True, "path": path, "content": pg.read_file(slug, path)})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)


@router.post("/daedalus/playground/api/projects/{slug}/file")
async def playground_write_file(slug: str, request: Request):
    from daedalus import playground as pg
    body = await _body(request)
    files = body.get("files") or ({body["path"]: body["content"]} if body.get("path") else {})
    if not files:
        return JSONResponse({"ok": False, "error": "no files"}, status_code=400)
    n = pg.write_files(slug, files)
    return JSONResponse({"ok": True, "written": n})


@router.get("/daedalus/playground/api/projects/{slug}/files-snapshot")
async def playground_files_snapshot(slug: str):
    from daedalus import playground as pg
    if not await pg.get_project(slug):
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    return JSONResponse({"ok": True, "tree": pg.read_tree(slug)})


@router.post("/daedalus/playground/api/projects/{slug}/delete")
async def playground_delete(slug: str):
    from daedalus import playground as pg
    from daedalus import playground_runner as runner
    await runner.stop(slug)
    ok = await pg.delete_project(slug, wipe_files=True)
    return JSONResponse({"ok": ok})


@router.post("/daedalus/playground/api/projects/{slug}/run")
async def playground_run(slug: str, request: Request):
    from daedalus import playground as pg_mod
    from daedalus import playground_runner as runner
    from daedalus.playground_runtime import is_local_only_kind, local_only_kind_message
    proj = await pg_mod.get_project(slug)
    if not proj:
        return JSONResponse({"ok": False, "error": "project not found"}, status_code=404)
    if is_local_only_kind(proj.get("kind")):
        return JSONResponse(
            {"ok": False, "error": local_only_kind_message(proj.get("kind", ""))},
            status_code=400,
        )
    body = await _body(request)
    try:
        rec = await runner.start(
            slug,
            install_deps=bool(body.get("install_deps")),
            tunnel=bool(body.get("tunnel")),
        )
        return JSONResponse({"ok": True, "run": rec})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@router.post("/daedalus/playground/api/projects/{slug}/stop")
async def playground_stop(slug: str):
    from daedalus import playground_runner as runner
    ok = await runner.stop(slug)
    return JSONResponse({"ok": ok})


@router.get("/daedalus/playground/api/projects/{slug}/logs")
async def playground_logs(slug: str):
    from daedalus import playground_runner as runner
    return JSONResponse({"slug": slug, "logs": runner.logs(slug), "status": runner.status(slug)})


@router.post("/daedalus/playground/api/projects/{slug}/set-mode")
async def playground_set_mode(slug: str, request: Request):
    from daedalus import playground as pg
    body = await _body(request)
    mode = (body.get("mode") or "lite").strip()
    if mode not in ("lite", "pro", "premium"):
        return JSONResponse({"ok": False, "error": "invalid mode — use lite/pro/premium"}, status_code=400)
    proj = await pg.update_project(slug, coding_mode=mode)
    if not proj:
        return JSONResponse({"ok": False, "error": "project not found"}, status_code=404)
    return JSONResponse({"ok": True, "slug": slug, "coding_mode": mode})


@router.get("/daedalus/playground/api/projects/{slug}/assets")
async def playground_list_assets(slug: str):
    from daedalus import playground as pg
    from daedalus import playground_assets as pa
    if not await pg.get_project(slug):
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    return JSONResponse({"ok": True, "assets": pa.list_assets(slug)})


@router.get("/daedalus/playground/api/projects/{slug}/errors")
async def playground_errors_get(slug: str):
    return JSONResponse({"ok": True, "errors": [], "slug": slug})


@router.post("/daedalus/playground/api/projects/{slug}/suggest")
async def playground_suggest(slug: str):
    from daedalus import playground as pg
    proj = await pg.get_project(slug)
    if not proj:
        return JSONResponse({"ok": False, "error": "project not found"}, status_code=404)
    try:
        from daedalus.llm_client import complete
        prompt = (
            f"Проект: \"{proj.get('name', slug)}\" · Тип: {proj.get('kind','static')}\n"
            "Дай точно 5 конкретни предложения за подобрение. "
            "Отговори САМО с JSON масив от 5 низа на български."
        )
        result = await complete(user=prompt, intent="json", max_tokens=400, temperature=0.8)
        text = (result.get("reply") or "") if isinstance(result, dict) else str(result)
        suggestions = _extract_json_list(text) or []
        suggestions = [str(s).strip() for s in suggestions if s][:6]
        if suggestions:
            return JSONResponse({"ok": True, "suggestions": suggestions})
    except Exception as exc:
        log.debug("suggest llm failed: %s", exc)
    return JSONResponse({"ok": True, "suggestions": list(_FALLBACK_SUGGESTIONS)})


# ── Autopilot (SSE, same contract as ADN) ───────────────────────────────────

@router.post("/daedalus/playground/api/projects/{slug}/autopilot")
async def playground_autopilot(slug: str, request: Request):
    from daedalus import playground as pg
    from daedalus.builder_runtime import start_builder_run_async
    body = await _body(request)
    description = (body.get("description") or "").strip()
    if not description:
        return JSONResponse({"ok": False, "error": "description required"}, status_code=400)
    proj = await pg.get_project(slug)
    if not proj:
        return JSONResponse({"ok": False, "error": "project not found"}, status_code=404)
    try:
        job = await start_builder_run_async(
            slug, description, mode="full", resume=bool(body.get("resume", False)),
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
    return _autopilot_sse_response(job, from_offset=0)


@router.get("/daedalus/playground/api/projects/{slug}/autopilot/stream")
async def playground_autopilot_stream(slug: str, request: Request):
    from daedalus import autopilot_jobs as aj
    job_id = (request.query_params.get("job_id") or "").strip()
    try:
        from_offset = int(request.query_params.get("from", "0"))
    except (TypeError, ValueError):
        from_offset = 0
    job = aj.get(job_id) if job_id else aj.get_active(slug)
    if not job or job.slug != slug:
        return JSONResponse({"ok": False, "error": "job not found or expired"}, status_code=404)
    return _autopilot_sse_response(job, from_offset=from_offset)


@router.get("/daedalus/playground/api/projects/{slug}/autopilot/active")
async def playground_autopilot_active(slug: str):
    from daedalus import autopilot_jobs as aj
    job = aj.get_active(slug)
    if not job:
        return JSONResponse({"active": False})
    return JSONResponse({
        "active": job.status == "running",
        "job_id": job.id,
        "status": job.status,
        "paused": getattr(job, "paused", False),
        "offset": len(job.events),
        "interrupted": job.status == "interrupted",
        "error": job.error,
    })


@router.post("/daedalus/playground/api/projects/{slug}/autopilot/stop")
async def playground_autopilot_stop(slug: str):
    from daedalus import autopilot_jobs as aj
    cancelled = await aj.cancel_active(slug)
    return JSONResponse({"ok": True, "cancelled": cancelled})


@router.post("/daedalus/playground/api/projects/{slug}/autopilot/pause")
async def playground_autopilot_pause(slug: str):
    from daedalus import autopilot_jobs as aj
    paused = await aj.pause_active(slug)
    return JSONResponse({"ok": True, "paused": paused})


@router.post("/daedalus/playground/api/projects/{slug}/autopilot/resume")
async def playground_autopilot_resume(slug: str):
    from daedalus import autopilot_jobs as aj
    resumed = await aj.resume_active(slug)
    return JSONResponse({"ok": True, "resumed": resumed})


@router.get("/daedalus/playground/api/projects/{slug}/autopilot/resume-info")
async def playground_autopilot_resume_info(slug: str):
    from daedalus import playground as pg
    info = await pg.get_resume_info(slug)
    if not info:
        return JSONResponse({"can_resume": False})
    return JSONResponse({"can_resume": True, **info})


@router.post("/daedalus/playground/api/projects/{slug}/autopilot/clear-plan")
async def playground_autopilot_clear_plan(slug: str):
    from daedalus import playground as pg
    await pg.clear_autopilot_plan(slug)
    return JSONResponse({"ok": True})


@router.post("/daedalus/playground/api/projects/{slug}/plan/create")
async def playground_plan_create(slug: str, request: Request):
    """Thinner standalone planner — same JSON shape the copied UI expects."""
    from daedalus import playground as pg
    from daedalus.game_platform_guide import is_likely_game
    from daedalus.playground_plan_guide import (
        PLAN_GRANULARITY_RULES,
        build_planner_system_prompt,
        enrich_plan,
        resolve_plan_kind,
    )

    body = await _body(request)
    description = (body.get("description") or "").strip()
    if not description:
        return JSONResponse({"ok": False, "error": "description required"}, status_code=400)
    proj = await pg.get_project(slug)
    if not proj:
        return JSONResponse({"ok": False, "error": "project not found"}, status_code=404)
    kind_key, kind_label = resolve_plan_kind(proj.get("kind", "static"))
    studio = bool(body.get("studio"))
    ultra = bool(body.get("ultra"))
    studio_preset = (body.get("studio_preset") or "").strip() or "custom"
    sys = build_planner_system_prompt(
        kind_key,
        PLAN_GRANULARITY_RULES + f"\nPlatform: {kind_label}",
        is_game=is_likely_game(f"{proj.get('name', '')} {description}"),
        ultra=ultra,
        studio=studio,
        studio_preset=studio_preset,
    )
    plan: dict[str, Any] | None = None
    try:
        from daedalus.llm_client import complete
        result = await complete(system=sys, user=description, intent="json", max_tokens=4000)
        text = (result.get("reply") or "") if isinstance(result, dict) else str(result)
        plan = _extract_json_obj(text)
    except Exception as exc:
        log.warning("plan/create llm failed: %s", exc)
    if not plan or not isinstance(plan.get("stages"), list):
        plan = {
            "title": proj.get("name") or slug,
            "stages": [
                {
                    "id": 1,
                    "title": "Scaffold",
                    "goal": "Create project files",
                    "instructions": description[:1500],
                    "uses_visuals": False,
                    "files": ["index.html"],
                    "status": "pending",
                }
            ],
        }
    if studio:
        from daedalus.playground_studio_mode import finalize_studio_plan
        plan = finalize_studio_plan(
            plan,
            kind=kind_key,
            preset=studio_preset,
            description=description,
            project_name=proj.get("name") or slug,
        )
    plan = enrich_plan(plan, kind_key, description)
    await pg.save_autopilot_plan(slug, plan)
    return JSONResponse({"ok": True, "plan": plan, "slug": slug})


# ── Preview ─────────────────────────────────────────────────────────────────

@router.api_route("/daedalus/playground/preview/{slug}/{path:path}", methods=["GET", "POST", "HEAD"])
@router.api_route("/daedalus/playground/preview/{slug}", methods=["GET", "POST", "HEAD"])
async def playground_preview(slug: str, request: Request, path: str = ""):
    from daedalus import playground as pg
    from daedalus import playground_runner as runner
    from daedalus.playground_runtime import PlaygroundRuntime

    proj = await pg.get_project(slug)
    if not proj:
        return Response("project not found", status_code=404)

    port = runner.get_port(proj["slug"])
    if port:
        from daedalus.playground_proxy import proxy_http
        return await proxy_http(port, path, request, slug=slug, kind=proj.get("kind", ""))

    static_root = PlaygroundRuntime.preview_static_root(proj["slug"], proj.get("kind", "static"))
    if static_root is None:
        return HTMLResponse(
            "<p>Приложението не върви. Натисни <b>▶ Run</b> в Playground.</p>",
            status_code=200,
        )
    rel = (path or "index.html").lstrip("/") or "index.html"
    try:
        target = (static_root / rel).resolve()
        if static_root.resolve() not in target.parents and target != static_root.resolve():
            return Response("forbidden", status_code=403)
    except Exception:
        return Response("bad path", status_code=400)
    if target.is_dir():
        target = target / "index.html"
    if not target.is_file():
        index = static_root / "index.html"
        if index.is_file() and not path:
            target = index
        else:
            return Response("file not found", status_code=404)
    ctype, _ = mimetypes.guess_type(str(target))
    return FileResponse(target, media_type=ctype or "application/octet-stream")


# ── Git / connectors ────────────────────────────────────────────────────────

@router.get("/daedalus/playground/api/github/status")
async def playground_github_status():
    from daedalus.connectors_config import (
        github_token_source, is_github_configured, test_github_connection,
    )
    test = await test_github_connection()
    return JSONResponse({
        "configured": is_github_configured(),
        "ok": test.get("ok", False),
        "login": test.get("login"),
        "source": github_token_source(),
        "can_push": test.get("can_push"),
        "error": test.get("error"),
        "hint": test.get("hint"),
        "connectors_url": "/admin/daedalus/connectors",
    })


@router.get("/daedalus/playground/api/github/repos")
async def playground_github_repos(limit: int = 30, page: int = 1):
    from daedalus import playground_github as gh
    from daedalus.playground_revive import compatibility_report
    if not gh.is_configured():
        return JSONResponse(
            {"ok": False, "error": "GITHUB_TOKEN not configured", "repos": []},
            status_code=400,
        )
    try:
        repos = gh.list_user_repos(limit=min(limit, 100), page=max(page, 1))
        for repo in repos:
            compat = compatibility_report(None, repo_meta=repo)
            repo["compat"] = {
                "score": compat["score"],
                "level": compat["level"],
                "kind": compat["kind"],
                "badges": compat["badges"],
                "warnings": compat["warnings"][:3],
            }
        return JSONResponse({"ok": True, "repos": repos, "count": len(repos)})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:400], "repos": []}, status_code=500)


@router.get("/daedalus/playground/api/github/repos/{owner}/{repo}/branches")
async def playground_github_branches(owner: str, repo: str):
    from daedalus import playground_github as gh
    if not gh.is_configured():
        return JSONResponse({"ok": False, "error": "GITHUB_TOKEN not configured"}, status_code=400)
    try:
        branches = gh.list_repo_branches(owner, repo)
        tags = gh.list_repo_tags(owner, repo, limit=10)
        meta = gh.get_repo(owner, repo)
        return JSONResponse({
            "ok": True,
            "branches": branches,
            "tags": tags,
            "default_branch": meta.get("default_branch", "main"),
        })
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:400]}, status_code=500)


@router.post("/daedalus/playground/api/github/compat-check")
async def playground_compat_check(request: Request):
    from daedalus.playground_revive import assess_url
    body = await _body(request)
    url = str(body.get("url") or "").strip()
    if not url:
        return JSONResponse({"ok": False, "error": "url required"}, status_code=400)
    try:
        report = await assess_url(url)
        return JSONResponse({"ok": True, "compat": report})
    except Exception as extra:
        return JSONResponse({"ok": False, "error": str(extra)[:400]}, status_code=500)


@router.get("/daedalus/playground/api/gitlab/status")
async def playground_gitlab_status():
    from daedalus.connectors_config import test_gitlab_connection
    test = await test_gitlab_connection()
    return JSONResponse({
        "configured": bool(test.get("configured")),
        "ok": test.get("ok", False),
        "error": test.get("error"),
        "hint": test.get("hint"),
        "username": test.get("username"),
        "host": test.get("host"),
    })


@router.get("/daedalus/playground/api/git/status")
async def playground_git_status():
    from daedalus.connectors_config import full_status
    st = await full_status()
    return JSONResponse({"ok": True, **st.get("git", {}), "github": st.get("github"), "gitlab": st.get("gitlab")})


@router.get("/daedalus/connectors/status")
async def connectors_status():
    from daedalus.connectors_config import full_status
    return await full_status()


@router.post("/daedalus/connectors/test-github")
async def connectors_test_github():
    from daedalus.connectors_config import test_github_connection
    return await test_github_connection()


@router.post("/daedalus/connectors/test-gitlab")
async def connectors_test_gitlab():
    from daedalus.connectors_config import test_gitlab_connection
    return await test_gitlab_connection()


@router.get("/daedalus/connectors/config")
async def connectors_get_config():
    from daedalus.connectors_config import get_prefs
    return get_prefs()


@router.post("/daedalus/connectors/config")
async def connectors_set_config(request: Request):
    from daedalus.connectors_config import set_prefs
    body = await _body(request)
    return set_prefs(**body)


@router.get("/daedalus/playground/api/durability/status")
async def durability_status():
    from daedalus import playground as pg
    from daedalus import playground_workspace_backup as wsbackup
    from daedalus.connectors_config import full_status
    connectors = await full_status()
    projects = await pg.list_projects()
    proj_stats = []
    for p in projects:
        slug = p["slug"]
        fc = wsbackup.workspace_file_count(slug)
        proj_stats.append({
            "slug": slug,
            "name": p.get("name"),
            "source": p.get("source"),
            "status": p.get("status"),
            "published": False,
            "workspace_files": fc,
            "workspace_empty": fc == 0,
            "durable": False,
        })
    return JSONResponse({
        "ok": True,
        "connectors": connectors,
        "projects": proj_stats,
        "summary": {
            "total": len(projects),
            "published": 0,
            "empty_workspace": len([x for x in proj_stats if x["workspace_empty"]]),
            "storage_configured": connectors.get("storage", {}).get("configured", False),
        },
    })


@router.get("/daedalus/playground/api/published")
async def playground_published():
    return JSONResponse({"ok": True, "apps": []})


# ── Game Studio + wizard helpers ────────────────────────────────────────────

@router.get("/daedalus/playground/api/game-hints")
async def playground_game_hints(kind: str = "auto", name: str = "", description: str = ""):
    from daedalus.game_platform_guide import get_hints
    return JSONResponse({"ok": True, **get_hints(kind, name, description)})


@router.get("/daedalus/playground/api/studio/presets")
async def playground_studio_presets():
    from daedalus.playground_studio_mode import list_presets, milestones_for_preset
    presets = [{**p, "milestones": milestones_for_preset(p.get("id", ""))} for p in list_presets()]
    return JSONResponse({"ok": True, "presets": presets})


@router.get("/daedalus/playground/api/game-studio/catalog")
@router.get("/daedalus/game-studio/api/catalog")
async def game_studio_catalog():
    from daedalus.game_studio import catalog
    return JSONResponse({"ok": True, **catalog()})


@router.post("/daedalus/playground/api/game-studio/brainstorm")
@router.post("/daedalus/game-studio/api/brainstorm")
async def game_studio_brainstorm(request: Request):
    from daedalus.game_studio import process_brainstorm
    try:
        result = process_brainstorm(await _body(request))
        return JSONResponse({"ok": True, **result})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:400]}, status_code=400)


@router.post("/daedalus/playground/api/game-studio/gdd")
@router.post("/daedalus/game-studio/api/gdd")
async def game_studio_gdd(request: Request):
    from daedalus.game_studio import generate_gdd_lite, normalize_brainstorm, resolve_platform_preset
    body = await _body(request)
    brainstorm = normalize_brainstorm(body.get("brainstorm") or body)
    try:
        resolved = resolve_platform_preset(
            brainstorm,
            studio_preset=(body.get("studio_preset") or "").strip(),
            kind=(body.get("kind") or "").strip(),
        )
        gdd = generate_gdd_lite(
            brainstorm, studio_preset=resolved["studio_preset"], kind=resolved["kind"],
        )
        return JSONResponse({
            "ok": True, "gdd": gdd, "gdd_path": "design/gdd-lite.md", "resolved": resolved,
        })
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:400]}, status_code=400)


@router.post("/daedalus/playground/api/game-studio/start")
@router.post("/daedalus/game-studio/api/start")
async def game_studio_start(request: Request):
    from daedalus.game_studio import normalize_brainstorm, start_game_studio_project
    body = await _body(request)
    brainstorm = normalize_brainstorm(body.get("brainstorm") or body)
    name = (body.get("name") or brainstorm.get("title") or "").strip()
    if not name:
        return JSONResponse({"ok": False, "error": "name required"}, status_code=400)
    try:
        result = await start_game_studio_project(
            name=name,
            brainstorm=brainstorm,
            studio_preset=(body.get("studio_preset") or "").strip(),
            kind=(body.get("kind") or "").strip(),
            build_mode=(body.get("build_mode") or "guided").strip(),
            slug=(body.get("slug") or "").strip(),
        )
        return JSONResponse({
            "ok": True,
            "project": _jsonable(result.get("project") or {}),
            "slug": result.get("slug"),
            "gdd_path": result.get("gdd_path"),
            "studio_preset": result.get("studio_preset"),
            "kind": result.get("kind"),
            "build_mode": result.get("build_mode"),
            "plan_description": result.get("plan_description"),
            "milestones": result.get("milestones"),
            "init_notes": result.get("init_notes"),
        })
    except Exception as exc:
        log.exception("game studio start")
        return JSONResponse({"ok": False, "error": str(exc)[:400]}, status_code=500)


@router.get("/daedalus/game-studio/api/projects")
async def game_studio_list_projects():
    from daedalus.game_studio import list_game_studio_projects
    projects = await list_game_studio_projects()
    return JSONResponse({"ok": True, "projects": [_jsonable(p) for p in projects]})


@router.get("/daedalus/game-studio/api/projects/{slug}")
async def game_studio_workspace(slug: str):
    from daedalus.game_studio import get_workspace_context
    try:
        ctx = await get_workspace_context(slug)
        return JSONResponse({
            "ok": True,
            "project": _jsonable(ctx.get("project") or {}),
            "config": ctx.get("config"),
            "gdd": ctx.get("gdd"),
            "gdd_path": ctx.get("gdd_path"),
            "plan": ctx.get("plan"),
            "milestones": ctx.get("milestones"),
            "studio_preset": ctx.get("studio_preset"),
            "build_mode": ctx.get("build_mode"),
            "gates": ctx.get("gates"),
            "can_guided": ctx.get("can_guided", False),
            "can_autopilot": ctx.get("can_autopilot", False),
            "gates_passed": ctx.get("gates_passed", False),
            "milestone_summary": ctx.get("milestone_summary"),
            "current_milestone": ctx.get("current_milestone"),
            "handoff": ctx.get("handoff"),
            "verify": ctx.get("verify"),
            "current_verify": ctx.get("current_verify"),
            "can_advance": ctx.get("can_advance", False),
            "can_unity_waiver": ctx.get("can_unity_waiver", False),
            "project_complete": ctx.get("project_complete", False),
        })
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)


@router.post("/daedalus/game-studio/api/projects/{slug}/gates")
async def game_studio_run_gates(slug: str, request: Request):
    from daedalus.game_studio import run_project_gates
    body = await _body(request)
    gate_ids = body.get("gates")
    if gate_ids is not None and not isinstance(gate_ids, list):
        gate_ids = None
    try:
        result = await run_project_gates(slug, gate_ids=gate_ids)
        return JSONResponse({"ok": True, **result})
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)


@router.post("/daedalus/game-studio/api/projects/{slug}/verify")
async def game_studio_verify(slug: str, request: Request):
    from daedalus.game_studio_verify import run_milestone_verify
    body = await _body(request)
    logs: list[dict[str, str]] = []

    async def _on_log(line: str, level: str = "info") -> None:
        logs.append({"line": line, "level": level})

    try:
        result = await run_milestone_verify(slug, body.get("milestone_id"), on_log=_on_log)
        return JSONResponse({"ok": True, "logs": logs, **(result if isinstance(result, dict) else {"result": result})})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:400], "logs": logs}, status_code=500)


@router.post("/daedalus/game-studio/api/projects/{slug}/milestones/advance")
async def game_studio_advance(slug: str, request: Request):
    from daedalus.game_studio import advance_project_milestone
    body = await _body(request)
    try:
        result = await advance_project_milestone(
            slug,
            force=bool(body.get("force") or body.get("waiver")),
            unity_waiver=bool(body.get("unity_waiver")),
            proof_waiver=bool(body.get("proof_waiver")),
        )
        return JSONResponse({"ok": True, **(result if isinstance(result, dict) else {})})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:400]}, status_code=400)


@router.post("/daedalus/game-studio/api/projects/{slug}/build/guided")
async def game_studio_build_guided(slug: str, request: Request):
    from daedalus.game_studio import start_milestone_guided
    body = await _body(request)
    try:
        result = await start_milestone_guided(
            slug, body.get("milestone_id"), auto_approve=bool(body.get("auto_approve")),
        )
        return JSONResponse({"ok": True, **result})
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/daedalus/playground/api/reference-scout")
async def reference_scout_global(request: Request):
    body = await _body(request)
    reference = (body.get("reference") or "").strip()
    if not reference:
        return JSONResponse({"ok": False, "error": "reference required"}, status_code=400)
    from daedalus.agent import get_daedalus
    from daedalus.reference_scout import scout_reference
    try:
        result = await scout_reference(
            reference,
            project_name=(body.get("name") or "").strip(),
            description=(body.get("description") or "").strip(),
            memory=get_daedalus().memory,
        )
        return JSONResponse(result)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)


@router.post("/daedalus/playground/api/ai-gen-desc")
async def playground_ai_gen_desc(request: Request):
    body = await _body(request)
    name = (body.get("name") or "").strip()
    kind = (body.get("kind") or "static").strip()
    criteria = (body.get("criteria") or "").strip()
    if not name:
        return JSONResponse({"ok": False, "error": "name required"}, status_code=400)
    brief = (
        f"Build {name} as a {kind} project.\n"
        f"{criteria or 'Make it playable and visually complete.'}\n"
        "Use real assets, not CSS-gradient placeholders. Games first."
    )
    try:
        from daedalus.llm_client import complete
        result = await complete(
            system="You write a ready-to-build product brief. Reply in Bulgarian. No markdown fences.",
            user=f"Name: {name}\nKind: {kind}\nCriteria: {criteria}",
            max_tokens=800,
        )
        text = (result.get("reply") or "").strip() if isinstance(result, dict) else ""
        if text:
            brief = text
    except Exception as exc:
        log.debug("ai-gen-desc llm skipped: %s", exc)
    return JSONResponse({"ok": True, "description": brief, "brief": brief})


@router.get("/daedalus/playground/api/learning/stats")
async def learning_stats(kind: str | None = None, coding_mode: str | None = None):
    try:
        from daedalus import learning as dlearn
        return await dlearn.get_stats(kind=kind, coding_mode=coding_mode)
    except Exception as exc:
        return JSONResponse({"ok": True, "stats": {}, "error": str(exc)[:200]})


@router.get("/daedalus/playground/api/learning/advice")
async def learning_advice(kind: str = "static", coding_mode: str = "lite"):
    try:
        from daedalus import learning as dlearn
        return await dlearn.get_advice(kind=kind, coding_mode=coding_mode)
    except Exception as exc:
        return JSONResponse({"ok": True, "advice": [], "error": str(exc)[:200]})


@router.get("/daedalus/playground/api/learning/recent")
async def learning_recent(limit: int = 50):
    try:
        from daedalus import learning as dlearn
        return {"records": await dlearn.list_recent(limit=limit)}
    except Exception:
        return {"records": []}


# ── Skills / proposals / bundles ────────────────────────────────────────────

@router.get("/daedalus/skills/api")
async def skills_list_api():
    from daedalus.self_improve import list_skills
    return JSONResponse({"skills": list_skills()})


@router.post("/daedalus/skills/{skill_name}/toggle")
async def skill_toggle(skill_name: str):
    from daedalus.self_improve import toggle_skill
    return JSONResponse(toggle_skill(skill_name))


@router.get("/daedalus/proposals/api")
async def proposals_list(status: str | None = None):
    from daedalus.self_improve import list_proposals
    return JSONResponse({"proposals": list_proposals(status)})


@router.get("/daedalus/proposals/stats")
async def proposals_stats():
    from daedalus.self_improve import list_proposals
    rows = list_proposals()
    counts = {"pending": 0, "applied": 0, "rejected": 0, "rolled_back": 0, "total": len(rows)}
    for r in rows:
        st = (r.get("status") or "")
        if st in counts:
            counts[st] += 1
    return JSONResponse(counts)


@router.get("/daedalus/proposals/{proposal_id}")
async def proposal_detail(proposal_id: str):
    from daedalus.self_improve import get_proposal
    p = get_proposal(proposal_id)
    if not p:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    return JSONResponse({"ok": True, "proposal": p})


@router.post("/daedalus/proposals/{proposal_id}/approve")
async def proposal_approve(proposal_id: str):
    from daedalus.self_improve import approve_proposal
    return JSONResponse(approve_proposal(proposal_id, approved_by="owner"))


@router.post("/daedalus/proposals/{proposal_id}/reject")
async def proposal_reject(proposal_id: str, request: Request):
    from daedalus.self_improve import reject_proposal
    body = await _body(request)
    return JSONResponse(reject_proposal(proposal_id, rejected_by="owner", note=str(body.get("note") or "")))


@router.post("/daedalus/proposals/{proposal_id}/rollback")
async def proposal_rollback(proposal_id: str):
    from daedalus.self_improve import rollback_proposal
    return JSONResponse(rollback_proposal(proposal_id, rolled_back_by="owner"))


@router.delete("/daedalus/proposals/{proposal_id}")
async def proposal_delete(proposal_id: str):
    from daedalus.self_improve import delete_proposal
    return JSONResponse(delete_proposal(proposal_id))


@router.get("/daedalus/bundles/api")
async def bundles_list(status: str | None = None):
    from daedalus.bundle import list_bundles
    try:
        bundles = await list_bundles(status)
        out = [_jsonable(b) for b in bundles]
        return JSONResponse({"bundles": out, "count": len(out)})
    except Exception as exc:
        return JSONResponse({"bundles": [], "count": 0, "error": str(exc)})


@router.get("/daedalus/bundles/api/{bundle_id}")
async def bundles_get(bundle_id: str):
    from daedalus.bundle import get_bundle
    row = await get_bundle(bundle_id)
    if not row:
        return JSONResponse({"ok": False, "error": "not found", "bundle": None}, status_code=404)
    files = [_jsonable(f) if isinstance(f, dict) else f for f in (row.get("files") or [])]
    bundle = _jsonable({k: v for k, v in row.items() if k != "files"})
    bundle["files"] = files
    return JSONResponse({"ok": True, "bundle": bundle})


# ── Guided (best-effort; jobs live in daedalus.guided_jobs) ─────────────────

@router.get("/daedalus/playground/api/projects/{slug}/guided/active")
async def guided_active(slug: str):
    from daedalus import guided_jobs as gj
    getter = getattr(gj, "get_active", None)
    job = getter(slug) if getter else None
    if not job:
        return JSONResponse({"active": False})
    return JSONResponse({
        "active": getattr(job, "status", "") not in ("done", "error", "interrupted"),
        "job_id": job.id,
        "status": getattr(job, "status", "running"),
        "offset": len(getattr(job, "events", []) or []),
    })


@router.get("/daedalus/playground/api/projects/{slug}/guided/stream")
async def guided_stream(slug: str, request: Request):
    from daedalus import guided_jobs as gj
    job_id = (request.query_params.get("job_id") or "").strip()
    try:
        from_offset = int(request.query_params.get("from", "0"))
    except (TypeError, ValueError):
        from_offset = 0
    job = gj.get_job(job_id) if job_id else gj.get_active(slug)
    if not job or getattr(job, "slug", None) != slug:
        return JSONResponse({"ok": False, "error": "job not found"}, status_code=404)
    return _guided_sse_response(job, from_offset=from_offset)


@router.post("/daedalus/playground/api/projects/{slug}/guided/stop")
async def guided_stop(slug: str):
    from daedalus import guided_jobs as gj
    cancelled = await gj.stop_active(slug)
    return JSONResponse({"ok": True, "cancelled": cancelled, "stopped": cancelled})


@router.post("/daedalus/playground/api/projects/{slug}/guided/start")
async def guided_start(slug: str, request: Request):
    from daedalus import guided_jobs as gj
    from daedalus import playground as pg
    from daedalus.playground_plan_guide import enrich_plan

    body = await _body(request)
    plan = body.get("plan") or {}
    description = (body.get("description") or "").strip()
    if not (plan.get("stages") or []):
        saved = await pg.load_autopilot_plan(slug)
        if saved:
            plan = saved
    stages = plan.get("stages") or []
    if not stages:
        return JSONResponse({"ok": False, "error": "no stages in plan"}, status_code=400)
    proj = await pg.get_project(slug)
    if not proj:
        return JSONResponse({"ok": False, "error": "project not found"}, status_code=404)
    kind = proj.get("kind", "static")
    plan = enrich_plan(plan, kind, description)
    for i, s in enumerate(plan.get("stages") or []):
        s.setdefault("id", i + 1)
        s.setdefault("status", "pending")
    try:
        await pg.save_autopilot_plan(slug, plan)
    except Exception:
        pass
    start_idx = 0
    for i, s in enumerate(plan["stages"]):
        if s.get("status") != "done":
            start_idx = i
            break
    else:
        start_idx = len(plan["stages"])
    job = gj.start_guided(
        slug,
        description or proj.get("description") or "",
        plan,
        stage_index=start_idx,
        ultra=bool(body.get("ultra") or plan.get("ultra")),
        kind=kind,
        studio=bool(body.get("studio") or plan.get("studio")),
        studio_preset=(body.get("studio_preset") or plan.get("studio_preset") or "custom").strip(),
        milestone_scope=(body.get("milestone_scope") or body.get("milestone_id") or "").strip(),
        auto_approve=bool(body.get("auto_approve")),
    )
    return JSONResponse({
        "ok": True,
        "job_id": job.id,
        "snapshot": job.snapshot(),
        "ultra": bool(job.ultra),
        "studio": bool(job.studio),
        "studio_preset": job.studio_preset,
        "milestone_scope": job.milestone_scope,
    })


@router.post("/daedalus/playground/api/projects/{slug}/guided/continue")
async def guided_continue(slug: str, request: Request):
    from daedalus import guided_jobs as gj
    body = await _body(request)
    job_id = (body.get("job_id") or "").strip()
    job = gj.get_job(job_id) if job_id else gj.get_active(slug)
    if not job:
        return JSONResponse({"ok": False, "error": "job not found"}, status_code=404)
    res = await gj.continue_job(job.id, body.get("action", "approve"), notes=(body.get("notes") or "").strip())
    return JSONResponse(res, status_code=200 if res.get("ok") else 400)


@router.post("/daedalus/playground/api/projects/{slug}/plan/expand-ultra")
async def plan_expand_ultra(slug: str, request: Request):
    from daedalus import playground as pg
    from daedalus.game_platform_guide import is_likely_game
    from daedalus.playground_ultra_plan import ultra_expand_plan
    body = await _body(request)
    plan = body.get("plan") or {}
    if not plan.get("stages"):
        return JSONResponse({"ok": False, "error": "no stages in plan"}, status_code=400)
    proj = await pg.get_project(slug)
    if not proj:
        return JSONResponse({"ok": False, "error": "project not found"}, status_code=404)
    description = (body.get("description") or proj.get("description") or "").strip()
    expanded = ultra_expand_plan(
        plan,
        kind=proj.get("kind", "static"),
        is_game=is_likely_game(f"{proj.get('name', '')} {description}"),
    )
    return JSONResponse({"ok": True, "plan": expanded, "ultra": True})


@router.post("/daedalus/playground/api/projects/{slug}/chat")
async def playground_chat(slug: str, request: Request):
    from daedalus.agent import get_daedalus
    from daedalus import playground as pg
    body = await _body(request)
    message = (body.get("message") or "").strip()
    if not message:
        return JSONResponse({"ok": False, "error": "message required"}, status_code=400)
    proj = await pg.get_project(slug)
    if not proj:
        return JSONResponse({"ok": False, "error": "project not found"}, status_code=404)
    try:
        from daedalus.playground_chat_context import build_chat_file_context
        tree_summary, file_ctx = build_chat_file_context(slug)
    except Exception:
        tree_summary, file_ctx = "", ""
    full = (
        f"[PLAYGROUND {slug} · {proj.get('kind')} · standalone]\n"
        f"{tree_summary}\n{file_ctx}\n\n{message}"
    )
    result = await get_daedalus().chat(message=full, session_id=f"pg-{slug}")
    reply = result.get("reply") if isinstance(result, dict) else str(result)
    return JSONResponse({
        "ok": True, "reply": reply, "files": [], "has_files": False,
        "tool_calls": result.get("tool_calls") if isinstance(result, dict) else [],
    })


@router.post("/daedalus/playground/api/projects/{slug}/chat/apply")
async def playground_chat_apply(slug: str, request: Request):
    from daedalus import playground as pg
    body = await _body(request)
    files_list = body.get("files") or []
    if not files_list:
        return JSONResponse({"ok": False, "error": "no files"}, status_code=400)
    if not await pg.get_project(slug):
        return JSONResponse({"ok": False, "error": "project not found"}, status_code=404)
    files_dict = {f["path"]: f["content"] for f in files_list if isinstance(f, dict) and f.get("path")}
    try:
        written = pg.write_files(slug, files_dict)
        try:
            from daedalus import playground_workspace_backup as _wsb
            await _wsb.backup_workspace(slug, trigger="chat_apply")
        except Exception:
            pass
        return JSONResponse({"ok": True, "written": written})
    except Exception as extra:
        return JSONResponse({"ok": False, "error": str(extra)}, status_code=500)


@router.post("/daedalus/playground/api/projects/{slug}/diff-preview")
async def playground_diff_preview(slug: str, request: Request):
    import difflib
    from daedalus import playground as pg
    if not await pg.get_project(slug):
        return JSONResponse({"ok": False, "error": "project not found"}, status_code=404)
    body = await _body(request)
    files_list = body.get("files") or []
    if not files_list:
        return JSONResponse({"ok": False, "error": "no files"}, status_code=400)
    diffs: list[dict[str, Any]] = []
    for item in files_list:
        if not isinstance(item, dict):
            continue
        path = (item.get("path") or "").strip()
        if not path:
            continue
        if "old_content" in item:
            old = item.get("old_content") or ""
        else:
            try:
                old = pg.read_file(slug, path, max_bytes=400_000)
            except Exception:
                old = ""
        new = item.get("content") or ""
        diff_lines = list(difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        ))
        additions = sum(1 for ln in diff_lines if ln.startswith("+") and not ln.startswith("+++"))
        deletions = sum(1 for ln in diff_lines if ln.startswith("-") and not ln.startswith("---"))
        diffs.append({
            "path": path,
            "diff": "\n".join(diff_lines) if diff_lines else f"(no changes — {path})",
            "is_new": not old.strip(),
            "stats": {"additions": additions, "deletions": deletions},
        })
    return JSONResponse({"ok": True, "diffs": diffs})


@router.post("/daedalus/playground/api/projects/{slug}/unity/init")
async def unity_init(slug: str, request: Request):
    from daedalus.playground_unity_init import init_unity_project
    body = await _body(request)
    try:
        result = await init_unity_project(
            slug,
            template_id=(body.get("template_id") or "daedalus_minimal").strip(),
            studio_preset=(body.get("studio_preset") or "custom").strip(),
            name=(body.get("name") or "").strip(),
        )
        return JSONResponse({"ok": True, **(result if isinstance(result, dict) else {})})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:400]}, status_code=400)


@router.post("/daedalus/playground/api/projects/{slug}/unity/attach-template")
async def unity_attach(slug: str, request: Request):
    from daedalus.playground_unity_init import attach_unity_template
    body = await _body(request)
    try:
        result = await attach_unity_template(slug, (body.get("template_id") or "daedalus_minimal").strip())
        return JSONResponse({"ok": True, **(result if isinstance(result, dict) else {})})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:400]}, status_code=400)


@router.post("/daedalus/playground/api/projects/{slug}/unity/generate-build-scripts")
async def unity_build_scripts(slug: str, request: Request):
    from daedalus.playground_unity_build import generate_unity_build_scripts
    body = await _body(request)
    try:
        return JSONResponse(await generate_unity_build_scripts(
            slug, bundle_id=(body.get("bundle_id") or "").strip(), name=(body.get("name") or "").strip(),
        ))
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:400]}, status_code=400)


@router.get("/daedalus/playground/api/unity/templates")
async def unity_templates():
    from daedalus.playground_integration_catalog import list_unity_templates
    return JSONResponse({"ok": True, "templates": list_unity_templates()})


@router.get("/daedalus/playground/api/integrations/catalog")
async def integrations_catalog():
    from daedalus.playground_integration_catalog import list_integrations
    return JSONResponse({"ok": True, "integrations": list_integrations()})


@router.post("/daedalus/playground/api/projects/{slug}/backup-workspace")
async def backup_workspace(slug: str):
    from daedalus.playground_workspace_backup import backup_workspace as _backup
    return JSONResponse(await _backup(slug, trigger="manual"))


@router.post("/daedalus/playground/api/projects/{slug}/firebase/configure")
async def firebase_configure(slug: str, request: Request):
    from daedalus.playground_firebase_config import configure_firebase
    try:
        return JSONResponse({"ok": True, **await configure_firebase(slug, await _body(request))})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:400]}, status_code=400)


@router.post("/daedalus/playground/api/projects/{slug}/hybrid-mobile/init")
async def hybrid_init(slug: str, request: Request):
    from daedalus.playground_hybrid_mobile import init_hybrid_mobile
    body = await _body(request)
    try:
        return JSONResponse({"ok": True, **await init_hybrid_mobile(
            slug,
            bundle_id=(body.get("bundle_id") or "").strip(),
            name=(body.get("name") or "").strip(),
        )})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:400]}, status_code=400)


@router.post("/daedalus/playground/api/projects/{slug}/hybrid-mobile/export")
async def hybrid_export(slug: str):
    from daedalus.playground_hybrid_mobile import export_hybrid_ios
    try:
        return JSONResponse(await export_hybrid_ios(slug))
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:400]}, status_code=400)


@router.post("/daedalus/playground/api/clone")
async def playground_clone(request: Request):
    from daedalus import playground_github as gh
    body = await _body(request)
    url = (body.get("url") or "").strip()
    if not url:
        return JSONResponse({"ok": False, "error": "url required"}, status_code=400)
    try:
        proj = await gh.clone_repo(
            url, name=body.get("name", ""), slug=body.get("slug", ""),
            branch=body.get("branch", ""), ref=body.get("ref", ""),
            source=body.get("source", "cloned"), keep_git=body.get("keep_git"),
            auto_onboard=bool(body.get("auto_onboard")),
        )
        return JSONResponse({"ok": True, "project": _jsonable(proj)})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": gh.github_error_hint(exc)}, status_code=500)


@router.post("/daedalus/playground/api/revive")
async def playground_revive(request: Request):
    from daedalus import playground_github as gh
    from daedalus.playground_revive import onboard_project, compatibility_report
    body = await _body(request)
    url = (body.get("url") or "").strip()
    if not url:
        return JSONResponse({"ok": False, "error": "url required"}, status_code=400)
    try:
        proj = await gh.clone_repo(
            url, name=body.get("name", ""), slug=body.get("slug", ""),
            branch=body.get("branch", ""), ref=body.get("ref", ""),
            source="revived", keep_git=True,
        )
        onboard = await onboard_project(proj["slug"])
        compat = compatibility_report(None, repo_meta={"html_url": url})
        return JSONResponse({"ok": True, "project": _jsonable(proj), "onboard": onboard, "compat": compat})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": gh.github_error_hint(exc)}, status_code=500)


@router.post("/daedalus/playground/api/projects/{slug}/push")
async def playground_push(slug: str, request: Request):
    from daedalus import playground_github as gh
    body = await _body(request)
    try:
        result = await gh.push_project(
            slug,
            repo_full_name=(body.get("repo") or body.get("repo_full_name") or "").strip(),
            create_if_missing=body.get("create_if_missing", True),
            private=body.get("private", True),
            branch=(body.get("branch") or "main").strip(),
            commit_msg=(body.get("commit_msg") or "").strip(),
        )
        return JSONResponse({"ok": True, **(result if isinstance(result, dict) else {})})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": gh.github_error_hint(exc)}, status_code=500)


@router.post("/daedalus/playground/api/projects/{slug}/assets/generate")
async def assets_generate(slug: str, request: Request):
    from daedalus import playground_assets as pa
    body = await _body(request)
    try:
        result = await pa.generate_asset(
            slug, path=body.get("path") or "assets/sprite.png",
            prompt=(body.get("prompt") or "").strip(),
            size=body.get("size") or pa.DEFAULT_SIZE_SPRITE,
            model=body.get("model") or pa.DEFAULT_MODEL,
        )
        return JSONResponse({"ok": True, **result})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:400]}, status_code=500)


@router.post("/daedalus/playground/api/projects/{slug}/assets/auto-pack")
async def assets_auto_pack(slug: str, request: Request):
    from daedalus import playground as pg
    from daedalus import playground_assets as pa
    body = await _body(request)
    proj = await pg.get_project(slug) or {}
    try:
        result = await pa.auto_pack_assets(
            slug,
            name=proj.get("name") or slug,
            kind=proj.get("kind") or "static",
            description=(body.get("description") or body.get("prompt") or proj.get("description") or ""),
        )
        return JSONResponse({"ok": True, **(result if isinstance(result, dict) else {})})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:400]}, status_code=500)


@router.get("/daedalus/playground/api/projects/{slug}/reference")
async def project_reference(slug: str):
    from daedalus.reference_scout import REFERENCE_REL
    from daedalus import playground as pg
    try:
        raw = pg.read_file(slug, REFERENCE_REL)
        return JSONResponse({"ok": True, "reference": json.loads(raw)})
    except Exception:
        return JSONResponse({"ok": True, "reference": None})


@router.post("/daedalus/playground/api/projects/{slug}/reference-scout")
async def project_reference_scout(slug: str, request: Request):
    from daedalus.agent import get_daedalus
    from daedalus.reference_scout import scout_reference
    body = await _body(request)
    reference = (body.get("reference") or "").strip()
    if not reference:
        return JSONResponse({"ok": False, "error": "reference required"}, status_code=400)
    try:
        return JSONResponse(await scout_reference(
            reference, project_name=slug, description=(body.get("description") or "").strip(),
            memory=get_daedalus().memory,
        ))
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:400]}, status_code=400)


@router.post("/daedalus/playground/api/skill-creator/start")
async def skill_creator_start(request: Request):
    from daedalus import playground_skill_creator as sc
    body = await _body(request)
    idea = (body.get("idea") or body.get("description") or "").strip()
    if not idea:
        return JSONResponse({"ok": False, "error": "idea required"}, status_code=400)
    platform = (body.get("platform") or "generic").strip().lower()
    session = sc.init_session(idea=idea, platform=platform, extra_context=(body.get("context") or "").strip())
    plan_data = {"title": idea, "summary": idea, "questions": [], "files": ["SKILL.md"]}
    try:
        from daedalus.llm_client import complete
        raw = await complete(system=sc.build_plan_prompt(idea=idea, platform=platform, research_notes=idea),
                             user="Генерирай JSON плана.", intent="json", max_tokens=1200)
        text = (raw.get("reply") or "") if isinstance(raw, dict) else ""
        parsed = sc.extract_json_object(text) or _extract_json_obj(text)
        if parsed:
            plan_data = sc.normalize_plan_payload(parsed)
    except Exception as exc:
        log.debug("skill-creator llm skipped: %s", exc)
    sc.apply_start_result(session, research_notes=idea, tool_steps=[], plan_data=plan_data)
    return JSONResponse({"ok": True, **sc.session_public_view(session)})


@router.post("/daedalus/playground/api/skill-creator/refine")
async def skill_creator_refine(request: Request):
    from daedalus import playground_skill_creator as sc
    from daedalus.llm_client import complete
    body = await _body(request)
    session_id = str(body.get("session_id") or "").strip()
    message = str(body.get("message") or "").strip()
    if not session_id:
        return JSONResponse({"ok": False, "error": "session_id required"}, status_code=400)
    if not message:
        return JSONResponse({"ok": False, "error": "message required"}, status_code=400)
    session = sc.get_session(session_id)
    if not session:
        return JSONResponse({"ok": False, "error": "session not found or expired"}, status_code=404)
    try:
        raw = await complete(
            system=sc.build_refine_prompt(session, message),
            user=message,
            intent="plan",
            json_mode=True,
            max_tokens=2000,
            client_id=f"skill-refine/{session_id}",
        )
        reply_text = (raw.get("reply") or "") if isinstance(raw, dict) else str(raw)
    except Exception as extra:
        return JSONResponse({"ok": False, "error": str(extra)[:400]}, status_code=500)
    plan_data = sc.extract_json_object(reply_text) or _extract_json_obj(reply_text) or {"reply": reply_text[:4000]}
    session = sc.apply_refine_result(session, user_message=message, plan_data=plan_data)
    return JSONResponse({"ok": True, **sc.session_public_view(session)})


@router.post("/daedalus/playground/api/skill-creator/generate")
async def skill_creator_generate(request: Request):
    from daedalus import playground_skill_creator as sc
    from daedalus.llm_client import complete
    body = await _body(request)
    session_id = str(body.get("session_id") or "").strip()
    force = bool(body.get("force"))
    if not session_id:
        return JSONResponse({"ok": False, "error": "session_id required"}, status_code=400)
    session = sc.get_session(session_id)
    if not session:
        return JSONResponse({"ok": False, "error": "session not found or expired"}, status_code=404)
    if not session.get("ready_for_generation") and not force:
        return JSONResponse({
            "ok": False,
            "error": "Skill не е готов за генериране — отговори на уточняващите въпроси или използвай force=true",
            **sc.session_public_view(session),
        }, status_code=409)
    try:
        raw = await complete(
            system=sc.build_generate_prompt(session),
            user="Generate the skill files now.",
            intent="code",
            max_tokens=8000,
            client_id=f"skill-gen/{session_id}",
        )
        reply_text = (raw.get("reply") or "") if isinstance(raw, dict) else str(raw)
    except Exception as extra:
        return JSONResponse({"ok": False, "error": str(extra)[:400]}, status_code=500)
    session = sc.apply_generate_result(session, raw_reply=reply_text)
    view = sc.session_public_view(session)
    view["files"] = sc.export_bundle(session)
    return JSONResponse({"ok": True, **view})


@router.post("/daedalus/playground/api/skill-creator/github-gist")
async def skill_creator_github_gist(request: Request):
    import httpx
    from daedalus import playground_skill_creator as sc
    from daedalus.connectors_config import github_token
    body = await _body(request)
    session_id = str(body.get("session_id") or "").strip()
    if not session_id:
        return JSONResponse({"ok": False, "error": "session_id required"}, status_code=400)
    session = sc.get_session(session_id)
    if not session or session.get("phase") != "generated":
        return JSONResponse({"ok": False, "error": "generate skill first"}, status_code=400)
    token = github_token()
    if not token:
        return JSONResponse(
            {"ok": False, "error": "GITHUB_TOKEN not configured — /admin/daedalus/connectors"},
            status_code=503,
        )
    bundle = sc.export_bundle(session)
    if not bundle:
        return JSONResponse({"ok": False, "error": "no files to publish"}, status_code=400)
    payload = {
        "description": f"Agent Skill: {session.get('display_title') or session.get('skill_name') or 'skill'}",
        "public": True,
        "files": {path: {"content": content} for path, content in bundle.items()},
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                "https://api.github.com/gists",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "daedalus",
                },
                json=payload,
            )
            if r.status_code >= 400:
                return JSONResponse({"ok": False, "error": r.text[:500]}, status_code=r.status_code)
            data = r.json()
            return JSONResponse({
                "ok": True,
                "gist_url": data.get("html_url"),
                "gist_id": data.get("id"),
            })
    except Exception as extra:
        return JSONResponse({"ok": False, "error": str(extra)[:400]}, status_code=500)


@router.get("/daedalus/playground/api/skill-creator/session/{session_id}")
async def skill_creator_session(session_id: str):
    from daedalus import playground_skill_creator as sc
    session = sc.get_session(session_id)
    if not session:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    return JSONResponse({"ok": True, **sc.session_public_view(session)})


# ── Command Center APIs the copied UI loads on the chat page ────────────────

def _kernel_defaults() -> dict:
    from daedalus.host import standalone_mode
    from daedalus import playground_github as gh
    if standalone_mode():
        return {
            "repo": "Blaue-Ente/Daedalus",
            "paths": ["daedalus", "app", "ui", "README.md", "pyproject.toml", "Dockerfile"],
            "branch": "main",
        }
    return {
        "repo": gh.DEFAULT_KERNEL_REPO,
        "paths": gh.DEFAULT_KERNEL_PATHS,
        "branch": "main",
    }


def _kernel_cfg() -> dict:
    from daedalus import kv
    defaults = _kernel_defaults()
    saved = kv.get("adn:daedalus_kernel_config") or {}
    if not isinstance(saved, dict):
        saved = {}
    return {
        "repo": saved.get("repo") or defaults["repo"],
        "paths": saved.get("paths") or defaults["paths"],
        "branch": saved.get("branch") or defaults["branch"],
    }


@router.post("/daedalus/llm-config")
async def set_llm_config(request: Request):
    from daedalus.llm_config import set_config
    from daedalus.llm_router_config import router_write_context
    body = await _body(request)
    with router_write_context():
        cfg = set_config(
            model=str(body.get("model") or ""),
            api_key=str(body.get("api_key") or ""),
            provider=str(body.get("provider") or "openai"),
            base_url=str(body.get("base_url") or ""),
            use_cache=bool(body.get("use_cache", True)),
        )
    safe = {k: v for k, v in cfg.items() if k != "api_key"}
    safe["api_key_set"] = bool(cfg.get("api_key"))
    return JSONResponse({"ok": True, **safe})


@router.get("/daedalus/llm-presets")
async def list_llm_presets():
    from daedalus.llm_presets import list_presets
    return JSONResponse({"ok": True, "presets": list_presets()})


@router.post("/daedalus/llm-presets/{preset_id}/apply")
async def apply_llm_preset(preset_id: str, request: Request):
    from daedalus.llm_presets import apply_preset
    from daedalus.llm_router_config import router_write_context
    body = await _body(request)
    try:
        with router_write_context():
            result = apply_preset(preset_id, master_api_key=str(body.get("api_key") or ""))
        return JSONResponse(result)
    except ValueError as extra:
        return JSONResponse({"ok": False, "error": str(extra)}, status_code=400)


@router.get("/daedalus/worker-config")
async def get_worker_config_api():
    from daedalus.workers import get_worker_config as _gwc, WORKER_SLOTS
    from daedalus.llm_config import list_preset_models
    cfg = _gwc()
    safe: dict = {}
    for slot, slot_cfg in cfg.items():
        safe[slot] = {k: v for k, v in slot_cfg.items() if k != "api_key"}
        safe[slot]["api_key_set"] = bool(slot_cfg.get("api_key"))
        safe[slot]["label"] = WORKER_SLOTS.get(slot, {}).get("label", slot)
    return JSONResponse({"ok": True, "slots": safe, "presets": list_preset_models()})


@router.post("/daedalus/test-provider")
async def test_provider(request: Request):
    import time as _t
    from daedalus.llm_config import PROVIDER_DEFAULTS, PROVIDER_ENV_KEYS
    body = await _body(request)
    provider = str(body.get("provider") or "alltoken").strip()
    model = str(body.get("model") or "").strip()
    api_key = str(body.get("api_key") or "").strip()
    base_url = str(body.get("base_url") or "").strip()
    pd = PROVIDER_DEFAULTS.get(provider, {})
    if not model:
        model = str(pd.get("default_model") or "")
    if not base_url:
        base_url = str(pd.get("base_url") or "")
    if not api_key:
        env_name = PROVIDER_ENV_KEYS.get(provider, "")
        if env_name:
            import os
            api_key = (os.environ.get(env_name) or "").strip()
    if not api_key or not model:
        return JSONResponse({
            "ok": False,
            "error": "API key or model missing",
            "provider": provider,
            "model": model,
        })
    t0 = _t.perf_counter()
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key, base_url=base_url or None)
        resp = await client.chat.completions.create(
            model=model, max_tokens=64,
            messages=[{"role": "user", "content": "Reply with exactly: OK"}],
        )
        reply = (resp.choices[0].message.content or "").strip()
        return JSONResponse({
            "ok": True, "provider": provider, "model": model,
            "reply": reply[:200], "latency_ms": round((_t.perf_counter() - t0) * 1000),
        })
    except Exception as extra:
        return JSONResponse({
            "ok": False, "provider": provider, "model": model,
            "error": str(extra)[:400],
            "latency_ms": round((_t.perf_counter() - t0) * 1000),
        })


@router.get("/daedalus/kernel/config")
async def kernel_config_get():
    return JSONResponse(_kernel_cfg())


@router.post("/daedalus/kernel/config")
async def kernel_config_set(request: Request):
    from daedalus import kv
    body = await _body(request)
    defaults = _kernel_defaults()
    cfg = {
        "repo": str(body.get("repo") or defaults["repo"]).strip(),
        "paths": body.get("paths") or defaults["paths"],
        "branch": str(body.get("branch") or "main").strip(),
    }
    kv.set("adn:daedalus_kernel_config", cfg)
    return JSONResponse({"ok": True, "config": cfg})


@router.post("/daedalus/kernel/push")
async def kernel_push(request: Request):
    from daedalus import playground_github as gh
    body = await _body(request)
    cfg = _kernel_cfg()
    try:
        result = await gh.push_kernel(
            commit_msg=str(body.get("commit_msg") or "").strip(),
            repo_full_name=str(body.get("repo") or cfg["repo"]).strip(),
            paths=body.get("paths") or cfg["paths"],
            branch=str(body.get("branch") or cfg["branch"]).strip(),
        )
        return JSONResponse(result if isinstance(result, dict) else {"ok": True, "result": result})
    except Exception as extra:
        return JSONResponse({"ok": False, "error": str(extra)[:400]}, status_code=400)


@router.get("/daedalus/settings/auto-apply")
async def auto_apply_get():
    from daedalus.auto_apply import is_auto_apply_enabled
    return JSONResponse({"enabled": is_auto_apply_enabled()})


@router.post("/daedalus/settings/auto-apply")
async def auto_apply_toggle():
    from daedalus.auto_apply import is_auto_apply_enabled, set_auto_apply_enabled
    new_state = not is_auto_apply_enabled()
    set_auto_apply_enabled(new_state)
    return JSONResponse({"ok": True, "enabled": new_state})


@router.get("/daedalus/blockers")
async def blockers_list():
    from daedalus.blockers import blockers_summary, list_blockers
    blockers = list_blockers(enrich=True)
    return JSONResponse({"blockers": blockers, "summary": blockers_summary(blockers)})


@router.post("/daedalus/blockers/{blocker_id}/resolve")
async def blocker_resolve(blocker_id: str, request: Request):
    from daedalus.blockers import resolve_blocker
    body = await _body(request)
    return JSONResponse(resolve_blocker(blocker_id, note=str(body.get("note") or "")))


@router.post("/daedalus/blockers/{blocker_id}/auto-fix")
async def blocker_auto_fix(blocker_id: str, request: Request):
    from daedalus.blockers import auto_fix_blocker
    body = await _body(request)
    return JSONResponse(auto_fix_blocker(blocker_id, hours=float(body.get("hours") or 8)))


@router.post("/daedalus/blockers/auto-fix-all")
async def blockers_auto_fix_all(request: Request):
    from daedalus.blockers import auto_fix_all_open
    body = await _body(request)
    return JSONResponse(auto_fix_all_open(hours=float(body.get("hours") or 8)))


@router.get("/daedalus/memory/stats")
async def daedalus_memory_stats():
    try:
        from daedalus.agent import get_daedalus
        d = get_daedalus()
        base = d.memory
        mem0 = getattr(d, "mem0", None)
        return JSONResponse({
            "global": base.global_col.count(),
            "sessions": base.sessions_col.count(),
            "artifacts": base.artifacts_col.count(),
            "lessons": base.lessons_col.count(),
            "episodes": base.episodes_col.count(),
            "discoveries": base.discoveries_col.count(),
            "episodic_facts": mem0.episodic.count() if mem0 else 0,
            "mem0": mem0.stats() if mem0 else {},
        })
    except Exception as exc:
        log.warning("memory stats unavailable: %s", exc)
        return JSONResponse({
            "global": 0, "sessions": 0, "artifacts": 0, "lessons": 0,
            "episodes": 0, "discoveries": 0, "episodic_facts": 0,
            "mem0": {}, "error": str(exc),
        })


@router.get("/daedalus/memory/episodic")
async def daedalus_memory_episodic(limit: int = 50):
    import time as _t
    try:
        from daedalus.agent import get_daedalus
        d = get_daedalus()
        mem0 = getattr(d, "mem0", None)
        if not mem0:
            return JSONResponse({"facts": [], "total": 0})
        facts = mem0.episodic.all_facts(limit=min(limit, 500))
    except Exception as exc:
        log.warning("episodic memory unavailable: %s", exc)
        return JSONResponse({"facts": [], "total": 0, "error": str(exc)})
    now = _t.time()
    out = []
    for f in facts:
        meta = f.get("metadata") or {}
        ts = float(meta.get("ts", now))
        out.append({
            "id": f.get("id"),
            "content": f.get("content"),
            "kind": meta.get("kind", "general"),
            "role": meta.get("role", "?"),
            "session_id": meta.get("session_id", ""),
            "access_count": int(meta.get("access_count", 0)),
            "importance": float(meta.get("importance", 1.0)),
            "promoted": bool(meta.get("promoted_to_lesson", False)),
            "age_hours": round((now - ts) / 3600.0, 1),
            "ts": ts,
        })
    return JSONResponse({"facts": out, "total": len(out)})


@router.delete("/daedalus/memory/episodic/{fact_id}")
async def daedalus_memory_delete_fact(fact_id: str):
    from daedalus.agent import get_daedalus
    d = get_daedalus()
    deleted = d.mem0.episodic.delete([fact_id])
    d.mem0.rebuild_bm25_indices()
    return JSONResponse({"deleted": deleted, "fact_id": fact_id})


@router.post("/daedalus/memory/consolidate")
async def daedalus_memory_consolidate():
    from daedalus.reflection import run_consolidation_cycle
    from daedalus.agent import get_daedalus, _call_claude
    try:
        result = await run_consolidation_cycle(_call_claude, get_daedalus())
        return JSONResponse({"ok": True, **result})
    except Exception as extra:
        return JSONResponse({"ok": False, "error": str(extra)}, status_code=500)


@router.get("/daedalus/memory/consolidations")
async def daedalus_memory_consolidations(limit: int = 20):
    from daedalus.reflection import read_consolidation_log
    return JSONResponse({"runs": read_consolidation_log(limit=limit)})


@router.get("/daedalus/tasks")
async def list_daedalus_tasks(status: str | None = None, limit: int = 50):
    from daedalus import continuity
    return JSONResponse({"tasks": continuity.list_tasks(status=status, limit=limit)})


@router.post("/daedalus/tasks")
async def create_daedalus_task(request: Request):
    from daedalus import continuity
    body = await _body(request)
    title = str(body.get("title") or "").strip()
    if not title:
        return JSONResponse({"ok": False, "error": "title required"}, status_code=400)
    return JSONResponse(continuity.create_task(title=title, summary=str(body.get("summary") or "")))


@router.api_route("/daedalus/tasks/{task_id}", methods=["PATCH", "POST"])
async def update_daedalus_task(task_id: str, request: Request):
    from daedalus import continuity
    body = await _body(request)
    allowed = {k: v for k, v in body.items() if k in ("status", "summary")}
    t = continuity.update_task(task_id, **allowed)
    if not t:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    return JSONResponse(t)


@router.get("/daedalus/discoveries")
async def list_discoveries(status: str | None = None, limit: int = 100):
    try:
        from daedalus.agent import get_daedalus
        return JSONResponse(get_daedalus().list_discoveries(status=status, limit=limit))
    except Exception as exc:
        log.warning("discoveries unavailable: %s", exc)
        return JSONResponse({"discoveries": [], "count": 0, "error": str(exc)})


@router.get("/daedalus/plans")
async def daedalus_list_plans(discovery_id: str | None = None):
    from daedalus.plans import list_plans as _list_plans
    return JSONResponse({"plans": _list_plans(discovery_id=discovery_id)})


@router.get("/daedalus/plans/{plan_id}")
async def daedalus_get_plan(plan_id: str):
    from daedalus.plans import get_plan as _get_plan
    plan = _get_plan(plan_id)
    if not plan:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    return JSONResponse(plan)


@router.get("/daedalus/preset-library")
async def get_preset_library():
    from daedalus import preset_library as pl
    from daedalus import model_registry as reg
    return JSONResponse({
        "ok": True,
        "presets": pl.library(),
        "active_id": pl.active_preset_id(),
        "active_mix": reg.get_active_mix(),
        "auto_mode": reg.get_auto_mode(),
    })


# ── Planner (SQLite-backed; same URLs as ADN admin.py) ──────────────────────

GOAL_MAX_LEN = 2000


def _step_tag(step_id: str) -> str:
    return f"[plan_step:{step_id}]"


def _plan_tag(plan_id: str) -> str:
    return f"[plan:{plan_id}]"


def _find_task_for_step(step_id: str):
    from daedalus import continuity
    tag = _step_tag(step_id)
    for t in continuity.list_tasks(limit=200):
        if tag in (t.get("summary") or ""):
            return t
    return None


@router.get("/daedalus/planner/plans")
async def daedalus_planner_list():
    from daedalus.planner import list_plans
    return JSONResponse({"plans": await list_plans()})


@router.post("/daedalus/planner/plans")
async def daedalus_planner_create(request: Request):
    body = await _body(request)
    title = str(body.get("title", "")).strip()
    description = str(body.get("description", "")).strip()
    if not title:
        return JSONResponse({"error": "title required"}, status_code=400)
    from daedalus.planner import create_plan
    return JSONResponse(await create_plan(title, description))


@router.post("/daedalus/planner/plans/auto")
async def daedalus_planner_auto(request: Request):
    body = await _body(request)
    goal = str(body.get("goal", "")).strip()
    if not goal:
        return JSONResponse({"error": "goal required"}, status_code=400)
    if len(goal) > GOAL_MAX_LEN:
        return JSONResponse(
            {"error": f"goal too long ({len(goal)} chars, max {GOAL_MAX_LEN})"},
            status_code=400,
        )
    from daedalus.planner import auto_plan_from_goal
    try:
        result = await auto_plan_from_goal(goal)
    except Exception as extra:
        return JSONResponse({"ok": False, "error": f"auto-plan failed: {extra}"}, status_code=500)
    if not result.get("ok"):
        return JSONResponse(result, status_code=422)
    result.setdefault("plan", {
        "id": result.get("plan_id"),
        "title": result.get("title"),
        "description": result.get("description"),
        "status": result.get("status"),
        "steps": result.get("steps") or [],
    })
    return JSONResponse(result)


@router.post("/daedalus/planner/plans/{plan_id}/approve")
async def daedalus_planner_approve(plan_id: str):
    from daedalus.planner import get_plan, update_plan_status
    plan = await get_plan(plan_id)
    if not plan:
        return JSONResponse({"error": "plan not found"}, status_code=404)
    current = plan.get("status")
    if current != "draft":
        return JSONResponse(
            {"error": f"invalid transition: plan status is '{current}', expected 'draft'"},
            status_code=409,
        )
    ok = await update_plan_status(plan_id, "active")
    from daedalus import continuity
    tasks_created = []
    for step in (plan.get("steps") or []):
        step_id = step.get("id", "")
        if not step_id or _find_task_for_step(step_id):
            continue
        task = continuity.create_task(
            title=str(step.get("title") or "Untitled")[:80],
            summary=(
                f"{_plan_tag(plan_id)} {_step_tag(step_id)}\n"
                + (step.get("description") or "")[:300]
            ),
            session_id=plan_id,
        )
        tasks_created.append(task.get("id"))
    return JSONResponse({
        "ok": ok, "plan_id": plan_id, "status": "active", "tasks_created": tasks_created,
    })


@router.get("/daedalus/planner/plans/{plan_id}")
async def daedalus_planner_get(plan_id: str):
    from daedalus.planner import get_plan
    plan = await get_plan(plan_id)
    if not plan:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(_jsonable(plan) if isinstance(plan, dict) else plan)


@router.get("/daedalus/planner/plans/{plan_id}/progress")
async def daedalus_planner_progress(plan_id: str):
    from daedalus.planner import CHECKPOINT_KEYS, get_plan
    plan = await get_plan(plan_id)
    if not plan:
        return JSONResponse({"error": "not found"}, status_code=404)
    steps_out = []
    for s in plan.get("steps", []):
        cps = s.get("checkpoints") or {}
        if isinstance(cps, str):
            try:
                cps = json.loads(cps)
            except Exception:
                cps = {}
        passed = [k for k in CHECKPOINT_KEYS if (cps.get(k) or {}).get("passed")]
        steps_out.append({
            "id": s["id"],
            "step_order": s["step_order"],
            "title": s["title"],
            "status": s["status"],
            "proposal_id": s.get("proposal_id"),
            "progress_note": s.get("progress_note"),
            "diagnostics": s.get("diagnostics"),
            "repair_attempts": s.get("repair_attempts") or 0,
            "manual_override": bool(s.get("manual_override")),
            "checkpoints": {
                "passed": passed,
                "passed_count": len(passed),
                "total": len(CHECKPOINT_KEYS),
                "all": CHECKPOINT_KEYS,
            },
            "error_msg": s.get("error_msg"),
        })
    return JSONResponse({
        "plan_id": plan_id,
        "plan_status": plan.get("status"),
        "title": plan.get("title"),
        "steps": steps_out,
    })


@router.post("/daedalus/planner/steps/{step_id}/override")
async def daedalus_planner_override(step_id: str):
    from daedalus.planner import manual_override_step
    return JSONResponse(await manual_override_step(step_id))


@router.post("/daedalus/planner/plans/{plan_id}/steps")
async def daedalus_planner_add_step(plan_id: str, request: Request):
    body = await _body(request)
    title = str(body.get("title", "")).strip()
    description = str(body.get("description", "")).strip()
    if not title:
        return JSONResponse({"error": "title required"}, status_code=400)
    depends_on_raw = body.get("depends_on") or []
    if isinstance(depends_on_raw, str):
        depends_on_list = [s.strip() for s in depends_on_raw.split(",") if s.strip()]
    elif isinstance(depends_on_raw, list):
        depends_on_list = [str(s).strip() for s in depends_on_raw if str(s).strip()]
    else:
        depends_on_list = []
    max_retries = int(body.get("max_retries", 3))
    from daedalus.planner import add_step, get_plan
    if not await get_plan(plan_id):
        return JSONResponse({"error": "plan not found"}, status_code=404)
    return JSONResponse(await add_step(
        plan_id, title, description,
        depends_on=depends_on_list or None,
        max_retries=max_retries,
    ))


@router.post("/daedalus/planner/plans/{plan_id}/pause")
async def daedalus_planner_pause(plan_id: str):
    from daedalus.planner import get_plan, update_plan_status
    if not await get_plan(plan_id):
        return JSONResponse({"error": "plan not found"}, status_code=404)
    ok = await update_plan_status(plan_id, "paused")
    return JSONResponse({"ok": ok})


@router.post("/daedalus/planner/plans/{plan_id}/resume")
async def daedalus_planner_resume(plan_id: str):
    from daedalus.planner import get_plan, update_plan_status
    if not await get_plan(plan_id):
        return JSONResponse({"error": "plan not found"}, status_code=404)
    ok = await update_plan_status(plan_id, "active")
    return JSONResponse({"ok": ok})


@router.delete("/daedalus/planner/plans/{plan_id}")
async def daedalus_planner_delete(plan_id: str):
    from daedalus.planner import delete_plan
    ok = await delete_plan(plan_id)
    if not ok:
        return JSONResponse({"error": "plan not found"}, status_code=404)
    return JSONResponse({"ok": ok})


@router.post("/daedalus/planner/plans/{plan_id}/steps/{step_id}/mark")
async def daedalus_planner_step_mark(plan_id: str, step_id: str, request: Request):
    body = await _body(request)
    status = str(body.get("status", "done"))
    from daedalus.planner import mark_step
    await mark_step(step_id, status, error_msg=body.get("error_msg"))
    return JSONResponse({"ok": True, "plan_id": plan_id, "step_id": step_id})


# ── Radar ───────────────────────────────────────────────────────────────────

@router.get("/daedalus/radar/sources")
async def daedalus_radar_sources():
    from daedalus.radar import RADAR_SOURCES
    return JSONResponse({"sources": RADAR_SOURCES})


@router.get("/daedalus/radar/state")
async def daedalus_radar_state():
    from daedalus.radar import get_radar_state
    return JSONResponse(get_radar_state())


@router.post("/daedalus/radar/scan")
async def daedalus_radar_scan():
    from daedalus.agent import get_daedalus
    from daedalus.radar import run_radar_scan
    try:
        return JSONResponse(await run_radar_scan(get_daedalus()))
    except Exception as extra:
        return JSONResponse({"ok": False, "error": str(extra)}, status_code=500)


# ── Background run (Command Center "run in background") ─────────────────────

@router.post("/daedalus/run-background")
async def daedalus_run_background(request: Request):
    import asyncio
    import uuid

    body = await _body(request)
    task_msg = str(body.get("task", "")).strip()
    if not task_msg:
        return JSONResponse({"ok": False, "error": "task required"}, status_code=400)
    task_id = body.get("task_id")
    session_id = str(
        body.get("session_id")
        or (f"task-{str(task_id)[:8]}" if task_id else f"bg-{uuid.uuid4().hex[:8]}")
    )
    grant_perms: list = body.get("grant_perms") or []

    from daedalus.agent import get_daedalus
    agent = get_daedalus()
    if grant_perms:
        from daedalus.outbox import grant_permission
        for perm in grant_perms:
            grant_permission(perm_class=str(perm), hours=8.0, granted_by="bg_task_auto")

    async def _run():
        import time as _time
        import traceback
        started = _time.time()
        try:
            result = await agent.chat(task_msg, session_id=session_id)
            elapsed = round(_time.time() - started, 1)
            reply = result.get("reply", "") if isinstance(result, dict) else str(result)
            ctx = result.get("context", {}) if isinstance(result, dict) else {}
            tc = ctx.get("tool_calls") or []
            tools_summary = ", ".join(set(c.get("tool", "?") for c in tc)) if tc else "—"
            from daedalus.outbox import push_outbox
            push_outbox(
                type="message",
                title=f"✅ Commit: {task_msg[:55]}",
                body=(
                    f"**Session:** {session_id}\n"
                    f"**Elapsed:** {elapsed}s  |  **Tools:** {tools_summary}\n\n"
                    + reply[:1800]
                ),
                category="bg_commit",
                session_id=session_id,
                payload={"task": task_msg, "elapsed": elapsed, "tools": tools_summary,
                         "task_id": task_id},
            )
            if task_id:
                try:
                    from daedalus import continuity
                    continuity.update_task(task_id, status="done",
                                           summary=f"[bg] {reply[:300]}")
                except Exception:
                    pass
        except Exception as extra:
            log.exception("background task failed")
            from daedalus.outbox import push_outbox
            push_outbox(
                type="message",
                title=f"🔴 Error: {task_msg[:50]}",
                body=(
                    f"**Session:** {session_id}\n\n"
                    f"**Грешка:**\n```\n{traceback.format_exc()[-800:]}\n```"
                ),
                category="bg_error",
                session_id=session_id,
                payload={"task": task_msg, "error": str(extra), "task_id": task_id},
            )
            if task_id:
                try:
                    from daedalus import continuity
                    continuity.update_task(task_id, status="rework",
                                           summary=f"[bg error] {extra}")
                except Exception:
                    pass

    asyncio.create_task(_run())
    return JSONResponse({
        "ok": True,
        "session_id": session_id,
        "granted_perms": grant_perms,
        "message": "Задачата е стартирана; резултатът ще отиде в outbox.",
    })


@router.post("/daedalus/discoveries/{discovery_id}/approve")
async def daedalus_approve_discovery(discovery_id: str):
    from daedalus.agent import get_daedalus
    return JSONResponse(get_daedalus().approve_discovery(discovery_id))


@router.post("/daedalus/discoveries/{discovery_id}/reject")
async def daedalus_reject_discovery(discovery_id: str):
    from daedalus.agent import get_daedalus
    return JSONResponse(get_daedalus().reject_discovery(discovery_id))


@router.delete("/daedalus/discoveries/{discovery_id}")
async def daedalus_delete_discovery(discovery_id: str):
    from daedalus.agent import get_daedalus
    ok = get_daedalus().memory.delete_discovery(discovery_id)
    return JSONResponse({"ok": ok, "discovery_id": discovery_id})


@router.post("/daedalus/discoveries/{discovery_id}/regenerate-plan")
async def daedalus_regenerate_plan(discovery_id: str):
    from daedalus.agent import get_daedalus
    from daedalus.plans import generate_plan
    daedalus = get_daedalus()
    res = daedalus.memory.discoveries_col.get(ids=[discovery_id])
    if not res.get("ids"):
        return JSONResponse({"error": "discovery not found"}, status_code=404)
    discovery = {
        "id": discovery_id,
        "content": (res.get("documents") or [""])[0],
        "metadata": (res.get("metadatas") or [{}])[0] or {},
    }
    registry: dict = {}
    try:
        from argos_data_nexus import mesh as mesh_mod
        registry = mesh_mod._load_registry()
    except Exception:
        pass
    try:
        plan = await generate_plan(discovery, registry, daedalus)
        return JSONResponse({"ok": True, "plan_id": plan["id"]})
    except Exception as extra:
        return JSONResponse({"ok": False, "error": str(extra)[:400]}, status_code=500)


@router.get("/daedalus/journal/list")
async def daedalus_journal_list():
    from daedalus import scratch_journal as sj
    return JSONResponse({
        "ok": True,
        "budget": sj.TOKEN_BUDGET_PER_SESSION,
        "threshold_pct": int(sj.PAUSE_THRESHOLD * 100),
        "journals": sj.list_active_journals(),
    })


@router.post("/daedalus/journal/{session_id}/pause")
async def daedalus_journal_pause(session_id: str):
    from daedalus import scratch_journal as sj
    sj.request_pause(session_id, reason="owner_requested")
    return JSONResponse({"ok": True})


@router.post("/daedalus/journal/{session_id}/clear")
async def daedalus_journal_clear(session_id: str):
    from daedalus import scratch_journal as sj
    sj.clear_journal(session_id)
    return JSONResponse({"ok": True})


@router.post("/daedalus/upload-doc")
async def daedalus_upload_doc(request: Request):
    form = await request.form()
    upload = form.get("file")
    if not upload:
        return JSONResponse({"error": "file field missing"}, status_code=400)
    filename = str(getattr(upload, "filename", None) or "document")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    raw = await upload.read()  # type: ignore[union-attr]
    text = ""
    try:
        if isinstance(raw, bytes):
            text = raw.decode("utf-8", errors="replace")
        else:
            text = str(raw)
    except Exception as extra:
        return JSONResponse({"error": str(extra)}, status_code=422)
    max_chars = 60_000
    text = text[:max_chars]
    if len(text) == max_chars:
        text += f"\n\n[… текстът е съкратен до {max_chars:,} символа]"
    return JSONResponse({
        "ok": True, "filename": filename, "ext": ext, "chars": len(text), "text": text,
    })


@router.post("/daedalus/plans/{plan_id}/status")
async def daedalus_set_plan_status(plan_id: str, request: Request):
    from daedalus.plans import set_plan_status
    body = await _body(request)
    status = str(body.get("status") or "")
    ok = set_plan_status(plan_id, status)
    return JSONResponse({"ok": ok, "plan_id": plan_id, "status": status})


@router.post("/daedalus/proposals/approve-all-pending")
async def daedalus_approve_all_pending():
    from daedalus.self_improve import approve_proposal, list_proposals
    applied: list[str] = []
    failed: list[dict[str, Any]] = []
    for p in list_proposals(status="pending"):
        pid = p.get("proposal_id") or p.get("id")
        if not pid:
            continue
        try:
            res = approve_proposal(str(pid), approved_by="owner")
            if res.get("ok"):
                applied.append(str(pid))
            else:
                failed.append({"id": pid, "error": res.get("error")})
        except Exception as extra:
            failed.append({"id": pid, "error": str(extra)})
    return JSONResponse({"ok": True, "applied": len(applied), "failed": failed})


@router.post("/daedalus/blockers/{blocker_id}/remediate")
async def blocker_remediate(blocker_id: str):
    from daedalus.blockers import remediate_infra_blocker
    return JSONResponse(await remediate_infra_blocker(blocker_id))


@router.post("/daedalus/blockers/grant-recommended")
async def blockers_grant_recommended(request: Request):
    from daedalus.blockers import blockers_summary
    from daedalus.outbox import grant_permissions_bulk
    body = await _body(request)
    hours = float(body.get("hours") or 8)
    perms = blockers_summary().get("grant_all_recommended") or []
    grant_permissions_bulk(perms, hours=hours, granted_by="blocker_recommended")
    return JSONResponse({"ok": True, "granted": perms, "hours": hours})


@router.post("/daedalus/bundles/{bundle_id}/apply")
async def bundle_apply(bundle_id: str):
    from daedalus.bundle import apply_bundle
    try:
        return JSONResponse(await apply_bundle(bundle_id))
    except Exception as extra:
        return JSONResponse({"ok": False, "error": str(extra)}, status_code=500)


@router.post("/daedalus/bundles/{bundle_id}/reject")
async def bundle_reject(bundle_id: str):
    from daedalus.bundle import reject_bundle
    try:
        return JSONResponse(await reject_bundle(bundle_id))
    except Exception as extra:
        return JSONResponse({"ok": False, "error": str(extra)}, status_code=500)


@router.post("/daedalus/upgrade-pool-scan")
async def daedalus_upgrade_pool_scan():
    import asyncio
    import uuid
    from daedalus.upgrade_pool_scanner import get_adn_candidates, run_upgrade_pool_scan
    candidates = get_adn_candidates()
    if not candidates:
        return JSONResponse({"ok": True, "candidates": 0, "message": "Няма нови кандидати в Upgrade Pool."})
    from daedalus.agent import get_daedalus
    agent = get_daedalus()
    session_id = f"bg-upgrade-{uuid.uuid4().hex[:8]}"

    async def _scan():
        await run_upgrade_pool_scan(agent, session_id=session_id)

    asyncio.create_task(_scan())
    return JSONResponse({
        "ok": True,
        "candidates": len(candidates),
        "session_id": session_id,
        "message": f"Стартиран scan на {len(candidates)} кандидата. Резултатът ще дойде в Outbox.",
    })


@router.get("/daedalus/clarification-stats")
async def daedalus_clarification_stats():
    from daedalus.clarification_tracker import get_clarification_stats
    stats = await get_clarification_stats()
    return JSONResponse({"ok": True, "stats": stats})


@router.post("/daedalus/generate-style-lesson")
async def daedalus_generate_style_lesson():
    import asyncio
    from daedalus.agent import get_daedalus
    agent = get_daedalus()

    async def _lesson():
        from daedalus.clarification_tracker import generate_daily_lesson
        await generate_daily_lesson(agent)

    asyncio.create_task(_lesson())
    return JSONResponse({"ok": True, "message": "Генериране на стил урок стартирано."})


@router.get("/daedalus/rate_limit_stats")
async def daedalus_rate_limit_stats():
    from daedalus.improvements import get_rate_limit_stats
    return JSONResponse(get_rate_limit_stats())


# ── Catch-all last so copied JS never 404s ──────────────────────────────────

@router.api_route("/daedalus/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def standalone_stub(rest: str, request: Request):
    return JSONResponse({
        "ok": False,
        "standalone": True,
        "wired": False,
        "configured": False,
        "path": f"/admin/daedalus/{rest}",
        "method": request.method,
        "projects": [],
        "items": [],
        "bundles": [],
        "proposals": [],
        "records": [],
        "sessions": [],
        "skills": [],
        "repos": [],
        "message": "This ADN-hosted route is not fully wired in standalone Daedalus yet.",
    })
