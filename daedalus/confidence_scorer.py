"""Daedalus Confidence Scorer  [FIX #2]

Deterministic scoring на всеки code proposal — без LLM self-rating.
Базира се на: risk level, sandbox validation, tests, blast radius, past rejections.

Използване:
    from .confidence_scorer import score_proposal

    score = score_proposal(
        risk=proposal["risk_level"] or 3,
        sandbox_result=proposal.get("sandbox_result", {}),
        changed_files=[proposal["file_path"]],
        tests_result=proposal.get("sandbox_result", {}).get("tests"),
        similar_rejections=0,
    )
    proposal["confidence_score"] = score["total"]
    proposal["confidence_tier"]  = score["tier"]
    proposal["confidence_flags"] = score["flags"]
"""
from __future__ import annotations

from typing import Any

# RISK 1-5 → delta (Дедал RISK скала)
_RISK_DELTA: dict[int, int] = {1: +8, 2: +4, 3: 0, 4: -12, 5: -22}


def score_proposal(
    *,
    risk: int | None,
    sandbox_result: dict[str, Any],
    changed_files: list[str],
    tests_result: dict[str, Any] | None = None,
    similar_rejections: int = 0,
    semantic_match: bool = False,
) -> dict[str, Any]:
    """Изчислява confidence score за proposal (0–100).

    Args:
        risk: RISK ниво (1–5). None се третира като 3 (неутрален).
        sandbox_result: резултат от validate_patch().
        changed_files: списък с засегнати файлове.
        tests_result: резултат от run_related_tests() (може да е вложен в sandbox_result).
        similar_rejections: брой similar rejections от ChromaDB lessons.
        semantic_match: дали Дедал е намерил existing pattern.

    Returns:
        {total, tier, color, emoji, breakdown, flags}
    """
    breakdown: dict[str, int] = {"base": 70}
    flags: list[str] = []

    # ── Risk level
    r = int(risk) if risk else 3
    breakdown["risk"] = _RISK_DELTA.get(r, 0)
    if r >= 4:
        flags.append(f"High risk level ({r}/5) — внимателен преглед")

    # ── Sandbox checks
    syntax_ok = sandbox_result.get("ok", False)
    if not sandbox_result:
        breakdown["sandbox"] = -5
        flags.append("Sandbox не е стартиран")
    else:
        # Syntax / import
        layers = sandbox_result.get("layers", [])
        layer_map = {l.get("layer"): l for l in layers}
        if layer_map.get("syntax", {}).get("ok", True):
            breakdown["syntax"] = +5
        else:
            breakdown["syntax"] = -30
            flags.append("Syntax errors detected")

        if "import" in layer_map:
            if layer_map["import"].get("ok", True):
                breakdown["imports"] = +5
            else:
                breakdown["imports"] = -20
                flags.append("Import resolution failed")

        # Static analysis
        static = sandbox_result.get("static", {})
        if static:
            high_count = (static.get("bandit") or {}).get("high_severity_count", 0)
            med_count  = (static.get("bandit") or {}).get("medium_severity_count", 0)
            ruff_block = (static.get("ruff") or {}).get("blocking", False)
            if high_count > 0:
                breakdown["security"] = -25
                flags.append(f"⛔ {high_count} HIGH severity security issue(s)")
            elif med_count > 0:
                breakdown["security"] = -8
                flags.append(f"⚠ {med_count} MEDIUM security warning(s)")
            else:
                breakdown["security"] = +5
            if ruff_block:
                breakdown["lint"] = -10
                flags.append("Ruff E/F errors detected")

    # ── Tests
    tr = tests_result or sandbox_result.get("tests")
    if tr is None or tr.get("status") == "skip":
        breakdown["tests"] = -10
        flags.append("No related tests — препоръчително да добавиш")
    elif tr.get("passed"):
        breakdown["tests"] = +15
    else:
        breakdown["tests"] = -25
        flags.append(f"Tests failing: {tr.get('summary', '')}")

    # Bonus: proposal добавя тест файлове
    if any("/tests/" in f or f.startswith("tests/") or "test_" in f for f in changed_files):
        breakdown["tests_added"] = +8

    # ── Blast radius
    n_files = len(changed_files)
    if n_files > 10:
        breakdown["blast_radius"] = -15
        flags.append(f"Wide blast radius: {n_files} файла")
    elif n_files > 5:
        breakdown["blast_radius"] = -5
    else:
        breakdown["blast_radius"] = 0

    # ── Past rejections от Lessons memory
    if similar_rejections > 0:
        penalty = -5 * min(similar_rejections, 4)
        breakdown["past_rejections"] = penalty
        flags.append(f"⚠ {similar_rejections} similar rejection(s) в паметта")

    # ── Semantic code reuse
    if semantic_match:
        breakdown["pattern_reuse"] = +5

    # Clamp 0–100
    total = max(0, min(100, sum(breakdown.values())))
    tier, color, emoji = _tier(total)

    return {
        "total": total,
        "tier": tier,
        "color": color,
        "emoji": emoji,
        "breakdown": breakdown,
        "flags": flags,
    }


def _tier(score: int) -> tuple[str, str, str]:
    if score >= 85:
        return ("HIGH", "green", "🟢")
    if score >= 60:
        return ("MEDIUM", "yellow", "🟡")
    if score >= 40:
        return ("LOW", "orange", "🟠")
    return ("SPECULATIVE", "red", "🔴")


def count_similar_rejections(
    changed_files: list[str],
    skill: str,
    memory: Any | None = None,
    threshold: float = 0.6,
) -> int:
    """Брои колко similar rejection lessons има в ChromaDB.

    Args:
        changed_files: засегнати файлове.
        skill: skill name на proposal-а.
        memory: DaedalusMemory instance (или None за 0).
        threshold: similarity threshold (default 0.6).

    Returns: брой similar rejections.
    """
    if memory is None:
        return 0
    try:
        lessons_col = memory.lessons_col
        if lessons_col.count() == 0:
            return 0
        query = f"Skill: {skill} | Files: {', '.join(changed_files[:5])}"
        results = lessons_col.query(
            query_texts=[query],
            n_results=min(10, lessons_col.count()),
            where={"type": "rejection_lesson"},
        )
        distances = (results.get("distances") or [[]])[0]
        similar = sum(1 for d in distances if d < (1 - threshold))
        return similar
    except Exception:
        return 0
