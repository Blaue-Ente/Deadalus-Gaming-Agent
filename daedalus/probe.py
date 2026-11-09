"""Daedalus Probe — eyes into the Iyveris services.

Two roles:
  1. SERVER — handles incoming ``daedalus.probe`` mesh action directed AT ADN itself.
     Walks ADN's own source tree and returns file listings / file content safely.

  2. CLIENT — sends ``daedalus.probe`` mesh actions to OTHER services that have
     installed the probe handler snippet, and returns their responses.

Probe operations (``op`` field in payload):
  - ``file_tree``   — directory listing up to max_depth levels
  - ``read_file``   — read a specific file (with optional line range)
  - ``capabilities`` — return service metadata + registered routes + capabilities
  - ``schema``      — best-effort DB/model schema (app-specific, may be empty)

Safety rules (server side, for ADN self-probe):
  - Paths are resolved relative to ADN_ROOT (argos_data_nexus/).
  - Path traversal (../) is rejected.
  - Blocked extensions: .env, .key, .pem, .p12, .pfx
  - Blocked names: .env, secrets, private_key
  - Max 500 lines per read_file response.
  - Max 300 files returned by file_tree.
"""
from __future__ import annotations

import fnmatch
import logging
import os
from pathlib import Path
from typing import Any

from .paths import adn_package_root, package_root, product_root

log = logging.getLogger(__name__)

# Source trees Daedalus may probe
ADN_PACKAGE = package_root()


def _source_root() -> Path:
    """ADN package when hosted there; the standalone product tree after export."""
    adn = adn_package_root()
    if adn is not None:
        return adn
    return product_root()

_BLOCKED_EXTENSIONS = {".env", ".key", ".pem", ".p12", ".pfx", ".crt"}
_BLOCKED_NAMES = {".env", "secrets", "private_key", ".git"}
_BINARY_EXTENSIONS = {
    ".pyc", ".pyo", ".so", ".dylib", ".dll", ".db", ".sqlite",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2",
    ".ttf", ".eot", ".zip", ".tar", ".gz",
}

_MAX_FILE_LINES = 500
_MAX_TREE_FILES = 300


def _is_safe_path(path: Path, root: Path) -> bool:
    """Reject path traversal and blocked names."""
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    for part in path.parts:
        if part in _BLOCKED_NAMES:
            return False
    ext = path.suffix.lower()
    if ext in _BLOCKED_EXTENSIONS:
        return False
    return True


def _is_readable_text(path: Path) -> bool:
    if path.suffix.lower() in _BINARY_EXTENSIONS:
        return False
    try:
        with open(path, "rb") as f:
            chunk = f.read(512)
        chunk.decode("utf-8")
        return True
    except (UnicodeDecodeError, OSError):
        return False


# ---------------------------------------------------------------------------
# Server — ADN self-probe operations
# ---------------------------------------------------------------------------

def probe_file_tree(
    sub_path: str = "",
    glob_pattern: str = "",
    max_depth: int = 4,
    include_hidden: bool = False,
) -> dict[str, Any]:
    """Walk ADN source tree and return a structured file listing."""
    root = _source_root()
    if sub_path:
        target = (root / sub_path).resolve()
        if not _is_safe_path(target, root):
            return {"ok": False, "error": "unsafe path rejected"}
        if not target.exists():
            return {"ok": False, "error": f"path not found: {sub_path}"}
    else:
        target = root

    entries: list[dict[str, Any]] = []
    count = 0

    def _walk(directory: Path, depth: int) -> None:
        nonlocal count
        if depth > max_depth or count >= _MAX_TREE_FILES:
            return
        try:
            items = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name))
        except PermissionError:
            return
        for item in items:
            if count >= _MAX_TREE_FILES:
                break
            name = item.name
            if not include_hidden and name.startswith(".") and name not in {".env"}:
                continue
            if name in _BLOCKED_NAMES:
                continue
            if not _is_safe_path(item, root):
                continue
            rel = str(item.relative_to(root))
            if item.is_dir():
                entries.append({"type": "dir", "path": rel, "depth": depth})
                _walk(item, depth + 1)
            elif item.is_file():
                if glob_pattern and not fnmatch.fnmatch(name, glob_pattern):
                    continue
                ext = item.suffix.lower()
                entries.append({
                    "type": "file",
                    "path": rel,
                    "depth": depth,
                    "size_bytes": item.stat().st_size,
                    "extension": ext,
                    "binary": ext in _BINARY_EXTENSIONS,
                })
                count += 1

    _walk(target, 1)
    return {
        "ok": True,
        "root": str(target.relative_to(root)),
        "total_entries": len(entries),
        "truncated": count >= _MAX_TREE_FILES,
        "entries": entries,
    }


def probe_read_file(
    file_path: str,
    start_line: int = 1,
    end_line: int | None = None,
) -> dict[str, Any]:
    """Read a file from the ADN source tree."""
    root = _source_root()
    target = (root / file_path).resolve()
    if not _is_safe_path(target, root):
        return {"ok": False, "error": "unsafe path rejected"}
    if not target.exists():
        return {"ok": False, "error": f"file not found: {file_path}"}
    if not target.is_file():
        return {"ok": False, "error": "path is not a file"}
    if not _is_readable_text(target):
        return {"ok": False, "error": "binary or unreadable file"}

    try:
        with open(target, encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
    except OSError as exc:
        return {"ok": False, "error": str(exc)}

    total = len(all_lines)
    s = max(1, start_line) - 1
    e = min(total, end_line) if end_line else min(total, s + _MAX_FILE_LINES)

    lines = all_lines[s:e]
    content = "".join(lines)
    return {
        "ok": True,
        "path": file_path,
        "total_lines": total,
        "start_line": s + 1,
        "end_line": e,
        "truncated": (e - s) >= _MAX_FILE_LINES and e < total,
        "content": content,
        "size_bytes": target.stat().st_size,
    }


def probe_capabilities() -> dict[str, Any]:
    """Return ADN's own capabilities + registered route count."""
    try:
        from argos_data_nexus import mesh as mesh_mod
        reg = mesh_mod._load_registry()
        adn = reg.get("services", {}).get("adn", {})
    except Exception:
        adn = {}

    # Count routes in the package
    py_files = list(_source_root().rglob("*.py"))
    route_count = 0
    module_count = 0
    for pf in py_files:
        if "_pycache_" in str(pf) or not _is_safe_path(pf, _source_root()):
            continue
        module_count += 1
        try:
            txt = pf.read_text(encoding="utf-8", errors="replace")
            route_count += txt.count("@app.") + txt.count("@router.")
        except OSError:
            pass

    return {
        "ok": True,
        "service": "daedalus" if adn_package_root() is None else "adn",
        "name": "Daedalus" if adn_package_root() is None else "ArgosDataNexus",
        "capabilities": adn.get("capabilities", []),
        "probe_ops": ["file_tree", "read_file", "capabilities"],
        "source_stats": {
            "py_modules": module_count,
            "route_decorators": route_count,
            "root_path": str(_source_root()),
        },
    }


def probe_schema() -> dict[str, Any]:
    """Return ADN data model / schema info."""
    schema_files: list[dict[str, Any]] = []
    models_dir = _source_root() / "models"
    if models_dir.exists():
        for f in sorted(models_dir.glob("*.py")):
            schema_files.append({"file": str(f.relative_to(_source_root())), "size": f.stat().st_size})
    return {
        "ok": True,
        "note": "ADN is a stateless gateway — no ORM. Schema files are response_models.",
        "model_files": schema_files,
        "database": None,
    }


# ---------------------------------------------------------------------------
# Dispatch — incoming mesh action on ADN
# ---------------------------------------------------------------------------

def handle_probe_action(payload: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a daedalus.probe action payload. Called from mesh.py self-endpoint."""
    op = payload.get("op", "capabilities")
    try:
        if op == "file_tree":
            return probe_file_tree(
                sub_path=payload.get("path", ""),
                glob_pattern=payload.get("glob", ""),
                max_depth=int(payload.get("max_depth", 4)),
                include_hidden=bool(payload.get("include_hidden", False)),
            )
        elif op == "read_file":
            return probe_read_file(
                file_path=payload.get("path", ""),
                start_line=int(payload.get("start_line", 1)),
                end_line=payload.get("end_line"),
            )
        elif op == "schema":
            return probe_schema()
        elif op == "capabilities":
            return probe_capabilities()
        else:
            return {"ok": False, "error": f"unknown probe op '{op}'. Valid: file_tree, read_file, capabilities, schema"}
    except Exception as exc:
        log.exception("probe_action op=%s failed", op)
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Client — probe other services via mesh
# ---------------------------------------------------------------------------

async def probe_service(
    slug: str,
    op: str = "capabilities",
    path: str = "",
    glob_pattern: str = "",
    max_depth: int = 4,
    start_line: int = 1,
    end_line: int | None = None,
    timeout: float = 20.0,
) -> dict[str, Any]:
    """Send a daedalus.probe request to a remote mesh service.

    For ADN self-probe, calls handle_probe_action() directly (no HTTP).
    """
    if slug in ("adn", "self"):
        return handle_probe_action({
            "op": op, "path": path, "glob": glob_pattern,
            "max_depth": max_depth, "start_line": start_line, "end_line": end_line,
        })

    # R5 (retired 2026-11-09): probing another service went over the old Iyveris mesh, which standalone Daedalus
    # does not have. Only the self-probe above is real.
    return {"ok": False, "error": "mesh_retired", "slug": slug}
