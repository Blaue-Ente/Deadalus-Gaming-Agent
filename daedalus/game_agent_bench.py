"""Honesty suite for this harness — not a vendor bake-off.

``GATES`` record whether Daedalus is wired honestly enough to *measure*.
``run_suite()`` actually runs in-process checks against this repo.
Public docs still name only Daedalus. We do not pull competitor games
or claim a leaderboard against other agents.

Chromium, Godot, and a Unity Editor are not required for the default path.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

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
    "skill_memory_approve": True,
    "production_layer": True,
    "unity_mcp_or_honest": True,
    "honesty_suite": True,
    "ci_ruff": True,
    "uv_lock": True,
}

NOTE = (
    "Gates are wired. The suite runs in-process honesty checks against this "
    "harness. This repo still does not ship a vendor bake-off. "
    "A skipped browser check is not a playtest."
)

CaseFn = Callable[[], dict[str, Any]]


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
        "note": NOTE,
        "bake_off": False,
        "suite": "python -m daedalus.game_agent_bench",
    }


def _ok(passed: bool, detail: str) -> dict[str, Any]:
    return {"ok": bool(passed), "detail": detail[:400]}


def _guided_racing_ctx() -> dict[str, Any]:
    return {
        "gdd": "GDD " + ("x" * 80),
        "config": {
            "studio_preset": "racing",
            "kind": "vite",
            "brainstorm": {"genre": "racing", "idea": "F-Zero night city"},
            "build_mode": "guided",
        },
        "plan": {"stages": [{"title": "boot", "goal": "menu"}] * 3},
        "project": {"kind": "vite", "slug": "mist"},
    }


def _case_skip_is_not_ok() -> dict[str, Any]:
    from daedalus.game_build_contract import skip_is_not_ok, verified

    skipped = skip_is_not_ok({"ok": True, "skipped": True})
    timeout = verified({"ok": True, "skipped": True, "reason": "timeout"})
    na = verified({"ok": True, "na": True, "skipped": False})
    live = verified({"ok": True, "skipped": False})
    passed = (
        skipped.get("ok") is False
        and timeout is False
        and na is True
        and live is True
    )
    return _ok(passed, "skipped browser is not evidence; N/A may still pass")


def _case_ship_is_not_store() -> dict[str, Any]:
    from daedalus.production_layer import looks_like_ship_claim

    racer = looks_like_ship_claim("one hover ship on a spline")
    steam = looks_like_ship_claim("store-ready")
    bg = looks_like_ship_claim("готово за стора")
    passed = racer is False and steam is True and bg is True
    return _ok(passed, "racer ship mesh is not a store claim")


def _case_empty_world_not_campaign() -> dict[str, Any]:
    from daedalus.world_runtime import prove_project

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        missing = prove_project(root)
        (root / "world").mkdir()
        (root / "world" / "map.json").write_text("{}", encoding="utf-8")
        hollow = prove_project(root)
    passed = missing.get("ok") is False and hollow.get("ok") is False
    errs = list(hollow.get("errors") or missing.get("errors") or [])
    return _ok(passed, "; ".join(str(e) for e in errs[:4]) or "empty JSON failed closed")


def _case_empty_systems_not_production() -> dict[str, Any]:
    from daedalus.production_layer import empty_map, prove_production, write_map

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "index.html").write_text("<canvas></canvas>", encoding="utf-8")
        write_map(root, empty_map(layer="production", systems=[]))
        hollow = prove_production(root)
        write_map(root, empty_map(layer="prototype", systems=[]))
        claimed = prove_production(root, claim="store-ready for the store")
    passed = hollow.get("ok") is False and claimed.get("ok") is False
    return _ok(passed, "empty systems map is not a production layer")


def _case_unity_without_editor_honest() -> dict[str, Any]:
    from daedalus.unity_runtime import (
        honest_refuse,
        looks_like_cloud_editor_claim,
        prove_unity,
    )

    refused = honest_refuse("run")
    bare = looks_like_cloud_editor_claim("unity URP mobile RPG")
    cloud = looks_like_cloud_editor_claim("unity editor in the cloud")
    with tempfile.TemporaryDirectory() as raw:
        na = prove_unity(Path(raw), kind="vite")
    passed = (
        refused.get("ok") is False
        and refused.get("need") == "unity_local"
        and refused.get("honest") is True
        and bare is False
        and cloud is True
        and na.get("na") is True
        and na.get("ok") is True
    )
    return _ok(passed, refused.get("error") or "honest refuse; racing is N/A")


def _case_explorer_cannot_write() -> dict[str, Any]:
    from daedalus.subagents import tools_for_role

    explorer = tools_for_role("explorer")
    verify = tools_for_role("verify")
    coder = tools_for_role("coder")
    passed = (
        "write_file" not in explorer
        and "write_file" not in verify
        and "write_file" in coder
        and "engine" in explorer
        and "production" in explorer
    )
    return _ok(passed, "explorer/verify are read-only; coder may write")


def _case_native_tools_present() -> dict[str, Any]:
    from daedalus.native_tools import NATIVE_TOOL_SPECS

    names = {str(t.get("name") or "") for t in NATIVE_TOOL_SPECS}
    need = {"engine", "world", "assets", "skill", "production"}
    missing = sorted(need - names)
    return _ok(not missing, "native tools: " + ", ".join(sorted(need)))


def _case_assets_coder_only() -> dict[str, Any]:
    from daedalus.subagents import tools_for_role

    passed = (
        "assets" not in tools_for_role("explorer")
        and "assets" not in tools_for_role("verify")
        and "assets" in tools_for_role("coder")
    )
    return _ok(passed, "assets is coder-only (writes files)")


def _case_skill_cannot_self_approve() -> dict[str, Any]:
    from daedalus.learned_skills import run_action

    blocked = run_action("approve", id="anything")
    catalog = run_action("catalog")
    passed = (
        blocked.get("ok") is False
        and "cannot approve" in str(blocked.get("error") or "").lower()
        and catalog.get("ok") is True
    )
    return _ok(passed, str(blocked.get("error") or "approve refused"))


def _case_racing_gates_not_blocking() -> dict[str, Any]:
    from daedalus.game_studio_gates import run_gates

    combined = run_gates(_guided_racing_ctx())
    gates = combined.get("gates") or {}
    passed = (
        combined.get("blocking") is False
        and (gates.get("play-check") or {}).get("passed") is True
        and (gates.get("world-check") or {}).get("passed") is True
        and (gates.get("asset-check") or {}).get("passed") is True
        and (gates.get("production-check") or {}).get("passed") is True
        and (gates.get("unity-check") or {}).get("passed") is True
    )
    return _ok(passed, "guided racing gates warn, they do not block")


def _case_gates_wired() -> dict[str, Any]:
    missing = missing_gates()
    return _ok(not missing, "all honesty flags are True" if not missing else ",".join(missing))


def _case_quality_default_on() -> dict[str, Any]:
    from daedalus.game_build_contract import quality_enabled

    old = os.environ.pop("DAEDALUS_QUALITY", None)
    try:
        on = quality_enabled()
        os.environ["DAEDALUS_QUALITY"] = "0"
        off = quality_enabled()
    finally:
        os.environ.pop("DAEDALUS_QUALITY", None)
        if old is not None:
            os.environ["DAEDALUS_QUALITY"] = old
    passed = on is True and off is False
    return _ok(passed, "quality critic on unless DAEDALUS_QUALITY=0")


def _case_persona_compact() -> dict[str, Any]:
    from daedalus.identity_standalone import DAEDALUS_STANDALONE_PREAMBLE, DAEDALUS_TOOL_CONTRACT

    text = DAEDALUS_STANDALONE_PREAMBLE + "\n" + DAEDALUS_TOOL_CONTRACT
    n_chars = len(text)
    n_lines = text.count("\n")
    passed = n_chars < 12_000 and n_lines < 200 and "bake-off" in text.lower()
    return _ok(passed, "standalone persona %s chars / %s newlines" % (n_chars, n_lines))


def _case_one_builder() -> dict[str, Any]:
    from daedalus.paths import package_root

    text = (package_root() / "builder_runtime.py").read_text(encoding="utf-8")
    passed = (
        "maps to full kernel pipeline" in text
        and "_start_light_builder_run" not in text
    )
    return _ok(passed, "Studio/API/light map to full_autopilot")


def _case_not_vendor_bakeoff() -> dict[str, Any]:
    st = status()
    note = str(st.get("note") or "").lower()
    passed = (
        st.get("bake_off") is False
        and "vendor bake-off" in note
        and "does not ship" in note
        and "playtest" in note
    )
    return _ok(passed, "suite measures this harness; not a competitor table")


CASES: dict[str, CaseFn] = {
    "skip_is_not_ok": _case_skip_is_not_ok,
    "ship_is_not_store": _case_ship_is_not_store,
    "empty_world_not_campaign": _case_empty_world_not_campaign,
    "empty_systems_not_production": _case_empty_systems_not_production,
    "unity_without_editor_honest": _case_unity_without_editor_honest,
    "explorer_cannot_write": _case_explorer_cannot_write,
    "native_tools_present": _case_native_tools_present,
    "assets_coder_only": _case_assets_coder_only,
    "skill_cannot_self_approve": _case_skill_cannot_self_approve,
    "racing_gates_not_blocking": _case_racing_gates_not_blocking,
    "gates_wired": _case_gates_wired,
    "quality_default_on": _case_quality_default_on,
    "persona_compact": _case_persona_compact,
    "one_builder": _case_one_builder,
    "not_vendor_bakeoff": _case_not_vendor_bakeoff,
}


def bench_dir() -> Path:
    from daedalus.paths import data_root

    path = data_root() / "bench"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_latest(report: dict[str, Any]) -> Path:
    path = bench_dir() / "latest.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def run_case(case_id: str) -> dict[str, Any]:
    fn = CASES.get(case_id)
    if fn is None:
        return {"id": case_id, "ok": False, "detail": "unknown case", "ms": 0}
    t0 = time.perf_counter()
    try:
        row = fn()
    except Exception as exc:
        row = {"ok": False, "detail": "%s: %s" % (type(exc).__name__, exc)}
    ms = int((time.perf_counter() - t0) * 1000)
    return {
        "id": case_id,
        "ok": bool(row.get("ok")),
        "detail": str(row.get("detail") or "")[:400],
        "ms": ms,
    }


def run_suite(
    *,
    persist: bool = False,
    case_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Execute honesty cases. Default path needs no Chromium, Godot, or Unity."""
    ids = list(case_ids or CASES)
    t0 = time.perf_counter()
    results = [run_case(cid) for cid in ids]
    failed = [r["id"] for r in results if not r.get("ok")]
    passed = sum(1 for r in results if r.get("ok"))
    report: dict[str, Any] = {
        "ok": not failed,
        "passed": passed,
        "total": len(results),
        "failed": failed,
        "cases": results,
        "ms": int((time.perf_counter() - t0) * 1000),
        "gates": status(),
        "note": NOTE,
        "bake_off": False,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    if persist:
        report["path"] = str(write_latest(report))
    return report


def evaluate(*, persist: bool = False) -> dict[str, Any]:
    """Alias of ``run_suite`` — the harness measures itself."""
    return run_suite(persist=persist)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m daedalus.game_agent_bench",
        description="Run Daedalus honesty checks. Not a vendor bake-off.",
    )
    parser.add_argument(
        "--no-persist",
        action="store_true",
        help="Do not write $DAEDALUS_DATA/bench/latest.json",
    )
    parser.add_argument(
        "--persist",
        action="store_true",
        help="Write latest.json (default)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full report JSON",
    )
    args = parser.parse_args(argv)
    persist = not args.no_persist
    report = run_suite(persist=persist)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        mark = "ok" if report["ok"] else "FAIL"
        print(
            "game_agent_bench %s  %s/%s  %sms  bake_off=false"
            % (mark, report["passed"], report["total"], report["ms"])
        )
        for row in report["cases"]:
            flag = "pass" if row["ok"] else "FAIL"
            print("  %s  %s  %s" % (flag, row["id"], row["detail"]))
        if report.get("path"):
            print("wrote %s" % report["path"])
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
