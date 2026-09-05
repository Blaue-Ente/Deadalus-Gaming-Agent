"""Parse tool-call arguments the way a forgiving server would.

OpenAI-compatible providers hand tool arguments over as a JSON *string*. Weaker
models break it in predictable ways: a code fence around the object, raw
newlines or tabs inside a string, a trailing comma — or the whole thing is cut
off by ``max_tokens`` halfway through a 20 KB ``content``. ``json.loads`` fails
on all of these and the build used to record ``{"raw": ...}``: a failed call
the model could never repair because it never learned what was wrong.

``parse`` tries the cheap repairs, and when the object is truncated it says so,
so the tool result can tell the model to split the file instead of retrying
the same oversized write.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*|\s*```\s*$")
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")
# Control characters that are legal in a JSON *document* only outside strings.
_CTRL_IN_STRING = re.compile(r'"(?:[^"\\]|\\.)*?[\x00-\x1f](?:[^"\\]|\\.)*"')


@dataclass
class ParsedArgs:
    args: dict[str, Any]
    ok: bool
    truncated: bool = False
    repaired: str = ""  # which repair produced a usable object
    raw_len: int = 0

    def note(self) -> str:
        if self.ok and not self.repaired:
            return ""
        if self.ok:
            return f"(kernel: tool arguments were not valid JSON — repaired: {self.repaired})"
        if self.truncated:
            return (
                f"arguments JSON was cut off after {self.raw_len} chars (output token limit). "
                "Nothing was written. Write smaller pieces: put CSS in style.css and the "
                "script in game.js, one write_file per file, each well under 12000 chars."
            )
        return "arguments were not valid JSON — send a plain JSON object."


def _escape_ctrl(text: str) -> str:
    def fix(m: re.Match[str]) -> str:
        s = m.group(0)
        return (
            s.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
        )
    return _CTRL_IN_STRING.sub(fix, text)


def _looks_truncated(text: str) -> bool:
    """An object that opens and never closes, or a string still open at EOF."""
    depth = 0
    in_str = False
    esc = False
    for ch in text:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
    return in_str or depth > 0


def parse(raw: Any) -> ParsedArgs:
    if isinstance(raw, dict):
        return ParsedArgs(raw, ok=True)
    if raw is None:
        return ParsedArgs({}, ok=True)
    text = str(raw)
    n = len(text)
    stripped = text.strip()
    if not stripped:
        return ParsedArgs({}, ok=True)
    try:
        obj = json.loads(stripped)
        return ParsedArgs(obj if isinstance(obj, dict) else {"value": obj}, ok=True, raw_len=n)
    except json.JSONDecodeError:
        pass

    attempts: list[tuple[str, str]] = []
    unfenced = _FENCE.sub("", stripped)
    if unfenced != stripped:
        attempts.append(("code fence removed", unfenced))
    attempts.append(("control characters escaped", _escape_ctrl(unfenced)))
    attempts.append(("trailing comma removed", _TRAILING_COMMA.sub(r"\1", _escape_ctrl(unfenced))))
    for how, cand in attempts:
        try:
            obj = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return ParsedArgs(obj, ok=True, repaired=how, raw_len=n)

    truncated = _looks_truncated(unfenced)
    return ParsedArgs({"raw": text}, ok=False, truncated=truncated, raw_len=n)


def head_keys(raw: str) -> dict[str, str]:
    """Best-effort ``slug`` / ``path`` from a broken payload so the failure is
    logged against the right file and a later good write can repair it."""
    out: dict[str, str] = {}
    for key in ("slug", "path"):
        m = re.search(rf'"{key}"\s*:\s*"([^"\\]{{1,120}})"', raw or "")
        if m:
            out[key] = m.group(1)
    return out
