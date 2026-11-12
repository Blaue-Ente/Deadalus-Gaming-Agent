"""read_file reads the Playground project being built (live 2026-11-12: it resolved every path against the harness, so
the writer failed four times to re-read the game it was editing, and then wrote blind)."""
from __future__ import annotations

import asyncio

import pytest
from a0_support import a0_tmp  # noqa: F401

from daedalus.native_tools import _project_read, execute_native_tool


@pytest.fixture
def game(a0_tmp):  # noqa: F811
    from daedalus import playground as pg

    slug = asyncio.run(pg.create_project("Frog Hop", kind="static"))["slug"]
    pg.write_file(slug, "game.js", "let lives = 3;\n")
    (pg.project_dir(slug) / ".env").write_text("KEY=secret\n")
    return slug


@pytest.mark.parametrize("path, slug_arg, session", [
    ("game.js", "{s}", "studio-x"),
    ("{s}/game.js", "", "studio-x"),
    ("playground/projects/{s}/game.js", "", "studio-x"),
    ("game.js", "", "pg-{s}"),                       # project chat: the bound project
])
def test_project_paths_are_read_from_the_project(game, path, slug_arg, session):
    r = _project_read(path.format(s=game), slug_arg.format(s=game), session.format(s=game))
    assert r["ok"] and r["content"] == "let lives = 3;\n" and r["path"] == f"playground/projects/{game}/game.js"


def test_escapes_and_secrets_are_refused(game):
    assert _project_read("../../etc/passwd", game, "x")["ok"] is False
    assert _project_read(".env", game, "x")["error"].startswith("secret")
    assert _project_read("README.md", "", "studio-x") is None          # not a project path: the harness reader decides


def test_the_tool_returns_the_file(game):
    out = asyncio.run(execute_native_tool("read_file", {"path": "game.js", "slug": game}, memory=None,
                                          session_id="studio-x", read_file_fn=lambda *a: {"ok": False, "error": "x"}))
    assert out.startswith(f"── read_file playground/projects/{game}/game.js") and "let lives = 3;" in out
