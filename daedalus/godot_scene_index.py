"""Compact Godot project index — the agent reads this, not a 4k-line .tscn.

Node tree, scripts by role, ext resources, signals. File-first; no Editor.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_NODE_RE = re.compile(
    r'^\[node name="([^"]+)" type="([^"]+)"(?: parent="([^"]*)")?',
    re.M,
)
_EXT_RE = re.compile(
    r'^\[ext_resource type="([^"]+)"[^\]]*path="([^"]+)"',
    re.M,
)
_SIGNAL_RE = re.compile(
    r"^(\w+)\s*=\s*Callable",
    re.M,
)
_CONNECT_RE = re.compile(
    r"\.connect\(",
)
_TSCN_CONN_RE = re.compile(
    r'^\[connection\s+signal="([^"]+)"\s+from="([^"]+)"\s+to="([^"]+)"\s+method="([^"]+)"',
    re.M,
)


def _role_for(path: str) -> str:
    low = path.replace("\\", "/").lower()
    if "autoload" in low:
        return "autoload"
    if "player" in low:
        return "player"
    if "plate" in low or "door" in low or "puzzle" in low:
        return "puzzle"
    if "hud" in low or "debug" in low or "ui/" in low:
        return "hud"
    if "npc" in low or "enemy" in low:
        return "npc"
    if low.endswith(".gd"):
        return "script"
    if low.endswith(".tscn"):
        return "scene"
    return "other"


def index_tscn(text: str, *, rel: str = "") -> dict[str, Any]:
    nodes = []
    for name, ntype, parent in _NODE_RE.findall(text or ""):
        nodes.append({
            "name": name,
            "type": ntype,
            "parent": parent or ".",
        })
    resources = [
        {"type": t, "path": p} for t, p in _EXT_RE.findall(text or "")
    ]
    connections = [
        {"signal": sig, "from": src, "to": dst, "method": method}
        for sig, src, dst, method in _TSCN_CONN_RE.findall(text or "")
    ]
    return {
        "path": rel,
        "nodes": nodes,
        "resources": resources,
        "connections": connections,
        "node_count": len(nodes),
    }


def index_script(text: str, *, rel: str = "") -> dict[str, Any]:
    connects = len(_CONNECT_RE.findall(text or ""))
    return {
        "path": rel,
        "role": _role_for(rel),
        "extends": _extends(text),
        "connect_calls": connects,
        "lines": text.count("\n") + (1 if text else 0),
    }


def _extends(text: str) -> str:
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith("extends "):
            return s.split(None, 1)[1]
    return ""


def build(pdir: Path) -> dict[str, Any]:
    root = Path(pdir)
    scenes: list[dict[str, Any]] = []
    scripts: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith(".godot/") or "/.godot/" in rel:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if rel.endswith(".tscn"):
            scenes.append(index_tscn(text, rel=rel))
        elif rel.endswith(".gd"):
            scripts.append(index_script(text, rel=rel))
    types = sorted({n["type"] for s in scenes for n in s["nodes"]})
    return {
        "scenes": scenes,
        "scripts": scripts,
        "node_types": types,
        "scene_count": len(scenes),
        "script_count": len(scripts),
        "note": "Read this index instead of dumping full .tscn into the model.",
    }


def write(pdir: Path) -> str:
    rel = "studio/scene_index.json"
    payload = build(pdir)
    target = Path(pdir) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return rel
