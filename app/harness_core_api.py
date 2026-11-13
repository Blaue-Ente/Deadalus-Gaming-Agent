"""Harness Core HTTP API (read-only in A1): the capability registry and the no-loss parity table."""
from __future__ import annotations

from typing import Any

import asyncio
import json
import os

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

router = APIRouter(tags=["harness-core"])


@router.get("/api/v1/harness/capabilities")
def harness_capabilities(request: Request, kind: str = "", status: str = "") -> dict[str, Any]:
    """Every tool, route, panel, setting and workflow, generated from the code that defines them."""
    from daedalus.harness.registry import registry_payload

    # Plain def on purpose: building the registry imports modules, so FastAPI runs it in a threadpool.
    return registry_payload(request.app, kind=kind.strip(), status=status.strip())


@router.get("/api/v1/onboarding")
def onboarding_report(quick: bool = False) -> dict[str, Any]:
    """First run: what this computer has and needs, which keys are set (names only), the SubQ switch, abilities.
    ``quick`` answers only whether Studio should open it (every page load asks; the checks take a second or two).
    Plain def on purpose: the checks start small processes (git --version), so FastAPI runs it in a threadpool."""
    from daedalus.onboarding import auto_open, report, seen

    if quick:
        return {"ok": True, "seen": seen(), "auto_open": auto_open()}
    return report()


@router.post("/api/v1/onboarding/seen")
def onboarding_seen() -> dict[str, Any]:
    """The owner closed the onboarding: Studio stops opening it on start (it stays under Setup)."""
    from daedalus.onboarding import mark_seen

    mark_seen()
    return {"ok": True}


@router.get("/api/v1/harness/subq")
def harness_subq(limit: int = 20) -> dict[str, Any]:
    """SubQ Snap in shadow mode: per question, how often it agreed with Daedalus's own decision, and where not."""
    from daedalus.subq_judge import summary

    return summary(limit=max(1, min(int(limit), 200)))


@router.get("/api/v1/harness/parity")
def harness_parity(request: Request) -> dict[str, Any]:
    """The no-loss parity table and any rows that do not resolve against the registry."""
    from daedalus.harness.parity import PARITY_ROWS, check_parity
    from daedalus.harness.registry import build_registry

    errors: list[str] = []
    problems = errors + check_parity(build_registry(request.app, errors))
    by_disposition: dict[str, int] = {}
    for row in PARITY_ROWS:
        by_disposition[row.disposition] = by_disposition.get(row.disposition, 0) + 1
    return {
        "ok": not problems,
        "problems": problems,
        "count": len(PARITY_ROWS),
        "by_disposition": by_disposition,
        "rows": [r.as_dict() for r in PARITY_ROWS],
    }


class IntentBody(BaseModel):
    intent: str = Field(..., min_length=1, max_length=40)
    model: str = Field(default="", max_length=200)  # "provider:model"; empty clears the owner's choice


def _intent_rows() -> tuple[list[dict[str, Any]], bool]:
    """One row per LLM intent (shared by GET intents and GET models) and whether DAEDALUS_MODEL locks them."""
    from daedalus.harness import gateway as gw

    rows: list[dict[str, Any]] = []
    overrides = gw.get_overrides()
    for intent in gw.INTENTS:
        sel = gw._SELECTOR.get(intent, intent)
        row: dict[str, Any] = {"intent": intent, "selectable": sel in gw.OVERRIDABLE or intent == "mentor"}
        if intent == "mentor":
            # The mentor keeps its own choice; this view reports it and POST delegates to it.
            from daedalus import mentor_helm

            try:
                choice = mentor_helm.get_choice()
                row.update({"owner_choice": choice.get("id"),
                            "owner_tag": (f"{choice.get('provider')}:{choice.get('model')}"
                                          if choice.get("id") == "custom" and choice.get("provider") and choice.get("model")
                                          else ""),
                            "resolved": {
                    "provider": choice.get("provider"), "model": choice.get("model"),
                    "source": "mentor-choice" if choice.get("source") != "env" else "env-lock",
                    "locked": choice.get("source") == "env"}})
            except Exception as exc:  # same rule as the other tasks: report it, do not fail the list
                row.update({"owner_choice": None, "resolved": {"provider": "", "model": "", "source": "unconfigured",
                                                               "error": str(exc)[:160]}})
        else:
            try:
                resolved = gw.resolve(intent).public()
            except Exception as exc:  # nothing configured yet: say so, never fail the whole list (fresh installs)
                resolved = {"provider": "", "model": "", "source": "unconfigured", "error": str(exc)[:160]}
            row.update({"owner_choice": overrides.get(sel), "default": gw.default_tag_for(intent),
                        "resolved": resolved})
        rows.append(row)
    return rows, gw.model_locked_by_env()


@router.get("/api/v1/harness/intents")
def harness_intents() -> dict[str, Any]:
    """Which model serves each LLM intent, and which of them the owner may change."""
    rows, locked = _intent_rows()
    return {"ok": True, "env_locked": locked, "intents": rows}


# ---- Model manager: the main model, the providers and models to pick from, a model per task -----------------------
_PROVIDER_LABELS = {
    "anthropic": "Anthropic", "openai": "OpenAI", "groq": "Groq", "gemini": "Google Gemini", "novita": "Novita",
    "openrouter": "OpenRouter", "alltoken": "AllToken", "poolside": "Poolside", "nvidia": "NVIDIA",
    "omniroute": "OmniRoute", "replit_openai": "OpenAI (Replit)", "replit_gemini": "Gemini (Replit)",
    "ollama": "Ollama", "lmstudio": "LM Studio", "llamacpp": "llama.cpp", "vllm": "vLLM",
}


def _brain_lock_env() -> str:
    """The environment variable that pins the main model, or an empty string."""
    for name in ("DAEDALUS_MODEL", "DAEDALUS_PROVIDER"):
        if (os.environ.get(name) or "").strip():
            return name
    return ""


def _source_kind(provider: str, source: str) -> str:
    from daedalus.local_llm import is_local_provider

    src = (source or "").lower()
    if provider and is_local_provider(provider):
        return "local"
    if src.startswith("db"):
        return "saved"
    if src.startswith("env"):
        return "server"
    return "auto" if provider else ""


def _provider_rows() -> list[dict[str, Any]]:
    from daedalus.llm_config import PROVIDER_DEFAULTS, resolve_provider_api_key
    from daedalus.local_llm import LOCAL_PROVIDERS

    show_replit = (os.environ.get("DAEDALUS_SHOW_REPLIT_LLM") or "").strip().lower() in ("1", "true", "yes", "on")
    rows: list[dict[str, Any]] = []
    for pid in PROVIDER_DEFAULTS:
        if pid in ("replit_openai", "replit_gemini") and not show_replit:
            continue
        local = pid in LOCAL_PROVIDERS
        try:
            has_key = True if local else bool(resolve_provider_api_key(pid))
        except Exception:
            has_key = False
        rows.append({"id": pid, "label": _PROVIDER_LABELS.get(pid, pid), "kind": "local" if local else "cloud",
                     "has_key": has_key})
    return rows


def _model_catalog() -> dict[str, list[dict[str, Any]]]:
    from daedalus.llm_config import list_preset_models

    catalog: dict[str, list[dict[str, Any]]] = {}
    for m in list_preset_models():
        prov, mid = str(m.get("provider") or ""), str(m.get("id") or "")
        if prov and mid:
            catalog.setdefault(prov, []).append({"id": mid, "label": str(m.get("label") or mid),
                                                 "tier": str(m.get("tier") or "unknown")})
    try:
        from daedalus import mentor_helm

        for row in mentor_helm.CATALOG:
            prov, mid = str(row.get("provider") or ""), str(row.get("model") or "")
            if prov and mid and not any(x["id"] == mid for x in catalog.get(prov, [])):
                catalog.setdefault(prov, []).append({"id": mid, "label": str(row.get("label") or mid),
                                                     "tier": "unknown"})
    except Exception:
        pass
    return catalog


def _brain_view() -> dict[str, Any]:
    from daedalus.llm_config import NotConfiguredError, get_brain_config

    lock_env = _brain_lock_env()
    out: dict[str, Any] = {"provider": "", "model": "", "base_url": "", "source": "", "source_kind": "",
                           "has_key": False, "locked": bool(lock_env), "lock_env": lock_env}
    try:
        cfg = get_brain_config() or {}
    except NotConfiguredError:
        return {"configured": False, **out}
    except Exception as exc:   # never fail the page: report it as "not set up" with the reason
        return {"configured": False, **out, "error": str(exc)[:160]}
    provider = str(cfg.get("provider") or "")
    out.update({"provider": provider, "model": str(cfg.get("model") or ""), "base_url": str(cfg.get("base_url") or ""),
                "source": str(cfg.get("source") or ""), "source_kind": _source_kind(provider, str(cfg.get("source") or "")),
                "has_key": bool(cfg.get("api_key"))})
    return {"configured": bool(provider and out["model"]), **out}


@router.get("/api/v1/harness/models")
def harness_models() -> dict[str, Any]:
    """Everything the Models page needs in one call: the main model, providers, model catalog and per-task rows.
    Never fails when nothing is configured. No key value is ever returned."""
    view = _brain_view()
    try:
        tasks, env_locked = _intent_rows()
    except Exception:
        tasks, env_locked = [], False
    return {"ok": True, "configured": view.pop("configured"), "env_locked": env_locked, "brain": view, "providers": _provider_rows(),
            "catalog": _model_catalog(), "tasks": tasks}


class BrainBody(BaseModel):
    provider: str = Field(..., min_length=1, max_length=40)
    model: str = Field(..., min_length=1, max_length=200)
    base_url: str = Field(default="", max_length=300)


@router.post("/api/v1/harness/brain")
def harness_brain_set(body: BrainBody) -> Any:
    """Choose the main model. Saved like Connectors does, and the wardrobe is kept in step so a leftover local
    backend cannot override the choice (step 0 of llm_config). Never accepts or returns a key."""
    from daedalus import llm_wardrobe
    from daedalus.llm_config import PROVIDER_DEFAULTS, set_config
    from daedalus.llm_router_config import router_write_context
    from daedalus.local_llm import is_local_provider, normalize_provider

    lock_env = _brain_lock_env()
    if lock_env:
        return JSONResponse({"ok": False, "error": "env_locked", "field": "brain", "env": lock_env}, status_code=409)
    raw = body.provider.strip().lower()
    local = is_local_provider(raw)
    provider = normalize_provider(raw) if local else raw
    if provider not in PROVIDER_DEFAULTS:
        return JSONResponse({"ok": False, "error": "unknown_provider", "provider": body.provider}, status_code=400)
    model = body.model.strip()
    base_url = body.base_url.strip()
    if not model:
        return JSONResponse({"ok": False, "error": "model_required"}, status_code=400)
    if base_url and not base_url.lower().startswith(("http://", "https://")):
        return JSONResponse({"ok": False, "error": "invalid_base_url"}, status_code=400)
    with router_write_context():
        set_config(model=model, api_key="", provider=provider, base_url=base_url, use_cache=provider == "anthropic")
    if local:
        llm_wardrobe.use_local(provider)
        data = llm_wardrobe.load()
        data["active"] = model
        llm_wardrobe.save(data)
    else:
        llm_wardrobe.use_cloud()
    return harness_models()


@router.post("/api/v1/harness/intents")
def harness_intents_set(body: IntentBody) -> Any:
    """Set (or clear) the owner's model for writer/persona, critic, planner, or the mentor."""
    from daedalus.env_lock import EnvLockedError
    from daedalus.harness import gateway as gw

    intent = gw.canonical_intent(body.intent)
    try:
        if intent == "mentor":
            from daedalus.mentor import apply_update
            from daedalus.model_router import parse_provider_model_tag

            if not body.model.strip():
                apply_update({"choice_id": "auto"})  # clearing the owner's choice = the mentor's automatic pick
            else:
                prov, mid = parse_provider_model_tag(body.model)
                if not (prov and mid):
                    raise ValueError("model must look like provider:model")
                apply_update({"provider": prov, "model": mid})
        else:
            gw.set_override(intent, body.model)
    except EnvLockedError as exc:
        return JSONResponse(exc.payload(), status_code=409)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    return harness_intents()


class ConfigBody(BaseModel):
    key: str = Field(..., min_length=1, max_length=80)
    value: Any = None


@router.get("/api/v1/harness/config")
def harness_config() -> dict[str, Any]:
    """Every owner setting as {effective, persisted, env_locked, env_value, default, source}. No secrets."""
    from daedalus.harness.config_view import config_payload

    return config_payload()


@router.post("/api/v1/harness/config")
def harness_config_set(body: ConfigBody) -> Any:
    """Change one setting. A setting pinned by an environment variable answers 409 and nothing is saved."""
    from daedalus.env_lock import EnvLockedError
    from daedalus.harness.config_view import config_payload, set_value

    try:
        row = set_value(body.key, body.value)
    except EnvLockedError as exc:
        return JSONResponse(exc.payload(), status_code=409)
    except KeyError:
        return JSONResponse({"ok": False, "error": "unknown_setting", "key": body.key}, status_code=404)
    except PermissionError:
        return JSONResponse({"ok": False, "error": "read_only", "key": body.key}, status_code=405)
    except (ValueError, TypeError) as exc:
        return JSONResponse({"ok": False, "error": "invalid_value", "detail": str(exc)[:160]}, status_code=400)
    return {"ok": True, "setting": row, **{k: v for k, v in config_payload().items() if k == "settings"}}


@router.get("/api/v1/harness/engines")
def harness_engines() -> dict[str, Any]:
    """Which game engine and tools this machine has for native play, how many sessions it may run, and the proof
    levels reachable here. Local paths are shown (owner's machine); no secrets."""
    import sys

    from daedalus.harness import proof
    from daedalus.harness.play_worker import engines
    from daedalus.harness.play_worker.backend import platform_cap
    from daedalus.harness.play_worker.manager import max_sessions

    try:
        status = engines.tools_status()
    except Exception as exc:  # discovery must never take the whole Settings panel down
        status = {"engine": {"ok": False, "error": "discovery_failed", "message": str(exc)[:160]}, "tools": {},
                  "ready": False}
    vision: dict[str, Any] = {"provider": "", "model": "", "verified": None}
    try:
        from daedalus import mentor_helm
        from daedalus.harness import perception

        choice = mentor_helm.get_choice()                      # public API: reports the model even without a key
        provider, model = str(choice.get("provider") or ""), str(choice.get("model") or "")
        if model:
            row = perception.verified_for(provider, model)
            vision = {"provider": provider, "model": model, "verified": (bool(row.get("ok")) if row else None)}
    except Exception:
        pass
    browser_found = False
    try:
        from daedalus import playground_browser

        browser_found = bool(playground_browser.chromium_executable())
    except Exception:
        pass
    return {
        "ok": True, "platform": sys.platform, "engine": status["engine"], "tools": status["tools"],
        "ready": status["ready"], "max_sessions": max_sessions(), "platform_cap": platform_cap(),
        "vision": vision,
        "proof_levels": proof.describe(engine_found=bool(status["engine"].get("ok")), browser_found=browser_found,
                                       native_ready=bool(status["ready"]), vision_verified=vision["verified"]),
    }


@router.get("/api/v1/harness/runs")
def harness_runs(slug: str = "", kind: str = "", state: str = "", live: bool = False, limit: int = 50) -> dict[str, Any]:
    """Recent runs from the ledger (every job system), newest first."""
    from daedalus.harness import runs

    rows = runs.list_runs(slug=slug.strip(), kind=kind.strip(), state=state.strip(), live_only=live, limit=limit)
    return {"ok": True, "count": len(rows), "runs": rows}


@router.get("/api/v1/harness/runs/{run_id}")
def harness_run(run_id: str) -> dict[str, Any]:
    from daedalus.harness import runs

    row = runs.get(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="run not found")
    return {"ok": True, "run": row, "events": runs.events(run_id, limit=200)}


def _sse(seq: int | None, event: str, data: Any) -> str:
    head = f"id: {seq}\n" if seq is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@router.get("/api/v1/harness/runs/{run_id}/events")
async def harness_run_events(run_id: str, after: int = 0, last_event_id: str | None = Header(default=None),
                             poll: float = 0.5, max_seconds: float = 0.0) -> StreamingResponse:
    """Server-sent events for one run, resumable: pass ``after=<seq>`` or the ``Last-Event-ID`` header.

    Replays every stored event after the cursor, then follows the run live. When the run is terminal and
    drained it sends ``event: end`` with the final state and closes. ``max_seconds`` bounds the stream (0 = none).
    """
    from daedalus.harness import runs

    if await asyncio.to_thread(runs.get, run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")
    cursor = max(int(after), 0)
    if last_event_id and str(last_event_id).strip().isdigit():
        cursor = max(cursor, int(last_event_id))
    poll = min(max(poll, 0.05), 5.0)

    async def gen():
        nonlocal cursor
        started = asyncio.get_event_loop().time()
        idle = 0.0
        while True:
            batch = await asyncio.to_thread(runs.events, run_id, after=cursor, limit=500)
            for ev in batch:
                cursor = ev["seq"]
                idle = 0.0
                yield _sse(ev["seq"], ev["type"], {"ts": ev["ts"], "payload": ev["payload"],
                                                   "artifact_path": ev["artifact_path"]})
            if batch:
                continue  # drain fully before deciding the stream is finished
            row = await asyncio.to_thread(runs.get, run_id)
            if row is None or not row["live"]:
                # One more read after a short grace: a writer may record its last event just after the state flip.
                await asyncio.sleep(0.05)
                tail = await asyncio.to_thread(runs.events, run_id, after=cursor, limit=500)
                if tail:
                    continue
                yield _sse(None, "end", {"state": row["state"] if row else "unknown", "last_seq": cursor})
                return
            if max_seconds and asyncio.get_event_loop().time() - started >= max_seconds:
                yield _sse(None, "end", {"state": row["state"], "last_seq": cursor, "timeout": True})
                return
            idle += poll
            if idle >= 15:
                idle = 0.0
                yield ": ping\n\n"
            await asyncio.sleep(poll)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---- A6.1b: workers, provider test, connections, MCP, durability, vision --------------------------------------
class TestProviderBody(BaseModel):
    slot: str = Field(default="master", max_length=40)


class GitTestBody(BaseModel):
    target: str = Field(..., max_length=10)


class McpBody(BaseModel):
    id: str = Field(..., min_length=1, max_length=80)
    enabled: bool


@router.get("/api/v1/harness/workers")
def harness_workers() -> dict[str, Any]:
    from daedalus.harness import connections

    return connections.workers_view()


@router.post("/api/v1/harness/test-provider")
async def harness_test_provider(body: TestProviderBody) -> Any:
    from daedalus.harness import connections

    return await connections.test_provider(body.slot)


@router.post("/api/v1/harness/connections/test")
async def harness_connection_test(body: GitTestBody) -> Any:
    from daedalus.harness import connections

    try:
        return await connections.test_git(body.target)
    except KeyError:
        return JSONResponse({"ok": False, "error": "unknown_target", "target": body.target}, status_code=404)


@router.get("/api/v1/harness/connections")
async def harness_connections() -> dict[str, Any]:
    from daedalus.harness import connections

    out: dict[str, Any] = {"ok": True}
    try:
        out["mcp"] = await asyncio.to_thread(connections.mcp_view)
    except Exception as exc:   # one broken section must not hide the other
        out["mcp"] = {"ok": False, "error": type(exc).__name__}
    try:
        out["durability"] = await connections.durability_view()
    except Exception as exc:
        out["durability"] = {"ok": False, "error": type(exc).__name__}
    return out


@router.post("/api/v1/harness/mcp")
def harness_mcp(body: McpBody) -> Any:
    from daedalus.harness import connections

    try:
        res = connections.set_mcp(body.id, body.enabled)
        return JSONResponse(res, status_code=409) if res.get("error") == "blocked_offline" else res
    except KeyError:
        return JSONResponse({"ok": False, "error": "unknown_server", "id": body.id}, status_code=404)


@router.post("/api/v1/harness/vision-probe")
async def harness_vision_probe() -> Any:
    from daedalus.harness import connections

    return await connections.vision_test()


# ---- A6.1c: kernel export and the upgrade-pool scan ---------------------------------------------------------
class KernelConfigBody(BaseModel):
    # no length limits here: a 422 would reach the browser as raw JSON, and validate() answers with a code
    repo: str = ""
    branch: str = ""
    paths: list[str] = Field(default_factory=list)


class KernelPushBody(BaseModel):
    confirm: bool = False
    force: bool = False


@router.get("/api/v1/harness/kernel")
def harness_kernel() -> dict[str, Any]:
    from daedalus.harness import maintenance

    return maintenance.kernel_view()


@router.post("/api/v1/harness/kernel/config")
def harness_kernel_config(body: KernelConfigBody) -> Any:
    from daedalus.harness import maintenance

    try:
        maintenance.save_kernel_config(body.repo, body.branch, body.paths)
    except maintenance.KernelConfigError as exc:
        return JSONResponse({"ok": False, "error": exc.code, **exc.extra}, status_code=409 if exc.code == "protected_branch" else 400)
    return maintenance.kernel_view()


@router.post("/api/v1/harness/kernel/push")
async def harness_kernel_push(body: KernelPushBody) -> Any:
    from daedalus.harness import maintenance

    res = await maintenance.kernel_push(confirm=body.confirm, force=body.force)
    if res.get("ok"):
        return res
    status = {"confirm_required": 400, "not_configured": 424, "non_fast_forward": 409, "protected_branch": 409,
              "no_files": 400, "push_failed": 502}.get(res.get("error", ""), 400)
    return JSONResponse(res, status_code=status)


@router.get("/api/v1/harness/upgrade-pool")
def harness_upgrade_pool() -> dict[str, Any]:
    from daedalus.harness import maintenance

    return maintenance.scan_status()


@router.post("/api/v1/harness/upgrade-pool/scan")
async def harness_upgrade_pool_scan() -> Any:
    from daedalus.harness import maintenance

    res = await maintenance.start_scan()
    return JSONResponse(res, status_code=409 if res.get("error") == "already_running" else 200)


# ---- A6.2a: Review actions (bulk permissions, blockers, details, approve-all) -------------------------------
class GrantBody(BaseModel):
    hours: float = 0.0
    confirm: bool = False


class AutoFixBody(BaseModel):
    id: str = ""
    all: bool = False                    # every open blocker: only ever on an explicit request, never by leaving the id out
    hours: float = 0.0
    confirm: bool = False


class RemediateBody(BaseModel):
    confirm_kind_change: bool = False


class ApproveBody(BaseModel):
    ids: list[str] = Field(default_factory=list)      # what the owner was shown; only these are approved
    confirm: bool = False


_REVIEW_STATUS = {"confirm_required": 400, "id_required": 400, "ids_required": 400, "unavailable": 503, "not_found": 404, "project_not_found": 404, "already_resolved": 409,
                  "no_project": 409, "not_infra": 409, "no_fix_available": 409, "remediate_failed": 502}


def _coded(res: dict[str, Any], table: dict[str, int]) -> Any:
    """A success as it is; a refusal as its code with the HTTP status the table gives it (400 when unlisted)."""
    if res.get("ok") or "error" not in res:
        return res
    return JSONResponse(res, status_code=table.get(str(res.get("error")), 400))


def _review_reply(res: dict[str, Any]) -> Any:
    return _coded(res, _REVIEW_STATUS)


@router.get("/api/v1/harness/review/permissions")
def harness_review_permissions() -> dict[str, Any]:
    from daedalus.harness import review_actions

    return review_actions.permissions_preview()


@router.post("/api/v1/harness/review/permissions/grant-all")
def harness_review_grant_all(body: GrantBody) -> Any:
    from daedalus.harness import review_actions

    return _review_reply(review_actions.grant_all(body.hours or None, body.confirm))


@router.post("/api/v1/harness/review/permissions/renew-expired")
def harness_review_renew(body: GrantBody) -> Any:
    from daedalus.harness import review_actions

    return _review_reply(review_actions.renew_expired(body.hours or None, body.confirm))


@router.get("/api/v1/harness/review/blockers")
def harness_review_blockers() -> dict[str, Any]:
    from daedalus.harness import review_actions

    return review_actions.blockers_preview()


@router.post("/api/v1/harness/review/blockers/grant-recommended")
def harness_review_grant_recommended(body: GrantBody) -> Any:
    from daedalus.harness import review_actions

    return _review_reply(review_actions.grant_recommended(body.hours or None, body.confirm))


@router.post("/api/v1/harness/review/blockers/auto-fix")
def harness_review_auto_fix(body: AutoFixBody) -> Any:
    from daedalus.harness import review_actions

    res = review_actions.auto_fix(body.id.strip(), body.hours or None, body.confirm, all_open=body.all)
    if body.all and res.get("error") == "no_fix_available":
        return res                       # a result, not a failure: "fixed 0, skipped N" is what the owner needs to see
    return _review_reply(res)


@router.get("/api/v1/harness/review/blockers/{blocker_id}/remediation")
async def harness_review_remediation(blocker_id: str) -> Any:
    from daedalus.harness import review_actions

    return _review_reply(await review_actions.remediation_preview(blocker_id))


@router.post("/api/v1/harness/review/blockers/{blocker_id}/remediate")
async def harness_review_remediate(blocker_id: str, body: RemediateBody) -> Any:
    from daedalus.harness import review_actions

    return _review_reply(await review_actions.remediate(blocker_id, body.confirm_kind_change))


@router.get("/api/v1/harness/review/proposals/{proposal_id}")
def harness_review_proposal(proposal_id: str) -> Any:
    from daedalus.harness import review_actions

    return _review_reply(review_actions.proposal_detail(proposal_id))


@router.get("/api/v1/harness/review/bundles/{bundle_id}")
async def harness_review_bundle(bundle_id: str) -> Any:
    from daedalus.harness import review_actions

    return _review_reply(await review_actions.bundle_detail(bundle_id))


@router.post("/api/v1/harness/review/proposals/approve-all")
def harness_review_approve_all(body: ApproveBody) -> Any:
    from daedalus.harness import review_actions

    return _review_reply(review_actions.approve_all_pending(body.ids, body.confirm))


# ---- A6.2b: planner and discoveries ------------------------------------------------------------------------
class ConfirmBody(BaseModel):
    confirm: bool = False


class PlanBody(BaseModel):
    title: str = ""
    description: str = ""


class StepBody(BaseModel):
    title: str = ""
    description: str = ""
    depends_on: list[str] = Field(default_factory=list)
    max_retries: Any = 3
    confirm: bool = False


class MarkBody(BaseModel):
    status: str = ""
    confirm: bool = False


class RunStepBody(BaseModel):
    confirm: bool = False
    grant_recommended: bool = False


_PLANNER_STATUS = {"confirm_required": 400, "title_required": 400, "too_long": 400, "too_many_steps": 400, "status_not_allowed": 400,
                   "bad_dependency": 400, "not_found": 404, "bad_state": 409, "use_approve": 409, "already_running": 409,
                   "plan_not_active": 409, "dependency_open": 409}


def _planner_reply(res: dict[str, Any]) -> Any:
    return _coded(res, _PLANNER_STATUS)


@router.post("/api/v1/harness/review/planner/plans")
async def harness_plan_create(body: PlanBody) -> Any:
    from daedalus.harness import planner_actions

    return _planner_reply(await planner_actions.create_manual(body.title, body.description))


@router.post("/api/v1/harness/review/planner/plans/{plan_id}/steps")
async def harness_plan_add_step(plan_id: str, body: StepBody) -> Any:
    from daedalus.harness import planner_actions

    return _planner_reply(await planner_actions.add_step(plan_id, body.title, body.description, body.depends_on, body.max_retries, body.confirm))


@router.post("/api/v1/harness/review/planner/plans/{plan_id}/steps/{step_id}/mark")
async def harness_plan_mark(plan_id: str, step_id: str, body: MarkBody) -> Any:
    from daedalus.harness import planner_actions

    return _planner_reply(await planner_actions.mark(plan_id, step_id, body.status, body.confirm))


@router.post("/api/v1/harness/review/planner/plans/{plan_id}/steps/{step_id}/override")
async def harness_plan_override(plan_id: str, step_id: str, body: ConfirmBody) -> Any:
    from daedalus.harness import planner_actions

    return _planner_reply(await planner_actions.override(plan_id, step_id, body.confirm))


@router.post("/api/v1/harness/review/planner/plans/{plan_id}/steps/{step_id}/run")
async def harness_plan_run_step(plan_id: str, step_id: str, body: RunStepBody) -> Any:
    from daedalus.harness import planner_actions

    return _planner_reply(await planner_actions.run_background(plan_id, step_id, body.confirm, body.grant_recommended))


@router.post("/api/v1/harness/review/planner/plans/{plan_id}/pause")
async def harness_plan_pause(plan_id: str) -> Any:
    from daedalus.harness import planner_actions

    return _planner_reply(await planner_actions.pause(plan_id))


@router.post("/api/v1/harness/review/planner/plans/{plan_id}/resume")
async def harness_plan_resume(plan_id: str, body: ConfirmBody) -> Any:
    from daedalus.harness import planner_actions

    return _planner_reply(await planner_actions.resume(plan_id, body.confirm))


@router.get("/api/v1/harness/review/planner/plans/{plan_id}/progress")
async def harness_plan_progress(plan_id: str) -> Any:
    from daedalus.harness import planner_actions

    return _planner_reply(await planner_actions.progress(plan_id))


@router.get("/api/v1/harness/review/discoveries/regenerate-status")
def harness_discovery_regenerate_status() -> dict[str, Any]:
    from daedalus.harness import planner_actions

    return planner_actions.regenerate_status()


@router.post("/api/v1/harness/review/discoveries/{discovery_id}/delete")
def harness_discovery_delete(discovery_id: str, body: ConfirmBody) -> Any:
    from daedalus.harness import planner_actions

    return _planner_reply(planner_actions.delete_discovery(discovery_id, body.confirm))


@router.post("/api/v1/harness/review/discoveries/{discovery_id}/regenerate")
async def harness_discovery_regenerate(discovery_id: str, body: ConfirmBody) -> Any:
    from daedalus.harness import planner_actions

    return _planner_reply(await planner_actions.regenerate_start(discovery_id, body.confirm))


# ---- A6.2c: Review → Memory -------------------------------------------------------------------------------
class ModeBody(BaseModel):
    mode: str = ""


class TaskBody(BaseModel):
    title: str = ""
    summary: str = ""


class SearchBody(BaseModel):
    query: str = ""
    limit: int = 5


_MEMORY_STATUS = {"confirm_required": 400, "title_required": 400, "too_long": 400, "query_required": 400, "bad_mode": 400,
                  "bad_status": 400, "not_found": 404, "unavailable": 503, "already_running": 409, "search_failed": 502,
                  "delete_failed": 500}


def _memory_reply(res: dict[str, Any]) -> Any:
    return _coded(res, _MEMORY_STATUS)


_M = "/api/v1/harness/memory"


@router.get(_M)
async def harness_memory_overview() -> dict[str, Any]:
    from daedalus.harness import memory_actions

    return await memory_actions.overview()


@router.get(_M + "/episodic")
def harness_memory_episodic(limit: int = 50) -> dict[str, Any]:
    from daedalus.harness import memory_actions

    return memory_actions.episodic(limit)


@router.post(_M + "/episodic/{fact_id}/delete")
def harness_memory_delete_fact(fact_id: str, body: ConfirmBody) -> Any:
    from daedalus.harness import memory_actions

    return _memory_reply(memory_actions.delete_fact(fact_id, body.confirm))


@router.post(_M + "/navigator/reset")
def harness_memory_navigator_reset(body: ConfirmBody) -> Any:
    from daedalus.harness import memory_actions

    return _memory_reply(memory_actions.reset_navigator(body.confirm))


@router.post(_M + "/mode")
def harness_memory_mode(body: ModeBody) -> Any:
    from daedalus.harness import memory_actions

    return _memory_reply(memory_actions.set_mode(body.mode))


@router.post(_M + "/progress/clear")
def harness_memory_progress_clear() -> dict[str, Any]:
    from daedalus.harness import memory_actions

    return memory_actions.clear_progress()


@router.get(_M + "/tasks")
def harness_memory_tasks(status: str = "", limit: int = 50) -> Any:
    from daedalus.harness import memory_actions

    return _memory_reply(memory_actions.tasks(status, limit))


@router.post(_M + "/tasks")
def harness_memory_task_create(body: TaskBody) -> Any:
    from daedalus.harness import memory_actions

    return _memory_reply(memory_actions.create_task(body.title, body.summary))


@router.get(_M + "/sessions")
def harness_memory_sessions(limit: int = 30) -> dict[str, Any]:
    from daedalus.harness import memory_actions

    return memory_actions.sessions(limit)


@router.get(_M + "/sessions/{session_id}")
def harness_memory_session(session_id: str, limit: int = 100) -> Any:
    from daedalus.harness import memory_actions

    return _memory_reply(memory_actions.session_turns(session_id, limit))


@router.post(_M + "/search/{kind}")
async def harness_memory_search(kind: str, body: SearchBody) -> Any:
    from daedalus.harness import memory_actions

    if kind not in ("web", "github"):
        return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
    return _memory_reply(await memory_actions.search(kind, body.query, body.limit))


@router.post(_M + "/jobs/{name}")
async def harness_memory_job(name: str, body: ConfirmBody) -> Any:
    from daedalus.harness import memory_actions

    return _memory_reply(await memory_actions.start_job(name, body.confirm))


@router.get(_M + "/jobs")
def harness_memory_jobs() -> dict[str, Any]:
    from daedalus.harness import memory_actions

    return {"ok": True, **memory_actions.jobs_status()}


# ---- A6.3a: Build run controls and Full Auto M0→M8 ----------------------------------------------------------
class PlanCreateBody(BaseModel):
    description: str = ""
    ultra: bool = False
    studio: bool = False
    studio_preset: str = ""
    confirm: bool = False


_BUILD_STATUS = {"confirm_required": 400, "description_required": 400, "too_long": 400, "not_found": 404, "build_running": 409,
                 "already_running": 409, "not_running": 409, "not_paused": 409, "no_plan": 409, "plan_failed": 502,
                 "not_game_studio": 409}
_B = "/api/v1/harness/build/{slug}"


def _build_reply(res: dict[str, Any]) -> Any:
    return _coded(res, _BUILD_STATUS)


@router.get(_B + "/controls")
async def harness_build_controls(slug: str) -> Any:
    from daedalus.harness import build_controls

    return _build_reply(await build_controls.state(slug))


@router.post(_B + "/autopilot/pause")
async def harness_build_pause(slug: str) -> Any:
    from daedalus.harness import build_controls

    return _build_reply(await build_controls.pause(slug))


@router.post(_B + "/autopilot/resume")
async def harness_build_resume(slug: str) -> Any:
    from daedalus.harness import build_controls

    return _build_reply(await build_controls.resume(slug))


@router.post(_B + "/plan/clear")
async def harness_build_plan_clear(slug: str, body: ConfirmBody) -> Any:
    from daedalus.harness import build_controls

    return _build_reply(await build_controls.clear_plan(slug, body.confirm))


@router.post(_B + "/plan/create")
async def harness_build_plan_create(slug: str, body: PlanCreateBody) -> Any:
    from daedalus.harness import build_controls

    return _build_reply(await build_controls.create_plan(slug, body.description, body.ultra, body.studio, body.studio_preset, body.confirm))


@router.post(_B + "/plan/expand")
async def harness_build_plan_expand(slug: str, body: ConfirmBody) -> Any:
    from daedalus.harness import build_controls

    return _build_reply(await build_controls.expand_plan(slug, body.confirm))


@router.get(_B + "/full-auto")
def harness_full_auto_status(slug: str, after: int = 0, tail: int = 0) -> dict[str, Any]:
    from daedalus.harness import full_auto

    return full_auto.status(slug, after, tail)


@router.post(_B + "/full-auto")
async def harness_full_auto_start(slug: str, body: ConfirmBody) -> Any:
    from daedalus.harness import full_auto

    return _build_reply(await full_auto.start(slug, body.confirm))


@router.post(_B + "/full-auto/stop")
async def harness_full_auto_stop(slug: str) -> Any:
    from daedalus.harness import full_auto

    return _build_reply(await full_auto.stop(slug))


# ── A7: ship requests (the brain asks to publish or push; the owner answers here, from the Studio) ─────────────────

_SHIP_STATUS = {"not_found": 404, "already_resolved": 409, "bad_outcome": 400}


class ShipResolveBody(BaseModel):
    outcome: str = ""


@router.get("/api/v1/harness/ship-requests")
async def ship_requests(slug: str = "", status: str = "") -> dict[str, Any]:
    from daedalus.harness.brain_tools import list_ship_requests

    return {"ok": True, "requests": list_ship_requests(slug=slug, status=status)}


@router.post("/api/v1/harness/ship-requests/{req_id}/resolve")
async def ship_request_resolve(req_id: str, body: ShipResolveBody) -> Any:
    from daedalus.harness.brain_tools import resolve_ship_request

    return _coded(resolve_ship_request(req_id, (body.outcome or "").strip().lower()), _SHIP_STATUS)
