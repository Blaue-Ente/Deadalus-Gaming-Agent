"""A project's bible: the plan and the history that carry a game across sessions.

Owner, 2026-11-12: a team builds a game over many sessions. The-Labirinth keeps docs/GAME_BIBLE.md (the story, the
plan, the rules) and every session starts from it. A Daedalus build planned each turn from scratch: the Mentor's
plan lived for one turn, so the next session lost the vision, the rules and what was still open.

- The first accepted plan becomes ``GAME_BIBLE.md`` (``PROJECT_BIBLE.md`` for an app): vision, rules and features,
  look, then a session log.
- Every build turn appends one log entry: what was asked, what the lead, the audience and the tests said, and what is
  still open.
- The next turn's Mentor plan and the builder read it first.

The owner may edit it freely; Daedalus only creates it and appends to its log. A cloned or revived repo
(``.daedalus/upstream_manifest.json``) gets none: its documents are the upstream's.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

GAME_BIBLE = "GAME_BIBLE.md"
PROJECT_BIBLE = "PROJECT_BIBLE.md"
LOG_HEADING = "## Session log"
PROMPT_MAX = 3000
_UPSTREAM = ".daedalus/upstream_manifest.json"


def _root(slug: str) -> Path | None:
    try:
        from . import playground as pg
        root = pg.project_dir(slug)
        return root if slug and root.is_dir() else None
    except Exception:
        return None


def path(slug: str) -> Path | None:
    """The bible that exists, else None."""
    root = _root(slug)
    if root is None:
        return None
    for name in (GAME_BIBLE, PROJECT_BIBLE):
        if (root / name).is_file():
            return root / name
    return None


def text(slug: str, *, max_chars: int = PROMPT_MAX) -> str:
    """The bible for a prompt: its head (vision, rules) and its latest log entries, within ``max_chars``."""
    p = path(slug)
    if p is None:
        return ""
    body = p.read_text(encoding="utf-8", errors="replace").strip()
    if len(body) <= max_chars:
        return body
    head, sep, logs = body.partition(LOG_HEADING)
    keep_head = head[: max_chars * 2 // 3].rstrip()
    tail = (sep + logs)[-(max_chars - len(keep_head) - 20):] if sep else ""
    return f"{keep_head}\n…\n{tail}".strip()


def ensure_from_plan(slug: str, plan: dict[str, Any] | None, *, name: str = "", game: bool = True) -> bool:
    """Create the bible from the lead's plan when the project has none. True if it wrote."""
    root = _root(slug)
    if root is None or not plan or path(slug) is not None or (root / _UPSTREAM).is_file():
        return False
    title = (name or slug).strip()
    lines = [f"# {title}: {'game' if game else 'project'} bible", "",
             "Kept by Daedalus across sessions. Edit it freely: every new session starts from it.", ""]
    if plan.get("brief"):
        lines += ["## Vision", str(plan["brief"]).strip(), ""]
    if plan.get("checklist"):
        lines += ["## Rules and features", *[f"- {c}" for c in plan["checklist"]], ""]
    if plan.get("look"):
        lines += ["## Look", *[f"- {c}" for c in plan["look"]], ""]
    lines += [LOG_HEADING, ""]
    (root / (GAME_BIBLE if game else PROJECT_BIBLE)).write_text("\n".join(lines), encoding="utf-8")
    return True


def log_turn(slug: str, request: str, loop: Any) -> bool:
    """Append this turn to the session log: the request, what was judged, what is open. True if it wrote."""
    p = path(slug)
    if p is None:
        return False
    rev = getattr(loop, "lead_review", None) if loop is not None else None
    aud = getattr(loop, "audience", None) if loop is not None else None
    tests = getattr(loop, "project_tests", None) if loop is not None else None
    result: list[str] = []
    if isinstance(rev, dict):
        result.append("lead accepted" if rev.get("accept") else "lead: not accepted")
    if isinstance(aud, dict) and aud.get("summary"):
        result.append("audience: players would keep playing" if aud.get("ship_ready") else "audience: most would quit")
    if isinstance(tests, dict) and tests.get("summary"):
        result.append(f"tests: {tests['summary']}")
    open_items = [str(x) for x in ((rev or {}).get("missing") or [])][:5] if isinstance(rev, dict) else []
    if isinstance(aud, dict) and not aud.get("ship_ready"):
        open_items += [f"players: {f}" for f in (aud.get("fixes") or [])[:2]]
    if isinstance(tests, dict):
        open_items += [f"regression: {r}" for r in (tests.get("regressions") or [])[:3]]
    entry = [f"### {time.strftime('%Y-%m-%d %H:%M', time.gmtime())} UTC: {' '.join((request or '').split())[:140]}",
             f"- Result: {'; '.join(result) or 'built (no review ran)'}"]
    if open_items:
        entry.append("- Still open:")
        entry += [f"  - {x[:200]}" for x in open_items]
    body = p.read_text(encoding="utf-8", errors="replace").rstrip()
    if LOG_HEADING not in body:
        body += f"\n\n{LOG_HEADING}"
    p.write_text(body + "\n\n" + "\n".join(entry) + "\n", encoding="utf-8")
    return True


def record_turn(loop: Any, request: str) -> None:
    """End of a build turn: create the bible from this turn's plan if there is none, then log the turn. Never raises."""
    try:
        slug = str(getattr(loop, "slug", "") or "")
        if not slug:
            return
        from .mentor_lead import is_game
        ensure_from_plan(slug, getattr(loop, "lead_plan", None), game=is_game(loop, request))
        log_turn(slug, request, loop)
    except Exception:
        log.debug("project bible not updated", exc_info=True)
