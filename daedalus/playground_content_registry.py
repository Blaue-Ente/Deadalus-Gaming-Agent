"""Content registry — entities.json / levels.json data layer between micro-files."""
from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Any

from .playground_file_trunk import FileTrunk

ENTITIES_PATH = "src/registry/entities.json"
LEVELS_PATH = "src/registry/levels.json"
UNITY_ENTITIES_PATH = "Assets/_Project/Data/Registry/entities.json"
UNITY_LEVELS_PATH = "Assets/_Project/Data/Registry/levels.json"
REGISTRY_README = "src/registry/README.md"

_EMPTY_ENTITIES = {"version": 1, "entities": {}}
_EMPTY_LEVELS = {"version": 1, "levels": {}}


def _load_json(trunk: FileTrunk, path: str, default: dict) -> dict:
    raw = trunk.trunk.get(path) or trunk.branches.get(path, None)
    content = raw.content if hasattr(raw, "content") else raw
    if not content:
        return dict(default)
    try:
        data = json.loads(content)
        return data if isinstance(data, dict) else dict(default)
    except Exception:
        return dict(default)


def _dump(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def bootstrap_registry(trunk: FileTrunk) -> list[str]:
    """Ensure registry files exist on trunk. Returns paths created."""
    created: list[str] = []
    for ep, lp in (
        (ENTITIES_PATH, LEVELS_PATH),
        (UNITY_ENTITIES_PATH, UNITY_LEVELS_PATH),
    ):
        if ep not in trunk.trunk:
            trunk.trunk[ep] = _dump(_EMPTY_ENTITIES)
            created.append(ep)
        if lp not in trunk.trunk:
            trunk.trunk[lp] = _dump(_EMPTY_LEVELS)
            created.append(lp)
    if REGISTRY_README not in trunk.trunk:
        trunk.trunk[REGISTRY_README] = (
            "# Content Registry\n\n"
            "- `entities.json` — герои, врагове, NPC (path + meta)\n"
            "- `levels.json` — нива/сцени (path + meta)\n\n"
            "Orchestratorът обновява тези файлове след всеки merge към trunk.\n"
        )
        created.append(REGISTRY_README)
    return created


def _entity_id_from_path(path: str) -> str:
    name = PurePosixPath(path.replace("\\", "/")).stem
    return name


def _level_id_from_path(path: str) -> str:
    stem = PurePosixPath(path.replace("\\", "/")).stem
    m = re.search(r"(\d+|[a-z]+)$", stem, re.I)
    return m.group(1) if m else stem


def _registry_paths(for_path: str) -> tuple[str, str]:
    norm = for_path.replace("\\", "/")
    if norm.startswith("Assets/"):
        return UNITY_ENTITIES_PATH, UNITY_LEVELS_PATH
    return ENTITIES_PATH, LEVELS_PATH


def register_merged_file(
    trunk: FileTrunk,
    path: str,
    stage: dict[str, Any],
    *,
    task_id: str = "",
) -> list[str]:
    """Update registry JSON after a file merges to trunk. Returns updated paths."""
    bootstrap_registry(trunk)
    updated: list[str] = []
    fkind = str(stage.get("fragment_kind") or "")
    low = path.lower()

    if fkind == "entity" or any(x in low for x in ("hero", "enemy", "boss", "player", "npc", "entity")):
        ent_path, _ = _registry_paths(path)
        data = _load_json(trunk, ent_path, _EMPTY_ENTITIES)
        entities = data.setdefault("entities", {})
        eid = _entity_id_from_path(path)
        entities[eid] = {
            "path": path,
            "stage_id": stage.get("id"),
            "task_id": task_id,
            "title": stage.get("title", ""),
            "status": "merged",
        }
        trunk.trunk[ent_path] = _dump(data)
        updated.append(ent_path)

    if fkind == "level" or "/levels/" in low or "level" in PurePosixPath(path).stem.lower():
        _, lvl_path = _registry_paths(path)
        data = _load_json(trunk, lvl_path, _EMPTY_LEVELS)
        levels = data.setdefault("levels", {})
        lid = _level_id_from_path(path)
        levels[lid] = {
            "path": path,
            "stage_id": stage.get("id"),
            "task_id": task_id,
            "title": stage.get("title", ""),
            "status": "merged",
        }
        trunk.trunk[lvl_path] = _dump(data)
        updated.append(lvl_path)

    return updated


def registry_prompt_block(trunk: FileTrunk) -> str:
    """Context snippet for builders — what's already in the registry."""
    parts: list[str] = []
    ent = _load_json(trunk, ENTITIES_PATH, _EMPTY_ENTITIES).get("entities") or {}
    ent_u = _load_json(trunk, UNITY_ENTITIES_PATH, _EMPTY_ENTITIES).get("entities") or {}
    lv = _load_json(trunk, LEVELS_PATH, _EMPTY_LEVELS).get("levels") or {}
    lv_u = _load_json(trunk, UNITY_LEVELS_PATH, _EMPTY_LEVELS).get("levels") or {}
    merged_ent = {**ent, **ent_u}
    merged_lv = {**lv, **lv_u}
    if merged_ent:
        parts.append("ENTITIES (registry): " + ", ".join(
            f"{k}→{v.get('path', '?')}" for k, v in list(merged_ent.items())[:12]
        ))
    if merged_lv:
        parts.append("LEVELS (registry): " + ", ".join(
            f"{k}→{v.get('path', '?')}" for k, v in list(merged_lv.items())[:12]
        ))
    if not parts:
        return ""
    return "\n".join(parts) + "\n"
