"""Run the independent visual critic. Unparsed / skipped is not a pass."""
from __future__ import annotations

import logging
from typing import Any

from .game_build_contract import quality_enabled
from .playground_quality import evaluate, parse_quality_result

log = logging.getLogger(__name__)


def critic_counts_as_pass(critic: dict[str, Any] | None) -> bool:
    row = critic or {}
    if row.get("skipped"):
        return False
    return bool(row.get("parsed") and row.get("passed"))


async def run_visual_critic(
    *,
    slug: str,
    kind: str,
    description: str,
    plan: dict[str, Any] | None,
    bv: dict[str, Any],
) -> dict[str, Any]:
    """Call ``playground_quality.evaluate`` with mesh + playground wiring."""
    from . import daedalus_mesh as mesh
    from . import playground as pg
    from .playground_verify_fix import _is_sensitive

    goals = [
        str(s.get("goal") or s.get("title") or "")
        for s in ((plan or {}).get("stages") or [])
        if isinstance(s, dict)
    ]
    try:
        out = await evaluate(
            slug=slug,
            kind=kind,
            description=description or "",
            plan_goals=goals,
            bv=bv,
            route_fn=mesh.route,
            extract_text_fn=mesh.extract_text,
            read_tree_fn=pg.read_tree,
            is_sensitive_fn=_is_sensitive,
            project_dir=pg.project_dir(slug),
            client_id=f"quality/{slug}",
        )
    except Exception as exc:
        log.warning("quality critic failed: %s", exc)
        out = parse_quality_result("")
        out["error"] = str(exc)[:160]
        out["parsed"] = False
        out["passed"] = False
    out.setdefault("parsed", False)
    out["passed"] = bool(out.get("parsed") and out.get("passed"))
    return out


async def maybe_critic(
    *,
    slug: str,
    kind: str,
    description: str,
    plan: dict[str, Any] | None,
    bv: dict[str, Any],
    visual: bool,
) -> dict[str, Any]:
    """If quality is on and the kind is visual, run the critic; else N/A pass."""
    if not visual or not quality_enabled():
        return {"ok": True, "na": True, "passed": True, "parsed": True, "skipped": False}
    critic = await run_visual_critic(
        slug=slug, kind=kind, description=description, plan=plan, bv=bv,
    )
    critic["ok"] = critic_counts_as_pass(critic)
    return critic


def polish_prompt(
    *,
    slug: str,
    kind: str,
    description: str,
    critic: dict[str, Any],
) -> str:
    """Holistic polish prompt from the last critic result."""
    from . import playground as pg
    from .playground_quality import build_polish_prompt
    from .playground_verify_fix import _is_sensitive

    return build_polish_prompt(
        description=description or "",
        kind=kind,
        critic=critic,
        slug=slug,
        read_tree_fn=pg.read_tree,
        read_file_fn=lambda s, p, max_bytes=90000: pg.read_file(s, p, max_bytes=max_bytes),
        is_sensitive_fn=_is_sensitive,
    )
