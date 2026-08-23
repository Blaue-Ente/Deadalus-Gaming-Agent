"""Multi-file changeset apply — portable filesystem writes."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .orchestrator import FileChange


def safe_join(root: Path, rel: str) -> Path:
    target = (root / rel).resolve()
    root_resolved = root.resolve()
    if root_resolved not in target.parents and target != root_resolved:
        raise ValueError(f"path traversal blocked: {rel!r}")
    return target


def apply_changes(root: Path, changes: list[FileChange]) -> dict[str, Any]:
    """Apply create/update/delete operations under root."""
    root.mkdir(parents=True, exist_ok=True)
    written = 0
    deleted = 0
    for ch in changes:
        target = safe_join(root, ch.path)
        if ch.action == "delete":
            if target.exists():
                target.unlink()
                deleted += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(ch.content, encoding="utf-8")
        written += 1
    return {"ok": True, "written": written, "deleted": deleted}
