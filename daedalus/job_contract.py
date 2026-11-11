"""Programmatic Definition of Done — the model does not get to declare victory.

Used by chat replies and standalone autopilot so "готово" requires evidence:
writes, smoke, and (for visual kinds) browser verify.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .capability_registry import proof_kinds
from .dod import is_coding_task

_DONE_WORDS = (
    "готово", "done", "завърших", "shipped", "completed", "fixed",
    "поправих", "публикувах",
)
_CONTRADICTION_PHRASES = (
    "не мога", "не можах", "could not complete", "cannot be automated",
    "requires manual", "needs human", "task_blocked", "blocked:",
)


@dataclass
class JobContract:
    coding_task: bool
    complete: bool
    checks: dict[str, bool] = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    summary: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _call_name(call: dict[str, Any]) -> str:
    return str(call.get("tool") or call.get("name") or "")


# A failed look (reading a file that does not exist yet, listing an empty folder) changes nothing and is not a defect
# of the work. Found live 2026-11-11: one such read_file kept a finished, playable build "not complete" for the turn.
_READ_PROBES = frozenset({"read_file", "list_dir", "NEED_PLAYGROUND_READ", "NEED_READ"})


def unrecovered_failure_indexes(tool_calls: list[dict[str, Any]] | None) -> list[int]:
    """Indexes of failed calls that no later successful call of the same tool repaired.

    A failed ``run_command`` followed by a passing ``run_command`` is a fix, not a
    blocker — otherwise the model could never recover inside one turn.
    """
    calls = tool_calls or []
    out: list[int] = []
    for i, call in enumerate(calls):
        if call.get("ok") is not False or call.get("blocked"):
            continue
        name = _call_name(call)
        if name in _READ_PROBES:
            continue
        recovered = any(
            later.get("ok") is True and _call_name(later) == name
            for later in calls[i + 1:]
        )
        if not recovered:
            out.append(i)
    return out


def _tools_ok(tool_calls: list[dict[str, Any]] | None) -> tuple[bool, bool, bool]:
    calls = tool_calls or []
    wrote = any(
        str(c.get("tool") or c.get("name") or "") in {
            "NEED_PLAYGROUND_WRITE", "NEED_PLAYGROUND_EDIT", "write_file",
            "edit_file", "propose_patch", "NEED_PROPOSE", "NEED_EDIT",
        }
        or "write" in str(c.get("tool") or "").lower()
        or (
            str(c.get("tool") or "") in {"spawn_subagent", "subagent"}
            and any(
                str(role).lower() == "coder"
                for role in (c.get("roles") or [c.get("role") or "coder"])
            )
        )
        for c in calls
    )
    ran = any(
        str(c.get("tool") or "") in {
            "NEED_PLAYGROUND_RUN", "NEED_SANDBOX", "NEED_VERIFY", "run_command",
            "NEED_TDD", "spawn_subagent", "subagent",
        }
        or "sandbox" in str(c.get("tool") or "").lower()
        or "verify" in str(c.get("tool") or "").lower()
        for c in calls
    )
    failed = bool(unrecovered_failure_indexes(calls))
    return wrote, ran, failed


def _static_preview_counts(user_message: str, kind: str) -> bool:
    """Static HTML/canvas Playground is served as preview — no NEED_PLAYGROUND_RUN."""
    k = (kind or "").strip().lower()
    if k in ("static", "streamlit"):
        return True
    msg = (user_message or "").lower()
    return bool(re.search(r"\b(playground|canvas|html|snake|tetris|pong|игра)\b", msg))


def _contradicts_done(reply: str) -> bool:
    low = (reply or "").lower()
    if not any(w in low for w in _DONE_WORDS):
        return False
    return any(p in low for p in _CONTRADICTION_PHRASES)


def evaluate(
    user_message: str,
    reply: str,
    *,
    tool_calls: list[dict[str, Any]] | None = None,
    test_ok: bool | None = None,
    browser_ok: bool | None = None,
    kind: str = "",
    intent: str = "",
    open_slices: int = 0,
    strict: bool = False,
) -> JobContract:
    """Score a turn. ``strict`` is the kernel build loop: the ask is a coding
    task by construction and the plan is scored even when the reply does not
    claim "готово" — the loop asks *before* the model gets to declare victory."""
    coding = strict or is_coding_task(user_message, intent)
    wrote, ran, failed = _tools_ok(tool_calls)
    if wrote and not ran and _static_preview_counts(user_message, kind):
        ran = True
    claims_done = any(w in (reply or "").lower() for w in _DONE_WORDS)
    honest_fail = _contradicts_done(reply)

    checks = {
        "coding_task": coding,
        "wrote_files": wrote if coding else True,
        "verified": (test_ok if test_ok is not None else ran) if coding else True,
        "no_failed_tools": not failed,
        "browser": True,
        "no_false_done": not (coding and honest_fail),
        "slices_closed": not (coding and open_slices > 0 and claims_done),
    }
    if coding and kind in proof_kinds() and browser_ok is not None:
        checks["browser"] = bool(browser_ok)

    blockers: list[str] = []
    if coding and not checks["wrote_files"]:
        blockers.append("Няма запис на файлове — задачата не е изпълнена.")
    if coding and not checks["verified"]:
        blockers.append("Липсва smoke/verify — не казвай готово.")
    if coding and not checks["no_failed_tools"]:
        blockers.append("Има неуспешни tool calls — поправи ги преди край.")
    if coding and not checks["browser"]:
        blockers.append("Browser verify не мина за визуален проект.")
    if coding and not checks["no_false_done"]:
        blockers.append("Каза готово, но отговорът признава провал — срезовете не са затворени.")
    if coding and not checks["slices_closed"]:
        blockers.append(
            f"Има {open_slices} незавършени среза в job ledger — не казвай готово."
        )
    if coding and (claims_done or strict):
        try:
            from .tool_router import route, score_plan
            scored = score_plan(
                route(user_message, kind=kind, intent=intent),
                tool_calls,
            )
        except Exception:
            scored = None
        if scored is not None:
            checks["task_plan"] = scored.complete
            if scored.blocker:
                blockers.append(scored.blocker)
        try:
            from .game_engineer import missing_eng_tools
            miss = missing_eng_tools(user_message, tool_calls)
        except Exception:
            miss = ""
        if miss:
            checks["eng_tools"] = False
            if not (scored and scored.blocker and "math3d" in scored.blocker and "math3d" in miss):
                if miss not in blockers:
                    blockers.append(miss)
        else:
            checks["eng_tools"] = True

    complete = (not coding) or (not blockers)
    if coding and claims_done and blockers:
        complete = False

    summary = "готово" if complete else ("; ".join(blockers) or "partial")
    return JobContract(
        coding_task=coding,
        complete=complete,
        checks=checks,
        blockers=blockers,
        summary=summary,
    )


def finish_nudge(contract: JobContract) -> str:
    if contract.complete or not contract.coding_task:
        return ""
    items = "\n".join(f"  • {b}" for b in contract.blockers)
    return (
        "\n\n━━━ DEFINITION OF DONE (системата, не моделът) ━━━\n"
        "Не си завършил задачата:\n"
        f"{items}\n"
        "Продължи: пиши файлове, пусни smoke, за игри — browser verify.\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
