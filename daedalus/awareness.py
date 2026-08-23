"""Project Awareness — produces a read-only snapshot of the ADN project state.

Called at session start so Дедал has situational context without being asked.
Rule: Awareness ≠ Action. Дедал sees everything but acts only on current request.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


async def build_project_awareness(session_id: str = "") -> str:
    """Return a structured PROJECT SNAPSHOT block for injection into Дедал's context.

    Covers:
    - Playground projects (slug, kind, stage completion)
    - Top error patterns (cross-session recurring failures)
    - Pending tasks from the continuity ledger
    - Last 3 lessons learned

    Returns empty string when all sources are unavailable (fully non-fatal).
    """
    sections: list[str] = []

    # ── 1. Playground projects ────────────────────────────────────────────────
    try:
        from .playground import PlaygroundDB
        _pdb = PlaygroundDB()
        slugs = _pdb.list_slugs()
        if slugs:
            lines = []
            for sl in slugs[:12]:
                meta   = _pdb.get_meta(sl) or {}
                kind   = meta.get("kind", "?")
                title  = meta.get("title") or sl
                stages = meta.get("plan", {}).get("stages", [])
                done   = sum(1 for s in stages if s.get("status") == "done")
                total  = len(stages)
                status = f"{done}/{total} stages" if total else "no plan"
                lines.append(f"  • {sl} [{kind}] {title!r} — {status}")
            sections.append("🗂 PLAYGROUND PROJECTS:\n" + "\n".join(lines))
    except Exception as exc:
        log.debug("awareness: playground scan failed: %s", exc)

    # ── 2. Top recurring error patterns ──────────────────────────────────────
    try:
        from .host import get_top_error_patterns
        patterns = await get_top_error_patterns(5)
        if patterns:
            lines = [
                f"  • {p['error_type']} — {p['occurrence']}× "
                f"(последно: {str(p.get('last_seen', ''))[:10]})"
                for p in patterns
            ]
            sections.append("⚠️ TOP ERROR PATTERNS (повтарящи се грешки):\n" + "\n".join(lines))
    except Exception as exc:
        log.debug("awareness: error patterns failed: %s", exc)

    # ── 3. Pending tasks from continuity ledger ───────────────────────────────
    try:
        from . import continuity as _cont
        ctx = _cont.build_continuity_context(session_id=session_id)
        if ctx:
            task_lines = [
                l.strip() for l in ctx.splitlines()
                if any(kw in l.lower() for kw in ("pending", "🔲", "📋", "in_progress", "todo"))
            ]
            if task_lines:
                sections.append(
                    "📋 PENDING TASKS (Task Ledger):\n" +
                    "\n".join(f"  {l}" for l in task_lines[:8])
                )
    except Exception as exc:
        log.debug("awareness: continuity scan failed: %s", exc)

    # ── 4. Last 3 lessons ─────────────────────────────────────────────────────
    try:
        from .memory import DaedalusMemory
        _mem = DaedalusMemory()
        lessons = _mem.retrieve_lessons(top_k=3)
        if lessons:
            lines = [f"  • {l.get('content', '')[:90]}" for l in lessons[:3]]
            sections.append("💡 RECENT LESSONS:\n" + "\n".join(lines))
    except Exception as exc:
        log.debug("awareness: lessons failed: %s", exc)

    if not sections:
        return ""

    return (
        "━━━ PROJECT SNAPSHOT (само за четене) ━━━\n"
        + "\n\n".join(sections)
        + "\n━━━ END SNAPSHOT — Awareness ≠ Action: четеш, действаш само по текущото искане ━━━"
    )
