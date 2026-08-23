"""Portable builder orchestration — shared by ADN and standalone Workshop.

Defines the autonomous coding pipeline contract:
  plan → code → test → repair → review → preview → publish

Implementations supply a BuilderBackend (LLM, filesystem, test runner).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger(__name__)


class PipelineStage(str, Enum):
    IDEA = "idea"
    PLAN = "plan"
    CODE = "code"
    TEST = "test"
    REPAIR = "repair"
    REVIEW = "review"
    PREVIEW = "preview"
    PUBLISH = "publish"


class AgentRole(str, Enum):
    PLANNER = "planner"
    CODER = "coder"
    UI = "ui"
    GAME_LOGIC = "game-logic"
    TESTER = "tester"
    REVIEWER = "reviewer"
    PUBLISHER = "publisher"


STAGE_ORDER: list[PipelineStage] = [
    PipelineStage.IDEA,
    PipelineStage.PLAN,
    PipelineStage.CODE,
    PipelineStage.TEST,
    PipelineStage.REPAIR,
    PipelineStage.REVIEW,
    PipelineStage.PREVIEW,
    PipelineStage.PUBLISH,
]


@dataclass
class AgentTask:
    role: AgentRole
    title: str
    status: str = "queued"  # queued | running | done | blocked


@dataclass
class FileChange:
    path: str
    action: str  # create | update | delete
    content: str = ""
    summary: str = ""


@dataclass
class PipelineState:
    slug: str
    description: str
    stage: PipelineStage = PipelineStage.IDEA
    agents: list[AgentTask] = field(default_factory=list)
    changes: list[FileChange] = field(default_factory=list)
    plan: dict[str, Any] = field(default_factory=dict)
    test_ok: bool = False
    review_ok: bool = False
    preview_url: str = ""
    publish_url: str = ""
    errors: list[str] = field(default_factory=list)

    def seed_agents(self) -> None:
        self.agents = [
            AgentTask(AgentRole.PLANNER, "Draft milestone plan"),
            AgentTask(AgentRole.CODER, "Write multi-file changeset"),
            AgentTask(AgentRole.TESTER, "Sandbox build + smoke test"),
            AgentTask(AgentRole.REVIEWER, "Code review gate"),
            AgentTask(AgentRole.PUBLISHER, "Preview / publish"),
        ]

    def set_agent_status(self, role: AgentRole, status: str) -> None:
        for a in self.agents:
            if a.role == role:
                a.status = status


@runtime_checkable
class BuilderBackend(Protocol):
    """Inject ADN (mesh + playground) or standalone (local LLM + disk)."""

    async def emit(self, event: str, data: dict[str, Any]) -> None: ...

    async def llm(self, role: AgentRole, prompt: str, *, system: str = "") -> str: ...

    async def write_changes(self, changes: list[FileChange]) -> int: ...

    async def smoke_test(self) -> dict[str, Any]: ...

    async def preview_url(self) -> str: ...


def _advance(stage: PipelineStage) -> PipelineStage:
    idx = STAGE_ORDER.index(stage)
    return STAGE_ORDER[min(idx + 1, len(STAGE_ORDER) - 1)]


async def run_builder_pipeline(
    backend: BuilderBackend,
    state: PipelineState,
    *,
    max_repair_rounds: int = 3,
) -> PipelineState:
    """Autonomous loop: plan → implement → test → repair → review → preview."""

    state.seed_agents()
    await backend.emit("phase", {"stage": state.stage.value, "label": "Starting"})

    # ── PLAN ─────────────────────────────────────────────────────────────
    state.stage = PipelineStage.PLAN
    state.set_agent_status(AgentRole.PLANNER, "running")
    await backend.emit("phase", {"stage": "plan", "label": "Planning"})

    plan_raw = await backend.llm(
        AgentRole.PLANNER,
        f"Project: {state.description}\n\n"
        "Return JSON: {\"milestones\": [\"...\"], \"files\": [\"path\"], \"kind\": \"vite-react|static|game\"}",
        system="You are a software architect. Output ONLY valid JSON.",
    )
    state.plan = {"raw": plan_raw[:8000]}
    state.set_agent_status(AgentRole.PLANNER, "done")

    # ── CODE ─────────────────────────────────────────────────────────────
    state.stage = PipelineStage.CODE
    state.set_agent_status(AgentRole.CODER, "running")
    await backend.emit("phase", {"stage": "code", "label": "Coding"})

    code_raw = await backend.llm(
        AgentRole.CODER,
        f"Build this app:\n{state.description}\n\nPlan:\n{plan_raw[:4000]}\n\n"
        "Return JSON: {\"files\": {\"index.html\": \"...\", \"src/App.tsx\": \"...\"}}",
        system=(
            "Senior full-stack engineer. Return ONLY JSON with a files map. "
            "No markdown fences. Complete minimal working app."
        ),
    )
    changes = _parse_files_json(code_raw, state.slug)
    if changes:
        n = await backend.write_changes(changes)
        state.changes = changes
        await backend.emit("log", {"line": f"Wrote {n} file(s)", "level": "info"})
    state.set_agent_status(AgentRole.CODER, "done")

    # ── TEST + REPAIR ────────────────────────────────────────────────────
    state.stage = PipelineStage.TEST
    state.set_agent_status(AgentRole.TESTER, "running")
    await backend.emit("phase", {"stage": "test", "label": "Testing"})

    for attempt in range(1, max_repair_rounds + 1):
        result = await backend.smoke_test()
        if result.get("ok"):
            state.test_ok = True
            break
        state.stage = PipelineStage.REPAIR
        err = (result.get("error") or "")[:3000]
        logs = "\n".join((result.get("logs") or [])[-40:])
        await backend.emit("log", {"line": f"Test failed (attempt {attempt}): {err[:200]}", "level": "warn"})

        fix_raw = await backend.llm(
            AgentRole.CODER,
            f"Fix the build.\n\nError:\n{err}\n\nLogs:\n{logs}\n\n"
            f"Current files context in plan:\n{state.plan.get('raw', '')[:2000]}\n\n"
            "Return JSON files map with corrected files only.",
            system="Fix build errors. Return ONLY JSON {\"files\": {...}}.",
        )
        fix_changes = _parse_files_json(fix_raw, state.slug)
        if fix_changes:
            await backend.write_changes(fix_changes)
            for c in fix_changes:
                _upsert_change(state, c)
        state.stage = PipelineStage.TEST

    state.set_agent_status(AgentRole.TESTER, "done" if state.test_ok else "blocked")

    # ── REVIEW ───────────────────────────────────────────────────────────
    state.stage = PipelineStage.REVIEW
    state.set_agent_status(AgentRole.REVIEWER, "running")
    await backend.emit("phase", {"stage": "review", "label": "Review"})

    review = await backend.llm(
        AgentRole.REVIEWER,
        f"Review this build for {state.slug}. Test ok={state.test_ok}. "
        f"Files: {[c.path for c in state.changes]}. "
        "Reply APPROVED or list blocking issues.",
        system="Code reviewer. Be concise.",
    )
    state.review_ok = "APPROVED" in review.upper()
    state.set_agent_status(AgentRole.REVIEWER, "done" if state.review_ok else "blocked")

    # ── PREVIEW / PUBLISH ────────────────────────────────────────────────
    if state.test_ok and state.review_ok:
        state.stage = PipelineStage.PREVIEW
        state.preview_url = await backend.preview_url()
        state.set_agent_status(AgentRole.PUBLISHER, "running")
        state.stage = PipelineStage.PUBLISH
        state.publish_url = state.preview_url
        state.set_agent_status(AgentRole.PUBLISHER, "done")
        await backend.emit("done", {"preview_url": state.preview_url, "files": [c.path for c in state.changes]})
    else:
        state.errors.append("Pipeline blocked at test/review")
        await backend.emit("error", {"message": "; ".join(state.errors) or "blocked"})

    return state


def _parse_files_json(raw: str, slug: str) -> list[FileChange]:
    import json
    import re

    text = raw.strip()
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict):
        return []
    out: list[FileChange] = []
    for path, content in files.items():
        if not isinstance(path, str) or not isinstance(content, str):
            continue
        safe = path.lstrip("/").replace("..", "")
        if not safe:
            continue
        out.append(FileChange(path=safe, action="create", content=content, summary=f"builder/{slug}"))
    return out


def _upsert_change(state: PipelineState, change: FileChange) -> None:
    for i, c in enumerate(state.changes):
        if c.path == change.path:
            state.changes[i] = change
            return
    state.changes.append(change)
