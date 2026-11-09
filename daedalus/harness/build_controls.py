"""Build panel run controls: autopilot pause/resume, resume info, and the saved build plan (clear, create, expand).

* Pause is cooperative: the autopilot stops before its next step (a model call already under way finishes first).
* The saved plan is what a build reads, so it is not cleared, replaced or expanded while a build owns the project.
* Clearing or replacing the plan asks first; creating one is a model turn. A failed model call saves NOTHING (the old route
  saved a one-stage stand-in plan and answered ok, which looked like a real plan).
Answers are codes and numbers so the Studio can say them in the owner's language."""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

DESC_MAX = 6000


def _plan_summary(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    if not plan or not isinstance(plan.get("stages"), list):
        return None
    stages = [s for s in plan["stages"] if isinstance(s, dict)]
    return {"title": str(plan.get("title") or ""), "stages": len(stages), "done": sum(1 for s in stages if s.get("status") == "done"),
            "ultra": bool(plan.get("ultra")), "studio": bool(plan.get("studio"))}


def _busy(slug: str) -> str:
    from .full_auto import _live, _other_owner, _runs_by_slug

    if _live(_runs_by_slug.get(slug)):
        return "full_auto"
    return _other_owner(slug)


async def state(slug: str) -> dict[str, Any]:
    from .. import autopilot_jobs as aj
    from .. import playground as pg

    if not await pg.get_project(slug):
        return {"ok": False, "error": "not_found"}
    job = aj.get_active(slug)
    running = bool(job and job.status == "running")
    try:
        plan = await pg.load_autopilot_plan(slug)
    except Exception as exc:
        log.warning("plan read failed: %s", exc)
        plan = None
    resume = None
    if not running:
        info = await pg.get_resume_info(slug)
        if info:
            resume = {k: info.get(k) for k in ("plan_title", "done_stages", "total_stages", "next_stage")}
    from .. import game_studio

    return {"ok": True, "game_studio": bool(game_studio.is_game_studio_slug(slug)), "autopilot": {"running": running, "paused": bool(running and getattr(job, "paused", False)),
                                      "job_id": job.id if running else None},
            "busy": _busy(slug), "plan": _plan_summary(plan), "resume": resume}


async def pause(slug: str) -> dict[str, Any]:
    from .. import autopilot_jobs as aj

    if not await aj.pause_active(slug):
        return {"ok": False, "error": "not_running"}
    return {"ok": True, "paused": True}


async def resume(slug: str) -> dict[str, Any]:
    from .. import autopilot_jobs as aj

    if not await aj.resume_active(slug):
        return {"ok": False, "error": "not_paused"}
    return {"ok": True, "paused": False}


async def clear_plan(slug: str, confirm: Any) -> dict[str, Any]:
    from .. import playground as pg

    if confirm is not True:
        return {"ok": False, "error": "confirm_required"}
    if not await pg.get_project(slug):
        return {"ok": False, "error": "not_found"}
    if (owner := _busy(slug)):
        return {"ok": False, "error": "build_running", "owner": owner}
    await pg.clear_autopilot_plan(slug)
    return {"ok": True}


async def make_plan(slug: str, description: str, *, ultra: bool, studio: bool, studio_preset: str, fallback: bool) -> dict[str, Any]:
    """The plan/create work, shared with the old route. ``fallback=True`` keeps the old route's stand-in plan on a failed call."""
    from .. import playground as pg
    from ..game_platform_guide import is_likely_game
    from ..playground_plan_guide import PLAN_GRANULARITY_RULES, build_planner_system_prompt, enrich_plan, resolve_plan_kind

    proj = await pg.get_project(slug)
    if not proj:
        return {"ok": False, "error": "not_found"}
    kind_key, kind_label = resolve_plan_kind(proj.get("kind", "static"))
    preset = (studio_preset or "").strip() or "custom"
    system = build_planner_system_prompt(
        kind_key, PLAN_GRANULARITY_RULES + f"\nPlatform: {kind_label}",
        is_game=is_likely_game(f"{proj.get('name', '')} {description}"), ultra=ultra, studio=studio, studio_preset=preset,
        description=f"{proj.get('name', '')} {description}", slug=slug)
    plan: dict[str, Any] | None = None
    try:
        from ..kernel.full_autopilot import _extract_json
        from ..llm_client import complete

        result = await complete(system=system, user=description, intent="json", max_tokens=4000)
        text = (result.get("reply") or "") if isinstance(result, dict) else str(result)
        found = _extract_json(text)
        plan = found if isinstance(found, dict) else None
    except Exception as exc:
        log.warning("plan/create llm failed: %s", exc)
    if not plan or not isinstance(plan.get("stages"), list) or not plan["stages"]:
        if not fallback:
            return {"ok": False, "error": "plan_failed"}
        plan = {"title": proj.get("name") or slug, "stages": [{
            "id": 1, "title": "Scaffold", "goal": "Create project files", "instructions": description[:1500],
            "uses_visuals": False, "files": ["index.html"], "status": "pending"}]}
    if studio:
        from ..playground_studio_mode import finalize_studio_plan

        plan = finalize_studio_plan(plan, kind=kind_key, preset=preset, description=description, project_name=proj.get("name") or slug)
    plan = enrich_plan(plan, kind_key, description)
    await pg.save_autopilot_plan(slug, plan)
    return {"ok": True, "plan": plan, "slug": slug}


async def create_plan(slug: str, description: Any, ultra: Any, studio: Any, studio_preset: Any, confirm: Any) -> dict[str, Any]:
    from .. import playground as pg

    desc = str(description or "").strip()
    if not desc:
        return {"ok": False, "error": "description_required"}
    if len(desc) > DESC_MAX:
        return {"ok": False, "error": "too_long"}
    if not await pg.get_project(slug):
        return {"ok": False, "error": "not_found"}
    if (owner := _busy(slug)):
        return {"ok": False, "error": "build_running", "owner": owner}
    had = _plan_summary(await pg.load_autopilot_plan(slug))
    if had and confirm is not True:                                   # it replaces the saved plan
        return {"ok": False, "error": "confirm_required", "replaces": had}
    res = await make_plan(slug, desc, ultra=ultra is True, studio=studio is True, studio_preset=str(studio_preset or ""), fallback=False)
    if not res.get("ok"):
        return res
    return {"ok": True, "plan": _plan_summary(res["plan"])}


async def expand_plan(slug: str, confirm: Any) -> dict[str, Any]:
    """Ultra-expand the SAVED plan (no model call: a local rewrite into smaller stages) and save it."""
    from .. import playground as pg
    from ..game_platform_guide import is_likely_game
    from ..playground_ultra_plan import ultra_expand_plan

    if confirm is not True:
        return {"ok": False, "error": "confirm_required"}
    proj = await pg.get_project(slug)
    if not proj:
        return {"ok": False, "error": "not_found"}
    if (owner := _busy(slug)):
        return {"ok": False, "error": "build_running", "owner": owner}
    plan = await pg.load_autopilot_plan(slug)
    if not _plan_summary(plan) or not plan.get("stages"):
        return {"ok": False, "error": "no_plan"}
    before = _plan_summary(plan)
    expanded = ultra_expand_plan(plan, kind=proj.get("kind", "static"),
                                 is_game=is_likely_game(f"{proj.get('name', '')} {proj.get('description') or ''}"))
    await pg.save_autopilot_plan(slug, expanded)
    return {"ok": True, "before": before, "plan": _plan_summary(expanded)}
