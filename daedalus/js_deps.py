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
# node's module.builtinModules (v22), without the "node:" forms (any spec with ":" is skipped anyway).
_NODE_BUILTINS = frozenset({
    "assert", "async_hooks", "buffer", "child_process", "cluster", "console", "constants", "crypto", "dgram",
    "diagnostics_channel", "dns", "domain", "events", "fs", "http", "http2", "https", "inspector", "module", "net",
    "os", "path", "perf_hooks", "process", "punycode", "querystring", "readline", "repl", "stream", "string_decoder",
    "sys", "timers", "tls", "trace_events", "tty", "url", "util", "v8", "vm", "wasi", "worker_threads", "zlib",
})


def _aliases(root: Path) -> tuple[str, ...]:
    """Path-alias prefixes from tsconfig/jsconfig ``compilerOptions.paths`` and ``baseUrl`` folders (review 2026-11-11:
    `@components/Button` or `src/utils` were reported as missing npm packages)."""
    out: list[str] = []
    for name in ("tsconfig.json", "jsconfig.json"):
        try:
            text = (root / name).read_text(encoding="utf-8")
            text = re.sub(r'//[^\n"]*$', "", text, flags=re.M)        # JSONC line comments, not "https://"
            opts = (json.loads(text).get("compilerOptions") or {})
        except (OSError, ValueError, AttributeError):
            continue
        for key in (opts.get("paths") or {}):
            prefix = str(key).split("*", 1)[0]
            if prefix:
                out.append(prefix)
        if opts.get("baseUrl"):
            base = (root / str(opts["baseUrl"])).resolve()
            if base.is_dir():
                out.extend(p.name + "/" for p in base.iterdir() if p.is_dir() and p.name != "node_modules")
    return tuple(dict.fromkeys(out))


def _package_name(spec: str, aliases: tuple[str, ...] = ()) -> str | None:
    if spec.startswith((".", "/", "@/", "~/", "#")) or ":" in spec or spec.startswith("virtual:"):
        return None
    if aliases and spec.startswith(aliases):
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
    import os

    aliases = _aliases(root)
    seen: set[str] = set()
    count = 0
    for base, dirs, files in os.walk(root):          # prune before descending: never walk node_modules
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
        for f in files:
            if not f.endswith(_SRC_EXT):
                continue
            count += 1
            if count > max_files:
                break
            try:
                text = Path(base, f).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for spec in _IMPORT_RE.findall(text):
                name = _package_name(spec, aliases)
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
