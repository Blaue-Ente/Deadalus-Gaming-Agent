"""Asset contracts — poly/tex/collision/pivot/naming/export before a mesh ships.

Week 1: schema + filename/field checks. Cleanup pass (decimate/LOD) stays a
Blender job the owner runs. Never let a raw generative mesh skip this object.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

NAMING_RE = re.compile(r"^[a-z][a-z0-9_]*$")

DEFAULT_CONTRACT: dict[str, Any] = {
    "name": "greybox_proxy",
    "poly_budget": 15000,
    "tex_budget": 2048,
    "collision": "box",
    "scale": 1.0,
    "pivot": "bottom_center",
    "naming": "snake",
    "export_target": "res://assets/greybox_proxy.glb",
    "license": "original",
}

ALLOWED_COLLISION = frozenset({"box", "convex", "trimesh", "none", "capsule"})
ALLOWED_PIVOT = frozenset({"bottom_center", "center", "origin"})


def normalize(raw: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(DEFAULT_CONTRACT)
    for k, v in (raw or {}).items():
        if k in out or k in ("notes", "source"):
            out[k] = v
    return out


def check(contract: dict[str, Any], *, stats: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return {ok, issues}. stats may include tris, tex_size, name."""
    c = normalize(contract)
    issues: list[str] = []
    name = str(c.get("name") or "")
    if not NAMING_RE.match(name):
        issues.append(f"naming: {name!r} is not snake_case")
    coll = str(c.get("collision") or "")
    if coll not in ALLOWED_COLLISION:
        issues.append(f"collision {coll!r} not in {sorted(ALLOWED_COLLISION)}")
    pivot = str(c.get("pivot") or "")
    if pivot not in ALLOWED_PIVOT:
        issues.append(f"pivot {pivot!r} not in {sorted(ALLOWED_PIVOT)}")
    try:
        scale = float(c.get("scale") or 1)
        if scale <= 0 or scale > 20:
            issues.append(f"scale {scale} outside (0, 20]")
    except (TypeError, ValueError):
        issues.append("scale is not a number")
    target = str(c.get("export_target") or "")
    if not target.startswith("res://assets/") or not target.endswith(".glb"):
        issues.append("export_target must be res://assets/*.glb")
    st = stats or {}
    tris = st.get("tris")
    if tris is not None:
        try:
            if int(tris) > int(c["poly_budget"]):
                issues.append(f"tris {tris} > poly_budget {c['poly_budget']}")
        except (TypeError, ValueError):
            issues.append("stats.tris is not an int")
    tex = st.get("tex_size")
    if tex is not None:
        try:
            if int(tex) > int(c["tex_budget"]):
                issues.append(f"tex {tex} > tex_budget {c['tex_budget']}")
        except (TypeError, ValueError):
            issues.append("stats.tex_size is not an int")
    return {"ok": not issues, "issues": issues, "contract": c}


def write_default(pdir: Path, *, name: str = "greybox_proxy") -> str:
    rel = "studio/asset_contract.json"
    payload = {
        "note": "Raw generative meshes do not skip this. Cleanup in Blender first.",
        "assets": [normalize({"name": name, "export_target": f"res://assets/{name}.glb"})],
    }
    target = Path(pdir) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return rel
