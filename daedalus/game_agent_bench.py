"""Honesty suite + hermetic game_v1 tasks — not a vendor bake-off.

``GATES`` record whether Daedalus is wired honestly enough to *measure*.
``run_suite()`` is the default honesty layer (15 cases). ``run_game_suite()``
runs exactly 20 hermetic game_v1 tasks. Live engine overlays are opt-in.
Public docs still name only Daedalus. We do not pull competitor games
or claim a leaderboard against other agents.

Chromium, Godot, Unity, and Blender are not required for the default path.
P0.22 may compare honesty + game_v1 snapshots; this module is not that gate.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Literal

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
    need = {"engine", "world", "assets", "skill", "production", "math3d", "spatial", "game_lint", "profiler", "game_canon"}
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
        and (gates.get("eng-check") or {}).get("passed") is True
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

HONESTY_CASE_COUNT = 15
GAME_TASK_COUNT = 20
SUITE_HONESTY = "honesty"
SUITE_GAME = "game"
SUITE_ALL = "all"
SUITES = (SUITE_HONESTY, SUITE_GAME, SUITE_ALL)

GAME_NOTE = (
    "game_v1 is a hermetic 20-task layer on this harness. "
    "Live Godot/Unity/Blender overlays are opt-in and fail closed. "
    "This is not a vendor bake-off. P0.22 may compare snapshots; this is not that gate."
)

P0_22_HOOKS: dict[str, Any] = {
    "honesty_cli": "python -m daedalus.game_agent_bench --no-persist --json",
    "game_cli": "python -m daedalus.game_agent_bench --suite game --no-persist --json",
    "honesty_artifact": "artifacts/baseline/benchmark-results.json",
    "game_artifact": "artifacts/baseline/game-benchmark-results.json",
    "honesty_case_count": HONESTY_CASE_COUNT,
    "game_task_count": GAME_TASK_COUNT,
    "live_required_for_release": False,
    "note": (
        "P0.22 compares honesty (15) plus hermetic game_v1 (20) against the "
        "recorded pack. Live rows are never required. This ticket does not "
        "implement the release-gate aggregator."
    ),
}

Family = Literal["godot", "unity", "blender", "build"]
Mode = Literal["hermetic", "live"]

_MIN_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


@dataclass(frozen=True)
class TaskSpec:
    id: str
    family: Family
    mode: Mode
    fn: CaseFn
    requires: tuple[str, ...] = ()


def _live_requested(flag: bool | None = None) -> bool:
    if flag:
        return True
    raw = (os.environ.get("DAEDALUS_BENCH_LIVE") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


@contextmanager
def _empty_engine_bins() -> Iterator[None]:
    """Keep hermetic tasks off live editors even if binaries are on PATH."""
    keys = (
        "GODOT_BIN",
        "GODOT_BINARY",
        "UNITY_PATH",
        "UNITY_EDITOR",
        "BLENDER_BIN",
        "DAEDALUS_UNITY_MCP",
    )
    saved = {key: os.environ.pop(key, None) for key in keys}
    import daedalus.blender_jobs as blender_jobs
    import daedalus.engine_runners as engine_runners
    import daedalus.godot_runtime_probe as godot_probe

    old_godot = godot_probe.godot_bin
    old_unity = engine_runners.unity_bin
    old_blender = blender_jobs.blender_bin
    godot_probe.godot_bin = lambda: ""
    engine_runners.unity_bin = lambda: ""
    blender_jobs.blender_bin = lambda: ""
    try:
        yield
    finally:
        godot_probe.godot_bin = old_godot
        engine_runners.unity_bin = old_unity
        blender_jobs.blender_bin = old_blender
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def _project_root(root: Path) -> Iterator[None]:
    from daedalus import playground as pg

    original = pg.project_dir
    pg.project_dir = lambda slug, _root=root: Path(_root)
    try:
        yield
    finally:
        pg.project_dir = original


def _task_godot_kind_never_vite() -> dict[str, Any]:
    from daedalus.godot_first import coerce_kind
    from daedalus.tool_router import match_playbook, route

    pb = match_playbook("Направи Godot 4 игра: CharacterBody2D, scenes/main.tscn, GDScript.")
    rt = route("Godot 4 GDScript dungeon")
    passed = (
        coerce_kind("godot", "Snake", "html canvas") == "godot"
        and coerce_kind("static", "Veins", "Godot 4 GDScript") == "godot"
        and coerce_kind("vite", "Room", "scenes/main.tscn") == "godot"
        and coerce_kind("static", "Snake", "canvas arcade") == "static"
        and coerce_kind("static", "x", "", "scripts/player.gd") == "godot"
        and pb.kind == "godot"
        and rt.kind == "godot"
    )
    return _ok(passed, "Godot words / .gd / .tscn never become Vite/HTML")


def _task_godot_2d_scaffold_dod() -> dict[str, Any]:
    from daedalus.godot_dod import dod_v2_static
    from daedalus.playground_godot_scaffold import write_godot_scaffold

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        write_godot_scaffold(root, "Veins", "veins", "Godot 4 platformer")
        player = (root / "scripts/player.gd").read_text(encoding="utf-8")
        preview = (root / "studio/preview/index.html").read_text(encoding="utf-8")
        dod = dod_v2_static(root)
        has_project = (root / "project.godot").is_file()
    passed = (
        has_project
        and "CharacterBody2D" in player
        and dod.get("ok") is True
        and dod.get("is_3d") is not True
        and "not Play" in preview
    )
    return _ok(passed, "2D scaffold DoD green; preview is not Play")


def _task_godot_3d_slice_not_racer() -> dict[str, Any]:
    from daedalus.game_factory import wants_godot_3d_slice
    from daedalus.godot_dod import dod_v2_static
    from daedalus.playground_godot3d_scaffold import write_godot3d_scaffold

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        write_godot3d_scaffold(root, "Plate Room", "plate-room", "Godot 3D puzzle room")
        tscn = (root / "scenes/main.tscn").read_text(encoding="utf-8")
        player = (root / "scripts/player.gd").read_text(encoding="utf-8")
        dod = dod_v2_static(root)
    passed = (
        wants_godot_3d_slice("Room", "Godot 3D puzzle room with one riddle") is True
        and wants_godot_3d_slice("Mist", "F-Zero hover racer 3d") is False
        and "CharacterBody3D" in tscn
        and "Camera3D" in tscn
        and ('name="Plate"' in tscn or 'name="Door"' in tscn)
        and "CharacterBody2D" not in player
        and dod.get("ok") is True
        and dod.get("is_3d") is True
    )
    return _ok(passed, "3D puzzle room, not the Vite racer")


def _task_godot_broken_signal_fails() -> dict[str, Any]:
    from daedalus.godot_dod import dod_signals_static, dod_v2_static
    from daedalus.playground_godot_scaffold import write_godot_scaffold

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        write_godot_scaffold(root, "Veins", "veins", "2D RPG")
        tscn = root / "scenes" / "main.tscn"
        tscn.write_text(
            tscn.read_text(encoding="utf-8")
            + '\n[connection signal="body_entered" from="Pit" to="Player" '
            'method="_on_missing_handler"]\n',
            encoding="utf-8",
        )
        sig = dod_signals_static(root)
        dod = dod_v2_static(root)
    passed = (
        sig.get("ok") is False
        and any("_on_missing_handler" in str(x) for x in (sig.get("issues") or []))
        and dod.get("ok") is False
        and "signals" in (dod.get("blocking") or [])
    )
    return _ok(passed, "planted missing signal handler fails static DoD")


def _task_godot_3d_planted_defects() -> dict[str, Any]:
    from daedalus.godot_dod import dod_v2_static
    from daedalus.playground_godot3d_scaffold import write_godot3d_scaffold

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        write_godot3d_scaffold(root, "Room", "room", "Godot 3D puzzle room")
        tscn_path = root / "scenes" / "main.tscn"
        original = tscn_path.read_text(encoding="utf-8")
        tscn_path.write_text(original.replace("Camera3D", "Node3D"), encoding="utf-8")
        no_cam = dod_v2_static(root)
        tscn_path.write_text(
            original.replace('name="Goal"', 'name="MissingGoal"')
            .replace('name="Door"', 'name="Slab"')
            .replace('name="Plate"', 'name="Pad"'),
            encoding="utf-8",
        )
        no_goal = dod_v2_static(root)
        tscn_path.write_text(original, encoding="utf-8")
        restored = dod_v2_static(root)
    passed = (
        no_cam.get("ok") is False
        and "camera" in (no_cam.get("blocking") or [])
        and no_goal.get("ok") is False
        and "goal" in (no_goal.get("blocking") or [])
        and restored.get("ok") is True
    )
    return _ok(passed, "stripped Camera3D / Goal-Door-Plate fail closed")


def _task_godot_missing_binary_not_ok() -> dict[str, Any]:
    from daedalus.engine_runners import run_godot
    from daedalus.game_build_contract import verified
    from daedalus.godot_first import preflight
    from daedalus.playground_godot_scaffold import write_godot_scaffold

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        write_godot_scaffold(root, "Veins", "veins", "godot dungeon")
        flight = preflight(root)
        with _project_root(root):
            out = run_godot("veins")
    passed = (
        flight.get("ok") is False
        and flight.get("skipped") is True
        and "godot_missing" in (flight.get("blockers") or [])
        and verified(out) is False
        and (out.get("skipped") or not out.get("ok"))
    )
    return _ok(passed, "missing GODOT_BIN is skip≠ok")


def _task_godot_pipeline_slice_fail_closed() -> dict[str, Any]:
    from daedalus.godot_first import run_pipeline
    from daedalus.playground_godot_scaffold import write_godot_scaffold

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        write_godot_scaffold(root, "Veins", "veins")
        with _project_root(root):
            pipe = run_pipeline("veins", through="slice")
    stages = pipe.get("stages") or {}
    passed = (
        pipe.get("kind") == "godot"
        and (stages.get("contract") or {}).get("ok") is True
        and (stages.get("slice") or {}).get("ok") is True
        and (stages.get("preflight") or {}).get("ok") is False
        and pipe.get("ok") is False
    )
    return _ok(passed, "pipeline through slice fail-closed without Godot")


def _task_godot_world_kit_waits() -> dict[str, Any]:
    from daedalus.godot_first import allow_world_kit

    passed = (
        allow_world_kit("godot", "small platformer") is False
        and allow_world_kit("godot", "small platformer", "epic") is True
        and allow_world_kit("godot", "epic campaign world kit") is True
        and allow_world_kit("static", "small platformer") is True
    )
    return _ok(passed, "world kit waits unless epic/campaign")


def _task_godot_bridge_sidecars() -> dict[str, Any]:
    from daedalus.godot_bridge import (
        BRIDGE_REL,
        ensure_autoload_line,
        ensure_bridge,
        ensure_import_sidecars,
    )

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "project.godot").write_text(
            '[application]\nconfig/name="X"\nrun/main_scene="res://scenes/main.tscn"\n',
            encoding="utf-8",
        )
        assets = root / "assets"
        assets.mkdir()
        (assets / "hero.png").write_bytes(_MIN_PNG)
        (assets / "crate.glb").write_bytes(b"glTF")
        info = ensure_bridge(root)
        written = ensure_import_sidecars(root)
        proj = (root / "project.godot").read_text(encoding="utf-8")
        has_bridge = (root / BRIDGE_REL).is_file()
        once = ensure_autoload_line(proj, "DaedalusBridge", "res://scripts/autoload/daedalus_bridge.gd")
        twice = ensure_autoload_line(once, "DaedalusBridge", "res://scripts/autoload/daedalus_bridge.gd")
    passed = (
        info.get("ok") is True
        and has_bridge
        and "DaedalusBridge=" in proj
        and any(p.endswith("hero.png.import") for p in written)
        and any(p.endswith("crate.glb.import") for p in written)
        and once.count("DaedalusBridge=") == 1
        and twice == once
    )
    return _ok(passed, "bridge autoload + .import sidecars; no duplicate")


def _task_godot_publish_needs_artifact() -> dict[str, Any]:
    from daedalus.godot_first import ensure_contract, publish_artifact
    from daedalus.playground_godot_scaffold import write_godot_scaffold

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        write_godot_scaffold(root, "Slice", "slice", "Godot 4")
        contract = ensure_contract(root, slug="slice", name="Slice")
        pub = publish_artifact(root, slug="slice", export_row={"ok": False})
    passed = (
        contract.get("html_is_not_play") is True
        and pub.get("ok") is False
        and "no_immutable_artifact" in (pub.get("blockers") or [])
    )
    return _ok(passed, "publish needs export artifact; HTML is not Play")


def _unity_tree(root: Path) -> None:
    (root / "Assets").mkdir(parents=True)
    (root / "Packages").mkdir(parents=True)
    (root / "ProjectSettings").mkdir(parents=True)
    (root / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
    (root / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 2022.3.48f1\n", encoding="utf-8",
    )
    (root / "Assets" / "GameEntry.cs").write_text("class GameEntry {}\n", encoding="utf-8")


def _task_unity_scaffold_not_play() -> dict[str, Any]:
    from daedalus.playground_runtime import verify_unity_scaffold
    from daedalus.playground_unity_scaffold import write_unity_scaffold
    from daedalus.unity_runtime import status

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        written = write_unity_scaffold(root, "Hub", "hub", "unity slice")
        with _project_root(root):
            verified_files = verify_unity_scaffold("hub")
        st = status(root)
        has_preview = (root / "studio/preview/index.html").is_file()
    passed = (
        "Packages/manifest.json" in written
        and any(path.endswith(".cs") for path in written)
        and verified_files.get("ok") is True
        and st.get("play") is False
        and has_preview
    )
    return _ok(passed, "Unity scaffold files exist; status.play is false")


def _task_unity_without_editor_honest() -> dict[str, Any]:
    from daedalus.game_build_contract import verified
    from daedalus.unity_runtime import honest_refuse, prove_unity

    refused = honest_refuse("run")
    with tempfile.TemporaryDirectory() as raw:
        na = prove_unity(Path(raw), kind="vite")
    passed = (
        refused.get("ok") is False
        and refused.get("need") == "unity_local"
        and refused.get("honest") is True
        and refused.get("play") is False
        and verified(refused) is False
        and na.get("na") is True
        and na.get("ok") is True
    )
    return _ok(passed, "no Editor → honest refuse; vite is N/A")


def _task_unity_cloud_claim_fails() -> dict[str, Any]:
    from daedalus.unity_runtime import looks_like_cloud_editor_claim, prove_unity

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        _unity_tree(root)
        proof = prove_unity(root, claim="unity editor in the cloud", kind="unity")
    blob = " ".join(str(x) for x in (proof.get("errors") or [])).lower()
    passed = (
        looks_like_cloud_editor_claim("unity URP mobile RPG") is False
        and looks_like_cloud_editor_claim("unity editor in the cloud") is True
        and proof.get("ok") is False
        and ("cloud" in blob or "vendor" in blob)
    )
    return _ok(passed, "cloud Editor claim fail-closed; bare unity is not a claim")


def _task_unity_batchmode_not_play() -> dict[str, Any]:
    import daedalus.engine_runners as engine_runners
    from daedalus.unity_runtime import run_action, status

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        _unity_tree(root)
        old = engine_runners.unity_bin
        engine_runners.unity_bin = lambda: "/tmp/fake-unity"
        try:
            st = status(root)
            with _project_root(root):
                live = run_action("inspect", "batch")
        finally:
            engine_runners.unity_bin = old
    passed = (
        st.get("promise") == "batchmode"
        and st.get("play") is False
        and live.get("play") is False
        and live.get("honest") is True
        and live.get("ok") is False
    )
    return _ok(passed, "batchmode promise is not Play")


def _task_blender_emit_jobs_no_bpy() -> dict[str, Any]:
    import ast
    import inspect

    import daedalus.blender_jobs as blender_jobs
    from daedalus.blender_jobs import JOB_IDS, render_script

    scripts_ok = True
    for job in JOB_IDS:
        script = render_script(job)
        if "import bpy" not in script or "{name}" in script or "{out}" in script:
            scripts_ok = False
            break
    tree = ast.parse(inspect.getsource(blender_jobs))
    imported_bpy = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(alias.name == "bpy" for alias in node.names):
            imported_bpy = True
        if isinstance(node, ast.ImportFrom) and node.module == "bpy":
            imported_bpy = True
    passed = scripts_ok and not imported_bpy and getattr(blender_jobs, "bpy", None) is None
    return _ok(passed, "emit bpy scripts; Daedalus does not import bpy")


def _task_blender_mcp_emit_only() -> dict[str, Any]:
    from daedalus.blender_jobs import JOB_IDS
    from daedalus.blender_mcp import handle

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        listed = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {t["name"] for t in listed["result"]["tools"]}
        called = handle({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "create_primitive",
                "arguments": {
                    "name": "HeroMesh",
                    "kind": "cube",
                    "project_dir": str(root),
                },
            },
        })
        text = called["result"]["content"][0]["text"]
        emitted = root / "tools/blender/create_primitive.py"
        emitted_text = emitted.read_text(encoding="utf-8") if emitted.is_file() else ""
    passed = (
        names == set(JOB_IDS)
        and bool(emitted_text)
        and ("create_primitive" in text or "HeroMesh" in text)
        and "import bpy" in emitted_text
    )
    return _ok(passed, "MCP lists JOB_IDS and emits tools/blender/*.py")


def _task_blender_execute_without_bin_honest() -> dict[str, Any]:
    from daedalus.blender_jobs import run_job
    from daedalus.game_build_contract import skip_is_not_ok, verified

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        info = run_job(root, "export_glb", {"execute": True, "out": "assets/hero.glb"})
        wrapped = skip_is_not_ok({
            **info,
            "skipped": not bool(info.get("ran")),
        })
        fake_glb = list(root.rglob("*.glb"))
        emitted = (root / "tools/blender/export_glb.py").is_file()
    passed = (
        info.get("ran") is not True
        and verified(wrapped) is False
        and not fake_glb
        and emitted
    )
    return _ok(passed, "execute without BLENDER_BIN is not a verified export")


def _task_build_skip_and_quality() -> dict[str, Any]:
    skip = _case_skip_is_not_ok()
    quality = _case_quality_default_on()
    passed = bool(skip.get("ok")) and bool(quality.get("ok"))
    return _ok(passed, "skip≠ok and quality default on")


def _task_build_leftover_ladder_stays() -> dict[str, Any]:
    from daedalus.paths import product_root

    root = product_root()
    agent = (root / "daedalus" / "agent.py").read_text(encoding="utf-8")
    scan = (root / "daedalus" / "need_scan.py").read_text(encoding="utf-8")
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    passed = (
        "NEED_PLAYGROUND_RUN" in scan
        and "pg_run_q" in scan
        and "NEED_SANDBOX" in agent
        and "async def _execute_leftover_markers" in agent
        and "daedalus/tool_loop.py" in pyproject
        and "F821" in pyproject
    )
    return _ok(passed, "leftover NEED_* ladder and F821 ignore stay")


def _task_build_docker_refuse_hermetic() -> dict[str, Any]:
    from daedalus.paths import product_root

    root = product_root()
    sandbox = (root / "daedalus" / "docker_sandbox.py").read_text(encoding="utf-8")
    backends = (root / "daedalus" / "execution_backends.py").read_text(encoding="utf-8")
    passed = (
        "class DockerBackend" in backends
        and "No Local fall-through" in backends
        and "def docker_bin" in sandbox
        and "def probe_docker" in sandbox
        and "docker not on PATH" in sandbox
        and "def run_in_docker" in sandbox
        and "from daedalus.agent" not in sandbox
        and "import daedalus.agent" not in sandbox
    )
    return _ok(passed, "DockerBackend refuses without docker; no host fall-through")


def _live_godot_bridge() -> dict[str, Any]:
    from daedalus.game_build_contract import skip_is_not_ok, verified
    from daedalus.godot_runtime_probe import godot_bin, run_runtime
    from daedalus.playground_godot_scaffold import write_godot_scaffold

    if not godot_bin():
        row = skip_is_not_ok({"ok": False, "skipped": True, "reason": "godot_missing"})
        return _ok(verified(row), "live Godot gated — missing binary is not a playtest")
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        write_godot_scaffold(root, "Live", "live", "Godot 4")
        probe = run_runtime(root, frames=8)
    if probe.get("skipped"):
        return _ok(False, str(probe.get("skipped") or "live Godot skipped"))
    return _ok(bool(probe.get("ok") and probe.get("ran")), "live Godot headless bridge")


def _live_unity_batchmode() -> dict[str, Any]:
    from daedalus.engine_runners import run_unity, unity_bin
    from daedalus.game_build_contract import skip_is_not_ok, verified
    from daedalus.playground_unity_scaffold import write_unity_scaffold

    if not unity_bin():
        row = skip_is_not_ok({"ok": False, "skipped": True, "reason": "unity_missing"})
        return _ok(verified(row), "live Unity gated — missing Editor is not Play")
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        write_unity_scaffold(root, "Live", "live", "unity")
        with _project_root(root):
            out = run_unity("live")
    if out.get("skipped"):
        return _ok(False, str(out.get("error") or "live Unity skipped"))
    return _ok(bool(out.get("ok") and out.get("ran") and out.get("play") is not True), "live Unity batchmode import")


def _live_blender_execute() -> dict[str, Any]:
    from daedalus.blender_jobs import blender_bin, run_job
    from daedalus.game_build_contract import skip_is_not_ok, verified

    if not blender_bin():
        row = skip_is_not_ok({"ok": False, "skipped": True, "reason": "blender_missing"})
        return _ok(verified(row), "live Blender gated — missing binary is not an export")
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        info = run_job(root, "export_glb", {"execute": True, "out": "assets/hero.glb"})
    if not info.get("ran"):
        wrapped = skip_is_not_ok({**info, "skipped": True})
        return _ok(verified(wrapped), "live Blender did not run")
    return _ok(bool(info.get("ok")), "live Blender execute")


GAME_TASKS: dict[str, TaskSpec] = {
    "godot_kind_never_vite": TaskSpec("godot_kind_never_vite", "godot", "hermetic", _task_godot_kind_never_vite),
    "godot_2d_scaffold_dod": TaskSpec("godot_2d_scaffold_dod", "godot", "hermetic", _task_godot_2d_scaffold_dod),
    "godot_3d_slice_not_racer": TaskSpec("godot_3d_slice_not_racer", "godot", "hermetic", _task_godot_3d_slice_not_racer),
    "godot_broken_signal_fails": TaskSpec("godot_broken_signal_fails", "godot", "hermetic", _task_godot_broken_signal_fails),
    "godot_3d_planted_defects": TaskSpec("godot_3d_planted_defects", "godot", "hermetic", _task_godot_3d_planted_defects),
    "godot_missing_binary_not_ok": TaskSpec("godot_missing_binary_not_ok", "godot", "hermetic", _task_godot_missing_binary_not_ok),
    "godot_pipeline_slice_fail_closed": TaskSpec("godot_pipeline_slice_fail_closed", "godot", "hermetic", _task_godot_pipeline_slice_fail_closed),
    "godot_world_kit_waits": TaskSpec("godot_world_kit_waits", "godot", "hermetic", _task_godot_world_kit_waits),
    "godot_bridge_sidecars": TaskSpec("godot_bridge_sidecars", "godot", "hermetic", _task_godot_bridge_sidecars),
    "godot_publish_needs_artifact": TaskSpec("godot_publish_needs_artifact", "godot", "hermetic", _task_godot_publish_needs_artifact),
    "unity_scaffold_not_play": TaskSpec("unity_scaffold_not_play", "unity", "hermetic", _task_unity_scaffold_not_play),
    "unity_without_editor_honest": TaskSpec("unity_without_editor_honest", "unity", "hermetic", _task_unity_without_editor_honest),
    "unity_cloud_claim_fails": TaskSpec("unity_cloud_claim_fails", "unity", "hermetic", _task_unity_cloud_claim_fails),
    "unity_batchmode_not_play": TaskSpec("unity_batchmode_not_play", "unity", "hermetic", _task_unity_batchmode_not_play),
    "blender_emit_jobs_no_bpy": TaskSpec("blender_emit_jobs_no_bpy", "blender", "hermetic", _task_blender_emit_jobs_no_bpy),
    "blender_mcp_emit_only": TaskSpec("blender_mcp_emit_only", "blender", "hermetic", _task_blender_mcp_emit_only),
    "blender_execute_without_bin_honest": TaskSpec("blender_execute_without_bin_honest", "blender", "hermetic", _task_blender_execute_without_bin_honest),
    "build_skip_and_quality": TaskSpec("build_skip_and_quality", "build", "hermetic", _task_build_skip_and_quality),
    "build_leftover_ladder_stays": TaskSpec("build_leftover_ladder_stays", "build", "hermetic", _task_build_leftover_ladder_stays),
    "build_docker_refuse_hermetic": TaskSpec("build_docker_refuse_hermetic", "build", "hermetic", _task_build_docker_refuse_hermetic),
}

LIVE_TASKS: dict[str, TaskSpec] = {
    "godot_live_bridge": TaskSpec("godot_live_bridge", "godot", "live", _live_godot_bridge, ("GODOT_BIN",)),
    "unity_live_batchmode": TaskSpec("unity_live_batchmode", "unity", "live", _live_unity_batchmode, ("UNITY_PATH",)),
    "blender_live_execute": TaskSpec("blender_live_execute", "blender", "live", _live_blender_execute, ("BLENDER_BIN",)),
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


def _row(
    spec: TaskSpec | None,
    case_id: str,
    fn: CaseFn,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        raw = fn()
    except Exception as exc:
        raw = {"ok": False, "detail": "%s: %s" % (type(exc).__name__, exc)}
    row: dict[str, Any] = {
        "id": case_id,
        "ok": bool(raw.get("ok")),
        "detail": str(raw.get("detail") or "")[:400],
        "ms": int((time.perf_counter() - t0) * 1000),
    }
    if spec is not None:
        row["family"] = spec.family
        row["mode"] = spec.mode
    return row


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
        "suite": SUITE_HONESTY,
        "layer": "honesty",
        "live": False,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    if persist:
        report["path"] = str(write_latest(report))
    return report


def run_task(task_id: str) -> dict[str, Any]:
    spec = GAME_TASKS.get(task_id) or LIVE_TASKS.get(task_id)
    if spec is None:
        return {"id": task_id, "ok": False, "detail": "unknown task", "ms": 0}
    return _row(spec, task_id, spec.fn)


def run_game_suite(
    *,
    persist: bool = False,
    live: bool = False,
    task_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Hermetic game_v1 tasks. Default path monkeypatches engine bins empty."""
    ids = list(task_ids or GAME_TASKS)
    t0 = time.perf_counter()
    results: list[dict[str, Any]] = []
    with _empty_engine_bins():
        for tid in ids:
            spec = GAME_TASKS.get(tid)
            if spec is None:
                results.append({"id": tid, "ok": False, "detail": "unknown task", "ms": 0})
                continue
            results.append(_row(spec, tid, spec.fn))
    live_ran: list[str] = []
    live_gated: list[str] = []
    if live:
        for spec in LIVE_TASKS.values():
            row = _row(spec, spec.id, spec.fn)
            results.append(row)
            if row.get("ok"):
                live_ran.append(spec.id)
            else:
                live_gated.append(spec.id)
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
        "note": GAME_NOTE,
        "bake_off": False,
        "suite": SUITE_GAME,
        "layer": "game_v1",
        "live": bool(live),
        "live_ran": live_ran,
        "live_gated": live_gated,
        "p0_22_hooks": dict(P0_22_HOOKS),
        "at": datetime.now(timezone.utc).isoformat(),
    }
    if persist:
        report["path"] = str(write_latest(report))
    return report


def run_all_suites(*, persist: bool = False, live: bool = False) -> dict[str, Any]:
    t0 = time.perf_counter()
    honesty = run_suite(persist=False)
    game = run_game_suite(persist=False, live=live)
    cases = list(honesty.get("cases") or []) + list(game.get("cases") or [])
    failed = [r["id"] for r in cases if not r.get("ok")]
    passed = sum(1 for r in cases if r.get("ok"))
    report: dict[str, Any] = {
        "ok": bool(honesty.get("ok") and game.get("ok")),
        "passed": passed,
        "total": len(cases),
        "failed": failed,
        "cases": cases,
        "ms": int((time.perf_counter() - t0) * 1000),
        "gates": status(),
        "note": GAME_NOTE,
        "bake_off": False,
        "suite": SUITE_ALL,
        "layer": "all",
        "live": bool(live),
        "layers": {
            "honesty": {
                "ok": honesty.get("ok"),
                "passed": honesty.get("passed"),
                "total": honesty.get("total"),
                "failed": honesty.get("failed"),
            },
            "game_v1": {
                "ok": game.get("ok"),
                "passed": game.get("passed"),
                "total": game.get("total"),
                "failed": game.get("failed"),
                "live": game.get("live"),
            },
        },
        "live_ran": list(game.get("live_ran") or []),
        "live_gated": list(game.get("live_gated") or []),
        "p0_22_hooks": dict(P0_22_HOOKS),
        "at": datetime.now(timezone.utc).isoformat(),
    }
    if persist:
        report["path"] = str(write_latest(report))
    return report


def evaluate(
    *,
    persist: bool = False,
    suite: str = SUITE_HONESTY,
    live: bool = False,
) -> dict[str, Any]:
    """Honesty by default. ``suite`` is honesty | game | all."""
    name = (suite or SUITE_HONESTY).strip().lower()
    if name not in SUITES:
        name = SUITE_HONESTY
    want_live = _live_requested(live)
    if name == SUITE_GAME:
        return run_game_suite(persist=persist, live=want_live)
    if name == SUITE_ALL:
        return run_all_suites(persist=persist, live=want_live)
    return run_suite(persist=persist)


def _print_report(report: dict[str, Any]) -> None:
    mark = "ok" if report["ok"] else "FAIL"
    suite = str(report.get("suite") or SUITE_HONESTY)
    label = "game_agent_bench" if suite == SUITE_HONESTY else "game_agent_bench %s" % suite
    extra = ""
    if suite in {SUITE_GAME, SUITE_ALL}:
        extra = "  live=%s" % str(bool(report.get("live"))).lower()
    print(
        "%s %s  %s/%s  %sms  bake_off=false%s"
        % (label, mark, report["passed"], report["total"], report["ms"], extra)
    )
    for row in report["cases"]:
        flag = "pass" if row["ok"] else "FAIL"
        print("  %s  %s  %s" % (flag, row["id"], row["detail"]))
    if report.get("path"):
        print("wrote %s" % report["path"])


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m daedalus.game_agent_bench",
        description="Run Daedalus honesty or hermetic game_v1 checks. Not a vendor bake-off.",
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
    parser.add_argument(
        "--suite",
        choices=list(SUITES),
        default=SUITE_HONESTY,
        help="honesty (default, 15 cases), game (20 hermetic tasks), or all",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Append live engine overlays (fail-closed without binaries). Also DAEDALUS_BENCH_LIVE=1.",
    )
    args = parser.parse_args(argv)
    persist = not args.no_persist
    report = evaluate(persist=persist, suite=args.suite, live=args.live)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        _print_report(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
