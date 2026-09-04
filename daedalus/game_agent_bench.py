"""GameAgentBench is a gate list. This repo does not ship a vendor bake-off.

The flags record whether the harness is honest enough to *allow* a comparison.
They do not run one. Public docs still name only Daedalus.
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
    "one_builder_pipeline": True,
    "quality_gate_default_on": True,
    "playwright_gameplay": True,
    "world_kit_runtime": True,
    "godot_headless_required": True,
    "unity_runner": True,
    "asset_pipeline": True,
    "asset_pipeline_v2": True,
    "ci_ruff": True,
    "uv_lock": True,
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
            "Gates are wired. This repo still does not ship a vendor bake-off. "
            "A skipped browser check is not a playtest."
        ),
    }
