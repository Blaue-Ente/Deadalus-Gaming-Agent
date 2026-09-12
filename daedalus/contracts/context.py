"""Per-prompt context window snapshots. Not part of Run / RunCheckpoint.

P1.06 records slice estimates and a model window beside the run.
Schema v1 on Run stays unchanged. Do not import agent, native_tools,
run_engine, scratch_journal, or context_assembler.
"""
from __future__ import annotations

from enum import Enum

from pydantic import Field, model_validator

from ._base import ContractModel


class ContextSliceKind(str, Enum):
    SYSTEM = "system"
    OWNER = "owner"
    BRIEFING = "briefing"
    SKILLS = "skills"
    MEMORY = "memory"
    HISTORY = "history"
    TOOLS = "tools"
    OTHER = "other"
    RESERVE = "reserve"


class ContextSlice(ContractModel):
    """One named prompt slice. skipped ⇒ not ok."""

    name: str
    kind: str
    tokens_est: int
    chars: int = 0
    kept: bool = True
    trimmed: bool = False
    skipped: bool = False
    ok: bool = True
    priority: int = 0

    @model_validator(mode="after")
    def skipped_implies_not_ok(self) -> ContextSlice:
        if self.skipped and self.ok:
            raise ValueError("skipped context slice cannot be ok")
        return self


class ContextWindow(ContractModel):
    """Resolved model window. Unknown or unusable is skip ≠ ok."""

    model: str = ""
    window_tokens: int = 0
    reserve_output: int = 0
    reserve_headroom: int = 0
    usable_tokens: int = 0
    source: str = "unknown"
    skipped: bool = False
    ok: bool = True

    @model_validator(mode="after")
    def skipped_implies_not_ok(self) -> ContextWindow:
        if self.skipped and self.ok:
            raise ValueError("skipped context window cannot be ok")
        return self


class ContextBudgetReport(ContractModel):
    """Frozen per-prompt window account. Optional persist under $DAEDALUS_DATA/context/."""

    run_id: str = ""
    session_id: str = ""
    window: ContextWindow
    slices: list[ContextSlice] = Field(default_factory=list)
    tokens_est: int = 0
    tokens_kept: int = 0
    overflow: int = 0
    trimmed: bool = False
    ok: bool
    skipped: bool = False
    blockers: list[str] = Field(default_factory=list)
    persisted: str = ""

    @model_validator(mode="after")
    def skipped_implies_not_ok(self) -> ContextBudgetReport:
        if self.skipped and self.ok:
            raise ValueError("skipped context budget cannot be ok")
        return self


__all__ = [
    "ContextBudgetReport",
    "ContextSlice",
    "ContextSliceKind",
    "ContextWindow",
]
