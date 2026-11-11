"""Full Auto's Guided job approves a parallel batch on its own.

Found live (2026-11-11): Full Auto starts Guided with auto_approve, but a parallel batch stopped at its checkpoint like a
manual one; Full Auto waited 120 s and ended "guided_stuck" on every milestone that builds files in parallel."""
from __future__ import annotations

import asyncio

from a0_support import a0_tmp  # noqa: F401


def test_a_parallel_batch_is_approved_when_the_job_auto_approves(a0_tmp, monkeypatch):  # noqa: F811
    from daedalus import guided_jobs as gj
    from daedalus import playground as pg
    from daedalus.playground_stage_builder import StageBuildResult

    slug = asyncio.run(pg.create_project("Orbit", kind="static"))["slug"]
    plan = {"stages": [{"id": 1, "title": "A", "files": ["a.js"]}, {"id": 2, "title": "B", "files": ["b.js"]},
                       {"id": 3, "title": "C", "files": ["c.js"]}]}

    async def batch(**kw):
        return [(i, kw["stages"][i], StageBuildResult(True, {f"{'abc'[i]}.js": "let x = 1;\n"}, "РЕЗУЛТАТ: ОДОБРЕН"))
                for i in kw["indices"]]

    monkeypatch.setattr(gj, "run_parallel_batch", batch)

    async def go(auto):
        job = gj.GuidedJob(slug, "orbit", {"stages": [dict(s) for s in plan["stages"]]})
        job.auto_approve = auto
        gj._GD_JOBS[job.id] = job
        await pg.save_autopilot_plan(slug, job.plan)
        await gj._run_guided_parallel_batch(job, kind="static", combined_sys="", default_model="m", is_game=False,
                                            stages=job.plan["stages"], batch_indices=[0, 1], route_fn=None)
        return job

    manual = asyncio.run(go(False))
    assert manual.status == "checkpoint" and manual.stage_index == 0          # the owner decides
    auto = asyncio.run(go(True))
    assert auto.status != "checkpoint" and auto.stage_index >= 2              # approved and moved on by itself
    assert [s["status"] for s in auto.plan["stages"][:2]] == ["done", "done"]
