"""GameAgentBench is gated. Do not run a vendor bake-off before the harness is honest.

Steps 1–4 of the kernel plan must land first. Remaining gates are listed so
nobody claims a win against another coding agent on skip-as-success evidence.
"""
from __future__ import annotations

from typing import Any

# Honest status of the 10-step plan. Update when a gate is actually wired.
GATES: dict[str, bool] = {
    "owner_token": True,
    "typed_tool_protocol": True,
    "atomic_model_router": True,
    "durable_run_log": True,
    "skip_is_not_ok": True,
    "one_builder_pipeline": False,
    "quality_gate_default_on": False,
    "playwright_gameplay": False,
    "godot_headless_required": False,
    "unity_runner": False,
    "asset_pipeline": True,
    "ci_ruff": True,
    "uv_lock": False,
}


def missing_gates() -> list[str]:
    return [name for name, ok in GATES.items() if not ok]


def allowed() -> bool:
    return not missing_gates()


def status() -> dict[str, Any]:
    missing = missing_gates()
    return {
        "ok": not missing,
        "allowed": not missing,
        "missing": missing,
        "gates": dict(GATES),
        "note": (
            "Do not compare Daedalus to other coding agents until missing is empty. "
            "A skipped browser check is not a playtest."
        ),
    }
