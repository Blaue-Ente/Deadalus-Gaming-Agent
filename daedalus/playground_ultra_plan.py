"""Ultra fragmentation — expand plan stages into atomic micro-tasks.

Each micro-task targets ONE file or ONE game entity/level so small models
can complete reliably. Used by guided orchestrator before build.
"""
from __future__ import annotations

import re
from typing import Any

from .playground_dependency_graph import annotate_stages

# Roles map to orchestrator model slots.
MODEL_ROLES = ("planner", "builder", "game_builder", "reviewer", "asset")

_LEVEL_PAT = re.compile(
    r"\b(level|ниво|етап|stage|world|зона|zone)\s*[#:]?\s*(\d+|[a-z]+)",
    re.I,
)
_ENTITY_PAT = re.compile(
    r"\b(hero|player|enemy|boss|герой|враг|противник|character|npc|"
    r"projectile|tile|block|joystick|hud|menu)\b",
    re.I,
)
_FILE_ENTITY = re.compile(
    r"(Hero|Player|Enemy|Boss|Level|Tile|Projectile|Joystick|HUD|Menu|Scene|Screen)",
)


def _role_for_file(path: str, is_game: bool) -> str:
    low = path.lower()
    if "scene" in low or "level" in low or "game" in low:
        return "game_builder" if is_game else "builder"
    if any(x in low for x in ("test", "spec", "__")):
        return "reviewer"
    return "game_builder" if is_game and _FILE_ENTITY.search(path) else "builder"


def _micro_stage(
    parent: dict[str, Any],
    *,
    micro_id: str,
    title: str,
    goal: str,
    instructions: str,
    files: list[str],
    model_role: str,
    uses_visuals: bool = False,
) -> dict[str, Any]:
    return {
        "id": micro_id,
        "parent_id": parent.get("id"),
        "title": title,
        "goal": goal,
        "instructions": instructions,
        "files": files,
        "uses_visuals": uses_visuals,
        "micro": True,
        "model_role": model_role,
        "status": parent.get("status", "pending"),
    }


def expand_stage_ultra(stage: dict[str, Any], *, kind: str, is_game: bool) -> list[dict[str, Any]]:
    """Split one plan stage into 1-file (or 1-entity) micro-stages."""
    files = list(stage.get("files") or [])
    sid = stage.get("id", 1)
    base_title = stage.get("title", f"Етап {sid}")
    base_goal = stage.get("goal", "")
    base_instr = stage.get("instructions", "")
    svis = bool(stage.get("uses_visuals"))

    # Already single-file and simple title → keep as one micro-task
    if len(files) <= 1 and not _LEVEL_PAT.search(base_instr + base_title):
        if files:
            return [_micro_stage(
                stage,
                micro_id=f"{sid}.1",
                title=f"{base_title} — {files[0]}",
                goal=f"Завършен {files[0]}: {base_goal}",
                instructions=(
                    f"Пиши САМО файла `{files[0]}`. MAX ~180 реда. "
                    f"{base_instr}"
                ),
                files=files,
                model_role=_role_for_file(files[0], is_game),
                uses_visuals=svis,
            )]
        return [dict(stage, micro=True, model_role="builder")]

    micros: list[dict[str, Any]] = []
    idx = 0

    # One micro-task per file (primary ultra split)
    for fpath in files:
        idx += 1
        role = _role_for_file(fpath, is_game)
        fname = fpath.rsplit("/", 1)[-1]
        micros.append(_micro_stage(
            stage,
            micro_id=f"{sid}.{idx}",
            title=f"{base_title} → {fname}",
            goal=f"Файл `{fpath}` е готов и синтактично завършен",
            instructions=(
                f"ORCHESTRATOR: Пиши ЕДИН файл `{fpath}`.\n"
                f"Контекст от родителския етап: {base_instr[:800]}\n"
                f"Не пиши други файлове. MAX ~180 реда. "
                f"Следвай платформата {kind}."
            ),
            files=[fpath],
            model_role=role,
            uses_visuals=svis and any(
                w in fpath.lower() for w in ("hero", "enemy", "sprite", "tile", "asset")
            ),
        ))

    # Game: extra split if instructions mention multiple levels (synthetic file hints)
    if is_game and kind in ("vite", "vite-react", "static", "expo"):
        levels = _LEVEL_PAT.findall(base_instr + " " + base_title)
        seen_level_files = {m["files"][0] for m in micros if m.get("files")}
        for _label, lvl_name in levels[:5]:
            hint_path = f"src/levels/Level{lvl_name}.js"
            if hint_path in seen_level_files:
                continue
            idx += 1
            micros.append(_micro_stage(
                stage,
                micro_id=f"{sid}.{idx}",
                title=f"{base_title} → Ниво {lvl_name}",
                goal=f"Логика/сцена за ниво {lvl_name}",
                instructions=(
                    f"Създай `{hint_path}` — само това ниво. "
                    f"Родителски етап: {base_instr[:600]}"
                ),
                files=[hint_path],
                model_role="game_builder",
                uses_visuals=svis,
            ))
            seen_level_files.add(hint_path)

    return micros or [dict(stage, micro=True)]


def ultra_expand_plan(
    plan: dict[str, Any],
    *,
    kind: str,
    is_game: bool,
    max_micro_per_stage: int = 12,
) -> dict[str, Any]:
    """Expand every stage into micro-tasks; renumber ids sequentially."""
    stages_in = plan.get("stages") or []
    out_stages: list[dict[str, Any]] = []
    new_id = 1
    for stage in stages_in:
        if stage.get("micro") and len(stage.get("files") or []) <= 1:
            s = dict(stage)
            s["id"] = new_id
            out_stages.append(s)
            new_id += 1
            continue
        micros = expand_stage_ultra(stage, kind=kind, is_game=is_game)[:max_micro_per_stage]
        for m in micros:
            m = dict(m)
            m["id"] = new_id
            m["ultra_parent_title"] = stage.get("title", "")
            out_stages.append(m)
            new_id += 1
    title = plan.get("title", "План")
    return {
        **plan,
        "title": f"{title} (ultra ×{len(out_stages)})",
        "ultra": True,
        "stages": out_stages,
        "stage_count_before": len(stages_in),
        "stage_count_after": len(out_stages),
    }


ULTRA_PLANNER_ADDENDUM = """
━━━ ULTRA FRAGMENTATION (оркестратор) — ЗАДЪЛЖИТЕЛНО при ultra ━━━
Потребителят иска КОМПЛЕКСЕН проект с micro-задачи от самото начало.

1) В корена на JSON добави:
   "ultra": true
   "fragment_command": {
     "strategy": "per_file|per_entity|per_level|hybrid",
     "entities": ["Hero","Enemy","Boss"],
     "levels": ["1","2","3"],
     "systems": ["input","collision","audio","hud"]
   }

2) Всеки stage:
   • "files": ТОЧНО 1 път
   • "micro": true
   • "fragment_kind": "file"|"entity"|"level"|"system"|"scaffold"
   • "model_role": "builder"|"game_builder"|"reviewer"

3) Игри с 5+ нива / 3+ героя → 15–40 micro-етапа, НЕ 8 общи етапа.
4) Scaffold (package.json, vite.config) = отделен micro-етап с model_role builder.
5) Hero.jsx, Enemy.jsx, Level1.js — ВИНАГИ отделни етапи.
"""

ULTRA_PLAN_JSON_FORMAT = (
    'ФОРМАТ (ultra): {"title":"...","ultra":true,"fragment_command":{"strategy":"hybrid",'
    '"entities":["Hero"],"levels":["1","2"],"systems":["hud"]},'
    '"stages":[{"id":1,"title":"...","goal":"...","instructions":"...",'
    '"uses_visuals":false,"files":["src/Hero.js"],"micro":true,'
    '"fragment_kind":"entity","model_role":"game_builder"}]}'
)


def _path_for_entity(name: str, kind: str) -> str:
    if kind == "unity":
        if "level" in name.lower() or name.lower().startswith("level"):
            return f"Assets/_Project/Code/Gameplay/Levels/{name}.cs"
        return f"Assets/_Project/Code/Gameplay/{name}.cs"
    ext = "tsx" if kind == "expo" else "jsx" if kind == "vite-react" else "js"
    if kind == "expo":
        return f"src/components/{name}.{ext}"
    if kind in ("vite-react",):
        return f"src/components/{name}.{ext}"
    if "level" in name.lower() or name.lower().startswith("level"):
        return f"src/levels/{name}.{ext}"
    return f"src/{name}.{ext}"


def expand_from_fragment_command(
    plan: dict[str, Any],
    cmd: dict[str, Any],
    *,
    kind: str,
    is_game: bool,
    description: str = "",
) -> list[dict[str, Any]]:
    """Build micro-stages from LLM fragment_command before rule-based split."""
    strategy = (cmd.get("strategy") or "hybrid").lower()
    stages_out: list[dict[str, Any]] = []
    idx = 0
    base_instr = description[:400]

    def _add(title: str, path: str, fkind: str, role: str | None = None) -> None:
        nonlocal idx
        idx += 1
        stages_out.append({
            "id": idx,
            "title": title,
            "goal": f"Завършен `{path}`",
            "instructions": (
                f"ORCHESTRATOR ultra: пиши САМО `{path}`. "
                f"fragment_kind={fkind}. MAX ~180 реда. {base_instr}"
            ),
            "files": [path],
            "uses_visuals": fkind in ("entity", "level") and is_game,
            "micro": True,
            "fragment_kind": fkind,
            "model_role": role or _role_for_file(path, is_game),
            "status": "pending",
        })

    if strategy in ("per_file", "hybrid"):
        for stage in plan.get("stages") or []:
            for fpath in stage.get("files") or []:
                fname = fpath.rsplit("/", 1)[-1]
                _add(
                    f"{stage.get('title', 'Етап')} → {fname}",
                    fpath,
                    stage.get("fragment_kind") or "file",
                )

    if strategy in ("per_entity", "hybrid") and is_game:
        for ent in cmd.get("entities") or []:
            name = str(ent).strip()
            if not name:
                continue
            path = _path_for_entity(name, kind)
            if any(s["files"][0] == path for s in stages_out if s.get("files")):
                continue
            _add(f"Entity → {name}", path, "entity", "game_builder")

    if strategy in ("per_level", "hybrid") and is_game:
        for lvl in cmd.get("levels") or []:
            lvl_s = str(lvl).strip()
            if not lvl_s:
                continue
            ext = "tsx" if kind == "expo" else "jsx" if kind == "vite-react" else "js"
            path = f"src/levels/Level{lvl_s}.{ext}"
            if any(s["files"][0] == path for s in stages_out if s.get("files")):
                continue
            _add(f"Ниво {lvl_s}", path, "level", "game_builder")

    for sys_name in cmd.get("systems") or []:
        s = str(sys_name).strip().lower()
        if not s:
            continue
        ext = "ts" if kind == "expo" else "js"
        path = f"src/systems/{s}.{ext}"
        if any(st.get("files") and st["files"][0] == path for st in stages_out):
            continue
        _add(f"System → {s}", path, "system", "builder")

    return stages_out


def _plan_already_ultra_micro(stages: list[dict[str, Any]]) -> bool:
    if not stages:
        return False
    return all(
        isinstance(s, dict)
        and s.get("micro")
        and len(s.get("files") or []) <= 1
        for s in stages
    )


def finalize_ultra_plan(
    plan: dict[str, Any],
    *,
    kind: str,
    is_game: bool,
    ultra_requested: bool = False,
    description: str = "",
) -> dict[str, Any]:
    """Apply LLM fragment hints, then rule-based ultra expansion if needed."""
    want_ultra = ultra_requested or bool(plan.get("ultra"))
    if not want_ultra:
        return plan

    cmd = plan.get("fragment_command")
    if isinstance(cmd, dict) and cmd:
        from_cmd = expand_from_fragment_command(
            plan, cmd, kind=kind, is_game=is_game, description=description,
        )
        if len(from_cmd) >= 2:
            title = plan.get("title", "План")
            return {
                **plan,
                "title": f"{title} (ultra LLM ×{len(from_cmd)})",
                "ultra": True,
                "stages": from_cmd,
                "stage_count_before": len(plan.get("stages") or []),
                "stage_count_after": len(from_cmd),
                "ultra_source": "llm_fragment_command",
            }

    stages = plan.get("stages") or []
    if _plan_already_ultra_micro(stages) and len(stages) >= 3:
        return {
            **plan,
            "ultra": True,
            "ultra_source": "llm_micro_stages",
            "stages": annotate_stages(stages),
        }

    expanded = ultra_expand_plan(plan, kind=kind, is_game=is_game)
    expanded["stages"] = annotate_stages(expanded.get("stages") or [])
    return expanded

