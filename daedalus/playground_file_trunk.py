"""Trunk / branch workspace for guided ultra builds.

Each file is built on a branch, validated, then merged to trunk (main)
before touching the project workspace — git-like merge gate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class BranchStatus(str, Enum):
    PENDING = "pending"
    VALIDATED = "validated"
    REJECTED = "rejected"
    MERGED = "merged"


@dataclass
class FileBranch:
    path: str
    content: str
    status: BranchStatus = BranchStatus.PENDING
    task_id: str = ""
    review: str = ""
    model: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "status": self.status.value,
            "task_id": self.task_id,
            "review": self.review[:500],
            "model": self.model,
            "bytes": len(self.content or ""),
        }


class FileTrunk:
    """In-memory trunk + branches for one guided build session."""

    def __init__(self) -> None:
        self.trunk: dict[str, str] = {}
        self.branches: dict[str, FileBranch] = {}

    def load_trunk(self, files: dict[str, str]) -> None:
        self.trunk.update(files)

    def propose(
        self,
        path: str,
        content: str,
        *,
        task_id: str = "",
        model: str = "",
        meta: dict[str, Any] | None = None,
    ) -> FileBranch:
        br = FileBranch(
            path=path,
            content=content,
            status=BranchStatus.PENDING,
            task_id=task_id,
            model=model,
            meta=meta or {},
        )
        self.branches[path] = br
        return br

    def mark_validated(self, path: str, *, review: str = "", ok: bool = True) -> None:
        br = self.branches.get(path)
        if not br:
            return
        br.status = BranchStatus.VALIDATED if ok else BranchStatus.REJECTED
        br.review = review[:2000]

    def merge(self, path: str) -> bool:
        br = self.branches.get(path)
        if not br or br.status != BranchStatus.VALIDATED:
            return False
        self.trunk[path] = br.content
        br.status = BranchStatus.MERGED
        return True

    def merge_all_validated(self) -> list[str]:
        merged: list[str] = []
        for path, br in list(self.branches.items()):
            if br.status == BranchStatus.VALIDATED and self.merge(path):
                merged.append(path)
        return merged

    def reject(self, path: str, reason: str = "") -> None:
        br = self.branches.get(path)
        if br:
            br.status = BranchStatus.REJECTED
            br.review = reason[:2000]

    def pending_paths(self) -> list[str]:
        return [p for p, b in self.branches.items() if b.status == BranchStatus.PENDING]

    def summary(self) -> dict[str, Any]:
        by_status: dict[str, list[str]] = {}
        for p, b in self.branches.items():
            by_status.setdefault(b.status.value, []).append(p)
        return {
            "trunk_files": len(self.trunk),
            "branches": {p: b.to_dict() for p, b in self.branches.items()},
            "by_status": by_status,
        }
