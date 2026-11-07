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

    from .tools import mesh_call_tool
    payload: dict[str, Any] = {"op": op}
    if path:
        payload["path"] = path
    if glob_pattern:
        payload["glob"] = glob_pattern
    if op == "file_tree":
        payload["max_depth"] = max_depth
    if op == "read_file":
        payload["start_line"] = start_line
        if end_line:
            payload["end_line"] = end_line

    try:
        result = await mesh_call_tool(slug, "daedalus.probe", payload, timeout=timeout)
        return result
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Probe snippet generator — template code each other app must add
# ---------------------------------------------------------------------------

JAVASCRIPT_PROBE_SNIPPET = '''\
// ─────────────────────────────────────────────────────────────────────────────
// Daedalus Probe — добави това в /api/iyveris action dispatcher (Node.js / Express)
// Required: action === "daedalus.probe"
// ─────────────────────────────────────────────────────────────────────────────
import fs from 'fs';
import path from 'path';

const PROBE_ROOT = path.resolve(process.cwd()); // adjust to your app root
const PROBE_BLOCKED = new Set(['.env', 'secrets', '.git', 'private_key', 'node_modules']);
const PROBE_BLOCKED_EXT = new Set(['.env', '.key', '.pem', '.p12']);
const PROBE_BINARY_EXT = new Set(['.png', '.jpg', '.jpeg', '.gif', '.ico',
  '.woff', '.woff2', '.ttf', '.zip', '.tar', '.gz', '.db', '.sqlite']);
const MAX_LINES = 400;
const MAX_FILES = 200;

function probeIsSafe(p) {
  const resolved = path.resolve(p);
  if (!resolved.startsWith(PROBE_ROOT)) return false;
  const parts = resolved.split(path.sep);
  return !parts.some(part => PROBE_BLOCKED.has(part));
}

function probeDispatch(payload = {}) {
  const op = payload.op || 'capabilities';

  if (op === 'file_tree') {
    const sub = payload.path || '';
    const root = sub ? path.resolve(PROBE_ROOT, sub) : PROBE_ROOT;
    if (!probeIsSafe(root)) return { ok: false, error: 'unsafe path' };
    const globPat = payload.glob || '';
    const maxDepth = parseInt(payload.max_depth) || 3;
    const entries = [];

    function walk(dir, depth) {
      if (depth > maxDepth || entries.length >= MAX_FILES) return;
      let items;
      try { items = fs.readdirSync(dir, { withFileTypes: true }); }
      catch { return; }
      for (const item of items.sort((a, b) => a.name.localeCompare(b.name))) {
        if (entries.length >= MAX_FILES) break;
        if (item.name.startsWith('.') || PROBE_BLOCKED.has(item.name)) continue;
        const full = path.join(dir, item.name);
        if (!probeIsSafe(full)) continue;
        const rel = path.relative(PROBE_ROOT, full);
        if (item.isDirectory()) {
          entries.push({ type: 'dir', path: rel, depth });
          walk(full, depth + 1);
        } else if (item.isFile()) {
          if (globPat) {
            const rx = new RegExp('^' + globPat.replace(/\./g, '\\.').replace(/\*/g, '.*') + '$');
            if (!rx.test(item.name)) continue;
          }
          const ext = path.extname(item.name).toLowerCase();
          entries.push({
            type: 'file', path: rel, depth,
            size_bytes: fs.statSync(full).size,
            binary: PROBE_BINARY_EXT.has(ext),
          });
        }
      }
    }
    walk(root, 1);
    return { ok: true, entries, truncated: entries.length >= MAX_FILES };
  }

  if (op === 'read_file') {
    const fp = path.resolve(PROBE_ROOT, payload.path || '');
    if (!probeIsSafe(fp) || !fs.existsSync(fp)) return { ok: false, error: 'not found or unsafe' };
    try {
      const lines = fs.readFileSync(fp, 'utf8').split('\\n');
      const s = Math.max(0, (parseInt(payload.start_line) || 1) - 1);
      const e = Math.min(lines.length, parseInt(payload.end_line) || s + MAX_LINES);
      return {
        ok: true, path: payload.path, total_lines: lines.length,
        start_line: s + 1, end_line: e,
        content: lines.slice(s, e).join('\\n'),
      };
    } catch (err) { return { ok: false, error: err.message }; }
  }

  if (op === 'capabilities') {
    return {
      ok: true,
      service: process.env.SERVICE_SLUG || 'unknown',
      probe_ops: ['file_tree', 'read_file', 'capabilities'],
      stack: 'node.js',
      // добави специфичните capabilities на твоето приложение тук
      capabilities: [],
    };
  }

  if (op === 'schema') {
    // Ако използваш Drizzle / Prisma / Mongoose — върни schema информация тук
    return { ok: true, note: 'schema not implemented', database: null };
  }

  return { ok: false, error: `unknown op: ${op}` };
}

// В твоя action dispatcher:
// } else if (action === 'daedalus.probe') {
//   return res.json(probeDispatch(payload));
// }
// ─────────────────────────────────────────────────────────────────────────────
'''

PYTHON_PROBE_SNIPPET = '''\
# ─────────────────────────────────────────────────────────────────────────────
# Daedalus Probe — add this inside your POST /api/iyveris action dispatcher
# Required: action == "daedalus.probe"
# ─────────────────────────────────────────────────────────────────────────────
import fnmatch
import os
from pathlib import Path

_PROBE_ROOT = Path(__file__).resolve().parent  # adjust to your app root
_PROBE_BLOCKED = {".env", "secrets", ".git", "private_key"}
_PROBE_BLOCKED_EXT = {".env", ".key", ".pem", ".p12"}
_PROBE_BINARY_EXT = {".pyc", ".so", ".db", ".sqlite", ".png", ".jpg", ".zip"}
_MAX_LINES = 400
_MAX_FILES = 200


def _probe_safe(p: Path) -> bool:
    try:
        p.resolve().relative_to(_PROBE_ROOT)
    except ValueError:
        return False
    for part in p.parts:
        if part in _PROBE_BLOCKED:
            return False
    if p.suffix.lower() in _PROBE_BLOCKED_EXT:
        return False
    return True


def _probe_dispatch(payload: dict) -> dict:
    op = payload.get("op", "capabilities")

    if op == "file_tree":
        root = _PROBE_ROOT
        sub = payload.get("path", "")
        if sub:
            root = (_PROBE_ROOT / sub).resolve()
            if not _probe_safe(root):
                return {"ok": False, "error": "unsafe path"}
        glob_pat = payload.get("glob", "")
        max_depth = int(payload.get("max_depth", 3))
        entries = []
        count = [0]

        def walk(d: Path, depth: int):
            if depth > max_depth or count[0] >= _MAX_FILES:
                return
            try:
                for item in sorted(d.iterdir(), key=lambda p: (p.is_file(), p.name)):
                    if count[0] >= _MAX_FILES:
                        break
                    if item.name.startswith(".") or item.name in _PROBE_BLOCKED:
                        continue
                    if not _probe_safe(item):
                        continue
                    rel = str(item.relative_to(_PROBE_ROOT))
                    if item.is_dir():
                        entries.append({"type": "dir", "path": rel, "depth": depth})
                        walk(item, depth + 1)
                    elif item.is_file():
                        if glob_pat and not fnmatch.fnmatch(item.name, glob_pat):
                            continue
                        entries.append({
                            "type": "file", "path": rel, "depth": depth,
                            "size_bytes": item.stat().st_size,
                            "binary": item.suffix.lower() in _PROBE_BINARY_EXT,
                        })
                        count[0] += 1
            except PermissionError:
                pass

        walk(root, 1)
        return {"ok": True, "entries": entries, "truncated": count[0] >= _MAX_FILES}

    elif op == "read_file":
        fp = _PROBE_ROOT / payload.get("path", "")
        if not _probe_safe(fp) or not fp.is_file():
            return {"ok": False, "error": "not found or unsafe"}
        try:
            lines = fp.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        except OSError as e:
            return {"ok": False, "error": str(e)}
        s = max(0, int(payload.get("start_line", 1)) - 1)
        e = min(len(lines), int(payload.get("end_line", s + _MAX_LINES)))
        return {
            "ok": True, "path": payload.get("path"),
            "total_lines": len(lines), "start_line": s + 1, "end_line": e,
            "content": "".join(lines[s:e]),
        }

    elif op == "capabilities":
        return {
            "ok": True,
            "service": os.getenv("SERVICE_SLUG", "unknown"),
            "probe_ops": ["file_tree", "read_file", "capabilities"],
            # Add your app-specific capabilities here
            "capabilities": [],
        }

    return {"ok": False, "error": f"unknown op: {op}"}


# In your action dispatcher:
# elif action == "daedalus.probe":
#     return _probe_dispatch(payload)
# ─────────────────────────────────────────────────────────────────────────────
'''
