"""Public builder API for Daedalus Workshop (and mesh clients).

Auth: X-Iyveris-Mesh-Key (preferred) or X-Argos-Key.
"""
from __future__ import annotations

import json
import os

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .host import keys as _adn_keys
from . import autopilot_jobs as aj
from . import builder_runtime as br

router = APIRouter(prefix="/api/v1/daedalus/builder", tags=["daedalus-builder"])


async def require_builder_auth(
    x_iyveris_mesh_key: str | None = Header(default=None, alias="X-Iyveris-Mesh-Key"),
    x_argos_key: str | None = Header(default=None, alias="X-Argos-Key"),
) -> str:
    mesh_expected = os.getenv("IYVERIS_MESH_KEY", "")
    if mesh_expected and x_iyveris_mesh_key == mesh_expected:
        return "mesh"
    if x_argos_key and _adn_keys().verify(x_argos_key) is not None:
        return "api"
    from .host import standalone_mode
    if standalone_mode():
        return "owner"
    if not mesh_expected:
        return "dev"
    raise HTTPException(status_code=401, detail="Missing or invalid builder auth.")


@router.get("/projects")
async def list_projects(
    _: str = Depends(require_builder_auth),
    limit: int = Query(50, ge=1, le=200),
):
    rows = await br.list_builder_projects(limit=limit)
    return {"ok": True, "projects": rows}


@router.post("/projects")
async def create_project(request: Request, _: str = Depends(require_builder_auth)):
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        return JSONResponse({"ok": False, "error": "name required"}, status_code=400)
    kind = (body.get("kind") or "vite-react").strip()
    description = (body.get("description") or "").strip()
    result = await br.create_builder_project(name, kind=kind, description=description)
    return result


@router.post("/projects/{slug}/run")
async def run_project(slug: str, request: Request, _: str = Depends(require_builder_auth)):
    body = await request.json()
    description = (body.get("description") or body.get("prompt") or "").strip()
    mode = (body.get("mode") or "full").strip().lower()
    resume = bool(body.get("resume", False))
    if not description:
        return JSONResponse({"ok": False, "error": "description required"}, status_code=400)
    try:
        job = await br.start_builder_run_async(
            slug, description, mode=mode, resume=resume,
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
    return {
        "ok": True,
        "job_id": job.id,
        "slug": slug,
        "status": job.status,
        "mode": mode,
    }


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, _: str = Depends(require_builder_auth)):
    snap = br.job_snapshot(job_id)
    if not snap:
        return JSONResponse({"ok": False, "error": "job not found"}, status_code=404)
    return {"ok": True, "job": snap}


@router.get("/projects/{slug}/jobs/active")
async def active_job(slug: str, _: str = Depends(require_builder_auth)):
    job = br.get_active_job(slug)
    if not job:
        return {"ok": True, "job": None}
    return {"ok": True, "job": job.snapshot()}


@router.get("/jobs/{job_id}/stream")
async def stream_job(
    job_id: str,
    _: str = Depends(require_builder_auth),
    from_offset: int = Query(0, alias="from", ge=0),
):
    job = aj.get(job_id)
    if not job:
        return JSONResponse({"ok": False, "error": "job not found"}, status_code=404)

    async def _sse():
        yield f"event: job\ndata: {json.dumps({'job_id': job.id, 'slug': job.slug, 'offset': from_offset})}\n\n"
        async for item in aj.subscribe(job, from_offset=from_offset):
            ev = item.get("event", "message")
            data = item.get("data", {})
            yield f"event: {ev}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        _sse(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
