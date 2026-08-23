"""Daedalus knowledge loader.

On boot (or on demand), scans .agents/skills/* and .agents/plans/* and indexes
every document into the global knowledge collection. This gives Daedalus durable,
searchable awareness of:

- Iyveris vision (manifest, all 11 services, ANL substrate, distributed identity)
- Argos Vault editions (Ed1 LIVE, Ed1.5, Ed2 capability tickets, Ed3 Shamir + break-glass)
- Cybersecurity backbone (Vault + Shamir + break-glass + mesh-peer trust)
- Code Agent system plan (this very agent's blueprint)
- All sister-app skills (StockArgos, Rechtsportal, KitchenOS, etc.)
- Mesh protocol details
- Upgrade pool (claude-mem, jcode, JustHireMe, rtk, ...)

Loader is idempotent: re-running it updates existing entries (delete + re-add).
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from .memory import DaedalusMemory
from .paths import data_root, repo_root

log = logging.getLogger(__name__)


def _knowledge_sources() -> list[tuple[Path, str]]:
    root = repo_root() / ".agents"
    return [
        (root / "skills", "skill"),
        (root / "plans", "plan"),
        (root / "upgrade-pool" / "items", "upgrade"),
    ]


class DaedalusKnowledge:
    """Scan the .agents/ tree and load every md/json doc into global memory."""

    def __init__(self, memory: DaedalusMemory | None = None):
        self.memory = memory or DaedalusMemory()
        self._manifest_path = data_root() / "knowledge_manifest.json"
        self._manifest_path.parent.mkdir(parents=True, exist_ok=True)

    def _load_manifest(self) -> dict[str, str]:
        if not self._manifest_path.exists():
            return {}
        try:
            return json.loads(self._manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_manifest(self, manifest: dict[str, str]) -> None:
        self._manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    def load_all(self, force: bool = False) -> dict[str, Any]:
        """Scan all known knowledge sources and (re)index changed files.

        Args:
            force: If True, re-index every file even if unchanged.
        Returns:
            Summary dict: {loaded, skipped, errors, total_chunks_before/after}
        """
        manifest = {} if force else self._load_manifest()
        before = self.memory.global_col.count()

        loaded = 0
        skipped = 0
        errors: list[str] = []
        new_manifest: dict[str, str] = {}

        for root_dir, category in _knowledge_sources():
            if not root_dir.exists():
                continue
            for path in root_dir.rglob("*"):
                if not path.is_file():
                    continue
                if path.suffix not in (".md", ".json", ".txt"):
                    continue
                try:
                    content = path.read_text(encoding="utf-8")
                except Exception as exc:
                    errors.append(f"{path}: {exc}")
                    continue

                rel_path = str(path)
                content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
                new_manifest[rel_path] = content_hash

                if manifest.get(rel_path) == content_hash and not force:
                    skipped += 1
                    continue

                # Title heuristic: first markdown heading, or filename
                title = _extract_title(content) or path.stem
                doc_id = f"{category}:{path.stem}"

                try:
                    self.memory.upsert_knowledge(
                        doc_id=doc_id,
                        content=content,
                        source=rel_path,
                        category=category,
                        title=title,
                    )
                    loaded += 1
                except Exception as exc:
                    errors.append(f"{path}: upsert failed: {exc}")

        self._save_manifest(new_manifest)
        after = self.memory.global_col.count()

        result = {
            "loaded": loaded,
            "skipped": skipped,
            "errors": errors,
            "chunks_before": before,
            "chunks_after": after,
            "delta": after - before,
        }
        log.info(
            "Daedalus knowledge: loaded=%d skipped=%d errors=%d chunks=%d→%d",
            loaded, skipped, len(errors), before, after,
        )
        return result


def _extract_title(content: str) -> str:
    """First markdown H1 (`# Title`) or empty string."""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()[:200]
    return ""
