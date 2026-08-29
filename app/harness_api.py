"""Studio / harness HTTP API — shell, local LLM, preferences, learned skills."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
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


class ModelSelectionBody(BaseModel):
    provider: str = Field(..., min_length=1, max_length=60)
    model: str = Field(..., min_length=1, max_length=180)
    base_url: str = Field(default="", max_length=500)
    api_key: str = Field(default="", max_length=1000)


class RoutingProfileBody(BaseModel):
    id: str = Field(..., min_length=1, max_length=40)


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


@router.get("/api/v1/llm/resources")
async def llm_resources(probe_local: bool = True) -> dict[str, Any]:
    from daedalus.provider_catalog import snapshot

    return snapshot(probe_local=probe_local)


@router.post("/admin/llm-router/api/selection")
async def llm_select(body: ModelSelectionBody) -> dict[str, Any]:
    from daedalus.llm_config import PROVIDER_DEFAULTS, set_config
    from daedalus.llm_router_config import router_write_context
    from daedalus.local_llm import is_local_provider
    from daedalus.provider_catalog import provider_definition, snapshot

    definition = provider_definition(body.provider)
    provider = definition.id
    if provider not in PROVIDER_DEFAULTS and provider != "custom":
        raise HTTPException(status_code=400, detail=f"unknown provider: {provider}")
    base_url = body.base_url.strip()
    if provider == "custom" and not base_url:
        raise HTTPException(status_code=400, detail="custom provider requires base_url")
    if base_url and not base_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="base_url must use http:// or https://")
    with router_write_context():
        saved = set_config(
            model=body.model.strip(),
            api_key=body.api_key.strip(),
            provider=provider,
            base_url=base_url,
            use_cache=provider == "anthropic",
        )
    return {
        "ok": True,
        "selection": {key: value for key, value in saved.items() if key != "api_key"},
        "local": is_local_provider(provider),
        "resources": snapshot(probe_local=False),
    }


@router.post("/admin/llm-router/api/profile")
async def llm_apply_profile(body: RoutingProfileBody) -> dict[str, Any]:
    from daedalus.llm_config import get_config
    from daedalus.llm_router_config import apply_profile, get_llm_profile, router_write_context
    from daedalus.provider_catalog import resources
    from daedalus.routing_profiles import profile_by_id

    provider_rows = resources(probe_local=True)
    profile = profile_by_id(body.id, provider_rows)
    if not profile:
        raise HTTPException(status_code=404, detail=f"unknown routing profile: {body.id}")
    if not profile.get("available"):
        raise HTTPException(status_code=409, detail=profile.get("reason") or "profile unavailable")
    current = get_config()
    master = profile["slots"]["master"]
    master_key = (
        str(current.get("api_key") or "")
        if current.get("provider") == master.get("provider")
        else ""
    )
    with router_write_context():
        applied = apply_profile(
            profile["slots"],
            source_id=profile["id"],
            source_label=profile["label"],
            source_kind="resource-profile",
            master_api_key=master_key,
        )
    return {"ok": True, "applied": applied, "profile": get_llm_profile()}


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
    from daedalus.learned_skills import list_skills
    return {"ok": True, "skills": list_skills()}


class ReachReadBody(BaseModel):
    url: str = Field(..., min_length=4, max_length=2000)
    max_chars: int | None = Field(default=8000, ge=200, le=20000)


class PlaytestBody(BaseModel):
    slug: str = Field(..., min_length=1, max_length=80)
    kind: str = Field(default="static", max_length=40)
    n: int = Field(default=4, ge=1, le=6)
    use_browser: bool = False
    with_llm: bool = False


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


class ConnectorTestBody(BaseModel):
    confirm: bool = False


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
async def connectors(probe: bool = False) -> dict[str, Any]:
    from daedalus.addons import catalog
    from daedalus.connectors_catalog import catalog as connector_catalog
    from daedalus.mcp_client import status as mcp_status_fn
    from daedalus.mirofish_client import ping as mf_ping
    from daedalus.reach import reach_status
    mf = await mf_ping()
    return {
        "ok": True,
        "connectors": await connector_catalog(probe=probe),
        "reach": reach_status(),
        "mirofish": mf,
        "mcp": mcp_status_fn(),
        "addons": catalog(),
    }


@router.post("/api/v1/daedalus/connectors/{connector_id}/test")
async def connector_test(connector_id: str, body: ConnectorTestBody) -> dict[str, Any]:
    from daedalus.connectors_catalog import test_connector

    result = await test_connector(connector_id, confirm=body.confirm)
    if not result.get("ok") and not result.get("needs_confirm"):
        raise HTTPException(status_code=400, detail=result.get("error") or "connector test failed")
    return result


class WardrobeActivate(BaseModel):
    id: str = Field(..., min_length=1, max_length=80)
    backend: str = Field(default="ollama", max_length=40)


class WardrobePull(BaseModel):
    id: str = Field(..., min_length=1, max_length=80)
    confirm: bool = False


class OfflineBody(BaseModel):
    offline: bool = True


class AddonBody(BaseModel):
    id: str = Field(..., min_length=1, max_length=40)


class EffortBody(BaseModel):
    text: str = Field(default="", max_length=8000)
    effort: str = Field(default="auto", max_length=16)


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
