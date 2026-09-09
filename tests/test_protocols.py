"""Protocol book — every known fail has a next move."""
from __future__ import annotations

from daedalus.protocols import catalog, match


def test_canvas_trap_rewrites_index_html():
    row = match({"canvas_game_trap": True, "play_stub": True})
    assert row is not None
    assert row.id == "canvas-game-webgl-black"
    assert row.path == "index.html"
    assert row.return_to == "writer"
    assert row.next_tool == "write_file"


def test_canvas_trap_beats_generic_proof_fail():
    row = match({"canvas_game_trap": True, "proof_failed": True})
    assert row is not None
    assert row.id == "canvas-game-webgl-black"
    assert row.path == "index.html"
    row = match({"play_stub": True, "kernel_proof_ran": True})
    assert row is not None
    assert row.id == "play-js-stub"
    assert "Play.js" in row.path
    assert "ship" in row.hint.lower() or "stub" in row.hint.lower()


def test_catalog_covers_live_ember_fails():
    ids = {r["id"] for r in catalog()}
    assert "canvas-game-webgl-black" in ids
    assert "play-js-stub" in ids
    assert "vite-hello-blank" in ids
    assert "computer-play-failed" in ids


def test_always_a_next_move_or_none():
    assert match({}) is None
    assert match({"needs_npm": True}).next_tool == "run_command"
