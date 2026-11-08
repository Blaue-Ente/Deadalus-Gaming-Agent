"""Harness Core HTTP API (read-only in A1): the capability registry and the no-loss parity table."""
from __future__ import annotations

from typing import Any

import asyncio
import json

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


@router.get("/api/v1/harness/intents")
def harness_intents() -> dict[str, Any]:
    """Which model serves each LLM intent, and which of them the owner may change."""
    from daedalus.harness import gateway as gw

    rows: list[dict[str, Any]] = []
    overrides = gw.get_overrides()
    for intent in gw.INTENTS:
        sel = gw._SELECTOR.get(intent, intent)
        row: dict[str, Any] = {"intent": intent, "selectable": sel in gw.OVERRIDABLE or intent == "mentor"}
        if intent == "mentor":
            # The mentor keeps its own choice (Studio header); this view reports it and POST delegates to it.
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
            row.update({"owner_choice": overrides.get(sel), "default": gw.INTENT_DEFAULTS.get(intent),
                        "resolved": resolved})
        rows.append(row)
    return {"ok": True, "env_locked": gw.model_locked_by_env(), "intents": rows}


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
    hours: float = 0.0
    confirm: bool = False


class RemediateBody(BaseModel):
    confirm_kind_change: bool = False


class ConfirmBody(BaseModel):
    confirm: bool = False


_REVIEW_STATUS = {"confirm_required": 400, "not_found": 404, "project_not_found": 404, "already_resolved": 409,
                  "no_project": 409, "not_infra": 409, "no_fix_available": 409, "remediate_failed": 502}


def _review_reply(res: dict[str, Any]) -> Any:
    if res.get("ok") or "error" not in res:
        return res
    return JSONResponse(res, status_code=_REVIEW_STATUS.get(str(res.get("error")), 400))


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

    return _review_reply(review_actions.auto_fix(body.id.strip(), body.hours or None, body.confirm))


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
def harness_review_approve_all(body: ConfirmBody) -> Any:
    from daedalus.harness import review_actions

    res = review_actions.approve_all_pending(body.confirm)
    return JSONResponse(res, status_code=400) if res.get("error") == "confirm_required" else res
