"""Arcade craft doctrine, FM1 field study, and studio shelf routing."""
from __future__ import annotations

import json

from daedalus.arcade_craft import (
    FM1_STACK,
    dead_end_advice,
    fun_signals,
    gdd_section,
    looks_like_campaign_bible,
    looks_like_racer,
    min_stages,
    planner_addendum,
    scope_warnings,
    wants_high_fidelity,
    wants_racer_scaffold,
)
from daedalus.game_platform_guide import suggest_kind_heuristic
from daedalus.game_studio import generate_gdd_lite, process_brainstorm
from daedalus.game_studio_gates import run_craft_check, run_gates
from daedalus.playground_studio_mode import infer_studio_preset, milestones_for_preset
from daedalus.studio_shelf import SHELF, prompt_block, tools_for


def test_fm1_stack_is_webgpu_not_phaser():
    blob = " ".join(FM1_STACK.values()).lower()
    assert "webgpu" in blob
    assert "tsl" in blob
    assert "phaser" not in blob
    assert "vite" in blob


def test_detects_racer_and_fidelity():
    brief = "F-Zero style hover racer in the browser like Wipeout"
    assert looks_like_racer(brief)
    assert wants_high_fidelity(brief)
    assert min_stages("vite", is_game=True, description=brief) >= 12
    add = planner_addendum("vite", brief)
    assert "WebGPU" in add
    assert "pixel" in add.lower() or "Tripo" in add


def test_fun_signals_conservative_on_snake():
    snake = "<canvas></canvas> keydown requestAnimationFrame score gameover"
    fun = fun_signals(snake)
    assert fun["signals"]["racer"] is False
    assert fun["blockers"] == []


def test_fun_signals_flags_gas_only_racer():
    snap = "racing turbo lap accelerate track vehicle"
    fun = fun_signals(snap)
    assert fun["signals"]["racer"] is True
    assert fun["blockers"]
    assert fun["ship_hint"] is False


def test_brainstorm_racing_preset_and_gdd():
    out = process_brainstorm({
        "title": "Mist Run",
        "genre": "racing",
        "idea": "F-Zero hover race in a night city",
        "reference_games": "Wipeout, Formula Minus One",
    })
    assert out["suggested"]["studio_preset"] == "racing"
    assert out["suggested"]["kind"] == "vite"
    gdd = generate_gdd_lite(out["brainstorm"], studio_preset="racing", kind="vite")
    assert "Arcade craft" in gdd
    assert "fm1.moises.cloud" in gdd
    assert "Studio shelf" in gdd
    assert "Tripo" in gdd or "three" in gdd.lower()


def test_infer_racing_before_platformer_runner():
    assert infer_studio_preset("futuristic racer f-zero circuit") == "racing"
    assert infer_studio_preset("mario side-scroll jump") == "platformer"
    ms = milestones_for_preset("racing")
    assert ms[0]["id"] == "M0"
    assert "ship" in ms[0]["goal"].lower() or "segment" in ms[0]["goal"].lower()


def test_kind_heuristic_three_not_static():
    kind, reason = suggest_kind_heuristic("FM1", "Wipeout 3D hover racing webgpu")
    assert kind == "vite"
    assert "Three" in reason or "3D" in reason


def test_shelf_routes_3d_not_everything():
    rows = tools_for(query="f-zero 3d ship mesh")
    ids = {r["id"] for r in rows}
    assert "tripo3d" in ids or "hunyuan3d-fal" in ids
    assert "three-webgpu-tsl" in ids
    assert tools_for(query="") == []
    block = prompt_block("puzzle word game")
    assert "STUDIO SHELF" in block


def test_craft_check_warns_but_does_not_block():
    ctx = {
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
    row = run_craft_check(ctx)
    assert row["gate"] == "craft-check"
    assert row["passed"] is True
    assert row["blocking"] is False
    combined = run_gates(ctx)
    assert "craft-check" in combined["gates"]


def test_campaign_bible_is_a_warning_not_a_template():
    veins = (
        "3D Action RPG inspired by Dark Souls and Dishonored. "
        "5 biomes, 12 chapters, 30+ side quests, PlayStation 5 and Xbox Series X, "
        "FMOD, dedicated servers."
    )
    assert looks_like_campaign_bible(veins)
    assert not looks_like_campaign_bible("simple 2D jump with one pit")
    assert not looks_like_campaign_bible("dark souls vibe pixel dungeon")
    warns = scope_warnings({"genre": "rpg", "idea": veins})
    assert any("Campaign-bible" in w or "campaign bible" in w.lower() for w in warns)
    gdd = gdd_section({"genre": "rpg", "idea": veins})
    assert "Campaign bible" in gdd
    ctx = {
        "gdd": veins,
        "config": {
            "studio_preset": "action_rpg",
            "kind": "godot",
            "brainstorm": {"genre": "rpg", "idea": veins},
            "build_mode": "guided",
        },
        "plan": {"stages": [{"title": "world", "goal": "five biomes"}]},
        "project": {"kind": "godot", "slug": "veins"},
    }
    row = run_craft_check(ctx)
    assert row["passed"] is True
    assert row["blocking"] is False
    detail = " ".join(str(c.get("detail", "")) for c in row.get("checks") or [])
    blob = json.dumps(row)
    assert "campaign bible" in (detail + blob).lower() or "Campaign bible" in blob


def test_gdd_section_mentions_vertical_slice():
    md = gdd_section({"genre": "racing", "idea": "hover ships"})
    assert "ONE ship" in md or "one ship" in md.lower() or "vertical" in md.lower()
    assert "cul-de-sac" in dead_end_advice().lower() or "Cul-de-sac" in dead_end_advice()


def test_planner_prompt_sees_fzero_brief():
    from daedalus.playground_plan_guide import build_planner_system_prompt
    sys = build_planner_system_prompt(
        "vite",
        "kind rules",
        is_game=True,
        studio=True,
        studio_preset="custom",
        description="F-Zero hover racer like Formula Minus One",
    )
    assert "WebGPU" in sys
    assert "STUDIO SHELF" in sys
    assert "tripo" in sys.lower() or "Tripo" in sys


def test_skill_select_arcade_craft():
    from daedalus.improvements import _SKILLS_CACHE, select_skills
    from daedalus import improvements as imp
    imp._SKILLS_LOADED = False
    _SKILLS_CACHE.clear()
    names = select_skills("Направи F-Zero игра в браузъра", "build")
    assert "arcade_craft" in names


def test_racer_scaffold_not_for_todo_tracker(tmp_path):
    from daedalus.playground import _write_scaffold
    from daedalus.playground_racer_scaffold import write_racer_scaffold

    assert wants_racer_scaffold("Mist Run", "F-Zero hover racer")
    assert wants_racer_scaffold("Cup", "genre: racing night city")
    assert not wants_racer_scaffold("Todo", "track your tasks")

    written = write_racer_scaffold(tmp_path / "mist", "Mist Run", "mist-run", "F-Zero")
    assert "src/racer/vehicle.js" in written
    pkg = json.loads((tmp_path / "mist" / "package.json").read_text(encoding="utf-8"))
    assert "three" in pkg["dependencies"]
    assert "phaser" not in pkg.get("dependencies", {})
    blob = "\n".join(p.read_text(encoding="utf-8") for p in (tmp_path / "mist").rglob("*.js"))
    assert not blob.lstrip().startswith("\\")
    for js in (tmp_path / "mist").rglob("*.js"):
        text = js.read_text(encoding="utf-8")
        assert not text.startswith("\\"), js.name
    assert "from 'phaser'" not in blob.lower()
    assert "phaser.game" not in blob.lower()
    assert "CatmullRomCurve3" in blob
    assert "integrity" in blob
    assert "chevron" in blob
    fun = fun_signals(blob)
    assert fun["signals"]["racer"] is True
    assert fun["blockers"] == []

    _write_scaffold(tmp_path / "fzero", "vite", "Mist", "mist", "F-Zero hover racer")
    main = (tmp_path / "fzero" / "src" / "main.js").read_text(encoding="utf-8")
    assert "Vanilla JS работи" not in main
    assert (tmp_path / "fzero" / "src" / "racer" / "track.js").is_file()

    _write_scaffold(tmp_path / "todo", "vite", "Todo", "todo", "track your tasks")
    assert not (tmp_path / "todo" / "src" / "racer" / "track.js").exists()
    assert "Vanilla JS работи" in (tmp_path / "todo" / "src" / "main.js").read_text(encoding="utf-8")


def test_plan_defaults_racer_files():
    from daedalus.playground_plan_guide import _default_files_for_kind
    files = _default_files_for_kind("vite", 2, "Track reading chevrons")
    assert any("racer" in f for f in files)
