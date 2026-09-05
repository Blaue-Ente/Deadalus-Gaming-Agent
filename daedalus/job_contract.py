"""Programmatic Definition of Done — the model does not get to declare victory.

Used by chat replies and standalone autopilot so "готово" requires evidence:
writes, smoke, and (for visual kinds) browser verify.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .dod import is_coding_task

_DONE_WORDS = (
    "готово", "done", "завърших", "shipped", "completed", "fixed",
    "поправих", "публикувах",
)
_CONTRADICTION_PHRASES = (
    "не мога", "не можах", "could not complete", "cannot be automated",
    "requires manual", "needs human", "task_blocked", "blocked:",
)
_VISUAL_KINDS = frozenset({"static", "vite", "vite-react", "streamlit", "expo", "phaser"})


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


def _repair_key(call: dict[str, Any]) -> tuple[str, str, str]:
    """A failed write of ``game.js`` is repaired only by a later good write of
    ``game.js`` — not by writing ``index.html``. Other tools repair by name."""
    args = call.get("input") if isinstance(call.get("input"), dict) else {}
    path = str(args.get("path") or "") if args else ""
    slug = str(args.get("slug") or "") if args else ""
    return (_call_name(call), slug if path else "", path)


_INSPECTION_CMD = re.compile(
    r"^\s*(?:node\s+(?:--check|-c)\b|cat\b|head\b|tail\b|less\b|more\b|ls\b|find\b|grep\b|wc\b|"
    r"python3?\s+-m\s+py_compile\b)"
)
_INSPECTION_TOOLS = frozenset({"game_lint", "read_file", "list_dir", "playtest"})


def _is_inspection_shell(call: dict[str, Any]) -> bool:
    if _call_name(call) != "run_command":
        return False
    args = call.get("input") if isinstance(call.get("input"), dict) else {}
    return bool(_INSPECTION_CMD.match(str((args or {}).get("command") or "")))


def unrecovered_failure_indexes(tool_calls: list[dict[str, Any]] | None) -> list[int]:
    """Indexes of failed calls that no later successful call repaired.

    A failed ``run_command`` followed by a passing ``run_command`` is a fix, not a
    blocker — otherwise the model could never recover inside one turn. A failed
    shell *inspection* (``node --check``, ``cat``, ``ls`` …) is also repaired by
    the tool that does that job (``game_lint``, ``read_file``, ``list_dir``):
    the point was to look at the files, and the files were looked at.
    """
    calls = tool_calls or []
    out: list[int] = []
    for i, call in enumerate(calls):
        if call.get("ok") is not False or call.get("blocked"):
            continue
        key = _repair_key(call)
        # A failure that never named a file (unparseable arguments, missing
        # path) is repaired by any later good call of the same tool.
        loose = not key[2]
        inspection = _is_inspection_shell(call)
        # Kernel verify: "index.html references game.js but it was never
        # written" — writing that file is the repair, not another lint.
        args = call.get("input") if isinstance(call.get("input"), dict) else {}
        never_written = bool((args or {}).get("missing")) and bool(key[2])
        recovered = any(
            later.get("ok") is True
            and (
                _repair_key(later) == key
                or (loose and _call_name(later) == key[0])
                or (inspection and _call_name(later) in _INSPECTION_TOOLS)
                or (never_written and _repair_key(later) == ("write_file", key[1], key[2]))
                or (
                    key[2]
                    and {key[0], _call_name(later)} <= {"write_file", "edit_file"}
                    and _repair_key(later)[1:] == key[1:]
                )
            )
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
            "playtest", "game_lint", "engine", "browser", "world",
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
    if coding and kind in _VISUAL_KINDS and browser_ok is not None:
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
