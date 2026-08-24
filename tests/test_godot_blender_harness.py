"""Godot kind, blender job emitter, and distilled CrewAI-harness mapping."""
from __future__ import annotations

from pathlib import Path

from daedalus.blender_jobs import JOB_IDS, emit_job, render_script
from daedalus.blender_mcp import handle
from daedalus.game_platform_guide import suggest_kind_heuristic
from daedalus.godot_platform_guide import GODOT_CREW_ROLES, GODOT_PHASES, pipeline_prompt
from daedalus.godot_tools import crew_tool_catalog, glb_instance_tscn, scene_tscn, script_template, signal_connect_snippet
from daedalus.playground_godot_scaffold import godot_scaffold_files, write_godot_scaffold
from daedalus.playground_role_catalog import STUDIO_ROLES
from daedalus.playground_studio_mode import infer_studio_preset, milestones_for_preset


def test_no_crewai_dependency():
    import inspect

    import daedalus.blender_jobs as b
    import daedalus.godot_platform_guide as g

    for mod in (g, b):
        src = inspect.getsource(mod)
        assert "from crewai" not in src
        assert "import crewai" not in src


def test_godot_scaffold_is_text_and_playable(tmp_path: Path):
    written = write_godot_scaffold(tmp_path, "Shadow Veins", "shadow-veins", "2D RPG")
    assert "project.godot" in written
    assert "scenes/main.tscn" in written
    files = godot_scaffold_files("Shadow Veins", "shadow-veins")
    proj = files["project.godot"]
    assert 'config/name="Shadow Veins"' in proj
    assert "Game=" in proj
    player = files["scripts/player.gd"]
    assert "extends CharacterBody2D" in player
    assert "Game.fail" in player
    assert "import Phaser" not in player
    tscn = files["scenes/main.tscn"]
    assert "[gd_scene" in tscn
    assert "CharacterBody2D" in tscn


def test_godot_preset_and_kind():
    assert infer_studio_preset("metroidvania in godot") == "godot_2d"
    assert infer_studio_preset("mario jump runner") == "platformer"
    kind, reason = suggest_kind_heuristic("Veins", "Godot 4 GDScript dungeon")
    assert kind == "godot"
    assert "Godot" in reason
    ms = milestones_for_preset("godot_2d")
    assert ms[0]["id"] == "M0"
    assert any("player" in (m.get("goal") or "").lower() for m in ms)


def test_crew_roles_map_to_studio():
    assert len(GODOT_PHASES) == len(GODOT_CREW_ROLES) == 8
    for role in GODOT_CREW_ROLES:
        assert role in STUDIO_ROLES
    prompt = pipeline_prompt()
    assert "Daedalus roles" in prompt
    tools = crew_tool_catalog()
    ids = {t["id"] for t in tools}
    assert {
        "scene_creator",
        "script_writer",
        "project_configurator",
        "build_exporter",
        "signal_manager",
        "glb_instance",
    } <= ids
    gd = script_template(
        class_name="Enemy",
        extends="CharacterBody2D",
        body="func _ready() -> void:\n\tpass",
    )
    assert "class_name Enemy" in gd
    conn = signal_connect_snippet("pit", "body_entered", "self", "_on_pit")
    assert ".connect(" in conn
    assert "[gd_scene" in scene_tscn(script_path="res://scripts/x.gd")
    wrap = glb_instance_tscn(root_name="Crate", glb_path="res://assets/crate.glb")
    assert 'path="res://assets/crate.glb"' in wrap
    assert "PackedScene" in wrap
    assert 'instance=ExtResource("1_glb")' in wrap
    assert "Node3D" in wrap


def test_blender_jobs_render_and_emit(tmp_path: Path):
    for job in JOB_IDS:
        script = render_script(job)
        assert "import bpy" in script
        assert "{name}" not in script
        assert "{out}" not in script
        assert "{kind}" not in script
    info = emit_job(tmp_path, "export_glb", {"out": "assets/hero.glb"})
    assert info["ok"]
    path = tmp_path / info["path"]
    assert path.is_file()
    glb = path.read_text(encoding="utf-8")
    assert "export_scene.gltf" in glb
    assert "export_yup=True" in glb
    prim = render_script("create_primitive", {"kind": "torus", "name": "Ring"})
    assert "primitive_torus_add" in prim
    assert "primitive_monkey_add" in render_script("create_primitive")
    mesh = render_script("modify_mesh", {"name": "HeroMesh", "operation": "bevel"})
    assert "HeroMesh" in mesh
    assert "mesh.bevel" in mesh
    mod = render_script("add_modifier", {"modifier_type": "ARRAY", "count": 4})
    assert "ARRAY" in mod
    light = render_script("add_light", {"kind": "AREA", "name": "Fill"})
    assert "AREA" in light
    preview = render_script("render_preview")
    assert "render.render" in preview
    rig = render_script("create_rig", {"name": "HeroArmature"})
    assert "HeroArmature" in rig
    assert "Hips" in rig


def test_blender_mcp_initialize_and_call(tmp_path: Path):
    init = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init["result"]["serverInfo"]["name"] == "daedalus-blender-jobs"
    listed = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in listed["result"]["tools"]}
    assert names == set(JOB_IDS)
    called = handle({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "create_primitive",
            "arguments": {
                "name": "HeroMesh",
                "kind": "cube",
                "project_dir": str(tmp_path),
            },
        },
    })
    text = called["result"]["content"][0]["text"]
    assert "create_primitive" in text or "HeroMesh" in text
    assert (tmp_path / "tools/blender/create_primitive.py").is_file()


def test_planner_sees_godot_pipeline():
    from daedalus.playground_plan_guide import build_planner_system_prompt

    sys = build_planner_system_prompt(
        "godot",
        "kind rules",
        is_game=True,
        studio=True,
        studio_preset="godot_2d",
        description="Metroidvania in Godot",
    )
    assert "GODOT" in sys or "Godot" in sys
    assert "BLENDER JOBS" in sys or "blender" in sys.lower()
    assert ".tscn" in sys
    from daedalus.godot_platform_guide import pipeline_prompt

    qa = pipeline_prompt()
    assert "get_node()" in qa
    assert "_exit_tree" in qa


def test_godot_3d_slice_not_2d_and_not_racer(tmp_path: Path):
    from daedalus.asset_contract import check as check_contract
    from daedalus.game_factory import ANTI_BUILD, wants_godot_3d_slice
    from daedalus.godot_dod import dod_v2_static
    from daedalus.godot_runtime_probe import run_probe
    from daedalus.godot_scene_index import build as build_index
    from daedalus.playground_godot_scaffold import write_godot_scaffold

    assert wants_godot_3d_slice("Room", "Godot 3D puzzle room with one riddle")
    assert not wants_godot_3d_slice("Veins", "Godot 4 GDScript dungeon")
    assert not wants_godot_3d_slice("Mist", "F-Zero hover racer 3d")
    assert infer_studio_preset("godot 3d puzzle room") == "godot_3d"
    kind, reason = suggest_kind_heuristic("Plate", "3d puzzle room vertical slice")
    assert kind == "godot"
    assert "3D" in reason or "3d" in reason.lower()

    written = write_godot_scaffold(tmp_path, "Plate Room", "plate-room", "Godot 3D puzzle room")
    assert "scenes/main.tscn" in written
    tscn = (tmp_path / "scenes/main.tscn").read_text(encoding="utf-8")
    assert "CharacterBody3D" in tscn
    assert "Camera3D" in tscn
    assert "SpringArm3D" in tscn
    assert 'name="Plate"' in tscn
    assert 'name="Door"' in tscn
    assert "StandardMaterial3D" in tscn
    player = (tmp_path / "scripts/player.gd").read_text(encoding="utf-8")
    assert "move_and_slide" in player
    assert "CharacterBody2D" not in player
    assert (tmp_path / "studio/scene_index.json").is_file()
    assert (tmp_path / "studio/asset_contract.json").is_file()
    idx = build_index(tmp_path)
    types = set(idx["node_types"])
    assert "CharacterBody3D" in types
    assert "Camera3D" in types
    dod = dod_v2_static(tmp_path)
    assert dod["ok"]
    assert dod["is_3d"]
    probe = run_probe(tmp_path)
    assert probe["ran"] is False
    assert probe["dod"]["ok"]
    bad = check_contract({"name": "Hero Mesh", "collision": "magic", "export_target": "foo.obj"})
    assert not bad["ok"]
    good = check_contract({"name": "crate", "collision": "box", "export_target": "res://assets/crate.glb"})
    assert good["ok"]
    assert "Own engine" in ANTI_BUILD[0]
    pivot = render_script("set_pivot", {"name": "HeroMesh"})
    assert "origin_set" in pivot
    assert "daedalus_pivot" in pivot
    hint = render_script("collision_hint", {"hint": "convex"})
    assert "daedalus_collision" in hint


def test_planner_sees_game_factory_on_godot_3d():
    from daedalus.playground_plan_guide import build_planner_system_prompt

    sys = build_planner_system_prompt(
        "godot",
        "kind rules",
        is_game=True,
        studio=True,
        studio_preset="godot_3d",
        description="Godot 3D puzzle room",
    )
    assert "GAME FACTORY" in sys
    assert "Headless first" in sys
    assert "CrewAI" not in sys or "Do not vendor CrewAI" in sys or "NEVER" in sys

