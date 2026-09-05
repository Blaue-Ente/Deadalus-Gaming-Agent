"""Studio / harness HTTP API — shell, local LLM, preferences, learned skills."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

router = APIRouter(tags=["harness"])


class ShellBody(BaseModel):
    command: str = Field(..., min_length=1, max_length=4000)
    cwd: str = Field(default="", max_length=500)
    slug: str = Field(default="", max_length=80)
    timeout: int | None = Field(default=None, ge=1, le=180)


class PrefBody(BaseModel):
    text: str = Field(..., min_length=4, max_length=400)
    kind: str = Field(default="preference", max_length=40)


@router.get("/api/v1/llm/local")
async def llm_local() -> dict[str, Any]:
    from daedalus.local_llm import probe_all, resolve_local_config, local_requested
    from daedalus.llm_config import get_config

    try:
        cfg = get_config()
    except Exception as exc:
        cfg = {"error": str(exc)[:200]}
    return {
        "ok": True,
        "requested": local_requested(),
        "effective": {k: v for k, v in cfg.items() if k != "api_key"} | {
            "has_key": bool(cfg.get("api_key")),
        } if isinstance(cfg, dict) else cfg,
        "backends": probe_all(),
        "resolved_local": resolve_local_config(
            str(cfg.get("provider") or "") if isinstance(cfg, dict) else None
        ),
    }


@router.get("/api/v1/llm/local/models")
async def llm_local_models() -> dict[str, Any]:
    from daedalus.local_llm import probe_all
    return {"ok": True, "backends": probe_all()}


@router.post("/api/v1/daedalus/shell")
async def shell_run(body: ShellBody) -> dict[str, Any]:
    from daedalus.shell_center import run_command
    result = run_command(
        body.command,
        cwd=body.cwd or None,
        slug=body.slug,
        timeout=body.timeout,
    )
    if result.get("disabled"):
        raise HTTPException(status_code=403, detail=result.get("error") or "shell disabled")
    return {"ok": bool(result.get("ok")), **result}


@router.get("/api/v1/daedalus/shell/history")
async def shell_history(limit: int = 40) -> dict[str, Any]:
    from daedalus.shell_center import history, jail_roots
    return {
        "ok": True,
        "history": history(limit=limit),
        "jail": [str(p) for p in jail_roots()],
    }


@router.get("/api/v1/daedalus/preferences")
async def prefs_list() -> dict[str, Any]:
    from daedalus.preferences import load
    return {"ok": True, "items": load()}


@router.post("/api/v1/daedalus/preferences")
async def prefs_add(body: PrefBody) -> dict[str, Any]:
    from daedalus.preferences import add
    try:
        row = add(body.text, kind=body.kind, source="studio")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "item": row}


@router.delete("/api/v1/daedalus/preferences/{pref_id}")
async def prefs_delete(pref_id: str) -> dict[str, Any]:
    from daedalus.preferences import remove
    ok = remove(pref_id)
    if not ok:
        raise HTTPException(status_code=404, detail="not found")
    return {"ok": True}


@router.get("/api/v1/daedalus/learned-skills")
async def skills_list() -> dict[str, Any]:
    from daedalus.learned_skills import list_pending, list_skills
    return {"ok": True, "skills": list_skills(), "pending": list_pending()}


@router.post("/api/v1/daedalus/learned-skills/{draft_id}/approve")
async def skills_approve(draft_id: str) -> dict[str, Any]:
    from daedalus.learned_skills import approve
    result = approve(draft_id)
    if not result.get("ok"):
        code = 404 if "unknown" in str(result.get("error") or "") else 400
        raise HTTPException(status_code=code, detail=result.get("error") or "not found")
    return result


@router.post("/api/v1/daedalus/learned-skills/{draft_id}/reject")
async def skills_reject(draft_id: str, reason: str = "") -> dict[str, Any]:
    from daedalus.learned_skills import reject
    result = reject(draft_id, reason=reason)
    if not result.get("ok"):
        code = 404 if "unknown" in str(result.get("error") or "") else 400
        raise HTTPException(status_code=code, detail=result.get("error") or "not found")
    return result


class ReachReadBody(BaseModel):
    url: str = Field(..., min_length=4, max_length=2000)
    max_chars: int | None = Field(default=8000, ge=200, le=20000)


class PlaytestBody(BaseModel):
    slug: str = Field(..., min_length=1, max_length=80)
    kind: str = Field(default="static", max_length=40)
    n: int = Field(default=4, ge=1, le=6)
    use_browser: bool = False
    with_llm: bool = False


class LabCreateBody(BaseModel):
    title: str = Field(default="", max_length=120)
    source: str = Field(default="", max_length=500)


class LabInstallBody(BaseModel):
    path: str = Field(..., min_length=1, max_length=500)


class ComputerOpenBody(BaseModel):
    lab_id: str = Field(default="", max_length=80)
    slug: str = Field(default="", max_length=80)
    live: bool = True


class ComputerStopBody(BaseModel):
    lab_id: str = Field(default="", max_length=80)


class DissectBody(BaseModel):
    source: str = Field(default="", max_length=500)
    slug: str = Field(default="", max_length=80)
    lab_id: str = Field(default="", max_length=80)
    path: str = Field(default="", max_length=500)
    create_lab: bool = True
    live: bool = True
    description: str = Field(default="", max_length=4000)
    name: str = Field(default="", max_length=120)


class MirofishSubmitBody(BaseModel):
    text: str = Field(..., min_length=20, max_length=80000)
    requirement: str = Field(default="", max_length=4000)
    project_name: str = Field(default="Daedalus GDD", max_length=120)


class BrowserBody(BaseModel):
    action: str = Field(..., min_length=2, max_length=20)
    url: str = Field(default="", max_length=2000)
    selector: str = Field(default="", max_length=300)
    text: str = Field(default="", max_length=4000)
    slug: str = Field(default="", max_length=80)


@router.get("/api/v1/daedalus/reach")
async def reach_doctor() -> dict[str, Any]:
    from daedalus.reach import reach_status
    return reach_status()


@router.post("/api/v1/daedalus/reach/read")
async def reach_read(body: ReachReadBody) -> dict[str, Any]:
    from daedalus.reach import looks_like_rss, read_rss, web_read
    if looks_like_rss(body.url):
        return read_rss(body.url)
    return await web_read(body.url, max_chars=body.max_chars or 8000)


@router.get("/api/v1/daedalus/playtest/personas")
async def playtest_personas() -> dict[str, Any]:
    from daedalus.playtest import list_personas
    return {"ok": True, "personas": list_personas()}


@router.post("/api/v1/daedalus/playtest")
async def playtest_run(body: PlaytestBody) -> dict[str, Any]:
    from daedalus.playtest import run_playtest
    llm_fn = None
    if body.with_llm:
        from daedalus.llm_client import complete

        async def llm_fn(system: str, user: str) -> str:
            r = await complete(system=system, user=user, max_tokens=600, temperature=0.4)
            return str(r.get("reply") or "")

    return await run_playtest(
        body.slug,
        kind=body.kind,
        n=body.n,
        use_browser=body.use_browser,
        llm_fn=llm_fn,
    )


@router.get("/api/v1/daedalus/labs/status")
async def labs_host_status() -> dict[str, Any]:
    from daedalus.play_lab import probe_host
    return probe_host()


@router.get("/api/v1/daedalus/labs")
async def labs_list() -> dict[str, Any]:
    from daedalus.play_lab import list_labs, probe_host
    return {"ok": True, "labs": list_labs(), "host": probe_host()}


@router.post("/api/v1/daedalus/labs")
async def labs_create(body: LabCreateBody) -> dict[str, Any]:
    from daedalus.play_lab import create_lab
    row = create_lab(title=body.title, source=body.source)
    return {"ok": True, **row}


@router.post("/api/v1/daedalus/labs/{lab_id}/install")
async def labs_install(lab_id: str, body: LabInstallBody) -> dict[str, Any]:
    from daedalus.play_lab import install_into_lab
    try:
        return install_into_lab(lab_id, body.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/v1/daedalus/labs/{lab_id}/start")
async def labs_start(lab_id: str) -> dict[str, Any]:
    from daedalus.play_lab import start_vm
    try:
        return start_vm(lab_id)
    except ValueError as extra:
        raise HTTPException(status_code=400, detail=str(extra)) from extra


@router.post("/api/v1/daedalus/labs/{lab_id}/stop")
async def labs_stop(lab_id: str) -> dict[str, Any]:
    from daedalus.play_lab import stop_vm
    return stop_vm(lab_id)


@router.delete("/api/v1/daedalus/labs/{lab_id}")
async def labs_destroy(lab_id: str) -> dict[str, Any]:
    from daedalus.play_lab import destroy_lab
    return destroy_lab(lab_id)


@router.get("/api/v1/daedalus/computer")
async def computer_status() -> dict[str, Any]:
    from daedalus.computer import live_computers, probe
    from daedalus.play_lab import probe_host
    return {
        "ok": True,
        "host": probe_host(),
        "computer": probe(),
        "live": live_computers(),
    }


@router.post("/api/v1/daedalus/computer")
async def computer_open(body: ComputerOpenBody) -> dict[str, Any]:
    from daedalus.computer import open_project_computer
    from daedalus.play_lab import start_vm
    try:
        if (body.lab_id or "").strip():
            row = start_vm(body.lab_id.strip())
        elif (body.slug or "").strip():
            row = open_project_computer(body.slug.strip())
        else:
            raise ValueError("lab_id or slug required")
    except ValueError as extra:
        raise HTTPException(status_code=400, detail=str(extra)) from extra
    return {"ok": True, **row}


@router.post("/api/v1/daedalus/computer/stop")
async def computer_stop(body: ComputerStopBody) -> dict[str, Any]:
    from daedalus.computer_smolvm import stop_all
    from daedalus.play_lab import stop_vm
    lab_id = (body.lab_id or "").strip()
    if lab_id:
        return stop_vm(lab_id)
    from daedalus.computer import live_computers
    stopped: list[str] = []
    for row in live_computers():
        lid = str(row.get("lab_id") or "")
        if lid:
            stop_vm(lid)
            stopped.append(lid)
    if not stopped:
        stop_all()
    return {"ok": True, "stopped": stopped}


@router.post("/api/v1/daedalus/dissect")
async def dissect_run(body: DissectBody) -> dict[str, Any]:
    from daedalus.agent import get_daedalus
    from daedalus.dissection import dissect
    try:
        memory = get_daedalus().memory
    except Exception:
        memory = None
    try:
        return await dissect(
            body.source,
            slug=body.slug or None,
            lab_id=body.lab_id or None,
            install_path=body.path,
            create_lab=body.create_lab,
            live=body.live,
            project_name=body.name or body.slug,
            description=body.description,
            memory=memory,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/v1/daedalus/mirofish")
async def mirofish_ping() -> dict[str, Any]:
    from daedalus.mirofish_client import ping
    return await ping()


@router.post("/api/v1/daedalus/mirofish/submit")
async def mirofish_submit(body: MirofishSubmitBody) -> dict[str, Any]:
    from daedalus.mirofish_client import submit_document
    try:
        return await submit_document(
            body.text,
            requirement=body.requirement,
            project_name=body.project_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/v1/daedalus/mcp")
async def mcp_status() -> dict[str, Any]:
    from daedalus.mcp_client import status
    return status()


@router.post("/api/v1/daedalus/browser")
async def browser_action(body: BrowserBody) -> dict[str, Any]:
    from daedalus.browser_session import run_action
    return await run_action(
        body.action,
        url=body.url,
        selector=body.selector,
        text=body.text,
        slug=body.slug,
    )


@router.get("/api/v1/daedalus/connectors")
async def connectors() -> dict[str, Any]:
    from daedalus.addons import catalog
    from daedalus.mcp_client import status as mcp_status_fn
    from daedalus.mirofish_client import ping as mf_ping
    from daedalus.reach import reach_status
    mf = await mf_ping()
    return {
        "ok": True,
        "reach": reach_status(),
        "mirofish": mf,
        "mcp": mcp_status_fn(),
        "addons": catalog(),
    }


class WardrobeActivate(BaseModel):
    id: str = Field(..., min_length=1, max_length=80)
    backend: str = Field(default="ollama", max_length=40)


class WardrobePull(BaseModel):
    id: str = Field(..., min_length=1, max_length=80)
    confirm: bool = False


class WardrobeLocal(BaseModel):
    backend: str = Field(default="ollama", max_length=40)


class OfflineBody(BaseModel):
    offline: bool = True


class AddonBody(BaseModel):
    id: str = Field(..., min_length=1, max_length=40)


class EffortBody(BaseModel):
    text: str = Field(default="", max_length=8000)
    effort: str = Field(default="auto", max_length=16)


class RolePickBody(BaseModel):
    mode: str = Field(default="manual", max_length=16)
    provider: str = Field(default="", max_length=40)
    model: str = Field(default="", max_length=120)


class RolesBody(BaseModel):
    chat: RolePickBody | None = None
    build: RolePickBody | None = None
    mentor: RolePickBody | None = None
    wrap_up: RolePickBody | None = None


@router.get("/api/v1/daedalus/effort")
async def effort_preview(q: str = "", effort: str = "auto") -> dict[str, Any]:
    from daedalus.task_effort import classify
    return classify(q, requested=effort)


@router.post("/api/v1/daedalus/effort")
async def effort_classify(body: EffortBody) -> dict[str, Any]:
    from daedalus.task_effort import classify
    return classify(body.text, requested=body.effort)


@router.get("/api/v1/llm/wardrobe")
async def llm_wardrobe() -> dict[str, Any]:
    from daedalus.llm_wardrobe import status
    return status()


@router.post("/api/v1/llm/wardrobe/activate")
async def llm_wardrobe_activate(body: WardrobeActivate) -> dict[str, Any]:
    from daedalus.llm_wardrobe import activate
    return activate(body.id, backend=body.backend or "ollama")


@router.post("/api/v1/llm/wardrobe/pull")
async def llm_wardrobe_pull(body: WardrobePull) -> dict[str, Any]:
    from daedalus.llm_wardrobe import install_plan, pull
    if not body.confirm:
        return {**install_plan(body.id), "needs_confirm": True}
    return pull(body.id)


@router.post("/api/v1/llm/wardrobe/cloud")
async def llm_wardrobe_cloud() -> dict[str, Any]:
    from daedalus.llm_wardrobe import use_cloud
    return use_cloud()


@router.post("/api/v1/llm/wardrobe/local")
async def llm_wardrobe_local(body: WardrobeLocal | None = None) -> dict[str, Any]:
    from daedalus.llm_wardrobe import use_local
    backend = (body.backend if body else None) or "ollama"
    return use_local(backend)


@router.get("/api/v1/llm/catalog")
async def llm_catalog(
    q: str = "",
    provider: str = "",
    tools: int = 0,
    refresh: int = 0,
    limit: int = 80,
) -> dict[str, Any]:
    from daedalus.model_catalog import list_catalog
    return list_catalog(
        q=q, provider=provider, tools_only=bool(tools),
        refresh=bool(refresh), limit=min(max(limit, 1), 200),
    )


@router.get("/api/v1/llm/roles")
async def llm_roles() -> dict[str, Any]:
    from daedalus.llm_config import describe_roles
    from daedalus.model_catalog import auto_assign, keyed_providers, upgrade_below_usd
    row = describe_roles()
    try:
        picks = auto_assign()
        row["auto"] = {k: v.as_dict() for k, v in picks.items()}
    except Exception as exc:
        row["auto"] = {"error": str(exc)[:200]}
    row["providers"] = keyed_providers()
    row["upgrade_below_usd"] = upgrade_below_usd()
    return row


@router.post("/api/v1/llm/roles")
async def llm_roles_set(body: RolesBody) -> dict[str, Any]:
    from daedalus.llm_config import set_config, set_roles, get_config
    from daedalus.llm_router_config import router_write_context
    payload = body.model_dump(exclude_none=True)
    chat = payload.pop("chat", None)
    with router_write_context():
        if chat and (chat.get("model") or chat.get("provider")):
            try:
                cur = get_config()
            except Exception:
                cur = {}
            set_config(
                model=str(chat.get("model") or cur.get("model") or ""),
                api_key="",
                provider=str(chat.get("provider") or cur.get("provider") or "openrouter"),
                base_url=str(cur.get("base_url") or ""),
                use_cache=bool(cur.get("use_cache", True)),
            )
        if payload:
            set_roles(payload)
    return await llm_roles()


@router.get("/api/v1/daedalus/offline")
async def offline_status() -> dict[str, Any]:
    from daedalus.offline import snapshot
    return snapshot()


@router.post("/api/v1/daedalus/offline")
async def offline_set(body: OfflineBody) -> dict[str, Any]:
    from daedalus.llm_wardrobe import set_offline
    from daedalus.offline import snapshot
    set_offline(body.offline)
    return snapshot()


@router.get("/api/v1/daedalus/addons")
async def addons_list() -> dict[str, Any]:
    from daedalus.addons import catalog
    return {"ok": True, "addons": catalog()}


@router.post("/api/v1/daedalus/addons")
async def addons_enable(body: AddonBody) -> dict[str, Any]:
    from daedalus.addons import enable
    r = enable(body.id)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("error") or "failed")
    return r


@router.delete("/api/v1/daedalus/addons/{addon_id}")
async def addons_disable(addon_id: str) -> dict[str, Any]:
    from daedalus.addons import disable
    return disable(addon_id)


@router.get("/api/v1/daedalus/live")
async def live_catalog() -> dict[str, Any]:
    from daedalus.live_publish import capabilities
    return capabilities()


@router.get("/api/v1/daedalus/bench")
async def bench_get(persist: bool = False) -> dict[str, Any]:
    from daedalus.game_agent_bench import run_suite
    return run_suite(persist=persist)


@router.post("/api/v1/daedalus/bench")
async def bench_run() -> dict[str, Any]:
    from daedalus.game_agent_bench import run_suite
    return run_suite(persist=True)


class EvolutionBody(BaseModel):
    enabled: bool | None = None
    interval_s: int | None = Field(default=None, ge=300, le=86400)
    clone_research: bool | None = None
    ask_llm: bool | None = None


class EvolutionCycleBody(BaseModel):
    force: bool = True


@router.get("/api/v1/daedalus/evolution")
async def evolution_status() -> dict[str, Any]:
    from daedalus.evolution import snapshot, start_gardener
    start_gardener()
    return snapshot()


@router.post("/api/v1/daedalus/evolution")
async def evolution_set(body: EvolutionBody) -> dict[str, Any]:
    from daedalus.evolution import set_config
    return set_config(
        enabled_flag=body.enabled,
        interval=body.interval_s,
        clone=body.clone_research,
        ask_llm=body.ask_llm,
    )


@router.post("/api/v1/daedalus/evolution/cycle")
async def evolution_cycle(body: EvolutionCycleBody = EvolutionCycleBody()) -> dict[str, Any]:
    from daedalus.evolution import enabled, run_cycle, set_enabled
    force = body.force
    if force and not enabled():
        # Owner clicked "now" — turn the mode on so the gardener keeps going.
        set_enabled(True)
    daedalus = None
    try:
        from daedalus.agent import get_daedalus
        daedalus = get_daedalus()
    except Exception:
        pass
    return await run_cycle(force=force, daedalus=daedalus)


# ── Studio chat jobs (SSE; blocking POST /api/v1/daedalus/chat stays) ────────

class ChatJobBody(BaseModel):
    message: str = Field(..., min_length=1, max_length=20000)
    session_id: str = Field(default="default", max_length=128)
    effort: str = Field(default="auto", max_length=16)


def _chat_sse(event: str, data: Any) -> str:
    payload = json.dumps(data, default=str, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


@router.post("/api/v1/daedalus/chat/jobs")
async def chat_job_start(body: ChatJobBody) -> dict[str, Any]:
    from daedalus.chat_jobs import start_chat
    return await start_chat(
        message=body.message,
        session_id=body.session_id,
        effort=body.effort,
    )


@router.get("/api/v1/daedalus/chat/jobs/{job_id}")
async def chat_job_get(job_id: str) -> dict[str, Any]:
    from daedalus.chat_jobs import get_job
    row = get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="unknown job")
    return {"ok": True, **row}


@router.post("/api/v1/daedalus/chat/jobs/{job_id}/cancel")
async def chat_job_cancel(job_id: str) -> dict[str, Any]:
    from daedalus.chat_jobs import cancel, get_job
    if not cancel(job_id):
        raise HTTPException(status_code=404, detail="unknown job")
    row = get_job(job_id) or {"id": job_id, "status": "cancelled"}
    return {"ok": True, **row}


@router.get("/api/v1/daedalus/chat/live")
async def chat_live(session_id: str = "") -> dict[str, Any]:
    """Live tool label for a Studio session (owner token). Not the /apps live catalog."""
    from daedalus.stream import get_live
    sid = (session_id or "").strip()
    if sid:
        entry = get_live(sid)
        return {"ok": True, "live": {sid: entry} if entry else {}}
    return {"ok": True, "live": get_live()}


@router.get("/api/v1/daedalus/chat/jobs/{job_id}/stream")
async def chat_job_stream(job_id: str, request: Request) -> StreamingResponse:
    from daedalus.chat_jobs import get_job
    from daedalus.stream import get_live

    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="unknown job")

    async def _gen():
        yield _chat_sse("job", job)
        last: tuple[Any, ...] | None = None
        while True:
            if await request.is_disconnected():
                break
            row = get_job(job_id)
            if not row:
                yield _chat_sse("error", {"error": "gone"})
                break
            live = get_live(row.get("session_id") or "") or {}
            stamp = (live.get("ts"), live.get("label"), live.get("tool"), row.get("status"))
            if stamp != last:
                last = stamp
                if live:
                    yield _chat_sse("live", live)
                yield _chat_sse("status", {"status": row.get("status")})
            if row.get("status") in ("done", "error", "cancelled"):
                yield _chat_sse("done", row)
                break
            await asyncio.sleep(0.35)

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/v1/daedalus/tools")
async def tool_catalog() -> dict[str, Any]:
    """Inspectable catalog: native tools, NEED_* leftovers, skills, knowledge."""
    from daedalus.task_playbooks import all_playbooks
    from daedalus.tool_catalog import full_catalog

    return {
        "ok": True,
        "tools": [row.as_dict() for row in full_catalog()],
        "playbooks": [pb.as_dict() for pb in all_playbooks()],
    }


@router.get("/api/v1/daedalus/tools/route")
async def tool_route(q: str = "", kind: str = "", intent: str = "") -> dict[str, Any]:
    """Compile the kernel task plan for an ask (no LLM)."""
    from daedalus.tool_router import route

    rt = route(q, kind=kind, intent=intent)
    return {"ok": True, **rt.as_dict()}
