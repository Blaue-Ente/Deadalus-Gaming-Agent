"""The game quality handbook reaches the writer (owner 2026-11-13: make the handbook useful and usable)."""
from __future__ import annotations

from daedalus import handbook


def test_every_section_but_core_and_checklist_names_its_words():
    titles = [t for t, _w, _b in handbook.sections()]
    assert titles[0] == "Core" and titles[-1] == "Checklist before done"
    assert all(words for t, words, body in handbook.sections() if body) and len(titles) >= 10


def test_the_genres_come_from_the_request_in_english_and_bulgarian():
    assert handbook.genres_for("a platformer where you jump") == ["Platformer"]
    assert handbook.genres_for("направи игра, в която колата се състезава по писта") == ["Racing"]
    assert handbook.genres_for("discard the old notes") == []          # whole words: 'card' is not in 'discard'
    assert len(handbook.genres_for("platformer shooter puzzle racing")) == handbook.MAX_GENRES


def test_the_block_keeps_core_and_the_checklist_within_the_limit():
    block = handbook.for_request("a puzzle platformer")
    assert "## Core" in block and "## Checklist before done" in block and "## Puzzle" in block
    assert len(block) <= handbook.LIMIT
    small = handbook.for_request("a puzzle platformer", limit=len(handbook.for_request("")) + 10)
    assert "## Core" in small and "## Checklist" in small and "## Puzzle" not in small


def test_the_writer_gets_the_handbook_on_a_game_build_not_on_an_app():
    from daedalus.tool_router import format_system, route

    game = format_system(route("Build a small snake game", kind="static"), user_message="Build a small snake game")
    assert "GAME QUALITY HANDBOOK" in game and "## Arcade and endless" in game
    app = format_system(route("Build a budget tracker app", kind="vite-react"), user_message="budget tracker app")
    assert "GAME QUALITY HANDBOOK" not in app


def test_a_follow_up_turn_gets_the_genre_from_the_project_bible():
    block = handbook.for_request("fix the bug please", context="A platformer: the frog jumps between lily pads.")
    assert "## Platformer" in block
    assert "## Racing" in handbook.for_request("a racing game", context="A platformer.")   # the request wins
