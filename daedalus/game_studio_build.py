"""Game Studio milestone-scoped build handoff — safe Guided/Autopilot entry."""
from __future__ import annotations

from typing import Any

from .playground_studio_mode import GAME_MILESTONES

MILESTONE_ORDER: tuple[str, ...] = tuple(m["id"] for m in GAME_MILESTONES)
MVP_MILESTONES: tuple[str, ...] = ("M0", "M1", "M2", "M3")
VALID_MILESTONE_IDS: frozenset[str] = frozenset(MILESTONE_ORDER)

HANDOFF_SYS_ADDENDUM = """
━━━ GAME STUDIO MILESTONE BUILD (ЗАДЪЛЖИТЕЛНО) ━━━
- ЕДИН проект = ЕДИН slug — НЕ създавай нов проект, НЕ сменяй kind
- Променяй САМО файловете в files[] на ТЕКУЩИЯ етап
- НЕ прегенерирай целия проект от нулата; НЕ трий работещ код извън scope
- Прочети design/gdd-lite.md и studio/*.md за контекст
- Запази boot, menu, save и prior milestone progress
- Един етап = един deliverable; merge incremental
- Приложи резултатите от проучването (skills, Phaser/canvas/Three.js patterns) — не импровизирай от нулата
- WRITE преди READ — не чети файл преди да го създадеш
- ARCADE CRAFT: един deliverable на етап; verify преди следващия. Не default-вай pixel art на 3D brief. Cul-de-sac (3× същата грешка) → смени подхода.
""".strip()


def _stage_resolved(stage: dict[str, Any]) -> bool:
    return stage.get("status") in ("done", "skipped")


def _plan_stages(plan: dict[str, Any] | None) -> list[dict[str, Any]]:
    stages = (plan or {}).get("stages") or []
    return [s for s in stages if isinstance(s, dict)]


def orphan_stage_count(plan: dict[str, Any] | None, valid_ids: frozenset[str] | None = None) -> int:
    """Stages with missing or unknown milestone_id — invisible to milestone_progress."""
    valid = valid_ids or VALID_MILESTONE_IDS
    return sum(
        1 for s in _plan_stages(plan)
        if (s.get("milestone_id") or "").strip() not in valid
    )


def ensure_plan_milestones(
    plan: dict[str, Any] | None,
    *,
    preset: str = "custom",
) -> tuple[dict[str, Any], bool]:
    """Normalize milestone_id on every stage; reassign orphans via keyword/round-robin."""
    from .playground_studio_mode import assign_milestone_ids, milestones_for_preset

    if not plan or not isinstance(plan.get("stages"), list):
        return plan or {}, False
    out = dict(plan)
    stages = [dict(s) for s in out["stages"] if isinstance(s, dict)]
    if not stages:
        return out, False
    milestones = out.get("milestones")
    if not isinstance(milestones, list) or not milestones:
        milestones = milestones_for_preset(preset)
        out["milestones"] = milestones
    valid = {str(m.get("id", "")).strip() for m in milestones if m.get("id")}
    valid |= set(VALID_MILESTONE_IDS)
    changed = False
    for stage in stages:
        raw = (stage.get("milestone_id") or "").strip()
        norm = raw.upper() if raw.upper() in VALID_MILESTONE_IDS else ""
        if norm and norm != raw:
            stage["milestone_id"] = norm
            changed = True
        elif norm:
            continue
        else:
            stage.pop("milestone_id", None)
            changed = True
    before = [s.get("milestone_id") for s in stages]
    assign_milestone_ids(stages, milestones)
    after = [s.get("milestone_id") for s in stages]
    if before != after:
        changed = True
    # Collapsed plan: all stages on last milestone while M0 empty — spread across MVP first.
    order = [m.get("id") for m in milestones if m.get("id")]
    if order and len(stages) >= 4:
        counts = {mid: sum(1 for s in stages if s.get("milestone_id") == mid) for mid in order}
        m0 = order[0]
        last = order[-1]
        if counts.get(m0, 0) == 0 and counts.get(last, 0) >= max(4, int(len(stages) * 0.6)):
            for i, stage in enumerate(stages):
                stage["milestone_id"] = order[min(i * len(order) // len(stages), len(order) - 1)]
            changed = True
    out["stages"] = stages
    out["studio"] = True
    return out, changed


def is_final_milestone(milestone_id: str, build_mode: str = "guided") -> bool:
    order = list(MVP_MILESTONES if build_mode == "prototype" else MILESTONE_ORDER)
    return milestone_id == order[-1] if order else False


def project_complete(
    config: dict[str, Any] | None,
    plan: dict[str, Any] | None,
) -> bool:
    """All milestones with stages are built and last milestone verify passed."""
    cfg = config or {}
    build_mode = cfg.get("build_mode") or "guided"
    order = list(MVP_MILESTONES if build_mode == "prototype" else MILESTONE_ORDER)
    with_stages = [mid for mid in order if milestone_progress(plan, mid)["total"] > 0]
    if not with_stages:
        return False
    for mid in with_stages:
        prog = milestone_progress(plan, mid)
        if not prog["complete"]:
            return False
        ver = (cfg.get("verify") or {}).get(mid) or {}
        if not ver.get("ok") or ver.get("proof_incomplete"):
            return False
    return True


def milestone_progress(plan: dict[str, Any] | None, milestone_id: str) -> dict[str, Any]:
    stages = (plan or {}).get("stages") or []
    if not isinstance(stages, list):
        stages = []
    in_ms = [s for s in stages if isinstance(s, dict) and s.get("milestone_id") == milestone_id]
    done = [s for s in in_ms if _stage_resolved(s)]
    pending = [s for s in in_ms if not _stage_resolved(s)]
    return {
        "milestone_id": milestone_id,
        "total": len(in_ms),
        "done": len(done),
        "pending": len(pending),
        "complete": len(in_ms) > 0 and len(pending) == 0,
    }


def resolve_current_milestone(config: dict[str, Any] | None, plan: dict[str, Any] | None) -> str:
    cfg = config or {}
    stages = _plan_stages(plan)
    if not stages:
        return "M0"
    if orphan_stage_count(plan):
        return "M0"
    build_mode = cfg.get("build_mode") or "guided"
    order = list(MVP_MILESTONES if build_mode == "prototype" else MILESTONE_ORDER)
    completed = set(cfg.get("completed_milestones") or [])

    explicit = (cfg.get("current_milestone") or "").strip()
    if explicit in order:
        prog = milestone_progress(plan, explicit)
        if prog["total"] > 0:
            if not prog["complete"]:
                return explicit
            # Build finished — stay until formally advanced (verify + advance click).
            if explicit not in completed:
                return explicit

    for mid in order:
        if mid in completed:
            continue
        prog = milestone_progress(plan, mid)
        if prog["total"] == 0:
            continue
        if not prog["complete"]:
            return mid
        # Built but not yet in completed_milestones — awaiting verify/advance.
        return mid

    for mid in reversed(order):
        if milestone_progress(plan, mid)["total"] > 0:
            return mid
    return "M0"


def first_pending_index(plan: dict[str, Any], milestone_id: str) -> int | None:
    stages = plan.get("stages") or []
    for i, s in enumerate(stages):
        if not isinstance(s, dict):
            continue
        if s.get("milestone_id") == milestone_id and not _stage_resolved(s):
            return i
    return None


def last_index_for_milestone(plan: dict[str, Any], milestone_id: str) -> int | None:
    stages = plan.get("stages") or []
    last: int | None = None
    for i, s in enumerate(stages):
        if isinstance(s, dict) and s.get("milestone_id") == milestone_id:
            last = i
    return last


def pending_stages_for_milestone(plan: dict[str, Any], milestone_id: str) -> list[dict[str, Any]]:
    return [
        s for s in (plan.get("stages") or [])
        if isinstance(s, dict) and s.get("milestone_id") == milestone_id and not _stage_resolved(s)
    ]


def build_description_for_milestone(
    base: str,
    *,
    gdd: str = "",
    milestone_id: str = "M0",
    preset: str = "custom",
    slug: str = "",
    kind: str = "static",
) -> str:
    from .playground_agent_guards import outbox_guardrails_block

    header = (
        f"[Game Studio · milestone {milestone_id} · preset {preset}]\n"
        f"ФИКСИРАН ПРОЕКТ: slug={slug or 'studio'} — НЕ създавай друг проект.\n"
        f"Строй САМО етапите за {milestone_id}. Не преписвай завършени milestone-и.\n\n"
        f"{outbox_guardrails_block(slug=slug, kind=kind)}\n"
    )
    gdd_excerpt = (gdd or "")[:1200]
    body = (base or "").strip()
    extra = ""
    try:
        from .arcade_craft import planner_addendum
        extra = "\n\n" + planner_addendum(kind, gdd_excerpt or body)
    except Exception:
        extra = ""
    try:
        from .game_engineer import planner_addendum as _eng
        eng = _eng(kind, gdd_excerpt or body)
        if eng and eng not in extra:
            extra = f"{extra}\n\n{eng}"
    except Exception:
        pass
    return f"{header}{body}\n\n--- GDD excerpt ---\n{gdd_excerpt}{extra}"[:5500]


def milestone_summary(config: dict[str, Any] | None, plan: dict[str, Any] | None) -> dict[str, Any]:
    cfg = config or {}
    current = resolve_current_milestone(cfg, plan)
    build_mode = cfg.get("build_mode") or "guided"
    order = list(MVP_MILESTONES if build_mode == "prototype" else MILESTONE_ORDER)
    rows = []
    for mid in order:
        prog = milestone_progress(plan, mid)
        if prog["total"] == 0 and mid not in (current, *order[:4]):
            continue
        rows.append({
            "id": mid,
            "title": next((m["title"] for m in GAME_MILESTONES if m["id"] == mid), mid),
            **prog,
            "current": mid == current,
        })
    cur_prog = milestone_progress(plan, current)
    orphans = orphan_stage_count(plan)
    done_real = sum(
        1 for s in _plan_stages(plan)
        if s.get("status") == "done"
    )
    return {
        "current_milestone": current,
        "build_mode": build_mode,
        "milestones": rows,
        "can_start_milestone": cur_prog["pending"] > 0 and cur_prog["total"] > 0 and orphans == 0,
        "pending_in_current": cur_prog["pending"],
        "completed_milestones": list(cfg.get("completed_milestones") or []),
        "orphan_stages": orphans,
        "stages_done": done_real,
        "stages_total": len(_plan_stages(plan)),
        "is_final_milestone": is_final_milestone(current, build_mode),
        "project_complete": project_complete(cfg, plan),
    }


def advance_milestone(config: dict[str, Any], plan: dict[str, Any] | None) -> dict[str, Any]:
    """Mark current milestone complete and move to next."""
    cfg = dict(config or {})
    explicit = (cfg.get("current_milestone") or "").strip()
    current = explicit if explicit in MILESTONE_ORDER else resolve_current_milestone(cfg, plan)
    completed = list(cfg.get("completed_milestones") or [])
    if current not in completed:
        completed.append(current)
    cfg["completed_milestones"] = completed
    build_mode = cfg.get("build_mode") or "guided"
    order = list(MVP_MILESTONES if build_mode == "prototype" else MILESTONE_ORDER)
    try:
        idx = order.index(current)
        next_mid = order[idx + 1] if idx + 1 < len(order) else current
    except ValueError:
        next_mid = "M1"
    cfg["current_milestone"] = next_mid
    return cfg


def handoff_preview(plan: dict[str, Any] | None, milestone_id: str) -> dict[str, Any]:
    pending = pending_stages_for_milestone(plan or {}, milestone_id)
    titles = [str(s.get("title") or s.get("id")) for s in pending[:8]]
    return {
        "milestone_id": milestone_id,
        "pending_count": len(pending),
        "stage_titles": titles,
        "will_skip_plan_generation": True,
        "will_only_touch_pending_stages": True,
    }
