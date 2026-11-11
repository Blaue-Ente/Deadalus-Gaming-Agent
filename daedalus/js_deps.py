"""Packages a JS/TS project imports but does not declare in package.json.

Found live (2026-11-11, Expo Habit Sprout): the writer rewrote package.json and dropped @tanstack/react-query while
src/app/_layout.tsx still imported it, so the app could never bundle. The writer is told right after its write."""
from __future__ import annotations

import json
import re
from pathlib import Path

_SRC_EXT = (".js", ".jsx", ".ts", ".tsx", ".mjs")
_SKIP_DIRS = {"node_modules", "dist", "build", ".expo", ".git", "studio", "playtest", "dist-web"}
_IMPORT_RE = re.compile(
    r"""(?:^|[^\w$.])(?:import\s+(?:[\w*{}\s,$]+\s+from\s+)?|export\s+[\w*{}\s,$]+\s+from\s+|require\s*\(\s*|import\s*\(\s*)"""
    r"""['"]([^'"\s]+)['"]""",
    re.M,
)
_NODE_BUILTINS = frozenset({
    "fs", "path", "os", "url", "util", "crypto", "events", "stream", "http", "https", "child_process", "buffer",
    "assert", "zlib", "net", "tls", "readline", "process", "querystring", "worker_threads", "module", "timers",
})


def _package_name(spec: str) -> str | None:
    if spec.startswith((".", "/", "@/", "~/", "#")) or ":" in spec or spec.startswith("virtual:"):
        return None
    parts = spec.split("/")
    name = "/".join(parts[:2]) if spec.startswith("@") else parts[0]
    if name in _NODE_BUILTINS or not name:
        return None
    return name


def undeclared(root: Path, *, max_files: int = 400) -> list[str]:
    """Sorted bare package names imported by the project's sources and missing from package.json."""
    pkg = root / "package.json"
    if not pkg.is_file():
        return []
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    declared: set[str] = set()
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        if isinstance(data.get(key), dict):
            declared |= set(data[key])
    seen: set[str] = set()
    count = 0
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if any(part in _SKIP_DIRS for part in rel.parts) or path.suffix not in _SRC_EXT or not path.is_file():
            continue
        count += 1
        if count > max_files:
            break
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for spec in _IMPORT_RE.findall(text):
            name = _package_name(spec)
            if name and name not in declared:
                seen.add(name)
    # A few names are provided by another declared package (expo bundles these through its own deps).
    if "expo" in declared:
        seen -= {"expo-modules-core", "@expo/vector-icons", "expo-asset", "expo-font", "expo-file-system"}
    if "react-native" in declared:
        seen -= {"react-native"}
    return sorted(seen)


def undeclared_note(root: Path) -> str:
    names = undeclared(root)
    if not names:
        return ""
    listed = ", ".join(names[:12])
    return (f"\n⚠ MISSING DEPENDENCIES: the code imports {listed} but package.json does not declare "
            f"{'it' if len(names) == 1 else 'them'}. The build will fail. Add them to package.json dependencies "
            "(or remove the imports) before you stop.")
