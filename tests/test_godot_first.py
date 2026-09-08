"""Godot-first pipeline — contract, skip=fail, no Vite fallback. No live Godot."""
from __future__ import annotations

from pathlib import Path

from daedalus.godot_first import (
    allow_world_kit,
    coerce_kind,
    ensure_contract,
    playbook_notes,
    preflight,
    publish_artifact,
    review_gate,
    rewrite_is_blind,
    run_pipeline,
    slice_static,
    write_context,
)
from daedalus.playground_godot_scaffold import write_godot_scaffold
from daedalus.tool_catalog import canonical
from daedalus.tool_router import match_playbook, route


def test_coerce_kind_never_falls_to_vite():
    assert coerce_kind("godot", "Snake", "html canvas") == "godot"
    assert coerce_kind("static", "Veins", "Godot 4 GDScript") == "godot"
    assert coerce_kind("vite", "Room", "scenes/main.tscn") == "godot"
    assert coerce_kind("static", "Snake", "canvas arcade") == "static"
    assert coerce_kind("static", "x", "", "scripts/player.gd") == "godot"


def test_allow_world_kit_waits_for_slice():
    assert allow_world_kit("godot", "small platformer") is False
    assert allow_world_kit("godot", "small platformer", "epic") is True
    assert allow_world_kit("godot", "epic campaign world kit") is True
    assert allow_world_kit("static", "small platformer") is True


def test_preflight_missing_binary_is_fail(monkeypatch):
    monkeypatch.delenv("GODOT_BIN", raising=False)
    monkeypatch.delenv("GODOT_BINARY", raising=False)
    monkeypatch.setattr("daedalus.godot_runtime_probe.godot_bin", lambda: "")
    row = preflight(None)
    assert row["ok"] is False
    assert row["skipped"] is True
    assert "godot_missing" in row["blockers"]


def test_2d_scaffold_contract_and_slice(tmp_path: Path):
    write_godot_scaffold(tmp_path, "Veins", "veins", "Godot 4 platformer")
    assert (tmp_path / "project.godot").is_file()
    assert (tmp_path / "scripts/hud.gd").is_file()
    assert (tmp_path / "export_presets.cfg").is_file()
    ignore = (tmp_path / ".gitignore").read_text(encoding="utf-8")
    assert "export_presets.cfg" not in ignore
    preview = (tmp_path / "studio/preview/index.html").read_text(encoding="utf-8")
    assert "not Play" in preview
    contract = ensure_contract(tmp_path, slug="veins", name="Veins")
    assert contract["ok"] is True
    assert contract["html_is_not_play"] is True
    ctx = write_context(tmp_path)
    assert ctx["ok"] is True
    assert ctx["hashes"]["project.godot"]
    slice_row = slice_static(tmp_path)
    assert slice_row["ok"] is True, slice_row
    assert not slice_row["blockers"]
    ids = {c["id"] for c in slice_row["checks"]}
    assert {"hud", "win", "lose", "restart", "mechanic"} <= ids
    player = (tmp_path / "scripts/player.gd").read_text(encoding="utf-8")
    assert "Game.fail" in player
    assert "Game.win" in player


def test_review_blocks_skipped_runtime():
    row = review_gate({
        "preflight": {"ok": False, "skipped": True, "blockers": ["godot_missing"]},
        "contract": {"ok": True, "skipped": False},
        "slice": {"ok": True, "skipped": False},
        "runtime": {"ok": False, "skipped": True, "blockers": ["runtime_skipped"]},
        "visual": {"ok": False, "skipped": True, "blockers": ["visual_skipped"]},
    })
    assert row["verdict"] == "BLOCKED"
    assert row["ok"] is False
    assert any("skipped" in b for b in row["blockers"])


def test_publish_without_export_fails(tmp_path: Path):
    write_godot_scaffold(tmp_path, "Slice", "slice", "Godot 4")
    row = publish_artifact(tmp_path, slug="slice", export_row={"ok": False})
    assert row["ok"] is False
    assert "no_immutable_artifact" in row["blockers"]


def test_rewrite_is_blind(tmp_path: Path):
    target = tmp_path / "scripts" / "player.gd"
    target.parent.mkdir(parents=True)
    target.write_text("\n".join(f"var x{i} := {i}" for i in range(50)), encoding="utf-8")
    assert rewrite_is_blind(tmp_path, "scripts/player.gd", "extends Node\nfunc _ready():\n\tpass\n")
    assert not rewrite_is_blind(tmp_path, "scripts/player.gd", target.read_text(encoding="utf-8"))


def test_playbook_stays_godot_not_vite():
    pb = match_playbook("Направи Godot 4 игра: CharacterBody2D, scenes/main.tscn, GDScript.")
    assert pb.id == "playground.godot"
    assert pb.kind == "godot"
    assert "edit_file" in pb.tools
    assert "preflight" in playbook_notes()
    rt = route("Godot 4 GDScript dungeon")
    assert rt.playbook.id == "playground.godot"
    assert rt.kind == "godot"
    assert canonical("NEED_PLAYGROUND_EDIT") == "edit_file"


def test_pipeline_through_slice_without_godot(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("daedalus.godot_runtime_probe.godot_bin", lambda: "")
    monkeypatch.setattr("daedalus.playground.project_dir", lambda slug: tmp_path)
    write_godot_scaffold(tmp_path, "Veins", "veins")
    pipe = run_pipeline("veins", through="slice")
    assert pipe["kind"] == "godot"
    assert pipe["stages"]["contract"]["ok"] is True
    assert pipe["stages"]["slice"]["ok"] is True
    assert pipe["stages"]["preflight"]["ok"] is False
    assert pipe["ok"] is False


def test_create_project_locks_godot_kind(tmp_path, monkeypatch):
    import asyncio

    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    from daedalus.paths import data_root, playground_projects_dir, product_root, repo_root
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    product_root.cache_clear()
    repo_root.cache_clear()
    from daedalus.store import reset_sqlite_pool
    reset_sqlite_pool()
    from daedalus import playground as pg
    root = tmp_path / "projects"
    root.mkdir(parents=True, exist_ok=True)
    pg.PROJECTS_DIR = root

    async def _run():
        coerced = await pg.create_project(
            "Veins", kind="static", description="Godot 4 GDScript",
        )
        assert coerced["kind"] == "godot"
        pdir = pg.project_dir(coerced["slug"])
        assert (pdir / "project.godot").is_file()
        assert not (pdir / "world" / "bible.md").is_file()
        locked = await pg.create_project(
            "Dungeon", kind="godot", description="html canvas phaser",
        )
        assert locked["kind"] == "godot"
        assert (pg.project_dir(locked["slug"]) / "project.godot").is_file()

    asyncio.run(_run())
    reset_sqlite_pool()
