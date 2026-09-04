"""Programmatic Definition of Done — the model does not get to declare victory.

Used by chat replies and standalone autopilot so "готово" requires evidence:
writes, smoke, and (for visual kinds) browser verify.
"""
from __future__ import annotations

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
    failed = any(c.get("ok") is False for c in calls)
    return wrote, ran, failed


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
) -> JobContract:
    coding = is_coding_task(user_message, intent)
    wrote, ran, failed = _tools_ok(tool_calls)
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
    if coding and claims_done:
        try:
            from .game_engineer import missing_eng_tools
            miss = missing_eng_tools(user_message, tool_calls)
        except Exception:
            miss = ""
        if miss:
            checks["eng_tools"] = False
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
