"""Wrap-up of a build turn — one short persona call over the kernel's ledger.

Before this module the persona received the whole enriched context after the
native build and was asked (in prose) not to plan again. It planned again:
NEED_* markers, JSON tool dumps, SQL, `<think>` transcripts, DoD stickers the
owner was never meant to see. The build already happened in the native rounds;
the wrap-up only has to tell the owner what exists, how to open it and what is
still missing. Anything else is stripped, and when nothing usable is left the
kernel writes the summary itself from the ledger.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Awaitable, Callable

from .build_loop import BuildLoop
from .tool_dumps import extract_tool_dumps, strip_tool_dumps

log = logging.getLogger(__name__)

WrapUpCall = Callable[..., Awaitable[str]]

_THINK = re.compile(r"<think(?:ing)?>.*?(?:</think(?:ing)?>|$)", re.S | re.I)
_NEED_LINE = re.compile(r"^[ \t]*(?:[-*•]\s*)?NEED_[A-Z_]+\b.*$", re.M)
_SQL_LINE = re.compile(
    r"^[ \t]*(?:SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM|CREATE\s+TABLE|DROP\s+TABLE|ALTER\s+TABLE)\b.*$",
    re.M | re.I,
)
_RULE_LINE = re.compile(r"^[ \t]*[━─═]{3,}.*$", re.M)
# <need_x …>…</need_x>, <need_x …/>, <tool_call>…</tool_call> — any XML-ish tool call
_XML_TOOL = re.compile(
    r"<(need_[a-z_]+|tool_call|function_call|invoke)\b[^>]*?/>"
    r"|<(need_[a-z_]+|tool_call|function_call|invoke)\b[^>]*>.*?</\2\s*>",
    re.S | re.I,
)
_EMPTY_FENCE = re.compile(r"```[a-zA-Z]*\s*```")
_BLANK_RUN = re.compile(r"\n{3,}")
_MIN_USEFUL = 24

WRAP_UP_RULES = (
    "The build turn is over — the kernel ran the plan and the tools. You are writing the "
    "closing message to the owner, nothing else. 4–10 short lines: what exists (files), how "
    "to open it (the preview path), what the kernel verified, what is still missing if "
    "anything. No tool calls, no NEED_* markers, no JSON, no XML, no SQL, no new plan. "
    "Do not repeat the ledger verbatim. State only what the ledger says: no guessing why "
    "something failed ('probably', 'вероятно'); a failure the ledger marks recovered is not "
    "an open issue and does not belong in the message."
)


def _ledger(loop: BuildLoop, native_log: list[dict[str, Any]]) -> str:
    calls = [r for r in native_log if r.get("tool") not in ("build_loop", None)]
    ok = sum(1 for r in calls if r.get("ok") is not False)
    failed = len(calls) - ok
    open_failures = loop.unrecovered_failures()
    if failed and not open_failures:
        tally = f"Tool calls: {ok} ok, {failed} failed and all recovered (not open issues)."
    elif open_failures:
        names = "; ".join(f"{o.tool} {o.args or ''}".strip()[:80] for o in open_failures[-3:])
        tally = f"Tool calls: {ok} ok, {failed} failed; still unrecovered: {names}."
    else:
        tally = f"Tool calls: {ok} ok, none failed."
    lines = [loop.summary_block().strip(), tally]
    files = loop.written_files()
    if files:
        lines.append("Files written: " + ", ".join(f"{s}/{p}" if s else p for s, p in files[:12]))
    mentor = getattr(loop, "mentor", None)
    if mentor is not None and getattr(mentor, "advice", None):
        lines.append(f"Mentor stepped in {len(mentor.advice)}×: " + "; ".join(
            (a.diagnosis or a.instruction)[:120] for a in mentor.advice[:3]
        ))
    if loop.slug:
        from .native_tools import project_nav

        lines.append(project_nav(loop.slug))
    try:
        from .llm_cost import current as _cost_now
        led = _cost_now()
        if led is not None and led.rows:
            lines.append(led.summary())
    except Exception:
        pass
    return "\n".join(lines)


def wrap_up_prompt(loop: BuildLoop, native_log: list[dict[str, Any]], user_message: str) -> str:
    ask = (user_message or "").strip()
    if len(ask) > 600:
        ask = ask[:600] + "…"
    return (
        "━━━ BUILD LEDGER (kernel) ━━━\n"
        f"Owner asked: {ask}\n\n{_ledger(loop, native_log)}\n\n"
        "Write the closing message now."
    )


def sanitize_reply(text: str) -> str:
    """Remove everything the owner must not see in a build wrap-up."""
    out = text or ""
    out = _THINK.sub("", out)
    dumps = extract_tool_dumps(out, limit=16)
    if dumps:
        out = strip_tool_dumps(out, dumps)
    out = _XML_TOOL.sub("", out)
    out = _NEED_LINE.sub("", out)
    out = _SQL_LINE.sub("", out)
    out = _RULE_LINE.sub("", out)
    out = _EMPTY_FENCE.sub("", out)
    out = _BLANK_RUN.sub("\n\n", out)
    return out.strip()


def fallback_summary(loop: BuildLoop) -> str:
    """The kernel's own closing message when the model gave nothing usable."""
    v = loop.last_verdict or loop.verdict("")
    files = loop.written_files()
    lines: list[str] = []
    if loop.slug:
        lines.append(f"Project: {loop.slug}")
    if files:
        lines.append("Files: " + ", ".join(p for _, p in files[:12]))
    if loop.slug:
        first = files[0][1] if files else "index.html"
        lines.append(f"Open: /api/v1/playground/projects/{loop.slug}/preview/{first if first.endswith(('.html', '.htm')) else 'index.html'}")
    if loop.verify_report:
        lines.append("Kernel verify: " + "; ".join(x.strip() for x in loop.verify_report[:4]))
    if v.complete:
        lines.append("Plan complete.")
    else:
        if v.missing_steps:
            lines.append("Still missing: " + ", ".join(v.missing_steps[:6]))
        if v.failed_calls:
            lines.append("Unrecovered: " + ", ".join(f"{o.tool}" for o in v.failed_calls[-3:]))
        if loop.stop_reason:
            lines.append(f"Stopped: {loop.stop_reason}.")
    return "\n".join(lines) or "Build turn ended without file changes."


async def wrap_up(
    loop: BuildLoop,
    native_log: list[dict[str, Any]],
    user_message: str,
    call_fn: WrapUpCall,
    persona: str,
) -> str:
    """One short call in the ``wrap_up`` role, sanitized; ledger summary on failure."""
    prompt = wrap_up_prompt(loop, native_log, user_message)
    try:
        raw = await call_fn(
            f"{persona}\n\n{WRAP_UP_RULES}",
            [{"role": "user", "content": prompt}],
            role="wrap_up",
        )
    except Exception as exc:
        log.warning("build wrap-up call failed, using the kernel ledger: %s", exc)
        return fallback_summary(loop)
    clean = sanitize_reply(raw or "")
    if len(clean) < _MIN_USEFUL:
        return fallback_summary(loop)
    return clean
