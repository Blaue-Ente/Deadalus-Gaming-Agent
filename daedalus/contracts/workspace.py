"""Per-run git worktree snapshots. Not part of Run / RunCheckpoint.

P1.01 records isolation metadata beside the run. Schema v1 on Run stays
unchanged. Do not import agent, native_tools, run_engine, or shell_center.
"""
from __future__ import annotations

from ._base import ContractModel


class DirtyBaseline(ContractModel):
    """``git status --porcelain=v1`` of the *main* checkout at open time."""

    head_sha: str
    porcelain: str = ""
    dirty: bool = False


class RunWorkspace(ContractModel):
    """Frozen handle for one run's worktree (or the passthrough when disabled)."""

    run_id: str
    repo: str
    worktree: str
    base_sha: str
    branch: str = ""
    enabled: bool = False
    slug: str = ""
    dirty: DirtyBaseline | None = None


__all__ = [
    "DirtyBaseline",
    "RunWorkspace",
]
