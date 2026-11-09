"""Full Auto M0→M8 as a server-side run (the classic Game Studio drove this loop from the browser tab).

One milestone at a time: build its pending stages with a Guided job (stages auto-approved), Verify it, and advance only
when Verify passed. It never forces an advance and never uses a waiver: those stay the owner's decisions in the Build
panel. It stops, and says why, instead of looping forever the way the old page did:
* ``blocked``  — no plan, gates not passed, or a milestone that does not verify (the owner looks, fixes, or waives);
* ``failed``   — a Guided job or a step raised;
* ``stopped``  — the owner pressed Stop (the running Guided job is cancelled too);
* ``succeeded``— every milestone is complete.
It refuses to start while another build owns the project. The run and its steps are in the run ledger (kind ``build``)."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

log = logging.getLogger(__name__)

MAX_STEPS = 24                  # milestone iterations per run (9 milestones with room for re-verifies); then it stops honestly
POLL_SECONDS = 1.0
CHECKPOINT_STALL_SECONDS = 120  # an auto-approved checkpoint that does not move on is a stuck job, not progress
EVENTS_MAX = 300


class _Run:
    def __init__(self, slug: str) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.slug = slug
        self.state = "running"
        self.reason = ""
        self.milestone = ""
        self.guided_job = ""
        self.started = time.time()
        self.ended: float | None = None
        self.events: list[dict[str, Any]] = []
        self.stop_requested = False
        self.task: asyncio.Task | None = None
        self.ledger = ""

    def say(self, type: str, **data: Any) -> None:
        ev = {"seq": len(self.events) + 1, "ts": time.time(), "type": type, **data}
        if len(self.events) < EVENTS_MAX:
            self.events.append(ev)
        from . import runs

        runs.safe_event(self.ledger, type, data) if self.ledger else None

    def snapshot(self, after: int = 0) -> dict[str, Any]:
        return {"id": self.id, "slug": self.slug, "state": self.state, "reason": self.reason, "milestone": self.milestone,
                "guided_job": self.guided_job, "started": self.started, "ended": self.ended,
                "events": [e for e in self.events if e["seq"] > after]}


_runs_by_slug: dict[str, _Run] = {}


def _live(run: _Run | None) -> bool:
    return bool(run and run.state in ("running", "stopping"))


def status(slug: str, after: int = 0) -> dict[str, Any]:
    run = _runs_by_slug.get(slug)
    return {"ok": True, "run": run.snapshot(after) if run else None}


def _other_owner(slug: str) -> str:
    """Another build on this project (a live Guided or Autopilot job): Full Auto must not start next to it."""
    from .. import autopilot_jobs as aj
    from .. import guided_jobs as gj

    g = gj.get_active(slug)
    if g and g.status in ("running", "checkpoint"):
        return "guided"
    a = aj.get_active(slug)
    if a and a.status == "running":
        return "autopilot"
    return ""


async def start(slug: str, confirm: Any) -> dict[str, Any]:
    from .. import playground as pg

    if confirm is not True:
        return {"ok": False, "error": "confirm_required"}
    if not await pg.get_project(slug):
        return {"ok": False, "error": "not_found"}
    if _live(_runs_by_slug.get(slug)):
        return {"ok": False, "error": "already_running"}
    other = _other_owner(slug)
    if other:
        return {"ok": False, "error": "build_running", "owner": other}
    from .. import game_studio

    if not game_studio.is_game_studio_slug(slug):
        return {"ok": False, "error": "not_game_studio"}
    run = _Run(slug)
    from . import runs

    run.ledger = runs.safe_open("build", slug=slug, ref=f"full_auto:{run.id}", summary="Full Auto M0→M8") or ""
    _runs_by_slug[slug] = run
    run.task = asyncio.get_running_loop().create_task(_drive(run))
    return {"ok": True, "run": run.snapshot()}


async def stop(slug: str) -> dict[str, Any]:
    from .. import guided_jobs as gj

    run = _runs_by_slug.get(slug)
    if not _live(run):
        return {"ok": False, "error": "not_running"}
    run.stop_requested = True
    run.state = "stopping"
    run.say("stop_requested")
    if run.guided_job:
        await gj.stop_active(slug)
    return {"ok": True, "state": run.state}


def _end(run: _Run, state: str, reason: str = "") -> None:
    from . import runs

    run.state, run.reason, run.ended = state, reason, time.time()
    run.say("ended", state=state, reason=reason)
    ledger_state = {"succeeded": "succeeded", "stopped": "stopped", "failed": "failed", "blocked": "stopped"}[state]
    if run.ledger:
        runs.safe_state(run.ledger, ledger_state, reason=f"{state}: {reason}" if reason else state)


async def _wait_guided(run: _Run, job_id: str) -> str:
    """Wait for the Guided job to end; returns its final status."""
    from .. import guided_jobs as gj

    stalled_since: float | None = None
    while True:
        job = gj.get_job(job_id)
        if job is None:
            return "lost"
        if job.status not in ("running", "checkpoint"):
            return job.status
        if job.status == "checkpoint":
            stalled_since = stalled_since or time.time()
            if time.time() - stalled_since > CHECKPOINT_STALL_SECONDS:
                return "stuck"
        else:
            stalled_since = None
        await asyncio.sleep(POLL_SECONDS)


async def _drive(run: _Run) -> None:
    from .. import game_studio
    from ..game_studio_verify import run_milestone_verify

    try:
        for _ in range(MAX_STEPS):
            if run.stop_requested:
                return _end(run, "stopped")
            ctx = await game_studio.get_workspace_context(run.slug)
            if ctx.get("project_complete") or (ctx.get("milestone_summary") or {}).get("project_complete"):
                return _end(run, "succeeded")
            plan = ctx.get("plan") or {}
            if not (plan.get("stages") or []):
                return _end(run, "blocked", "no_plan")
            if not ctx.get("can_guided"):
                return _end(run, "blocked", "gates_not_passed")
            cur = ctx.get("current_milestone")
            cur = cur.get("id") if isinstance(cur, dict) else str(cur or "")
            run.milestone = cur
            pending = any(isinstance(s, dict) and (s.get("milestone_id") or "") == cur and s.get("status") != "done"
                          for s in plan.get("stages") or [])
            if pending:
                started = await game_studio.start_milestone_guided(run.slug, cur, auto_approve=True)
                run.guided_job = started.get("job_id") or ""
                run.say("milestone_build", milestone=cur, guided_job=run.guided_job)
                final = await _wait_guided(run, run.guided_job)
                run.guided_job = ""
                if run.stop_requested:
                    return _end(run, "stopped")
                if final != "done":
                    return _end(run, "failed", f"guided_{final}")
            run.say("verify", milestone=cur)
            verdict = await run_milestone_verify(run.slug, cur)
            run.say("verified", milestone=cur, ok=bool(verdict.get("ok")), runtime_ok=bool(verdict.get("runtime_ok")))
            if run.stop_requested:
                return _end(run, "stopped")
            if not verdict.get("ok"):
                return _end(run, "blocked", "verify_failed")             # waivers and fixes are the owner's call
            adv = await game_studio.advance_project_milestone(run.slug)  # never force, never a waiver
            run.say("advanced", previous=adv.get("previous"), current=adv.get("current_milestone"))
        return _end(run, "blocked", "step_limit")
    except asyncio.CancelledError:
        _end(run, "stopped", "cancelled")
        raise
    except Exception as exc:
        log.exception("full auto failed")
        _end(run, "failed", f"{type(exc).__name__}: {str(exc)[:200]}")
