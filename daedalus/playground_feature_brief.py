"""Feature brief templates — document §20 workflow for studio builds."""
from __future__ import annotations

from typing import Any

_BRIEF_TEMPLATE = """# Feature Brief: {title}

## Goal
{goal}

## Player Flow
{player_flow}

## Systems Involved
{systems}

## Data Needed
{data_needed}

## Acceptance Criteria
{acceptance}
"""


def _default_player_flow(stage: dict[str, Any]) -> str:
    goal = (stage.get("goal") or "").strip()
    if goal:
        return f"1. Играчът достига функцията.\n2. {goal}\n3. Прогресът се записва."
    return "1. Feature е достъпен от основния flow.\n2. Действието работи без crash.\n3. Save/load запазва състоянието."


def _systems_from_stage(stage: dict[str, Any], milestone_title: str) -> str:
    role = stage.get("studio_role") or stage.get("model_role") or "builder"
    files = ", ".join(stage.get("files") or []) or "(TBD)"
    return f"- Milestone: {milestone_title}\n- Role: {role}\n- Files: {files}"


def _data_from_files(files: list[str]) -> str:
    if not files:
        return "- Runtime save data\n- Static definitions (JSON/TS)"
    lines = []
    for f in files:
        low = f.lower()
        if "creature" in low or "hero" in low:
            lines.append("- CreatureDefinition + CreatureInstance")
        elif "inventory" in low or "item" in low:
            lines.append("- ItemDefinition + inventory slots")
        elif "combat" in low or "battle" in low:
            lines.append("- Combat state + ability definitions")
        elif "save" in low:
            lines.append("- Versioned save blob")
        elif "schema" in low or "data/" in low:
            lines.append("- DATA_SCHEMA entries")
    if not lines:
        lines.append("- Stage-specific runtime + static data")
    return "\n".join(dict.fromkeys(lines))


def _acceptance_criteria(stage: dict[str, Any]) -> str:
    goal = stage.get("goal") or "Feature works"
    files = stage.get("files") or []
    lines = [
        f"- {goal}",
        "- No unrelated files modified",
        "- Follows PROJECT_RULES.md",
    ]
    if files:
        lines.append(f"- Files complete: {', '.join(files)}")
    if stage.get("micro"):
        lines.append("- Single-file scope respected (~180 lines max)")
    return "\n".join(lines)


def build_feature_brief(
    stage: dict[str, Any],
    *,
    milestone_id: str = "",
    milestone_title: str = "",
) -> str:
    title = stage.get("title") or f"Stage {stage.get('id', '?')}"
    mtitle = milestone_title or milestone_id or "General"
    return _BRIEF_TEMPLATE.format(
        title=title,
        goal=stage.get("goal") or title,
        player_flow=_default_player_flow(stage),
        systems=_systems_from_stage(stage, mtitle),
        data_needed=_data_from_files(list(stage.get("files") or [])),
        acceptance=_acceptance_criteria(stage),
    ).strip()


def enrich_stages_with_briefs(
    stages: list[dict[str, Any]],
    milestones: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach feature_brief markdown to each stage."""
    mid_map = {m.get("id", ""): m for m in milestones if isinstance(m, dict)}
    for stage in stages:
        if not isinstance(stage, dict):
            continue
        mid = str(stage.get("milestone_id") or "")
        m = mid_map.get(mid, {})
        brief = build_feature_brief(
            stage,
            milestone_id=mid,
            milestone_title=str(m.get("title") or mid),
        )
        stage["feature_brief"] = brief
    return stages


def brief_prompt_block(stage: dict[str, Any]) -> str:
    brief = (stage.get("feature_brief") or "").strip()
    if not brief:
        return ""
    return f"\n━━━ FEATURE BRIEF ━━━\n{brief[:2500]}\n"
