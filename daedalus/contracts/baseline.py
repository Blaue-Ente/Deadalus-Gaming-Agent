"""Pre-edit baseline snapshot. Not part of Run / RunCheckpoint.

P1.02 records git, dependencies, engine probes, targeted tests, and known
failures beside the run. Schema v1 on Run stays unchanged. Do not import
agent, native_tools, run_engine, or shell_center.
"""
from __future__ import annotations

from pydantic import Field, model_validator

from ._base import ContractModel
from .workspace import DirtyBaseline


class EngineProbe(ContractModel):
    """Presence/version of one local engine. Missing binary is skip ≠ ok."""

    name: str
    present: bool
    version: str = ""
    pinned: str = ""
    skipped: bool = False
    ok: bool = False
    error: str = ""

    @model_validator(mode="after")
    def skipped_implies_not_ok(self) -> EngineProbe:
        if self.skipped and self.ok:
            raise ValueError("skipped engine probe cannot be ok")
        return self


class DependencySnapshot(ContractModel):
    """pyproject + uv.lock of the capture root. Observational lock check."""

    python: str
    package_version: str
    python_requires: str = ""
    uv_lock_sha256: str = ""
    uv_lock_check: str = "unavailable"
    git_commit: str = ""
    git_branch: str = ""


class KnownFailure(ContractModel):
    """One recorded fail/error/skip. Not permission to weaken tests."""

    source: str
    kind: str
    id: str
    message: str = ""


class BaselineCheck(ContractModel):
    """One named slice of a pre-edit capture. skipped ⇒ not ok."""

    id: str
    ok: bool
    skipped: bool = False
    detail: str = ""
    blockers: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def skipped_implies_not_ok(self) -> BaselineCheck:
        if self.skipped and self.ok:
            raise ValueError("skipped baseline check cannot be ok")
        return self


class BaselineReport(ContractModel):
    """Frozen pre-edit snapshot. Persist under $DAEDALUS_DATA/baselines/."""

    run_id: str
    root: str
    worktree_bound: bool = False
    dirty: DirtyBaseline | None = None
    dependencies: DependencySnapshot
    engines: list[EngineProbe] = Field(default_factory=list)
    checks: list[BaselineCheck] = Field(default_factory=list)
    known_failures: list[KnownFailure] = Field(default_factory=list)
    ok: bool
    blockers: list[str] = Field(default_factory=list)
    persisted: str = ""


__all__ = [
    "BaselineCheck",
    "BaselineReport",
    "DependencySnapshot",
    "EngineProbe",
    "KnownFailure",
]
