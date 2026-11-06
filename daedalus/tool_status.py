"""Parse native tool outcomes without substring false-positives on ``ERROR`` in file contents."""

from __future__ import annotations

import re

# Lines like: ``── write_file OK …`` / ``── run_command FAILED …``
_TOOL_LINE_STATUS = re.compile(
    r"(?m)^\s*(?:[─–]{2,}\s*)?(?P<tool>[\w.-]+)\s+(?P<st>OK|FAILED)\b",
)


def native_tool_result_ok(text: str, *, allow_blocked_pass: bool = True) -> bool:
    """True unless a tool line explicitly reports FAILED.

    Words like ``ERROR`` inside written code do not affect this —
    only structured ``<tool> OK|FAILED`` status lines do.
    """
    if allow_blocked_pass and "BLOCKED" in (text or ""):
        return True
    failed = False
    for m in _TOOL_LINE_STATUS.finditer(text or ""):
        if m.group("st") == "FAILED":
            failed = True
    return not failed
