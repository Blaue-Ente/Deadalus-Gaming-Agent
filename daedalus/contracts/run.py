"""Run / checkpoint envelopes mirroring job_ledger and run_engine rows."""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field

from ._base import ContractModel


class RunStatus(str, Enum):
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    FAILED = "failed"
    CLEARED = "cleared"
    INTERRUPTED = "interrupted"


class RunStage(str, Enum):
    """Pipeline position. Orthogonal to RunStatus (lifecycle)."""

    INTAKE = "intake"
    DISCOVER = "discover"
    PLAN = "plan"
    IMPLEMENT = "implement"
    STATIC_VERIFY = "static_verify"
    BUILD = "build"
    ENGINE_TEST = "engine_test"
    PLAYTEST = "playtest"
    VISUAL_VERIFY = "visual_verify"
    REVIEW = "review"
    REPAIR = "repair"
    ACCEPTED = "accepted"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SliceRole(str, Enum):
    EXPLORER = "explorer"
    CODER = "coder"
    VERIFY = "verify"


class SliceStatus(str, Enum):
    OPEN = "open"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class EffortTier(str, Enum):
    WHISPER = "whisper"
    CRAFT = "craft"
    EPIC = "epic"


class Slice(ContractModel):
    id: str
    title: str
    detail: str = ""
    role: SliceRole = SliceRole.CODER
    status: SliceStatus = SliceStatus.OPEN
    verify: bool = True


class RunBudgetSpend(ContractModel):
    cap_usd: int = 0
    spent_usd: float = 0.0
    remaining_usd: float = 0.0
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    unknown_cost_calls: int = 0
    exhausted: bool = False
    last_model: str = ""
    last_role: str = ""
    last_source: str = ""


class Run(ContractModel):
    run_id: str
    job_id: str = ""
    slug: str = ""
    session_id: str = ""
    original_ask: str = ""
    acceptance: list[str] = Field(default_factory=list)
    slices: list[Slice] = Field(default_factory=list)
    status: RunStatus = RunStatus.IN_PROGRESS
    created_at: float = 0.0
    updated_at: float = 0.0
    effort: EffortTier | None = None
    model: str = ""
    complete: bool | None = None
    stop_reason: str = ""
    spend: RunBudgetSpend | None = None


class RunCheckpoint(ContractModel):
    run_id: str
    stage: int = 0
    plan: dict[str, Any] = Field(default_factory=dict)
    status: str = ""
    slug: str = ""
    updated: float = 0.0
    error: str = ""
