"""Playground + autopilot HTTP API for standalone Daedalus."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/v1/playground", tags=["playground"])


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    kind: str = Field(default="static", max_length=40)
    description: str = Field(default="", max_length=8000)
    coding_mode: str = Field(default="lite", max_length=20)


class AutopilotStart(BaseModel):
    description: str = Field(..., min_length=1, max_length=8000)
    mode: str = Field(default="full", max_length=20)


def _jsonable(row: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


@router.get("/projects")
async def list_projects() -> dict:
    from daedalus import playground as pg
    rows = await pg.list_projects()
    return {"ok": True, "projects": [_jsonable(r) for r in rows]}


@router.post("/projects")
async def create_project(body: ProjectCreate) -> dict:
    from daedalus import playground as pg
    proj = await pg.create_project(
        body.name,
        kind=body.kind,
        description=body.description,
        coding_mode=body.coding_mode,
        source="built",
    )
    return {"ok": True, "project": _jsonable(proj)}


@router.get("/projects/{slug}")
async def get_project(slug: str) -> dict:
    from daedalus import playground as pg
    proj = await pg.get_project(slug)
    if not proj:
        raise HTTPException(status_code=404, detail="project not found")
    return {"ok": True, "project": _jsonable(proj), "files": pg.read_tree(slug)}


@router.delete("/projects/{slug}")
async def delete_project(slug: str) -> dict:
    from daedalus import playground as pg
    ok = await pg.delete_project(slug, wipe_files=True)
    if not ok:
        raise HTTPException(status_code=404, detail="project not found")
    return {"ok": True}


@router.get("/projects/{slug}/file")
async def read_file(slug: str, path: str) -> dict:
    from daedalus import playground as pg
    try:
        text = pg.read_file(slug, path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="file not found")
    return {"ok": True, "path": path, "content": text}


@router.get("/projects/{slug}/preview/{path:path}")
async def preview_file(slug: str, path: str) -> FileResponse:
    from daedalus import playground as pg
    try:
        target = pg._safe_join(pg.project_dir(slug), path or "index.html")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not target.is_file():
        index = pg.project_dir(slug) / "index.html"
        if index.is_file() and not path:
            target = index
        else:
            raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(target)


@router.post("/projects/{slug}/autopilot")
async def start_autopilot(slug: str, body: AutopilotStart) -> dict:
    from daedalus import playground as pg
    from daedalus.builder_runtime import start_builder_run_async

    proj = await pg.get_project(slug)
    if not proj:
        raise HTTPException(status_code=404, detail="project not found")
    desc = body.description or proj.get("description") or proj.get("name") or slug
    job = await start_builder_run_async(slug, desc, mode=body.mode or "full")
    return {"ok": True, "job": job.snapshot()}


@router.get("/projects/{slug}/autopilot")
async def autopilot_status(slug: str) -> dict:
    from daedalus.builder_runtime import get_active_job
    job = get_active_job(slug)
    if not job:
        return {"ok": True, "job": None}
    return {"ok": True, "job": job.snapshot(), "events": job.events[-80:]}


@router.get("/projects/{slug}/autopilot/stream")
async def autopilot_stream(slug: str, offset: int = 0) -> StreamingResponse:
    from daedalus import autopilot_jobs as aj
    from daedalus.builder_runtime import get_active_job

    job = get_active_job(slug)
    if not job:
        raise HTTPException(status_code=404, detail="no active autopilot")

    async def gen():
        async for ev in aj.subscribe(job, from_offset=offset):
            yield f"event: {ev.get('event')}\ndata: {json.dumps(ev.get('data') or {}, ensure_ascii=False)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
