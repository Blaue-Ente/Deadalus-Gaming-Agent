"""Game Studio Mode — brainstorm → GDD lite → platform/preset → project bootstrap.

Sprint GS-A: steps 1–3 + safe project start. Extends existing Studio mode; does not replace it.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .game_platform_guide import suggest_kind_heuristic
from .game_studio_skills import (
    AUDIENCE_OPTIONS,
    BRAINSTORM_GENRES,
    GDD_SECTIONS,
    PLATFORM_HINTS,
    section_title,
    systems_for_genre,
)
from .playground_studio_mode import (
    STUDIO_PRESETS,
    infer_studio_preset,
    list_presets,
    milestones_for_preset,
)
from .game_studio_gates import gates_for_build_mode, merge_gate_summary, run_gates
from .game_studio_build import (
    advance_milestone,
    build_description_for_milestone,
    ensure_plan_milestones,
    first_pending_index,
    handoff_preview,
    milestone_progress,
    milestone_summary,
    project_complete,
    resolve_current_milestone,
)
from .game_studio_verify import (
    can_advance_milestone,
    can_advance_with_proof_waiver,
    can_advance_with_unity_waiver,
    verify_summary_for_milestone,
)

GDD_PATH = "design/gdd-lite.md"
CONFIG_PATH = "design/game-studio.json"
BUILD_MODES = ("guided", "autopilot", "prototype")


def catalog() -> dict[str, Any]:
    """Static catalog for Game Studio wizard (no DB)."""
    return {
        "genres": list(BRAINSTORM_GENRES),
        "audiences": list(AUDIENCE_OPTIONS),
        "platform_hints": list(PLATFORM_HINTS),
        "build_modes": list(BUILD_MODES),
        "gdd_sections": list(GDD_SECTIONS),
        "presets": list_presets(),
    }


def normalize_brainstorm(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize brainstorm form payload."""
    raw = raw or {}
    genre = (raw.get("genre") or "other").strip().lower()
    if genre not in BRAINSTORM_GENRES:
        genre = "other"
    audience = (raw.get("audience") or "casual").strip().lower()
    if audience not in AUDIENCE_OPTIONS:
        audience = "casual"
    platform_hint = (raw.get("platform_hint") or "web").strip().lower()
    if platform_hint not in PLATFORM_HINTS:
        platform_hint = "web"

    return {
        "title": (raw.get("title") or raw.get("name") or "").strip(),
        "genre": genre,
        "idea": (raw.get("idea") or raw.get("description") or "").strip(),
        "audience": audience,
        "platform_hint": platform_hint,
        "unique_hook": (raw.get("unique_hook") or raw.get("hook") or "").strip(),
        "core_loop": (raw.get("core_loop") or "").strip(),
        "systems": _normalize_systems(raw.get("systems"), genre),
        "reference_games": (raw.get("reference_games") or "").strip(),
    }


def _normalize_systems(systems: Any, genre: str) -> list[str]:
    if isinstance(systems, list):
        out = [str(s).strip() for s in systems if str(s).strip()]
        if out:
            return out[:12]
    if isinstance(systems, str) and systems.strip():
        return [s.strip() for s in systems.replace(";", ",").split(",") if s.strip()][:12]
    return systems_for_genre(genre)


def _description_blob(brainstorm: dict[str, Any]) -> str:
    parts = [
        brainstorm.get("title", ""),
        brainstorm.get("idea", ""),
        brainstorm.get("unique_hook", ""),
        brainstorm.get("core_loop", ""),
        brainstorm.get("genre", ""),
        brainstorm.get("platform_hint", ""),
        " ".join(brainstorm.get("systems") or []),
        brainstorm.get("reference_games", ""),
    ]
    return " ".join(p for p in parts if p).strip()


def process_brainstorm(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Structure brainstorm + suggest preset/kind (rule-based, no LLM)."""
    brainstorm = normalize_brainstorm(raw)
    blob = _description_blob(brainstorm)

    preset = infer_studio_preset(blob)
    if brainstorm.get("genre") == "puzzle":
        preset = "puzzle"
    elif brainstorm.get("genre") == "platformer":
        preset = "platformer"
    elif brainstorm.get("genre") == "strategy":
        preset = "strategy"
    elif brainstorm.get("genre") == "survival":
        preset = "survival"
    elif brainstorm.get("genre") == "multiplayer":
        preset = "multiplayer"
    elif brainstorm.get("genre") == "rpg":
        preset = "rpg_collection"
    elif brainstorm.get("genre") == "racing":
        preset = "racing"

    platform_hint = brainstorm.get("platform_hint", "web")
    hint_blob = f"{blob} {platform_hint}"
    if platform_hint == "mobile":
        hint_blob += " iOS Android mobile touch"
    elif platform_hint == "desktop":
        hint_blob += " unity desktop"
    elif platform_hint == "cross_platform":
        hint_blob += " mobile web cross platform"

    kind, kind_reason = suggest_kind_heuristic(brainstorm.get("title", ""), hint_blob)

    preset_info = STUDIO_PRESETS.get(preset, STUDIO_PRESETS["custom"])
    recommended_kind = preset_info.get("recommended_kind", kind)
    if preset == "backend_api":
        kind = "fastapi"
        kind_reason = "Studio preset backend_api → FastAPI"
    elif recommended_kind and kind == "static" and recommended_kind != "static":
        kind = recommended_kind
        kind_reason = f"Studio preset {preset} → {recommended_kind}"

    pitch = _build_pitch(brainstorm)
    if not brainstorm.get("core_loop"):
        brainstorm["core_loop"] = _default_core_loop(brainstorm)

    return {
        "brainstorm": brainstorm,
        "pitch": pitch,
        "suggested": {
            "studio_preset": preset,
            "preset_label": preset_info.get("label", preset),
            "kind": kind,
            "kind_reason": kind_reason,
            "systems": brainstorm.get("systems") or [],
        },
        "warnings": _scope_warnings(brainstorm, preset),
    }


def _build_pitch(brainstorm: dict[str, Any]) -> str:
    title = brainstorm.get("title") or "Игра"
    idea = brainstorm.get("idea") or "интерактивно изживяване"
    hook = brainstorm.get("unique_hook")
    audience = brainstorm.get("audience", "casual")
    if hook:
        return f"{title}: {idea}. Уникален ъгъл: {hook}. Аудитория: {audience}."
    return f"{title}: {idea}. Аудитория: {audience}."


def _default_core_loop(brainstorm: dict[str, Any]) -> str:
    genre = brainstorm.get("genre", "other")
    loops = {
        "action": "Влез → бий врагове → печели награда → повтори",
        "puzzle": "Виж пъзел → направи ход → feedback → следващо ниво",
        "rpg": "Събери екип → битка → XP/loot → подобри герои",
        "strategy": "Планирай → изпълни ход → реакция на AI → ескалирай",
        "platformer": "Бягай/скачай → избягвай препятствия → стигни цел",
        "racing": "Ускори → чети пистата → turbo/риск → обиколка → позиция",
        "survival": "Събери ресурси → craft → оцелей → разшири база",
        "casual": "Кратка сесия → мигновен feedback → награда → replay",
        "multiplayer": "Match → играй рунд → резултат → rematch",
    }
    return loops.get(genre, "Действие → награда → мотивация за повторение")


def _scope_warnings(brainstorm: dict[str, Any], preset: str) -> list[str]:
    warnings: list[str] = []
    systems = brainstorm.get("systems") or []
    if len(systems) > 6:
        warnings.append("Много системи за MVP — фокусирай се върху 3–4 за M0–M3.")
    if preset in ("open_world", "multiplayer"):
        warnings.append(f"Preset '{preset}' е амбициозен — MVP ще е offline-first със scope-check.")
    if brainstorm.get("platform_hint") == "cross_platform":
        warnings.append("Cross-platform: започни с една платформа (web или mobile), после порт.")
    if not brainstorm.get("idea"):
        warnings.append("Добави кратко описание на идеята за по-добър GDD и план.")
    try:
        from .arcade_craft import scope_warnings as _craft_scope
        warnings.extend(_craft_scope(brainstorm))
    except Exception:
        pass
    return warnings


def resolve_platform_preset(
    brainstorm: dict[str, Any],
    *,
    studio_preset: str = "",
    kind: str = "",
) -> dict[str, Any]:
    """Resolve final kind + studio_preset (user override > inference)."""
    processed = process_brainstorm(brainstorm)
    out_preset = (studio_preset or "").strip() or processed["suggested"]["studio_preset"]
    if out_preset not in STUDIO_PRESETS:
        out_preset = processed["suggested"]["studio_preset"]

    out_kind = (kind or "").strip() or processed["suggested"]["kind"]
    _, kind_reason = suggest_kind_heuristic(
        brainstorm.get("title", ""),
        _description_blob(brainstorm) + f" {brainstorm.get('platform_hint', '')}",
    )
    if kind:
        out_kind = kind
        kind_reason = f"Избрана платформа: {kind}"

    preset_info = STUDIO_PRESETS.get(out_preset, STUDIO_PRESETS["custom"])
    if out_preset == "backend_api":
        out_kind = "fastapi"
    elif not kind and preset_info.get("recommended_kind"):
        rec = preset_info["recommended_kind"]
        if out_kind in ("static", "vite-react") and rec not in ("static",):
            out_kind = rec

    return {
        "studio_preset": out_preset,
        "preset_label": preset_info.get("label", out_preset),
        "kind": out_kind,
        "kind_reason": kind_reason,
        "milestones": milestones_for_preset(out_preset),
    }


def generate_gdd_lite(
    brainstorm: dict[str, Any],
    *,
    studio_preset: str = "",
    kind: str = "",
    dissection: dict[str, Any] | None = None,
) -> str:
    """Generate design/gdd-lite.md content (template-based)."""
    b = normalize_brainstorm(brainstorm)
    resolved = resolve_platform_preset(b, studio_preset=studio_preset, kind=kind)
    preset = resolved["studio_preset"]
    preset_label = resolved["preset_label"]
    effective_kind = resolved["kind"]
    pitch = _build_pitch(b)
    core_loop = b.get("core_loop") or _default_core_loop(b)
    systems = b.get("systems") or systems_for_genre(b.get("genre", "other"))
    milestones = resolved.get("milestones") or milestones_for_preset(preset)
    mvp_ms = milestones[:4] if len(milestones) >= 4 else milestones

    lines = [
        f"# GDD Lite — {b.get('title') or 'Нова игра'}",
        "",
        f"_Генерирано от Daedalus Game Studio · {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
        f"## {section_title('overview')}",
        "",
        pitch,
        "",
        f"- **Жанр:** {b.get('genre', 'other')}",
        f"- **Аудитория:** {b.get('audience', 'casual')}",
        f"- **Платформа (hint):** {b.get('platform_hint', 'web')}",
        f"- **Studio preset:** {preset_label} (`{preset}`)",
        f"- **Playground kind:** `{effective_kind}`",
        "",
    ]
    if b.get("reference_games"):
        lines.extend([f"- **Референции:** {b['reference_games']}", ""])
    if dissection and (dissection.get("title") or dissection.get("summary") or dissection.get("specimen_block")):
        from .dissection import format_specimen_block
        lines.extend([
            "## Дисекция (мостра)",
            "",
            dissection.get("specimen_block") or format_specimen_block(dissection),
            "",
        ])

    if b.get("unique_hook"):
        lines.extend([
            f"## Уникален ъгъл",
            "",
            b["unique_hook"],
            "",
        ])

    lines.extend([
        f"## {section_title('core_loop')}",
        "",
        core_loop,
        "",
        f"## {section_title('player_experience')}",
        "",
        _player_experience_block(b),
        "",
        f"## {section_title('systems')}",
        "",
    ])
    for sys in systems:
        lines.append(f"- **{sys}** — MVP: да")
    lines.append("")

    lines.extend([
        f"## {section_title('progression')}",
        "",
        _progression_block(preset),
        "",
        f"## {section_title('content_scope')}",
        "",
        _content_scope_block(b, preset),
        "",
        f"## {section_title('technical')}",
        "",
        f"- Engine/stack: Playground `{effective_kind}`",
        f"- Studio milestones: M0–M8 (виж по-долу)",
        f"- Assets: `assets/` в Playground workspace",
        "",
        f"## {section_title('mvp_scope')}",
        "",
        "MVP = първите 4 milestone-а (M0–M3):",
        "",
    ])
    for m in mvp_ms:
        lines.append(f"- **{m.get('id', '?')} — {m.get('title', '')}:** {m.get('goal', '')}")
    lines.append("")
    try:
        from .arcade_craft import gdd_section
        from .studio_shelf import prompt_block
        lines.append(gdd_section(b).rstrip())
        lines.append("")
        idea_blob = " ".join(
            str(x) for x in (b.get("genre"), b.get("idea"), b.get("reference_games")) if x
        )
        lines.append("## Studio shelf")
        lines.append("")
        lines.append(prompt_block(idea_blob, limit=8))
        lines.append("")
    except Exception:
        pass
    lines.append("---")
    lines.append("*Следваща стъпка: Studio план с `studio=true` и gate-check преди guided build (GS-B).*")
    lines.append("")
    return "\n".join(lines)


def _player_experience_block(b: dict[str, Any]) -> str:
    audience = b.get("audience", "casual")
    hints = {
        "kids": "Кратки сесии, ясни цели, без насилие, големи бутони.",
        "casual": "Лесен вход, бърз feedback, 1–3 мин сесии.",
        "core": "Дълбочина, mastery curve, meaningful choices.",
        "hardcore": "Висок skill ceiling, punishment, оптимизация.",
        "all_ages": "Достъпно за всички, scalable difficulty.",
    }
    return hints.get(audience, hints["casual"])


def _progression_block(preset: str) -> str:
    if preset in ("rpg_collection", "creature_rpg", "action"):
        return "XP, нива, unlock на способности/герои, колекция."
    if preset == "puzzle":
        return "Нива с rising difficulty, звезди, optional challenges."
    if preset == "racing":
        return "Обиколки, позиции, ghost/рекърд, unlock на пилоти и купи."
    if preset == "strategy":
        return "Tech tree, unit upgrades, campaign map."
    return "Краткосрочни цели (run) + дългосрочни unlock-и (meta)."


def _content_scope_block(b: dict[str, Any], preset: str) -> str:
    idea = b.get("idea") or "основен gameplay"
    return (
        f"Фокус MVP: {idea}. "
        f"Preset `{preset}` — избягвай feature creep извън M0–M3. "
        "Art: greybox OK само в M0; hero ships/vehicles не остават кубчета. Polish в M7."
    )


def build_config(
    brainstorm: dict[str, Any],
    *,
    studio_preset: str,
    kind: str,
    build_mode: str = "guided",
) -> dict[str, Any]:
    """Persisted game-studio.json for GS-B integration."""
    mode = build_mode if build_mode in BUILD_MODES else "guided"
    resolved = resolve_platform_preset(brainstorm, studio_preset=studio_preset, kind=kind)
    return {
        "version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "brainstorm": normalize_brainstorm(brainstorm),
        "studio_preset": resolved["studio_preset"],
        "kind": resolved["kind"],
        "build_mode": mode,
        "current_milestone": "M0",
        "completed_milestones": [],
        "mvp_milestones": ["M0", "M1", "M2", "M3"] if mode == "prototype" else None,
        "studio": True,
        "gates_pending": gates_for_build_mode(mode),
    }


def project_description_from_gdd(brainstorm: dict[str, Any], gdd_md: str) -> str:
    """Short description for playground_projects row."""
    b = normalize_brainstorm(brainstorm)
    pitch = _build_pitch(b)
    excerpt = gdd_md[:500].replace("\n", " ").strip()
    return f"[Game Studio] {pitch}\n\n{excerpt}"[:2000]


async def start_game_studio_project(
    *,
    name: str,
    brainstorm: dict[str, Any],
    studio_preset: str = "",
    kind: str = "",
    build_mode: str = "guided",
    slug: str = "",
    dissection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create Playground project + write GDD and config. Does not run plan/build."""
    from . import playground as pg

    title = (name or brainstorm.get("title") or "").strip()
    if not title:
        raise ValueError("name or brainstorm.title required")

    b = normalize_brainstorm({**brainstorm, "title": title})
    resolved = resolve_platform_preset(b, studio_preset=studio_preset, kind=kind)
    effective_kind = resolved["kind"]
    effective_preset = resolved["studio_preset"]
    gdd_md = generate_gdd_lite(
        b, studio_preset=effective_preset, kind=effective_kind, dissection=dissection,
    )
    config = build_config(
        b,
        studio_preset=effective_preset,
        kind=effective_kind,
        build_mode=build_mode,
    )
    description = project_description_from_gdd(b, gdd_md)

    proj = await pg.create_project(
        title,
        kind=effective_kind,
        source="built",
        description=description,
        slug=slug,
    )
    project_slug = proj["slug"]
    try:
        from .playground_racer_scaffold import maybe_write_racer_scaffold
        maybe_write_racer_scaffold(
            pg.project_dir(project_slug),
            effective_kind,
            title,
            project_slug,
            description,
            studio_preset=effective_preset,
        )
    except Exception:
        pass

    files = {
        GDD_PATH: gdd_md,
        CONFIG_PATH: json.dumps(config, ensure_ascii=False, indent=2),
    }
    pg.write_files(project_slug, files)
    try:
        from .production_layer import seed_map
        seed_map(
            pg.project_dir(project_slug),
            systems=list(b.get("systems") or []),
            layer="prototype",
        )
    except Exception:
        pass
    if dissection and isinstance(dissection, dict):
        try:
            from .dissection import save_dissection
            save_dissection(project_slug, dissection)
        except Exception:
            pass

    # Unity / hybrid init hooks (same as create wizard)
    init_notes: list[str] = []
    if effective_kind == "unity":
        try:
            from .playground_unity_init import init_unity_project
            await init_unity_project(project_slug, studio_preset=effective_preset)
            init_notes.append("unity_init")
        except Exception as exc:
            init_notes.append(f"unity_init_skipped:{exc}")
    if effective_preset == "hybrid_mobile":
        try:
            from .playground_hybrid_mobile import init_hybrid_mobile
            await init_hybrid_mobile(project_slug)
            init_notes.append("hybrid_mobile_init")
        except Exception as exc:
            init_notes.append(f"hybrid_init_skipped:{exc}")
        try:
            from .playground_capacitor import init_capacitor
            await init_capacitor(project_slug)
            init_notes.append("capacitor_init")
        except Exception as exc:
            init_notes.append(f"capacitor_init_skipped:{exc}")

    proj = await pg.get_project(project_slug)
    plan_description = (
        f"Game Studio проект.\n\n"
        f"Прочети {GDD_PATH} и следвай Studio milestones M0–M8.\n"
        f"Preset: {effective_preset}. Build mode: {build_mode}.\n\n"
        f"{_description_blob(b)}"
    )
    if dissection and dissection.get("specimen_block"):
        plan_description += "\n\n" + dissection["specimen_block"]
    return {
        "project": proj,
        "slug": project_slug,
        "gdd_path": GDD_PATH,
        "config_path": CONFIG_PATH,
        "studio_preset": effective_preset,
        "kind": effective_kind,
        "build_mode": build_mode,
        "plan_description": plan_description,
        "milestones": resolved.get("milestones") or [],
        "init_notes": init_notes,
    }


def read_gdd_if_exists(project_root: Path) -> str | None:
    path = project_root / GDD_PATH
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return None


def is_game_studio_project_dir(project_root: Path) -> bool:
    return (project_root / CONFIG_PATH).is_file()


def load_config_from_root(project_root: Path) -> dict[str, Any] | None:
    path = project_root / CONFIG_PATH
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def is_game_studio_slug(slug: str) -> bool:
    from . import playground as pg

    return is_game_studio_project_dir(pg.project_dir(slug))


async def list_game_studio_projects() -> list[dict[str, Any]]:
    """Playground projects that have design/game-studio.json."""
    from . import playground as pg

    out: list[dict[str, Any]] = []
    for proj in await pg.list_projects():
        slug = proj.get("slug") or ""
        if not slug:
            continue
        root = pg.project_dir(slug)
        if not is_game_studio_project_dir(root):
            continue
        cfg = load_config_from_root(root) or {}
        row = dict(proj)
        row["game_studio"] = True
        row["studio_preset"] = cfg.get("studio_preset") or "custom"
        row["build_mode"] = cfg.get("build_mode") or "guided"
        row["game_title"] = (cfg.get("brainstorm") or {}).get("title") or proj.get("name")
        out.append(row)
    return out


def _workspace_stats(project_root: Path) -> dict[str, Any]:
    """Lightweight file stats for smoke-check."""
    stats: dict[str, Any] = {
        "file_count": 0,
        "has_index_html": False,
        "has_package_json": False,
        "has_gdd": (project_root / GDD_PATH).is_file(),
        "has_unity": (project_root / "Assets").is_dir(),
    }
    if not project_root.is_dir():
        return stats
    for path in project_root.rglob("*"):
        if not path.is_file():
            continue
        if path.name.startswith(".") or "node_modules" in path.parts:
            continue
        stats["file_count"] += 1
        rel = path.relative_to(project_root).as_posix()
        if rel == "index.html":
            stats["has_index_html"] = True
        if rel == "package.json":
            stats["has_package_json"] = True
    return stats


def save_config(slug: str, config: dict[str, Any]) -> None:
    from . import playground as pg

    pg.write_files(slug, {CONFIG_PATH: json.dumps(config, ensure_ascii=False, indent=2)})


async def run_project_gates(
    slug: str,
    gate_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Run gates, persist results in design/game-studio.json."""
    ctx = await get_workspace_context(slug)
    from . import playground as pg

    root = pg.project_dir(slug)
    ctx["workspace_stats"] = _workspace_stats(root)
    verify_map = (ctx.get("config") or {}).get("verify") or ctx.get("verify") or {}
    mid = (ctx.get("current_milestone") or {}).get("id") if isinstance(ctx.get("current_milestone"), dict) else ctx.get("current_milestone")
    mid = mid or "M0"
    last = verify_map.get(mid) if isinstance(verify_map, dict) else None
    if isinstance(last, dict) and last.get("gameplay_checks") is not None:
        ctx["gameplay"] = {
            "ok": last.get("gameplay_ok"),
            "skipped": bool(last.get("chromium_missing") or last.get("browser_skipped")),
            "checks": last.get("gameplay_checks") or [],
            "summary": last.get("gameplay_summary") or "",
        }
    result = run_gates(ctx, gate_ids)
    cfg = load_config_from_root(root) or {}
    cfg["gates"] = result["gates"]
    cfg["can_guided"] = result["can_guided"]
    cfg["can_autopilot"] = result["can_autopilot"]
    cfg["gates_passed"] = result["all_passed"] and not result["blocking"]
    cfg["gates_at"] = result["at"]
    save_config(slug, cfg)
    return result


async def get_workspace_context(slug: str) -> dict[str, Any]:
    """Project + GDD + config + optional saved plan for Game Studio workspace."""
    from . import playground as pg

    proj = await pg.get_project(slug)
    if not proj:
        raise ValueError("project not found")
    root = pg.project_dir(slug)
    if not is_game_studio_project_dir(root):
        raise ValueError("not a game studio project")
    cfg = load_config_from_root(root) or {}
    gdd = read_gdd_if_exists(root)
    plan = None
    try:
        plan = await pg.load_autopilot_plan(slug)
    except Exception:
        plan = None
    preset = cfg.get("studio_preset") or "custom"
    if plan and isinstance(plan.get("stages"), list) and plan["stages"]:
        repaired, changed = ensure_plan_milestones(plan, preset=preset)
        if changed:
            plan = repaired
            try:
                await pg.save_autopilot_plan(slug, plan)
            except Exception:
                pass
    gate_state = merge_gate_summary(cfg)
    current_ms = resolve_current_milestone(cfg, plan)
    ms_summary = milestone_summary(cfg, plan)
    cur_verify = verify_summary_for_milestone(cfg, current_ms)
    dissection = None
    try:
        from .dissection import load_dissection
        dissection = load_dissection(slug)
    except Exception:
        dissection = None
    return {
        "project": proj,
        "config": cfg,
        "gdd": gdd,
        "gdd_path": GDD_PATH,
        "plan": plan,
        "milestones": milestones_for_preset(preset),
        "milestone_summary": ms_summary,
        "current_milestone": current_ms,
        "handoff": handoff_preview(plan, current_ms),
        "verify": cfg.get("verify") or {},
        "current_verify": cur_verify,
        "can_advance": can_advance_milestone(cfg, plan),
        "can_unity_waiver": can_advance_with_unity_waiver(cfg, plan),
        "can_proof_waiver": can_advance_with_proof_waiver(cfg, plan),
        "project_complete": project_complete(cfg, plan),
        "studio_preset": preset,
        "build_mode": cfg.get("build_mode") or "guided",
        "dissection": dissection,
        **gate_state,
    }


async def start_milestone_guided(
    slug: str,
    milestone_id: str | None = None,
    *,
    auto_approve: bool = False,
) -> dict[str, Any]:
    """Start scoped Guided build for one Game Studio milestone."""
    from . import guided_jobs as gj
    from . import playground as pg
    from .playground_plan_guide import enrich_plan

    ctx = await get_workspace_context(slug)
    plan = ctx.get("plan")
    if not plan or not isinstance(plan.get("stages"), list) or not plan["stages"]:
        raise ValueError("no saved plan — create Studio plan first")
    cfg = ctx.get("config") or {}
    gate_state = merge_gate_summary(cfg)
    if not gate_state.get("can_guided"):
        raise ValueError("gates not passed — run gate-check before guided build")
    proj = ctx.get("project") or {}
    mid = (milestone_id or resolve_current_milestone(cfg, plan)).strip()
    start_idx = first_pending_index(plan, mid)
    if start_idx is None:
        prog = milestone_progress(plan, mid)
        if prog["total"] == 0:
            orphans = sum(
                1 for s in (plan.get("stages") or [])
                if isinstance(s, dict) and not (s.get("milestone_id") or "").strip()
            )
            raise ValueError(
                f"milestone {mid} has no stages — "
                + ("планът има етапи без milestone_id; презареди workspace или прегенерирай плана"
                   if orphans else f"прегенерирай Studio плана или избери друг milestone")
            )
        raise ValueError(f"milestone {mid} has no pending stages — run Verify or advance")

    kind = proj.get("kind", "static")
    plan = enrich_plan(dict(plan), kind, proj.get("description") or "")
    for i, s in enumerate(plan.get("stages") or []):
        s.setdefault("id", i + 1)
    preset = ctx.get("studio_preset") or "custom"
    description = build_description_for_milestone(
        proj.get("description", ""),
        gdd=ctx.get("gdd", ""),
        milestone_id=mid,
        preset=preset,
        slug=slug,
        kind=kind,
    )
    try:
        await pg.save_autopilot_plan(slug, plan)
    except Exception:
        pass

    job = gj.start_guided(
        slug,
        description,
        plan,
        stage_index=start_idx,
        ultra=True,
        kind=kind,
        studio=True,
        studio_preset=preset,
        milestone_scope=mid,
        auto_approve=auto_approve,
    )
    cfg["current_milestone"] = mid
    save_config(slug, cfg)
    return {
        "job_id": job.id,
        "milestone_id": mid,
        "handoff": handoff_preview(plan, mid),
        "snapshot": job.snapshot(),
    }


async def advance_project_milestone(
    slug: str,
    *,
    force: bool = False,
    unity_waiver: bool = False,
    proof_waiver: bool = False,
) -> dict[str, Any]:
    """Mark current milestone complete and advance pointer — requires verify pass."""
    ctx = await get_workspace_context(slug)
    cfg = dict(ctx.get("config") or {})
    plan = ctx.get("plan")
    current = resolve_current_milestone(cfg, plan).strip()
    prog = milestone_progress(plan, current)
    if not prog["complete"]:
        raise ValueError(f"milestone {current} still has pending stages — finish guided build first")
    if not force and not can_advance_milestone(cfg, plan):
        if unity_waiver and can_advance_with_unity_waiver(cfg, plan):
            waivers = dict(cfg.get("unity_waivers") or {})
            waivers[current] = {
                "at": datetime.now(timezone.utc).isoformat(),
                "reason": "unity_local_hub_required",
            }
            cfg["unity_waivers"] = waivers
            save_config(slug, cfg)
        elif proof_waiver and can_advance_with_proof_waiver(cfg, plan):
            waivers = dict(cfg.get("proof_waivers") or {})
            waivers[current] = {
                "at": datetime.now(timezone.utc).isoformat(),
                "reason": "runtime_ok_chromium_unavailable",
            }
            cfg["proof_waivers"] = waivers
            save_config(slug, cfg)
        else:
            ver = verify_summary_for_milestone(cfg, current) or {}
            if not ver:
                raise ValueError(f"run Verify for {current} before advancing")
            if can_advance_with_unity_waiver(cfg, plan):
                raise ValueError(
                    f"verify incomplete for {current} — Unity изисква локален Hub; "
                    "използвай waiver или поправи и пусни Verify отново"
                )
            raise ValueError(f"verify failed for {current} — fix errors and re-run Verify")
    new_cfg = advance_milestone(cfg, plan)
    save_config(slug, new_cfg)
    return {
        "previous": current,
        "current_milestone": new_cfg.get("current_milestone"),
        "completed_milestones": new_cfg.get("completed_milestones") or [],
        "milestone_summary": milestone_summary(new_cfg, plan),
        "can_advance": can_advance_milestone(new_cfg, plan),
        "auto_start_guided": bool(
            milestone_summary(new_cfg, plan).get("can_start_milestone")
        ),
    }
