"""Guided build orchestrator — model routing, merge gates, checkpoint AI options."""
from __future__ import annotations

import logging
import os
from typing import Any

from .playground_build_utils import extract_llm_text
from .playground_file_trunk import FileTrunk
from .playground_stage_builder import StageBuildResult

log = logging.getLogger(__name__)

# Optional per-role model overrides via env:
# DAEDALUS_ORCH_MODEL_BUILDER, _GAME, _REVIEWER, _PLANNER
_ROLE_ENV = {
    "builder": "DAEDALUS_ORCH_MODEL_BUILDER",
    "game_builder": "DAEDALUS_ORCH_MODEL_GAME",
    "reviewer": "DAEDALUS_ORCH_MODEL_REVIEWER",
    "planner": "DAEDALUS_ORCH_MODEL_PLANNER",
    "asset": "DAEDALUS_ORCH_MODEL_BUILDER",
}


def resolve_role_model(role: str, default_model: str | None) -> str | None:
    """Pick model for orchestrator role (multi-model builds)."""
    env_key = _ROLE_ENV.get(role, "")
    if env_key:
        override = os.environ.get(env_key, "").strip()
        if override:
            return override
    if role == "game_builder":
        game = os.environ.get("DAEDALUS_ORCH_MODEL_GAME", "").strip()
        if game:
            return game
    if role == "reviewer":
        rev = os.environ.get("DAEDALUS_ORCH_MODEL_REVIEWER", "").strip()
        if rev:
            return rev
    return default_model


def apply_trunk_merge(
    trunk: FileTrunk,
    result: StageBuildResult,
    *,
    task_id: str,
    model: str,
) -> tuple[list[str], list[str]]:
    """Propose branch files, validate, merge to trunk. Returns (merged, rejected)."""
    merged: list[str] = []
    rejected: list[str] = []
    for path, content in (result.stage_files or {}).items():
        trunk.propose(path, content, task_id=task_id, model=model)
        if result.ok:
            trunk.mark_validated(path, review=result.review_notes, ok=True)
            if trunk.merge(path):
                merged.append(path)
            else:
                rejected.append(path)
        else:
            trunk.mark_validated(path, review=result.review_notes, ok=False)
            rejected.append(path)
    return merged, rejected


def default_checkpoint_options(
    stage: dict[str, Any],
    result: StageBuildResult,
    *,
    trunk: FileTrunk | None = None,
    ultra: bool = False,
) -> list[dict[str, str]]:
    from .playground_stage_builder import default_checkpoint_options as _base

    opts = _base(stage, result)
    if trunk:
        summ = trunk.summary()
        rejected = summ.get("by_status", {}).get("rejected", [])
        if rejected:
            opts.insert(0, {
                "id": "retry",
                "label": f"🔧 Поправи отхвърлени ({len(rejected)})",
                "hint": ", ".join(rejected[:3]),
            })
    opts.append({
        "id": "edit_plan",
        "label": "📝 Редактирай етапа",
        "hint": "Change instructions/goal in the notes field (JSON or text)",
    })
    if not ultra and len(stage.get("files") or []) > 1:
        opts.append({
            "id": "ultra_split",
            "label": "🔬 Ултра фрагментирай този етап",
            "hint": "Splits into micro-tasks (1 file = 1 task)",
        })
    return opts


build_checkpoint_options = default_checkpoint_options


async def suggest_checkpoint_options(
    stage: dict[str, Any],
    result: StageBuildResult,
    *,
    description: str,
    route_fn: Any,
    model: str | None,
    client_id: str,
) -> list[dict[str, str]]:
    """Phase C: LLM suggests 2–3 contextual next actions."""
    base_ids = {o["id"] for o in default_checkpoint_options(stage, result)}
    prompt = (
        f"Етап: {stage.get('title')}\nЦел: {stage.get('goal')}\n"
        f"OK: {result.ok}\nReview: {result.review_notes[:400]}\n"
        f"Проект: {description[:300]}\n\n"
        "Предложи 2–3 КОНКРЕТНИ следващи действия за потребителя (кратки).\n"
        'JSON: {"options":[{"id":"custom_1","label":"...","hint":"..."}]}\n'
        "id: само букви/цифри/_. Без код."
    )
    try:
        res = await route_fn(
            messages=[
                {"role": "system", "content": "Връщаш САМО JSON с options масив."},
                {"role": "user", "content": prompt},
            ],
            model=model,
            intent="chat",
            max_tokens=500,
            temperature=0.3,
            json_mode=True,
            client_id=client_id,
        )
        raw = extract_llm_text(res)
        import json
        data = json.loads(raw) if raw.strip().startswith("{") else {}
        out: list[dict[str, str]] = []
        for o in (data.get("options") or [])[:3]:
            oid = str(o.get("id", "custom")).strip()
            if not oid or oid in base_ids:
                oid = f"custom_{len(out)+1}"
            out.append({
                "id": oid,
                "label": str(o.get("label", "Предложение"))[:80],
                "hint": str(o.get("hint", ""))[:120],
                "ai": True,
            })
        return out
    except Exception as exc:
        log.debug("checkpoint AI options failed: %s", exc)
        return []


def parse_plan_stage_edit(notes: str, stage: dict[str, Any]) -> dict[str, Any]:
    """Apply user edit from checkpoint notes (plain or JSON)."""
    import json
    text = (notes or "").strip()
    if not text:
        return stage
    if text.startswith("{"):
        try:
            patch = json.loads(text)
            merged = dict(stage)
            for k in ("title", "goal", "instructions", "uses_visuals"):
                if k in patch:
                    merged[k] = patch[k]
            if "files" in patch and isinstance(patch["files"], list):
                merged["files"] = patch["files"]
            return merged
        except Exception:
            pass
    merged = dict(stage)
    merged["instructions"] = (
        (stage.get("instructions") or "") + "\n\n👤 Потребител: " + text
    ).strip()
    return merged


def orchestrator_brief(ultra: bool, trunk: FileTrunk | None) -> str:
    if not ultra and not trunk:
        return ""
    parts = ["🎛 Orchestrator: trunk/branch merge gate активен."]
    if ultra:
        parts.append("Ultra: 1 файл = 1 micro-task.")
    if trunk:
        parts.append(f"Trunk: {len(trunk.trunk)} файла.")
    return " ".join(parts)
