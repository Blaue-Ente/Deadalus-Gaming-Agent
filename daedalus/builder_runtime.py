"""Adapter for the in-package builder orchestrator.

Wires MeshRouter LLM + playground workspace + smoke_test into
``daedalus.kernel.orchestrator``.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

from .kernel.changeset import apply_changes
from .kernel.orchestrator import (
    AgentRole,
    BuilderBackend,
    FileChange,
    PipelineState,
    run_builder_pipeline,
)

from . import autopilot_jobs as aj
from . import playground as pg
from . import playground_runner as pgr
from .workers import call_workers_parallel

_KIND_MAP = {
    "static": "static",
    "vite-react": "vite-react",
    "vite-game": "vite",
    "node-api": "node",
    "auto": "auto",
    "expo": "expo",
    "fastapi": "fastapi",
    "streamlit": "streamlit",
}


async def create_builder_project(
    name: str,
    *,
    kind: str = "vite-react",
    description: str = "",
) -> dict[str, Any]:
    pg_kind = _KIND_MAP.get(kind, "vite-react")
    proj = await pg.create_project(
        name,
        kind=pg_kind,
        source="built",
        description=description,
        coding_mode="pro",
    )
    return {
        "ok": True,
        "project": proj,
        "workspace_path": f"playground/projects/{proj['slug']}",
    }


async def list_builder_projects(limit: int = 50) -> list[dict[str, Any]]:
    rows = await pg.list_projects()
    return rows[:limit]


class _ADNBuilderBackend(BuilderBackend):
    def __init__(self, slug: str, job: aj.AutopilotJob | None = None):
        self.slug = slug
        self.job = job
        self._root = pg.project_dir(slug)

    async def emit(self, event: str, data: dict[str, Any]) -> None:
        if self.job:
            await self.job.append_event(event, data)

    async def llm(self, role: AgentRole, prompt: str, *, system: str = "") -> str:
        slot = {
            AgentRole.PLANNER: "balanced",
            AgentRole.CODER: "deep",
            AgentRole.UI: "balanced",
            AgentRole.GAME_LOGIC: "deep",
            AgentRole.TESTER: "fast",
            AgentRole.REVIEWER: "deep",
            AgentRole.PUBLISHER: "fast",
        }.get(role, "balanced")

        task = prompt if not system else f"{system}\n\n---\n\n{prompt}"
        results = await call_workers_parallel([{"slot": slot, "task": task}])
        if not results:
            return ""
        r0 = results[0]
        return (r0.get("result") or r0.get("text") or "").strip()

    async def write_changes(self, changes: list[FileChange]) -> int:
        files = {c.path: c.content for c in changes if c.action != "delete"}
        if files:
            return pg.write_files(self.slug, files)
        apply_changes(self._root, changes)
        return len(changes)

    async def smoke_test(self) -> dict[str, Any]:
        return await pgr.smoke_test(self.slug)

    async def preview_url(self) -> str:
        return f"/admin/daedalus/playground/preview/{self.slug}/"

    async def project_kind(self) -> str:
        proj = await pg.get_project(self.slug)
        return (proj or {}).get("kind") or "static"

    async def browser_verify(self) -> dict[str, Any]:
        from . import playground_browser as pb
        kind = await self.project_kind()
        return await pb.verify_app(self.slug, kind)


def _use_adn_admin_pipeline() -> bool:
    """ADN's rich Phase 0–6 generator lives in admin.py — never delete it.

    Standalone (or missing ADN) uses the portable kernel instead.
    """
    from .host import standalone_mode, adn_available
    if standalone_mode() or not adn_available():
        return False
    return True


def start_builder_run(
    slug: str,
    description: str,
    *,
    mode: str = "full",
    resume: bool = False,
) -> aj.AutopilotJob:
    """Detached autonomous build — survives client disconnect.

    mode=full  → ADN admin autopilot when hosted by ADN; kernel full-autopilot standalone
    mode=light → portable orchestrator only (fast smoke path)
    """
    if mode == "light":
        return _start_light_builder_run(slug, description)
    if not _use_adn_admin_pipeline():
        return _start_full_kernel_run(slug, description)
    from argos_data_nexus.admin import start_playground_autopilot_job

    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # Called from async context — caller must await start_playground_autopilot_job directly.
        raise RuntimeError("use start_builder_run_async for full mode inside async handlers")

    return asyncio.run(start_playground_autopilot_job(slug, description, resume=resume))


async def start_builder_run_async(
    slug: str,
    description: str,
    *,
    mode: str = "full",
    resume: bool = False,
) -> aj.AutopilotJob:
    if mode == "light":
        return _start_light_builder_run(slug, description)
    if not _use_adn_admin_pipeline():
        return _start_full_kernel_run(slug, description)
    from argos_data_nexus.admin import start_playground_autopilot_job

    return await start_playground_autopilot_job(slug, description, resume=resume)


def _start_full_kernel_run(slug: str, description: str) -> aj.AutopilotJob:
    """Hermes-class staged loop without touching ADN admin.py."""
    from .kernel.full_autopilot import run_full_autopilot
    from .kernel.orchestrator import PipelineState

    async def _gen():
        yield ("phase", {"n": 0, "label": "full-autopilot"})
        yield ("log", {"line": f"Standalone full autopilot · {slug}", "level": "info"})
        job = aj.get_active(slug)
        backend = _ADNBuilderBackend(slug, job)
        state = PipelineState(slug=slug, description=description)
        try:
            await run_full_autopilot(backend, state)
            files = [c.path for c in state.changes]
            if state.test_ok and state.review_ok:
                yield (
                    "done",
                    {
                        "files": files,
                        "assets": [],
                        "preview_url": state.preview_url,
                        "verified": True,
                    },
                )
            else:
                yield (
                    "error",
                    {"message": "; ".join(state.errors) or "pipeline blocked at test/review"},
                )
        except Exception as exc:
            log.exception("full kernel autopilot failed (%s)", slug)
            yield ("error", {"message": str(exc)[:300]})

    return aj.start_autopilot(slug, description, _gen)


def _start_light_builder_run(slug: str, description: str) -> aj.AutopilotJob:
    async def _gen():
        yield ("phase", {"n": 0, "label": "builder-pipeline"})
        yield ("log", {"line": f"Starting light builder for {slug}", "level": "info"})
        job = aj.get_active(slug)
        backend = _ADNBuilderBackend(slug, job)
        state = PipelineState(slug=slug, description=description)
        try:
            await run_builder_pipeline(backend, state)
            if state.test_ok:
                proj = await pg.get_project(slug)
                kind = (proj or {}).get("kind", "static")
                from .playground_runtime import VISUAL_KINDS
                from . import playground_browser as pb

                if kind in VISUAL_KINDS:
                    yield ("phase", {"n": 4, "label": "Browser verify"})
                    bresult = await pb.verify_app(slug, kind)
                    if not bresult.get("ok") and not bresult.get("skipped"):
                        state.review_ok = False
                        state.errors.append(pb.format_errors_for_fix(bresult)[:800])
                    elif not bresult.get("skipped"):
                        yield ("log", {"line": "✓ Browser verify OK", "level": "info"})
            if state.test_ok and state.review_ok:
                preview = await backend.preview_url()
                yield (
                    "done",
                    {
                        "files": [c.path for c in state.changes],
                        "assets": [],
                        "preview_url": preview,
                        "verified": state.test_ok,
                    },
                )
            else:
                yield (
                    "error",
                    {"message": "; ".join(state.errors) or "pipeline blocked at test/review"},
                )
        except Exception as exc:
            log.exception("builder pipeline failed (%s)", slug)
            yield ("error", {"message": str(exc)[:300]})

    return aj.start_autopilot(slug, description, _gen)


def job_snapshot(job_id: str) -> dict[str, Any] | None:
    job = aj.get(job_id)
    if not job:
        return None
    return job.snapshot()


def get_active_job(slug: str) -> aj.AutopilotJob | None:
    return aj.get_active(slug)
