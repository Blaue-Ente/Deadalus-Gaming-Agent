"""Review: planner and discovery actions.

``planner.run_planner_loop`` would run the steps of an ACTIVE plan on its own, but nothing in this app starts it today
(``loop_running`` reports the truth at runtime, so the Studio never claims otherwise). The rules below hold either way,
so wiring the loop later cannot widen what a click does:
* a manual plan is created as a DRAFT (the old route made it active at once, so every step added to it went straight into
  the queue with no approval); the existing Approve flow is what activates a plan;
* anything that queues, re-queues or claims work (resume, add a step to an active plan, mark pending/passed, manual
  override, run in background) needs an explicit ``confirm``;
* run-in-background runs a SPECIFIC plan step, and the only permissions it can grant are the server's own recommended
  set (never a list from the request);
* a model-driven action (run a step, regenerate a plan) is single-flight and reports how it ended.
Answers are codes and numbers so the Studio can say them in the owner's language."""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

TITLE_MAX, DESC_MAX, STEPS_MAX = 200, 4000, 60
MARK_STATUSES = ("pending", "passed", "failed", "blocked")      # states an owner can set by hand; the rest are the loop's
RETRY_MAX = 5


def loop_running() -> bool:
    """True only if a ``run_planner_loop`` task is alive in this process right now (nothing in this app starts one)."""
    try:
        from ..planner import run_planner_loop

        code = run_planner_loop.__code__
        return any(getattr(t.get_coro(), "cr_code", None) is code for t in asyncio.all_tasks() if not t.done())
    except RuntimeError:                                            # no running loop
        return False


def _need(confirm: Any) -> dict[str, Any] | None:
    return None if confirm is True else {"ok": False, "error": "confirm_required"}


def _clean(title: Any, description: Any) -> tuple[str, str] | dict[str, Any]:
    t, d = str(title or "").strip(), str(description or "").strip()
    if not t:
        return {"ok": False, "error": "title_required"}
    if len(t) > TITLE_MAX or len(d) > DESC_MAX:
        return {"ok": False, "error": "too_long"}
    return t, d


async def _plan(plan_id: str) -> dict[str, Any] | None:
    from ..planner import get_plan

    return await get_plan(plan_id)


def _step(plan: dict[str, Any], step_id: str) -> dict[str, Any] | None:
    return next((s for s in plan.get("steps") or [] if s.get("id") == step_id), None)


# ---- plans and steps -------------------------------------------------------------------------------------
async def create_manual(title: Any, description: Any) -> dict[str, Any]:
    from ..planner import create_plan

    cleaned = _clean(title, description)
    if isinstance(cleaned, dict):
        return cleaned
    res = await create_plan(cleaned[0], cleaned[1], status="draft")           # never active: nothing runs until approved
    return {"ok": True, "plan_id": res["plan_id"], "status": "draft"}


async def add_step(plan_id: str, title: Any, description: Any, depends_on: Any, max_retries: Any, confirm: Any) -> dict[str, Any]:
    from ..planner import add_step as _add

    plan = await _plan(plan_id)
    if not plan:
        return {"ok": False, "error": "not_found"}
    cleaned = _clean(title, description)
    if isinstance(cleaned, dict):
        return cleaned
    steps = plan.get("steps") or []
    if len(steps) >= STEPS_MAX:
        return {"ok": False, "error": "too_many_steps"}
    known = {s.get("id") for s in steps}
    deps = [str(x).strip() for x in (depends_on if isinstance(depends_on, list) else []) if str(x).strip()]
    for dep in deps:
        if dep not in known:
            return {"ok": False, "error": "bad_dependency", "step": dep}
    try:
        retries = max(0, min(RETRY_MAX, int(max_retries)))
    except (TypeError, ValueError):
        retries = 3
    if plan.get("status") == "active" and (refused := _need(confirm)):        # a step of an active plan goes into the queue
        return {**refused, "queues_work": True}
    res = await _add(plan_id, cleaned[0], cleaned[1], depends_on=deps or None, max_retries=retries)
    return {"ok": True, "step_id": res["step_id"], "plan_id": plan_id}


async def mark(plan_id: str, step_id: str, status: Any, confirm: Any) -> dict[str, Any]:
    from ..planner import mark_step

    status = str(status or "")
    if status not in MARK_STATUSES:
        return {"ok": False, "error": "status_not_allowed"}
    plan = await _plan(plan_id)
    if not plan or not _step(plan, step_id):
        return {"ok": False, "error": "not_found"}
    # "passed" is the owner's claim that it is done (nothing was checked); "pending" on an active plan queues it again
    if (status == "passed" or (status == "pending" and plan.get("status") == "active")) and (refused := _need(confirm)):
        return refused
    await mark_step(step_id, status)
    return {"ok": True, "step_id": step_id, "status": status}


async def override(plan_id: str, step_id: str, confirm: Any) -> dict[str, Any]:
    from ..planner import manual_override_step

    if (refused := _need(confirm)):
        return refused
    plan = await _plan(plan_id)
    if not plan or not _step(plan, step_id):
        return {"ok": False, "error": "not_found"}
    await manual_override_step(step_id, approved_by="owner")
    return {"ok": True, "step_id": step_id, "status": "passed", "manual_override": True}


async def pause(plan_id: str) -> dict[str, Any]:
    from ..planner import update_plan_status

    plan = await _plan(plan_id)
    if not plan:
        return {"ok": False, "error": "not_found"}
    if plan.get("status") != "active":
        return {"ok": False, "error": "bad_state", "status": plan.get("status")}
    await update_plan_status(plan_id, "paused")
    return {"ok": True, "status": "paused"}


async def resume(plan_id: str, confirm: Any) -> dict[str, Any]:
    """Resume makes the plan active again (its pending steps are queued), so it needs a confirmation. A draft is not resumed: the existing
    Approve flow is what activates it (and creates its tasks)."""
    from ..planner import update_plan_status

    plan = await _plan(plan_id)
    if not plan:
        return {"ok": False, "error": "not_found"}
    if plan.get("status") == "draft":
        return {"ok": False, "error": "use_approve"}
    if plan.get("status") != "paused":
        return {"ok": False, "error": "bad_state", "status": plan.get("status")}
    if (refused := _need(confirm)):
        return refused
    await update_plan_status(plan_id, "active")
    return {"ok": True, "status": "active"}


def plan_progress(plan: dict[str, Any]) -> dict[str, Any]:
    """The progress read (shared with the old page's route)."""
    import json

    from ..planner import CHECKPOINT_KEYS

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
            "id": s["id"], "step_order": s["step_order"], "title": s["title"], "status": s["status"],
            "proposal_id": s.get("proposal_id"), "progress_note": s.get("progress_note"), "diagnostics": s.get("diagnostics"),
            "repair_attempts": s.get("repair_attempts") or 0, "manual_override": bool(s.get("manual_override")),
            "checkpoints": {"passed": passed, "passed_count": len(passed), "total": len(CHECKPOINT_KEYS), "all": CHECKPOINT_KEYS},
            "error_msg": s.get("error_msg"), "bg_running": s["id"] in running_steps(),
        })
    return {"plan_id": plan.get("id"), "plan_status": plan.get("status"), "title": plan.get("title"), "steps": steps_out,
            "loop_running": loop_running()}


async def progress(plan_id: str) -> dict[str, Any]:
    plan = await _plan(plan_id)
    if not plan:
        return {"ok": False, "error": "not_found"}
    return {"ok": True, **plan_progress(plan)}


# ---- run a step in the background ------------------------------------------------------------------------
_bg: dict[str, asyncio.Task] = {}                                  # step id -> its running task (kept: a dropped task can be collected)


def running_steps() -> set[str]:
    return {sid for sid, t in _bg.items() if not t.done()}


async def execute_task(agent: Any, task_msg: str, session_id: str, task_id: Any = None) -> None:
    """One background agent turn; the result (or the failure) goes to the Outbox. Shared with the old page's route."""
    import logging
    import traceback

    from .. import outbox

    started = time.time()
    try:
        result = await agent.chat(task_msg, session_id=session_id)
        elapsed = round(time.time() - started, 1)
        reply = result.get("reply", "") if isinstance(result, dict) else str(result)
        ctx = result.get("context", {}) if isinstance(result, dict) else {}
        tc = ctx.get("tool_calls") or []
        tools_summary = ", ".join(sorted(set(c.get("tool", "?") for c in tc))) if tc else "-"
        outbox.push_outbox(
            type="message", title=f"Commit: {task_msg[:55]}",
            body=f"**Session:** {session_id}\n**Elapsed:** {elapsed}s  |  **Tools:** {tools_summary}\n\n" + reply[:1800],
            category="bg_commit", session_id=session_id,
            payload={"task": task_msg, "elapsed": elapsed, "tools": tools_summary, "task_id": task_id})
        if task_id:
            try:
                from .. import continuity
                continuity.update_task(task_id, status="done", summary=f"[bg] {reply[:300]}")
            except Exception:
                pass
    except Exception as exc:
        logging.getLogger(__name__).exception("background task failed")
        outbox.push_outbox(
            type="message", title=f"Error: {task_msg[:50]}",
            body=f"**Session:** {session_id}\n\n**Error:**\n```\n{traceback.format_exc()[-800:]}\n```",
            category="bg_error", session_id=session_id, payload={"task": task_msg, "error": str(exc), "task_id": task_id})
        if task_id:
            try:
                from .. import continuity
                continuity.update_task(task_id, status="rework", summary=f"[bg error] {exc}")
            except Exception:
                pass


def recommended_permissions() -> list[str]:
    from .. import blockers

    return list(blockers.blockers_summary().get("grant_all_recommended") or [])


async def run_background(plan_id: str, step_id: str, confirm: Any, grant_recommended: Any) -> dict[str, Any]:
    """Run ONE plan step now, as an agent turn in the background. The task text is built here from the step, and the only
    permissions that can be granted for it are the server's recommended set (8 hours), and only when asked."""
    from .. import outbox

    if (refused := _need(confirm)):
        return refused
    plan = await _plan(plan_id)
    step = _step(plan, step_id) if plan else None
    if not plan or not step:
        return {"ok": False, "error": "not_found"}
    for done in [sid for sid, t in _bg.items() if t.done()]:                 # finished tasks are not kept around
        del _bg[done]
    if step_id in running_steps():                                          # only a task alive here counts: a DB "running" left by a crash must not block
        return {"ok": False, "error": "already_running"}
    if plan.get("status") != "active":
        return {"ok": False, "error": "plan_not_active", "status": plan.get("status")}
    states = {s.get("id"): s.get("status") for s in plan.get("steps") or []}
    for dep in [d.strip() for d in str(step.get("depends_on") or "").split(",") if d.strip()]:
        if states.get(dep) not in ("passed", "done"):
            return {"ok": False, "error": "dependency_open", "step": dep}
    from ..agent import get_daedalus

    agent = get_daedalus()
    granted: list[str] = []
    if grant_recommended is True:
        for perm in recommended_permissions():
            outbox.grant_permission(perm_class=perm, hours=8.0, granted_by="owner_bg_task")
            granted.append(perm)
    task_msg = (f"Plan: {plan.get('title')}\nStep #{step.get('step_order')}: {step.get('title')}\n"
                f"{step.get('description') or ''}\nStep ID: {step_id}").strip()
    session_id = f"bg-{uuid.uuid4().hex[:8]}"
    _bg[step_id] = asyncio.get_running_loop().create_task(_run_step(agent, task_msg, session_id, step_id))
    return {"ok": True, "session_id": session_id, "granted": granted}


async def _note(step_id: str, text: str) -> None:
    from ..planner import update_step_progress

    try:
        await update_step_progress(step_id, text)
    except Exception:
        pass                                                         # a note is information, never a reason to fail the run


async def _run_step(agent: Any, task_msg: str, session_id: str, step_id: str) -> None:
    """The background turn plus honest notes on the step. The step's status is NOT changed: the agent turn is not a verification, so
    passing the step stays the owner's decision (Mark / Override)."""
    await _note(step_id, f"background run started ({session_id})")
    try:
        await execute_task(agent, task_msg, session_id)
        await _note(step_id, f"background run ended ({session_id}); result is in the Outbox, step status unchanged")
    except asyncio.CancelledError:
        await _note(step_id, f"background run cancelled ({session_id})")
        raise


# ---- discoveries -----------------------------------------------------------------------------------------
_regen: dict[str, asyncio.Task] = {}
_regen_last: dict[str, dict[str, Any]] = {}


def _discovery(discovery_id: str) -> dict[str, Any] | None:
    from ..agent import get_daedalus

    res = get_daedalus().memory.discoveries_col.get(ids=[discovery_id])
    if not res.get("ids"):
        return None
    return {"id": discovery_id, "content": (res.get("documents") or [""])[0], "metadata": (res.get("metadatas") or [{}])[0] or {}}


def delete_discovery(discovery_id: str, confirm: Any) -> dict[str, Any]:
    from ..agent import get_daedalus

    if (refused := _need(confirm)):
        return refused
    if _discovery(discovery_id) is None:
        return {"ok": False, "error": "not_found"}
    task = _regen.pop(discovery_id, None)
    if task and not task.done():
        task.cancel()                                               # a plan for a deleted discovery is not wanted
    if not get_daedalus().memory.delete_discovery(discovery_id):
        return {"ok": False, "error": "not_found"}
    _regen_last.pop(discovery_id, None)
    return {"ok": True, "discovery_id": discovery_id}


async def regenerate_start(discovery_id: str, confirm: Any) -> dict[str, Any]:
    """Make a new plan from a discovery. It is a model-driven turn, so: confirmed, one at a time per discovery, in the
    background (the answer is read with ``regenerate_status``)."""
    if (refused := _need(confirm)):
        return refused
    for done in [i for i, t in _regen.items() if t.done()]:
        del _regen[done]
    task = _regen.get(discovery_id)
    if task and not task.done():
        return {"ok": False, "error": "already_running"}
    discovery = _discovery(discovery_id)
    if discovery is None:
        return {"ok": False, "error": "not_found"}
    from ..agent import get_daedalus
    from ..plans import generate_plan

    agent = get_daedalus()
    registry: dict = {}      # R5: the old ADN mesh service registry is retired; plans match no external services

    async def run() -> None:
        started = time.time()
        try:
            plan = await generate_plan(discovery, registry, agent, save_on_error=False)   # a failed model call saves no junk plan
            if discovery_id not in _regen:                           # deleted meanwhile: record nothing
                return
            _regen_last[discovery_id] = {"ok": True, "plan_id": plan["id"], "finished_at": time.time(), "seconds": round(time.time() - started, 1)}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if discovery_id in _regen:
                _regen_last[discovery_id] = {"ok": False, "error": type(exc).__name__, "finished_at": time.time()}

    _regen[discovery_id] = asyncio.get_running_loop().create_task(run())
    _regen_last.pop(discovery_id, None)
    return {"ok": True, "started": True, "discovery_id": discovery_id}


def regenerate_status() -> dict[str, Any]:
    running = sorted(i for i, t in _regen.items() if not t.done())
    return {"ok": True, "running": running, "last": dict(_regen_last)}
