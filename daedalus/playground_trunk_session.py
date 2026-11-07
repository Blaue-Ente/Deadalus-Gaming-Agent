"""Trunk session — merge gate + registry + disk flush for builds."""
from __future__ import annotations

import logging
from typing import Any

from . import playground as pg
from .playground_content_registry import bootstrap_registry, register_merged_file
from .playground_file_trunk import FileTrunk
from .playground_orchestrator import apply_trunk_merge
from .playground_stage_builder import StageBuildResult

log = logging.getLogger(__name__)


class TrunkSession:
    """FileTrunk + content registry + project writes."""

    def __init__(self, slug: str):
        self.slug = slug
        self.trunk = FileTrunk()
        self.all_files: dict[str, str] = {}

    def load(self, files: dict[str, str] | None = None) -> None:
        if files:
            self.all_files.update(files)
        self.trunk.load_trunk(self.all_files)
        bootstrap_registry(self.trunk)
        for p, c in self.trunk.trunk.items():
            self.all_files[p] = c

    def commit_stage(
        self,
        result: StageBuildResult,
        stage: dict[str, Any],
        *,
        task_id: str = "",
        model: str = "",
    ) -> tuple[list[str], list[str]]:
        """Merge stage result to trunk; update registry. Returns (merged, rejected)."""
        merged, rejected = apply_trunk_merge(
            self.trunk, result, task_id=task_id, model=model,
        )
        for path in merged:
            register_merged_file(
                self.trunk, path, stage, task_id=task_id,
            )
        return merged, rejected

    def flush_to_disk(self) -> list[str]:
        written: list[str] = []
        for path, content in self.trunk.trunk.items():
            try:
                pg.write_file(self.slug, path, content)
                written.append(path)
                self.all_files[path] = content
            except Exception as exc:
                log.warning("trunk flush %s: %s", path, exc)
        return written

    def summary(self) -> dict[str, Any]:
        return self.trunk.summary()
