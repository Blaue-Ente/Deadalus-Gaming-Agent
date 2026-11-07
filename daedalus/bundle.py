"""Daedalus Staged Bundle system.

Дедал натрупва файлови промени в bundle (staging зона) без да ги прилага.
Owner преглежда bundle-а в admin UI и натиска "Приложи всичко" → файловете
се записват на диск атомично.

Маркери:
  <NEED_BUNDLE_START: Title>               — опционален, auto-create ако липсва
  <NEED_STAGE>                             — добавя файл към текущия bundle
  FILE: path/to/file.py
  ACTION: replace | create | delete
  REASON: Защо тази промяна
  CODE_START
  # full new content
  CODE_END
  </NEED_STAGE>
  <NEED_BUNDLE_COMMIT: Title | Summary>    — финализира bundle → status=ready
"""
from __future__ import annotations
from .paths import repo_root

import hashlib
import logging
import re
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

ADN_ROOT = repo_root()

# ---------------------------------------------------------------------------
# Regex for NEED_STAGE block
# ---------------------------------------------------------------------------
_STAGE_BLOCK_RE = re.compile(
    r"<NEED_STAGE>\s*\n"
    r"FILE:\s*(.+?)\n"
    r"ACTION:\s*(replace|create|delete)[^\n]*\n"
    r"REASON:\s*(.+?)\n"
    r"(?:CODE_START\n(.*?)CODE_END)?\s*\n?"
    r"</NEED_STAGE>",
    re.DOTALL,
)

_BUNDLE_START_RE = re.compile(
    r"<NEED_BUNDLE_START:\s*([^>]{1,200})\s*>",
)
_BUNDLE_COMMIT_RE = re.compile(
    r"<NEED_BUNDLE_COMMIT:\s*([^|>]{1,200})\|([^>]{1,1000})\s*>",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Path safety: a bundle may only touch ordinary files inside the product root
# ---------------------------------------------------------------------------

_BLOCKED_TOP_DIRS = frozenset({".git", "data"})


def _safe_target(file_path: str) -> tuple[Path | None, str]:
    """Resolve ``file_path`` under ``ADN_ROOT`` or explain why it is refused.

    Refuses absolute paths, ``..``, symlink escapes, ``.git``, the data directory
    and secret files (``.env*``, ``secrets.env``, keys).
    """
    from pathlib import PurePosixPath, PureWindowsPath

    from .secret_paths import is_secret_path

    raw = str(file_path or "").strip()
    if not raw or "\x00" in raw:
        return None, "empty or invalid path"
    norm = raw.replace("\\", "/")
    if norm.startswith("/") or PureWindowsPath(raw).is_absolute() or PureWindowsPath(raw).drive:
        return None, "absolute paths are not allowed"
    parts = PurePosixPath(norm).parts
    if any(part == ".." for part in parts):
        return None, "path traversal is not allowed"
    if parts and parts[0].lower() in _BLOCKED_TOP_DIRS or any(p.lower() == ".git" for p in parts):
        return None, "protected directory"
    if is_secret_path(norm):
        return None, "secret files are never bundled"
    root = ADN_ROOT.resolve()
    target = (ADN_ROOT / norm).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None, "path resolves outside the product root"
    return target, ""


# ---------------------------------------------------------------------------
# DB helpers (async, uses shared pool from db.py)
# ---------------------------------------------------------------------------

async def _pool():
    from .host import get_pool as _get_pool
    return await _get_pool()


async def create_bundle(title: str, session_id: str = "") -> str:
    """Create a new bundle, return bundle_id."""
    bundle_id = hashlib.sha1(f"{title}{time.time()}".encode()).hexdigest()[:10]
    pool = await _pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO daedalus_bundles (id, title, session_id, status)
               VALUES ($1, $2, $3, 'staging')
               ON CONFLICT (id) DO NOTHING""",
            bundle_id, title, session_id,
        )
    log.info("Bundle created: %s — %s", bundle_id, title)
    return bundle_id


async def stage_file(
    bundle_id: str,
    file_path: str,
    action: str,
    reason: str,
    new_content: str | None,
) -> dict[str, Any]:
    """Add a file to the staged bundle. Reads original content for diff."""
    abs_path, why = _safe_target(file_path)
    if abs_path is None:
        return {"ok": False, "error": "unsafe_path", "file_path": file_path, "reason": why}
    original = ""
    if abs_path.exists():
        try:
            original = abs_path.read_text("utf-8")
        except Exception:
            original = ""

    pool = await _pool()
    async with pool.acquire() as conn:
        # ensure bundle exists
        row = await conn.fetchrow("SELECT id FROM daedalus_bundles WHERE id=$1", bundle_id)
        if not row:
            return {"ok": False, "error": f"Bundle {bundle_id} not found"}
        # upsert staged file (replace if same path already staged)
        await conn.execute(
            """INSERT INTO daedalus_bundle_files
               (bundle_id, file_path, action, reason, new_content, original_content)
               VALUES ($1,$2,$3,$4,$5,$6)
               ON CONFLICT (bundle_id, file_path)
               DO UPDATE SET action=$3, reason=$4, new_content=$5, original_content=$6,
                             staged_at=NOW()""",
            bundle_id, file_path, action, reason, new_content or "", original,
        )
    lines_added = len((new_content or "").splitlines())
    lines_orig  = len(original.splitlines())
    log.info("Staged file %s → bundle %s (%s, +%d/-%d lines)",
             file_path, bundle_id, action, lines_added, lines_orig)
    return {
        "ok": True,
        "bundle_id": bundle_id,
        "file_path": file_path,
        "action": action,
        "lines_new": lines_added,
        "lines_orig": lines_orig,
    }


async def commit_bundle(bundle_id: str, title: str, summary: str) -> dict[str, Any]:
    """Mark bundle as ready for owner review."""
    pool = await _pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM daedalus_bundles WHERE id=$1 AND status='staging'",
            bundle_id,
        )
        if not row:
            return {"ok": False, "error": f"Bundle {bundle_id} not in staging"}
        await conn.execute(
            """UPDATE daedalus_bundles
               SET status='ready', title=$2, summary=$3, committed_at=NOW()
               WHERE id=$1""",
            bundle_id, title, summary,
        )
        files = await conn.fetch(
            "SELECT file_path, action FROM daedalus_bundle_files WHERE bundle_id=$1",
            bundle_id,
        )
    log.info("Bundle committed: %s — %s (%d files)", bundle_id, title, len(files))
    return {
        "ok": True,
        "bundle_id": bundle_id,
        "title": title,
        "files_count": len(files),
        "files": [dict(f) for f in files],
    }


async def apply_bundle(bundle_id: str) -> dict[str, Any]:
    """Apply all staged files to disk atomically. Returns list of applied paths."""
    pool = await _pool()
    async with pool.acquire() as conn:
        bundle = await conn.fetchrow(
            "SELECT * FROM daedalus_bundles WHERE id=$1", bundle_id
        )
        if not bundle:
            return {"ok": False, "error": "Bundle not found"}
        if bundle["status"] not in ("ready", "staging"):
            return {"ok": False, "error": f"Bundle status is {bundle['status']}, not ready"}
        files = await conn.fetch(
            "SELECT * FROM daedalus_bundle_files WHERE bundle_id=$1 ORDER BY staged_at",
            bundle_id,
        )

    # Preflight: every path must be safe before anything is written.
    plan: list[tuple[Any, Path]] = []
    unsafe: list[str] = []
    for f in files:
        fpath, why = _safe_target(f["file_path"])
        if fpath is None:
            unsafe.append(f"{f['file_path']}: {why}")
        else:
            plan.append((f, fpath))
    if unsafe:
        log.warning("Bundle %s refused: %d unsafe path(s)", bundle_id, len(unsafe))
        return {"ok": False, "error": "unsafe_path", "bundle_id": bundle_id,
                "applied": [], "errors": unsafe}

    applied: list[str] = []
    errors: list[str] = []
    undo: list[tuple[Path, bytes | None]] = []
    for f, fpath in plan:
        try:
            before = fpath.read_bytes() if fpath.is_file() else None
            if f["action"] == "delete":
                if fpath.exists():
                    fpath.unlink()
                undo.append((fpath, before))
                applied.append(f["file_path"])
            else:  # replace or create
                fpath.parent.mkdir(parents=True, exist_ok=True)
                fpath.write_text(f["new_content"] or "", "utf-8")
                undo.append((fpath, before))
                applied.append(f["file_path"])
        except Exception as exc:
            errors.append(f"{f['file_path']}: {exc}")
            break

    if errors:
        # All or nothing: put back everything already written and keep the bundle retryable.
        for path, before in reversed(undo):
            try:
                if before is None:
                    if path.exists():
                        path.unlink()
                else:
                    path.write_bytes(before)
            except Exception as exc:  # pragma: no cover - best effort restore
                errors.append(f"rollback {path}: {exc}")
        log.warning("Bundle %s rolled back: %s", bundle_id, errors[0])
        return {"ok": False, "bundle_id": bundle_id, "applied": [], "errors": errors,
                "rolled_back": True}

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE daedalus_bundles SET status=$2, applied_at=NOW() WHERE id=$1",
            bundle_id, "applied",
        )

    log.info("Bundle applied: %s — %d files", bundle_id, len(applied))
    return {"ok": True, "bundle_id": bundle_id, "applied": applied, "errors": []}


async def reject_bundle(bundle_id: str) -> dict[str, Any]:
    pool = await _pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE daedalus_bundles SET status='rejected' WHERE id=$1", bundle_id
        )
    return {"ok": True, "bundle_id": bundle_id}


async def list_bundles(status: str | None = None) -> list[dict[str, Any]]:
    pool = await _pool()
    async with pool.acquire() as conn:
        if status:
            rows = await conn.fetch(
                "SELECT b.*, COUNT(f.id) AS files_count FROM daedalus_bundles b "
                "LEFT JOIN daedalus_bundle_files f ON f.bundle_id=b.id "
                "WHERE b.status=$1 GROUP BY b.id ORDER BY b.created_at DESC",
                status,
            )
        else:
            rows = await conn.fetch(
                "SELECT b.*, COUNT(f.id) AS files_count FROM daedalus_bundles b "
                "LEFT JOIN daedalus_bundle_files f ON f.bundle_id=b.id "
                "GROUP BY b.id ORDER BY b.created_at DESC LIMIT 50",
            )
    return [dict(r) for r in rows]


async def get_bundle(bundle_id: str) -> dict[str, Any] | None:
    pool = await _pool()
    async with pool.acquire() as conn:
        bundle = await conn.fetchrow(
            "SELECT * FROM daedalus_bundles WHERE id=$1", bundle_id
        )
        if not bundle:
            return None
        files = await conn.fetch(
            "SELECT * FROM daedalus_bundle_files WHERE bundle_id=$1 ORDER BY staged_at",
            bundle_id,
        )
    return {**dict(bundle), "files": [dict(f) for f in files]}


# ---------------------------------------------------------------------------
# Parser helpers (used by agent.py)
# ---------------------------------------------------------------------------

def extract_stage_block(raw: str) -> dict[str, Any] | None:
    """Parse first NEED_STAGE block from LLM output."""
    if "<NEED_STAGE>" not in raw:
        return None
    m = _STAGE_BLOCK_RE.search(raw)
    if not m:
        return None
    return {
        "file_path": m.group(1).strip(),
        "action":    m.group(2).strip(),
        "reason":    m.group(3).strip(),
        "content":   (m.group(4) or "").rstrip(),
    }


def extract_all_stage_blocks(raw: str) -> list[dict[str, Any]]:
    """Parse ALL NEED_STAGE blocks (Дедал може да ги emit-не няколко наведнъж)."""
    results = []
    for m in _STAGE_BLOCK_RE.finditer(raw or ""):
        results.append({
            "file_path": m.group(1).strip(),
            "action":    m.group(2).strip(),
            "reason":    m.group(3).strip(),
            "content":   (m.group(4) or "").rstrip(),
        })
    return results


def extract_bundle_start(raw: str) -> str | None:
    """Extract NEED_BUNDLE_START title, or None."""
    m = _BUNDLE_START_RE.search(raw or "")
    return m.group(1).strip() if m else None


def extract_bundle_commit(raw: str) -> dict[str, str] | None:
    """Extract NEED_BUNDLE_COMMIT fields, or None."""
    m = _BUNDLE_COMMIT_RE.search(raw or "")
    if not m:
        return None
    return {"title": m.group(1).strip(), "summary": m.group(2).strip()}


def strip_stage_blocks(raw: str) -> str:
    """Remove all NEED_STAGE blocks and NEED_BUNDLE_* markers from raw."""
    raw = _STAGE_BLOCK_RE.sub("", raw)
    raw = _BUNDLE_START_RE.sub("", raw)
    raw = _BUNDLE_COMMIT_RE.sub("", raw)
    return raw.strip()
