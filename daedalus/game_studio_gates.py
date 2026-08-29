"""Game Studio quality gates — gate-check, scope-check, smoke-check (rule-based).

CCGS-inspired checks before guided/autopilot build. No LLM dependency.
"""
from __future__ import annotations

from datetime import datetime, timezone
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
    """Heuristic economy/progression balance — warn only (GS-C)."""
    checks: list[dict[str, Any]] = []
    config = ctx.get("config") or {}
    brainstorm = config.get("brainstorm") or {}
    gdd = (ctx.get("gdd") or "").lower()
    blob = f"{brainstorm.get('idea', '')} {gdd}"
    if any(w in blob for w in ("gacha", "loot box", "pay to win", "pvp ranked")):
        checks.append(_check("monetization", "Монетизация", "warn", "Сложна икономика — балансирай рано"))
    if "currency" in blob and "upgrade" not in blob and "progression" not in blob:
        checks.append(_check("progression", "Прогресия", "warn", "Currency без ясен upgrade path"))
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


GATE_RUNNERS = {
    "gate-check": run_gate_check,
    "scope-check": run_scope_check,
    "smoke-check": run_smoke_check,
    "balance-check": run_balance_check,
    "craft-check": run_craft_check,
}


def gates_for_build_mode(build_mode: str) -> list[str]:
    if build_mode == "prototype":
        return ["smoke-check", "gate-check", "scope-check", "craft-check"]
    return ["gate-check", "scope-check", "balance-check", "craft-check"]


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
