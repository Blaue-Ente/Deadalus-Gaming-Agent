"""Game build contract — skip is not success; quality is on unless opted out.

Visual kinds need a real browser (or Godot headless / Unity batchmode) result.
``skipped=True`` must never flip ``ok`` to true. The visual critic runs unless
``DAEDALUS_QUALITY=0``. An unparsed critic is not a pass.
"""
from __future__ import annotations

import os
from typing import Any

from .job_contract import evaluate
from .capability_registry import platform_for_kind, proof_kinds


def skip_is_not_ok(result: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize a verify/playtest payload: skipped ⇒ not ok."""
    row = dict(result or {})
    if row.get("skipped"):
        row["ok"] = False
        row.setdefault("summary", "skipped — not evidence")
    return row


def verified(result: dict[str, Any] | None) -> bool:
    row = skip_is_not_ok(result)
    if row.get("na"):
        return bool(row.get("ok", True))
    return bool(row.get("ok")) and not row.get("skipped")


def quality_enabled() -> bool:
    flag = (os.environ.get("DAEDALUS_QUALITY") or "").strip().lower()
    if flag in ("0", "false", "no", "off"):
        return False
    return True


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
    contract = evaluate(
        user_message,
        reply,
        tool_calls=tool_calls,
        test_ok=test_ok,
        browser_ok=browser_ok,
        kind=kind,
        intent=intent,
    )
    platform = platform_for_kind(kind)
    if (kind in proof_kinds() or platform in {"godot", "expo", "capacitor"}) and (
        (reply or "").lower().find("готово") >= 0
        or "done" in (reply or "").lower()
    ):
        if browser_result is None and platform in {"godot", "expo", "capacitor"}:
            contract.complete = False
            contract.blockers.append(
                "missing required capability evidence for this platform"
            )
            contract.checks["capability_evidence"] = False
    return contract
