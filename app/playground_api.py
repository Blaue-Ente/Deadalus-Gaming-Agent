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
    effort: str = Field(default="auto", max_length=16)


class AutopilotStart(BaseModel):
    description: str = Field(..., min_length=1, max_length=8000)
    mode: str = Field(default="full", max_length=20)
    effort: str = Field(default="auto", max_length=16)
    resume: bool = False


class AssetGenerate(BaseModel):
    path: str = Field(default="assets/sprite.png", max_length=300)
    prompt: str = Field(..., min_length=1, max_length=2000)
    size: str = Field(default="", max_length=32)
    model: str = Field(default="", max_length=80)


class AssetPipelineBody(BaseModel):
    action: str = Field(..., min_length=1, max_length=32)
    sources: list[str] | None = None
    prompt: str = Field(default="", max_length=2000)
    name: str = Field(default="", max_length=80)
    provider: str = Field(default="auto", max_length=32)
    job_id: str = Field(default="", max_length=80)


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
        effort=body.effort,
    )
    return {"ok": True, "project": _jsonable(proj)}


@router.get("/projects/{slug}")
async def get_project(slug: str) -> dict:
    from daedalus import playground as pg
    proj = await pg.get_project(slug)
    if not proj:
        raise HTTPException(status_code=404, detail="project not found")
    return {"ok": True, "project": _jsonable(proj), "files": pg.read_tree(slug)}


@router.get("/projects/{slug}/dissection")
async def get_project_dissection(slug: str) -> dict:
    from daedalus.dissection import load_dissection
    return {"ok": True, "dissection": load_dissection(slug)}


@router.get("/projects/{slug}/world")
async def get_project_world(slug: str) -> dict:
    from daedalus import playground as pg
    from daedalus.world_kit import status as world_status
    proj = await pg.get_project(slug)
    if not proj:
        raise HTTPException(status_code=404, detail="project not found")
    return {"ok": True, "slug": slug, "world": world_status(pg.project_dir(slug))}


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
    from daedalus.task_effort import classify
    from daedalus.world_kit import write as write_world
    from daedalus.playground import project_dir
    dec = classify(desc, requested=body.effort)
    if dec.get("world_kit"):
        write_world(project_dir(slug), name=proj.get("name") or slug, slug=slug, description=desc, kind=proj.get("kind") or "")
    job = await start_builder_run_async(slug, desc, mode=body.mode or "full", resume=body.resume)
    return {"ok": True, "job": job.snapshot()}


@router.get("/projects/{slug}/autopilot")
async def autopilot_status(slug: str) -> dict:
    from daedalus.builder_runtime import get_active_job
    from daedalus.run_engine import checkpoint_for_slug
    job = get_active_job(slug)
    ckpt = checkpoint_for_slug(slug)
    if not job:
        return {"ok": True, "job": None, "checkpoint": ckpt}
    return {"ok": True, "job": job.snapshot(), "events": job.events[-80:], "checkpoint": ckpt}


@router.get("/projects/{slug}/capsule")
async def download_capsule(slug: str):
    from fastapi.responses import Response
    from daedalus.capsule import capsule_bytes, capsule_meta
    meta = capsule_meta(slug)
    if not meta.get("ok"):
        raise HTTPException(status_code=404, detail="project not found")
    data = capsule_bytes(slug)
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{slug}.zip"'},
    )


@router.get("/projects/{slug}/capsule.json")
async def capsule_info(slug: str) -> dict:
    from daedalus.capsule import capsule_meta
    meta = capsule_meta(slug)
    if not meta.get("ok"):
        raise HTTPException(status_code=404, detail="project not found")
    return meta


@router.post("/projects/{slug}/publish-local")
async def publish_local(slug: str) -> dict:
    from daedalus.playground_agent_guards import publish_with_repair
    try:
        result = await publish_with_repair(slug, icon="🎮")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:300]) from exc
    return {"ok": True, "published": result, "url": f"/apps/{slug}/"}


class LiveBody(BaseModel):
    backend: str = Field(default="auto", max_length=40)


@router.get("/projects/{slug}/live")
async def live_status(slug: str) -> dict:
    from daedalus.live_publish import capabilities, get_site
    return {"ok": True, "site": get_site(slug), "capabilities": capabilities()}


@router.post("/projects/{slug}/live")
async def live_publish(slug: str, body: LiveBody = LiveBody()) -> dict:
    from daedalus.live_publish import publish_live
    backend = body.backend or "auto"
    result = await publish_live(slug, backend=backend)
    if not result.get("ok") and not result.get("local_url"):
        raise HTTPException(status_code=400, detail=result.get("error") or "live publish failed")
    return result


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


@router.post("/projects/{slug}/assets/generate")
async def generate_project_asset(slug: str, body: AssetGenerate) -> dict:
    from daedalus import playground as pg
    from daedalus import playground_assets as pa

    proj = await pg.get_project(slug)
    if not proj:
        raise HTTPException(status_code=404, detail="project not found")
    try:
        result = await pa.generate_asset(
            slug,
            path=body.path or "assets/sprite.png",
            prompt=(body.prompt or "").strip(),
            size=body.size or pa.DEFAULT_SIZE_SPRITE,
            model=body.model or pa.DEFAULT_MODEL,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:400]) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)[:400]) from exc
    return {"ok": True, **result}


@router.post("/projects/{slug}/assets/pipeline")
async def project_asset_pipeline(slug: str, body: AssetPipelineBody) -> dict:
    from daedalus import playground as pg
    from daedalus.asset_pipeline import run_action

    proj = await pg.get_project(slug)
    if not proj:
        raise HTTPException(status_code=404, detail="project not found")
    result = run_action(
        body.action,
        slug,
        sources=body.sources,
        prompt=body.prompt,
        name=body.name,
        provider=body.provider,
        job_id=body.job_id,
    )
    if not result.get("ok") and (result.get("need_key") or result.get("handoff")):
        return {"ok": False, **result}
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "pipeline failed")
    return {"ok": True, **result}
