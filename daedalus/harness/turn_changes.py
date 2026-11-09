"""What a project-chat turn changed in the project, and how to take one change back.

In standalone Daedalus the agent writes files itself (its playground tools), so there is never a "proposal" to preview
before it is applied. The honest review is after the fact: the project is snapshotted when the turn starts, compared
when it ends, and every changed file is listed with its diff. A change can be reverted, but only while the file is
still exactly what the turn left (a later edit is never overwritten), and secret files are never read or restored.
"""
from __future__ import annotations

import difflib
import hashlib
from typing import Any

MAX_FILES = 500
MAX_FILE_BYTES = 300_000
MAX_TOTAL_BYTES = 20_000_000
MAX_DIFF_LINES = 400


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def snapshot(slug: str) -> dict[str, dict[str, Any]]:
    """path -> {sha, text|None}; text is kept only for small UTF-8 files that are not secrets (the revert source)."""
    from .. import playground as pg
    from ..secret_paths import is_secret_path

    root = pg.project_dir(slug)
    out: dict[str, dict[str, Any]] = {}
    total = 0
    for ent in pg.read_tree(slug, max_files=MAX_FILES):
        rel = ent["path"]
        if is_secret_path(rel):
            out[rel] = {"sha": None, "text": None, "secret": True}
            continue
        try:
            raw = (root / rel).read_bytes()
        except OSError:
            continue
        text = None
        if len(raw) <= MAX_FILE_BYTES and total + len(raw) <= MAX_TOTAL_BYTES:
            try:
                text = raw.decode("utf-8")
                total += len(raw)
            except UnicodeDecodeError:
                text = None
        out[rel] = {"sha": _sha(raw), "text": text}
    return out


def compare(before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The files the turn created, modified or deleted, with a unified diff where both sides are text."""
    changes: list[dict[str, Any]] = []
    for path in sorted(set(before) | set(after)):
        b, a = before.get(path), after.get(path)
        if (b or {}).get("secret") or (a or {}).get("secret"):
            if (b is None) != (a is None):
                changes.append({"path": path, "status": "created" if b is None else "deleted", "secret": True,
                                "diff": "", "revertible": False})
            continue
        if b and a and b["sha"] == a["sha"]:
            continue
        status = "created" if b is None else "deleted" if a is None else "modified"
        old = (b or {}).get("text") if b else ""
        new = (a or {}).get("text") if a else ""
        diff = ""
        if old is not None and new is not None:
            lines = list(difflib.unified_diff((old or "").splitlines(keepends=True), (new or "").splitlines(keepends=True),
                                              fromfile=f"a/{path}", tofile=f"b/{path}"))
            if len(lines) > MAX_DIFF_LINES:
                lines = lines[:MAX_DIFF_LINES] + [f"\n… {len(lines) - MAX_DIFF_LINES} more diff lines not shown\n"]
            diff = "".join(lines)
        revertible = status == "created" or (b is not None and b.get("text") is not None)
        changes.append({"path": path, "status": status, "diff": diff, "binary": diff == "" and status != "deleted" and new is None,
                        "revertible": revertible, "after_sha": (a or {}).get("sha"), "before_text": (b or {}).get("text")})
    return changes


def public(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What a client sees: no stored old content, no hashes."""
    return [{k: v for k, v in c.items() if k not in ("before_text", "after_sha")} for c in changes]


def revert(slug: str, change: dict[str, Any]) -> dict[str, Any]:
    """Take one change back, only if the file is still exactly what the turn left."""
    from .. import playground as pg
    from ..secret_paths import is_secret_path

    path = change["path"]
    if change.get("secret") or is_secret_path(path) or not change.get("revertible"):
        return {"ok": False, "error": "not_revertible"}
    target = pg._safe_join(pg.project_dir(slug), path)
    try:
        current = _sha(target.read_bytes()) if target.exists() else None
    except OSError:
        return {"ok": False, "error": "unreadable"}
    if current != change.get("after_sha"):
        return {"ok": False, "error": "changed_since"}          # someone (or a later turn) edited it: never overwrite that
    if change["status"] == "created":
        target.unlink()
        return {"ok": True, "path": path, "reverted": "removed"}
    pg.write_file(slug, path, change["before_text"])
    return {"ok": True, "path": path, "reverted": "restored"}
