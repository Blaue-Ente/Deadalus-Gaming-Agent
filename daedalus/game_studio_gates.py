"""Game Studio quality gates — gate-check, scope-check, smoke-check (rule-based).

CCGS-inspired checks before guided/autopilot build. No LLM dependency.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

GateLevel = Literal["pass", "warn", "fail"]

AMBITIOUS_PRESETS = frozenset({"open_world", "multiplayer", "hybrid_mobile", "racing"})
PROTOTYPE_MILESTONES = frozenset({"M0", "M1", "M2", "M3"})


def _check(
    check_id: str,
    label: str,
    level: GateLevel,
    detail: str = "",
) -> dict[str, Any]:
    return {
        "id": check_id,
        "label": label,
        "level": level,
        "detail": detail,
        "passed": level != "fail",
    }


def _aggregate(checks: list[dict[str, Any]]) -> dict[str, Any]:
    levels = [c["level"] for c in checks]
    if "fail" in levels:
        status = "fail"
        passed = False
    elif "warn" in levels:
        status = "warn"
        passed = True
    else:
        status = "pass"
        passed = True
    return {
        "status": status,
        "passed": passed,
        "blocking": status == "fail",
        "checks": checks,
        "summary": _summary_text(checks, status),
    }


def _summary_text(checks: list[dict[str, Any]], status: str) -> str:
    n_fail = sum(1 for c in checks if c["level"] == "fail")
    n_warn = sum(1 for c in checks if c["level"] == "warn")
    if status == "fail":
        return f"{n_fail} блокиращи проблема" + (f", {n_warn} предупреждения" if n_warn else "")
    if status == "warn":
        return f"{n_warn} предупреждения — може да продължиш с внимание"
    return "Всички проверки минаха"


def run_gate_check(ctx: dict[str, Any]) -> dict[str, Any]:
    """Readiness: GDD, config, plan structure."""
    checks: list[dict[str, Any]] = []
    gdd = (ctx.get("gdd") or "").strip()
    config = ctx.get("config") or {}
    plan = ctx.get("plan") or {}
    stages = plan.get("stages") if isinstance(plan, dict) else None
    if not isinstance(stages, list):
        stages = []

    if len(gdd) < 80:
        checks.append(_check(
            "gdd", "GDD Lite наличен",
            "fail" if not gdd else "warn",
            "Липсва или е твърде кратък design/gdd-lite.md",
        ))
    else:
        checks.append(_check("gdd", "GDD Lite наличен", "pass", f"{len(gdd)} символа"))

    brainstorm = config.get("brainstorm") or {}
    if not (brainstorm.get("idea") or "").strip():
        checks.append(_check("idea", "Идея в brainstorm", "warn", "Добави idea в game-studio.json"))
    else:
        checks.append(_check("idea", "Идея в brainstorm", "pass"))

    preset = config.get("studio_preset") or ctx.get("studio_preset") or ""
    kind = config.get("kind") or (ctx.get("project") or {}).get("kind") or ""
    if not preset:
        checks.append(_check("preset", "Studio preset", "fail", "Липсва studio_preset"))
    else:
        checks.append(_check("preset", "Studio preset", "pass", preset))

    if not kind:
        checks.append(_check("kind", "Playground kind", "fail", "Липсва kind"))
    else:
        checks.append(_check("kind", "Playground kind", "pass", kind))

    if len(stages) < 3:
        checks.append(_check(
            "plan_stages", "Studio план (≥3 етапа)",
            "fail" if len(stages) == 0 else "warn",
            "Създай Studio план преди guided build",
        ))
    else:
        checks.append(_check("plan_stages", "Studio план (≥3 етапа)", "pass", f"{len(stages)} етапа"))

    if plan.get("studio") or any(s.get("milestone_id") for s in stages if isinstance(s, dict)):
        checks.append(_check("studio_plan", "Milestone структура", "pass", "studio / milestone_id"))
    elif stages:
        checks.append(_check(
            "studio_plan", "Milestone структура", "warn",
            "Планът не е маркиран studio=true — препоръчително при създаване",
        ))

    return {
        "gate": "gate-check",
        "label": "Gate Check",
        "at": datetime.now(timezone.utc).isoformat(),
        **_aggregate(checks),
    }


def run_scope_check(ctx: dict[str, Any]) -> dict[str, Any]:
    """Scope creep / MVP discipline."""
    checks: list[dict[str, Any]] = []
    config = ctx.get("config") or {}
    plan = ctx.get("plan") or {}
    stages = plan.get("stages") if isinstance(plan, dict) else []
    if not isinstance(stages, list):
        stages = []

    build_mode = config.get("build_mode") or ctx.get("build_mode") or "guided"
    preset = config.get("studio_preset") or ctx.get("studio_preset") or "custom"
    brainstorm = config.get("brainstorm") or {}
    systems = brainstorm.get("systems") or []
    if isinstance(systems, str):
        systems = [s.strip() for s in systems.split(",") if s.strip()]

    n_sys = len(systems)
    if n_sys > 10:
        checks.append(_check("systems", "Брой системи", "fail", f"{n_sys} — фокусирай MVP до 4–6"))
    elif n_sys > 6:
        checks.append(_check("systems", "Брой системи", "warn", f"{n_sys} — риск от scope creep"))
    else:
        checks.append(_check("systems", "Брой системи", "pass", str(n_sys or "авто")))

    n_stages = len(stages)
    if build_mode == "prototype":
        over_mvp = [
            s for s in stages
            if isinstance(s, dict) and s.get("milestone_id") and s["milestone_id"] not in PROTOTYPE_MILESTONES
        ]
        if over_mvp:
            checks.append(_check(
                "prototype_scope", "Prototype M0–M3",
                "fail",
                f"{len(over_mvp)} етапа извън MVP milestones",
            ))
        elif n_stages > 16:
            checks.append(_check("prototype_scope", "Prototype етапи", "warn", f"{n_stages} етапа — много за MVP"))
        else:
            checks.append(_check("prototype_scope", "Prototype M0–M3", "pass"))
    else:
        if n_stages > 40:
            checks.append(_check("stage_count", "Брой етапи в плана", "fail", f"{n_stages} — фрагментирай или ultra"))
        elif n_stages > 25:
            checks.append(_check("stage_count", "Брой етапи в плана", "warn", f"{n_stages} етапа"))
        elif n_stages:
            checks.append(_check("stage_count", "Брой етапи в плана", "pass", str(n_stages)))

    if preset in AMBITIOUS_PRESETS:
        checks.append(_check(
            "preset_scope", "Амбициозен preset",
            "warn",
            f"{preset} — дръж MVP offline-first",
        ))
    else:
        checks.append(_check("preset_scope", "Preset обхват", "pass", preset))

    platform = (brainstorm.get("platform_hint") or "").strip()
    if platform == "cross_platform":
        checks.append(_check("platform", "Cross-platform", "warn", "Започни с една платформа"))
    else:
        checks.append(_check("platform", "Платформа", "pass", platform or "web"))

    missing_files = sum(
        1 for s in stages
        if isinstance(s, dict) and not (s.get("files") or [])
    )
    if stages and missing_files > len(stages) // 2:
        checks.append(_check(
            "stage_files", "files[] в етапи",
            "warn",
            f"{missing_files}/{len(stages)} без files[]",
        ))
    elif stages:
        checks.append(_check("stage_files", "files[] в етапи", "pass"))

    return {
        "gate": "scope-check",
        "label": "Scope Check",
        "at": datetime.now(timezone.utc).isoformat(),
        **_aggregate(checks),
    }


def run_balance_check(ctx: dict[str, Any]) -> dict[str, Any]:
    """Economy/combat sim when playtest/balance.json exists; else keyword warn-only."""
    checks: list[dict[str, Any]] = []
    root = _project_root(ctx)
    sim = None
    if root is not None:
        from .balance_sim import run_for_project
        sim = run_for_project(root)
    if sim and not sim.get("skipped"):
        level: GateLevel = "pass" if sim.get("ok") else "fail"
        checks.append(_check(
            "sim",
            "Balance sim (1000 rounds)",
            level,
            sim.get("detail") or "",
        ))
        return {
            "gate": "balance-check",
            "label": "Balance Check",
            "at": datetime.now(timezone.utc).isoformat(),
            **_aggregate(checks),
        }

    config = ctx.get("config") or {}
    brainstorm = config.get("brainstorm") or {}
    gdd = (ctx.get("gdd") or "").lower()
    blob = f"{brainstorm.get('idea', '')} {gdd}"
    if any(w in blob for w in ("gacha", "loot box", "pay to win", "pvp ranked")):
        checks.append(_check("monetization", "Монетизация", "warn", "Сложна икономика — балансирай рано"))
    if "currency" in blob and "upgrade" not in blob and "progression" not in blob:
        checks.append(_check("progression", "Прогресия", "warn", "Currency без ясен upgrade path"))
    if sim and sim.get("skipped"):
        checks.append(_check("sim", "Balance sim", "warn", sim.get("detail") or "skipped"))
    if not checks:
        checks.append(_check("balance", "Balance heuristic", "pass", "Няма очевидни red flags"))
    return {
        "gate": "balance-check",
        "label": "Balance Check",
        "at": datetime.now(timezone.utc).isoformat(),
        **_aggregate(checks),
    }


def run_smoke_check(ctx: dict[str, Any]) -> dict[str, Any]:
    """Minimal workspace / entry sanity."""
    checks: list[dict[str, Any]] = []
    project = ctx.get("project") or {}
    slug = project.get("slug") or ""
    stats = ctx.get("workspace_stats") or {}
    kind = project.get("kind") or config_kind(ctx)

    file_count = int(stats.get("file_count") or 0)
    if file_count < 1:
        checks.append(_check("files", "Workspace файлове", "fail", "Празен workspace"))
    else:
        checks.append(_check("files", "Workspace файлове", "pass", str(file_count)))

    has_entry = bool(
        stats.get("has_index_html")
        or stats.get("has_package_json")
        or stats.get("has_unity")
    )
    if not has_entry and file_count > 0:
        checks.append(_check("entry", "Entry point", "warn", f"kind={kind}, провери scaffold"))
    elif has_entry:
        checks.append(_check("entry", "Entry point", "pass", kind))
    else:
        checks.append(_check("entry", "Entry point", "fail", "Няма index/package"))

    if slug and not stats.get("has_gdd"):
        checks.append(_check("gdd_file", "design/gdd-lite.md", "warn", "GDD файл липсва на диска"))
    else:
        checks.append(_check("gdd_file", "design/gdd-lite.md", "pass"))

    return {
        "gate": "smoke-check",
        "label": "Workspace Check",
        "at": datetime.now(timezone.utc).isoformat(),
        **_aggregate(checks),
    }


def config_kind(ctx: dict[str, Any]) -> str:
    config = ctx.get("config") or {}
    return config.get("kind") or (ctx.get("project") or {}).get("kind") or ""


def run_craft_check(ctx: dict[str, Any]) -> dict[str, Any]:
    """Arcade-craft / fun / fidelity — warn-only, never blocks guided."""
    from .arcade_craft import (
        fun_signals,
        looks_like_campaign_bible,
        looks_like_racer,
        scope_warnings,
        wants_high_fidelity,
    )

    checks: list[dict[str, Any]] = []
    config = ctx.get("config") or {}
    brainstorm = config.get("brainstorm") or {}
    gdd = (ctx.get("gdd") or "")
    plan = ctx.get("plan") or {}
    stages = plan.get("stages") if isinstance(plan, dict) else []
    if not isinstance(stages, list):
        stages = []
    blob = f"{brainstorm.get('idea', '')} {brainstorm.get('genre', '')} {gdd}"
    hi = wants_high_fidelity(blob) or looks_like_racer(blob) or (config.get("studio_preset") == "racing")
    bible = looks_like_campaign_bible(blob)

    if bible:
        checks.append(_check(
            "scope",
            "Campaign bible vs M0",
            "warn",
            "GDD looks like a campaign bible (biomes/chapters/console SKUs). M0 is one verb + one fail.",
        ))

    if hi:
        checks.append(_check(
            "vertical_slice",
            "Vertical slice first",
            "warn" if len(stages) < 8 else "pass",
            "High-fidelity racer: ≥8 tiny stages; M0 = one ship on one segment",
        ))
        traps = scope_warnings(brainstorm)
        if traps:
            checks.append(_check("fidelity", "Fidelity traps", "warn", traps[0][:180]))
        else:
            checks.append(_check("fidelity", "Fidelity traps", "pass", "no pixel-art/mesh-from-zero flags"))
        snap_bits = [str(s.get("title", "")) + " " + str(s.get("goal", "")) for s in stages if isinstance(s, dict)]
        fun = fun_signals(" ".join(snap_bits) + " " + blob)
        if fun["blockers"]:
            checks.append(_check("fun", "Fun gate (plan)", "warn", fun["blockers"][0][:180]))
        else:
            checks.append(_check("fun", "Fun gate (plan)", "pass", "wayfinding/fail/risk mentioned"))
    elif not bible:
        checks.append(_check("craft", "Arcade craft", "pass", "standard game — craft extras optional"))

    return {
        "gate": "craft-check",
        "label": "Craft Check",
        "at": datetime.now(timezone.utc).isoformat(),
        **_aggregate(checks),
    }


def _project_root(ctx: dict[str, Any]) -> Path | None:
    """Resolve on-disk project only when the caller pointed at a workspace or stats show files.

    Pre-build gate runs (no files yet) must not touch the real playground tree.
    """
    ws = ctx.get("workspace")
    if ws:
        path = Path(ws)
        return path if path.is_dir() else None
    stats = ctx.get("workspace_stats") or {}
    if int(stats.get("file_count") or 0) < 1:
        return None
    slug = (ctx.get("project") or {}).get("slug") or ""
    if not slug:
        return None
    from . import playground as pg
    root = pg.project_dir(slug)
    return root if root.is_dir() else None


def _has_game_files(ctx: dict[str, Any], root: Path | None) -> bool:
    stats = ctx.get("workspace_stats") or {}
    if int(stats.get("file_count") or 0) > 0:
        return True
    if root is None or not root.is_dir():
        return False
    return (
        (root / "index.html").is_file()
        or (root / "src").is_dir()
        or (root / "playtest").is_dir()
    )


def run_play_check(ctx: dict[str, Any]) -> dict[str, Any]:
    """Scenario bot presence + live FPS/win/lose when gameplay results are attached."""
    checks: list[dict[str, Any]] = []
    config = ctx.get("config") or {}
    kind = (config.get("kind") or (ctx.get("project") or {}).get("kind") or "").strip().lower()
    preset = config.get("studio_preset") or ctx.get("studio_preset") or ""
    if kind in ("godot", "unity"):
        checks.append(_check(
            "engine",
            "Engine playtest",
            "pass",
            f"{kind} използва engine runner, не Chromium bot",
        ))
        return {
            "gate": "play-check",
            "label": "Play Check",
            "at": datetime.now(timezone.utc).isoformat(),
            **_aggregate(checks),
        }

    root = _project_root(ctx)
    has_files = _has_game_files(ctx, root)
    racer = bool(root and (root / "src" / "racer" / "vehicle.js").is_file())
    racing = preset == "racing" or racer
    scenario_path = (root / "playtest" / "scenario.json") if root else None

    if not has_files:
        checks.append(_check(
            "scenario",
            "playtest/scenario.json",
            "warn",
            "ще се изисква след M0 файлове — не блокира планирането",
        ))
    elif scenario_path is not None and scenario_path.is_file():
        from .playtest_scenario import load_scenario
        _sc, err = load_scenario(root)
        if err and err != "missing":
            checks.append(_check("scenario", "scenario schema", "fail", err))
        else:
            checks.append(_check("scenario", "playtest/scenario.json", "pass", "валиден bot script"))
    elif racing:
        checks.append(_check(
            "scenario",
            "playtest/scenario.json",
            "fail",
            "racer без scenario — ботът не може да докаже движение/провал",
        ))
    else:
        checks.append(_check(
            "scenario",
            "playtest/scenario.json",
            "warn",
            "липсва — verify ще запише default",
        ))

    gameplay = ctx.get("gameplay") or ctx.get("gameplay_result") or {}
    live = isinstance(gameplay, dict) and (gameplay.get("checks") or []) and not gameplay.get("skipped")
    if live:
        by_id = {c.get("id"): c for c in (gameplay.get("checks") or []) if isinstance(c, dict)}
        mapping = (
            ("scenario_canvas_changed", "Платното се промени (pixel-diff)"),
            ("scenario_lose", "Ботът достигна lose"),
            ("scenario_win", "Ботът достигна win"),
            ("fps_budget", "FPS бюджет"),
            ("memory_budget", "Memory бюджет"),
        )
        for cid, label in mapping:
            row = by_id.get(cid)
            if not row:
                continue
            if row.get("warn") and row.get("ok"):
                level: GateLevel = "warn"
            elif row.get("ok"):
                level = "pass"
            else:
                level = "fail"
            checks.append(_check(cid, label, level, str(row.get("detail") or "")))

    if not checks:
        checks.append(_check("play", "Play check", "pass", "няма web bot за този kind"))

    return {
        "gate": "play-check",
        "label": "Play Check",
        "at": datetime.now(timezone.utc).isoformat(),
        **_aggregate(checks),
    }


def run_world_check(ctx: dict[str, Any]) -> dict[str, Any]:
    """Campaign runtime: save/quest/dialogue/graph. Hollow JSON fails once files exist."""
    checks: list[dict[str, Any]] = []
    config = ctx.get("config") or {}
    brainstorm = config.get("brainstorm") or {}
    blob = f"{brainstorm.get('idea', '')} {brainstorm.get('genre', '')} {ctx.get('gdd') or ''}"
    epic = False
    try:
        from .world_kit import needed as world_needed
        epic = world_needed(blob)
    except Exception:
        epic = False
    if not epic:
        try:
            from .arcade_craft import looks_like_campaign_bible
            epic = looks_like_campaign_bible(blob)
        except Exception:
            pass

    root = _project_root(ctx)
    has_files = _has_game_files(ctx, root)
    map_path = (root / "world" / "map.json") if root else None
    has_kit = bool(map_path and map_path.is_file())

    if not has_files:
        checks.append(_check(
            "kit",
            "World kit runtime",
            "warn" if epic else "pass",
            "ще се изисква след файлове" if epic else "не е кампания — N/A",
        ))
        return {
            "gate": "world-check",
            "label": "World Check",
            "at": datetime.now(timezone.utc).isoformat(),
            **_aggregate(checks),
        }

    if not has_kit:
        checks.append(_check(
            "kit",
            "World kit runtime",
            "warn" if epic else "pass",
            "липсва world/map.json" if epic else "няма campaign kit",
        ))
        return {
            "gate": "world-check",
            "label": "World Check",
            "at": datetime.now(timezone.utc).isoformat(),
            **_aggregate(checks),
        }

    from .world_runtime import prove_project
    proof = prove_project(root)
    if proof.get("ok"):
        checks.append(_check(
            "runtime",
            "Save / quest / dialogue / graph",
            "pass",
            f"regions={proof.get('regions')} flags={list((proof.get('flags') or {}).keys())}",
        ))
    else:
        detail = "; ".join(proof.get("errors") or ["hollow kit"])
        checks.append(_check("runtime", "Save / quest / dialogue / graph", "fail", detail[:240]))

    return {
        "gate": "world-check",
        "label": "World Check",
        "at": datetime.now(timezone.utc).isoformat(),
        **_aggregate(checks),
    }


def run_asset_check(ctx: dict[str, Any]) -> dict[str, Any]:
    """Atlas/SFX/font are opt-in. Claimed empty GLB fails. Racing without a mesh warns."""
    checks: list[dict[str, Any]] = []
    config = ctx.get("config") or {}
    brainstorm = config.get("brainstorm") or {}
    blob = f"{brainstorm.get('idea', '')} {brainstorm.get('genre', '')} {ctx.get('gdd') or ''}"
    wants_3d = False
    try:
        from .arcade_craft import looks_like_racer, wants_high_fidelity
        wants_3d = looks_like_racer(blob) or wants_high_fidelity(blob)
    except Exception:
        wants_3d = False
    kind = (config.get("kind") or (ctx.get("project") or {}).get("kind") or "").strip().lower()
    if "3d" in blob.lower() or kind in ("godot",) and "mesh" in blob.lower():
        wants_3d = True

    root = _project_root(ctx)
    has_files = _has_game_files(ctx, root)
    if not has_files or root is None:
        checks.append(_check(
            "kit",
            "Asset pipeline",
            "warn" if wants_3d else "pass",
            "3D mesh след файлове (ключ или greybox)" if wants_3d else "atlas/sfx/font по нужда",
        ))
        return {
            "gate": "asset-check",
            "label": "Asset Check",
            "at": datetime.now(timezone.utc).isoformat(),
            **_aggregate(checks),
        }

    from .asset_pipeline import prove_assets
    proof = prove_assets(root, wants_3d=wants_3d)
    if proof.get("ok"):
        warns = proof.get("warnings") or []
        checks.append(_check(
            "pipeline",
            "Atlas / SFX / mesh contract",
            "warn" if warns else "pass",
            "; ".join(warns)[:240] if warns else f"atlas={proof.get('atlas')} glb={proof.get('glb')}",
        ))
    else:
        detail = "; ".join(proof.get("errors") or ["asset pipeline"])
        checks.append(_check("pipeline", "Atlas / SFX / mesh contract", "fail", detail[:240]))

    return {
        "gate": "asset-check",
        "label": "Asset Check",
        "at": datetime.now(timezone.utc).isoformat(),
        **_aggregate(checks),
    }


GATE_RUNNERS = {
    "gate-check": run_gate_check,
    "scope-check": run_scope_check,
    "smoke-check": run_smoke_check,
    "balance-check": run_balance_check,
    "craft-check": run_craft_check,
    "play-check": run_play_check,
    "world-check": run_world_check,
    "asset-check": run_asset_check,
}


def gates_for_build_mode(build_mode: str) -> list[str]:
    if build_mode == "prototype":
        return ["smoke-check", "gate-check", "scope-check", "craft-check", "play-check", "world-check", "asset-check"]
    return ["gate-check", "scope-check", "balance-check", "craft-check", "play-check", "world-check", "asset-check"]


def run_gates(ctx: dict[str, Any], gate_ids: list[str] | None = None) -> dict[str, Any]:
    """Run selected gates; return combined result."""
    build_mode = (ctx.get("config") or {}).get("build_mode") or ctx.get("build_mode") or "guided"
    ids = gate_ids or gates_for_build_mode(build_mode)
    results: dict[str, Any] = {}
    blocking = False
    all_passed = True
    for gid in ids:
        runner = GATE_RUNNERS.get(gid)
        if not runner:
            continue
        row = runner(ctx)
        results[gid] = row
        if row.get("blocking"):
            blocking = True
        if not row.get("passed"):
            all_passed = False

    can_guided = not blocking and results.get("gate-check", {}).get("passed", False)
    can_autopilot = can_guided and results.get("scope-check", {}).get("passed", False)

    if build_mode == "prototype":
        can_guided = results.get("smoke-check", {}).get("passed", False) and not blocking
        can_autopilot = can_guided

    return {
        "gates": results,
        "can_guided": can_guided,
        "can_autopilot": can_autopilot,
        "blocking": blocking,
        "all_passed": all_passed,
        "at": datetime.now(timezone.utc).isoformat(),
    }


def merge_gate_summary(config: dict[str, Any] | None) -> dict[str, Any]:
    """Extract gate state from saved config for API responses."""
    cfg = config or {}
    gates = cfg.get("gates") or {}
    if not gates:
        return {
            "gates": {},
            "can_guided": False,
            "can_autopilot": False,
            "gates_passed": False,
            "pending": cfg.get("gates_pending") or ["gate-check", "scope-check"],
        }
    return {
        "gates": gates,
        "can_guided": bool(cfg.get("can_guided")),
        "can_autopilot": bool(cfg.get("can_autopilot")),
        "gates_passed": bool(cfg.get("gates_passed")),
        "gates_at": cfg.get("gates_at"),
        "pending": [],
    }
