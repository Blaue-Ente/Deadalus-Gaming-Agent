"""Game build contract — skip is not success; quality is optional and explicit.

Visual kinds need a real browser (or Godot headless) result. ``skipped=True``
must never flip ``ok`` to true. ``playground_quality`` stays off unless
``DAEDALUS_QUALITY=1``.
"""
from __future__ import annotations

import os
from typing import Any

from .job_contract import JobContract, evaluate


def skip_is_not_ok(result: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize a verify/playtest payload: skipped ⇒ not ok."""
    row = dict(result or {})
    if row.get("skipped"):
        row["ok"] = False
        row.setdefault("summary", "skipped — not evidence")
    return row


def verified(result: dict[str, Any] | None) -> bool:
    row = skip_is_not_ok(result)
    return bool(row.get("ok")) and not row.get("skipped")


def quality_enabled() -> bool:
    return (os.environ.get("DAEDALUS_QUALITY") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def evaluate_build(
    user_message: str,
    reply: str,
    *,
    tool_calls: list[dict[str, Any]] | None = None,
    test_ok: bool | None = None,
    browser_result: dict[str, Any] | None = None,
    kind: str = "",
    intent: str = "",
) -> JobContract:
    browser_ok = None
    if browser_result is not None:
        browser_ok = verified(browser_result)
    return evaluate(
        user_message,
        reply,
        tool_calls=tool_calls,
        test_ok=test_ok,
        browser_ok=browser_ok,
        kind=kind,
        intent=intent,
    )
