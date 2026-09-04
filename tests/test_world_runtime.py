"""F3 — world kit runtime: save, quests, dialogue, gated graph."""
from __future__ import annotations

import json
from pathlib import Path

from daedalus.game_studio_gates import run_gates, run_world_check
from daedalus.native_tools import NATIVE_TOOL_SPECS
from daedalus.subagents import tools_for_role
from daedalus.world_kit import status, write
from daedalus.world_runtime import (
    can_travel,
    empty_save,
    load_kit,
    migrate_save,
    prove_project,
    run_script,
    validate_kit,
    default_hearth_script,
)


def test_default_kit_proves_hearth_to_wilds(tmp_path: Path):
    written = write(tmp_path, name="Сага", slug="saga", description="епос")
    assert "world/quests.json" in written
    assert "characters/keeper.dialogue.json" in written
    assert "src/world/runtime.js" in written
    js = (tmp_path / "src" / "world" / "runtime.js").read_text(encoding="utf-8")
    assert "export function canTravel" in js
    assert "export function applyChoice" in js
    st = status(tmp_path)
    assert st["ready"] is True
    assert st["runtime"] is True
    proof = prove_project(tmp_path)
    assert proof["ok"] is True, proof.get("errors")
    assert proof["flags"].get("gate_open") is True
    kit, err = load_kit(tmp_path)
    assert err == ""
    sim = run_script(kit, default_hearth_script())
    assert sim["ok"] is True, sim.get("errors")
    assert sim["save"]["region"] == "wilds"
    assert sim["save"]["flags"].get("gate_open") is True
    again = write(tmp_path, name="Сага", slug="saga", description="епос")
    assert again == []


def test_hollow_map_is_not_a_runtime(tmp_path: Path):
    (tmp_path / "world").mkdir()
    (tmp_path / "levels").mkdir()
    (tmp_path / "world" / "map.json").write_text(
        '{"regions":[{"id":"hearth","name":"H","exits":["wilds"]}],"start":"hearth"}\n',
        encoding="utf-8",
    )
    (tmp_path / "levels" / "graph.json").write_text(
        '{"acts":[{"id":"act-1","levels":["hearth"]}],"edges":[]}\n',
        encoding="utf-8",
    )
    proof = prove_project(tmp_path)
    assert proof["ok"] is False
    blob = " ".join(proof.get("errors") or [])
    assert "wilds" in blob or "quests" in blob or "dialogue" in blob


def test_migrate_save_v0():
    doc = migrate_save({"region": "hearth", "flags": {"old": 1}})
    assert doc["version"] == 1
    assert doc["region"] == "hearth"
    assert doc["flags"]["old"] is True
    assert doc["inventory"] == []
    assert empty_save()["quests"] == {}


def test_travel_blocked_until_flag(tmp_path: Path):
    write(tmp_path, name="Gate", slug="gate", description="кампания")
    kit, _ = load_kit(tmp_path)
    save = empty_save(region="hearth")
    ok, detail = can_travel(kit, save, "wilds")
    assert ok is False
    assert "flag" in detail
    save["flags"]["gate_open"] = True
    ok, _ = can_travel(kit, save, "wilds")
    assert ok is True


def test_dialogue_without_flags_fails_validate(tmp_path: Path):
    write(tmp_path, name="Gate", slug="gate", description="кампания")
    path = tmp_path / "characters" / "keeper.dialogue.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    for node in raw["nodes"]:
        for ch in node.get("choices") or []:
            ch.pop("set_flags", None)
    path.write_text(json.dumps(raw), encoding="utf-8")
    kit, err = load_kit(tmp_path)
    assert err == ""
    issues = validate_kit(kit)
    assert any("never set a flag" in e for e in issues)


def test_world_check_pre_build_does_not_block():
    ctx = {
        "gdd": "GDD " + ("x" * 80),
        "config": {
            "studio_preset": "racing",
            "kind": "vite",
            "brainstorm": {"genre": "racing", "idea": "F-Zero night city"},
            "build_mode": "guided",
        },
        "plan": {"stages": [{"title": "boot"}] * 3},
        "project": {"kind": "vite", "slug": "mist"},
    }
    row = run_world_check(ctx)
    assert row["passed"] is True
    assert row["blocking"] is False
    combined = run_gates(ctx)
    assert combined["blocking"] is False
    assert combined["gates"]["world-check"]["passed"] is True


def test_world_check_hollow_on_disk_fails(tmp_path: Path):
    (tmp_path / "world").mkdir()
    (tmp_path / "index.html").write_text("<canvas></canvas>", encoding="utf-8")
    (tmp_path / "world" / "map.json").write_text(
        '{"regions":[{"id":"hearth","exits":["nowhere"]}],"start":"hearth"}',
        encoding="utf-8",
    )
    ctx = {
        "workspace": str(tmp_path),
        "config": {"kind": "static", "studio_preset": "action_rpg", "build_mode": "guided"},
        "project": {"kind": "static", "slug": "ghost"},
        "gdd": "кампания с персонажи",
    }
    row = run_world_check(ctx)
    assert row["blocking"] is True


def test_world_check_living_kit_passes(tmp_path: Path):
    write(tmp_path, name="Mist", slug="mist", description="необятен свят")
    ctx = {
        "workspace": str(tmp_path),
        "config": {"kind": "static", "build_mode": "guided"},
        "project": {"kind": "static", "slug": "mist"},
        "gdd": "",
    }
    row = run_world_check(ctx)
    assert row["passed"] is True, row
    assert row["blocking"] is False


def test_world_native_tool_and_verify_role():
    spec = next(t for t in NATIVE_TOOL_SPECS if t["name"] == "world")
    assert "prove" in spec["description"]
    assert "world" in tools_for_role("verify")
    assert "world" in tools_for_role("explorer")
    assert "world" in tools_for_role("coder")


def test_godot_kind_writes_gdscript_helper(tmp_path: Path):
    write(tmp_path, name="Room", slug="room", description="епос", kind="godot")
    assert (tmp_path / "scripts" / "world_runtime.gd").is_file()
    write(tmp_path / "web", name="Web", slug="web", description="епос", kind="static")
    assert not (tmp_path / "web" / "scripts" / "world_runtime.gd").exists()
    assert (tmp_path / "web" / "src" / "world" / "runtime.js").is_file()
