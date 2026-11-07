"""Daedalus Task State — Phase 17B.

Persistent task state file that survives restarts and session boundaries.
Complements scratch_journal (per-session token budget) with cross-session
goal/phase/files tracking.

Storage: data/daedalus/task-state.json (disk, survives deploys via object store)

Marker: <NEED_TASK_STATE: goal | phase | remaining_steps>
  - goal          : short description of the overall task (or "update" to keep existing)
  - phase         : current phase label (e.g. "recon", "coding", "validation", "done")
  - remaining_steps: comma-separated list of what's still to do

Дедал uses this to:
1. Announce the start of a multi-session task
2. Update progress after each phase
3. Mark task as done (phase="done" or goal="clear")
System uses this to:
1. Inject task state into user_content on every request (as context)
2. Auto-clear on "done"
"""
from __future__ import annotations
from .paths import data_root

import json
import logging
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_DATA_ROOT = data_root()  # paths
_STATE_FILE = _DATA_ROOT / "task-state.json"
_DATA_ROOT.mkdir(parents=True, exist_ok=True)

# Max age of a task state before it's considered stale (48 hours)
_MAX_AGE_SECONDS = 48 * 3600

# Phases that signal task completion → auto-clear
_DONE_PHASES = frozenset({"done", "complete", "завършено", "finished", "completed"})


def load() -> dict[str, Any] | None:
    """Load active task state from disk. Returns None if no active task."""
    if not _STATE_FILE.exists():
        return None
    try:
        state = json.loads(_STATE_FILE.read_text("utf-8"))
        # Stale check
        age = time.time() - state.get("updated_at", 0)
        if age > _MAX_AGE_SECONDS:
            log.info("task-state.json is stale (%dh) — auto-clearing", age // 3600)
            clear()
            return None
        if state.get("status") in ("done", "cleared"):
            return None
        return state
    except Exception as exc:
        log.debug("task-state load failed (non-fatal): %s", exc)
        return None


def save(
    goal: str,
    phase: str,
    remaining_steps: str = "",
    completed_step: str = "",
    target_files: list[str] | None = None,
    do_not_touch: list[str] | None = None,
) -> dict[str, Any]:
    """Create or update task state. Returns the saved state dict."""
    existing = load() or {}

    # "update" keeps existing goal
    if goal.strip().lower() == "update":
        goal = existing.get("goal", goal)

    # Auto-clear if done
    if phase.strip().lower() in _DONE_PHASES:
        clear()
        return {"status": "cleared", "goal": goal, "phase": phase}

    # Build completed_steps list
    completed = list(existing.get("completed_steps", []))
    if completed_step and completed_step.strip():
        completed.append(completed_step.strip())

    # Build remaining list
    remaining = [s.strip() for s in remaining_steps.split(",") if s.strip()] if remaining_steps else existing.get("remaining_steps", [])

    state: dict[str, Any] = {
        "task_id": existing.get("task_id") or f"task_{int(time.time())}",
        "goal": goal.strip(),
        "status": "in_progress",
        "phase": phase.strip(),
        "target_files": target_files or existing.get("target_files", []),
        "completed_steps": completed,
        "remaining_steps": remaining,
        "do_not_touch": do_not_touch or existing.get("do_not_touch", []),
        "created_at": existing.get("created_at", time.time()),
        "updated_at": time.time(),
    }
    try:
        _STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), "utf-8")
        log.info("task-state saved: goal=%r phase=%r remaining=%d", goal[:60], phase, len(remaining))
    except Exception as exc:
        log.warning("task-state write failed: %s", exc)
    return state


def clear() -> None:
    """Remove active task state."""
    try:
        if _STATE_FILE.exists():
            _STATE_FILE.unlink()
            log.info("task-state cleared")
    except Exception as exc:
        log.debug("task-state clear failed (non-fatal): %s", exc)


def is_active() -> bool:
    """True if there is a non-stale in-progress task."""
    return load() is not None


def get_context_block() -> str:
    """Return a compact context string to inject into user_content.

    Only injected when there is an active task — keeps context clean otherwise.
    """
    state = load()
    if not state:
        return ""

    goal = state.get("goal", "?")
    phase = state.get("phase", "?")
    completed = state.get("completed_steps", [])
    remaining = state.get("remaining_steps", [])
    task_id = state.get("task_id", "?")
    updated = state.get("updated_at", 0)
    age_min = int((time.time() - updated) / 60)

    completed_str = "; ".join(completed[-3:]) if completed else "—"
    remaining_str = "; ".join(remaining[:5]) if remaining else "—"

    return (
        f"━━━ АКТИВНА ЗАДАЧА (task-state) ━━━\n"
        f"ID          : {task_id}\n"
        f"Цел         : {goal}\n"
        f"Фаза        : {phase}\n"
        f"Завършено   : {completed_str}\n"
        f"Остава      : {remaining_str}\n"
        f"Обновено    : преди {age_min} мин.\n"
        f"Ако задачата е ЗАВЪРШЕНА → емитирай "
        f"<NEED_TASK_STATE: update | done | > за да я изчистиш.\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
