"""S3 — tool results steer the model: real paths, slug instead of cwd, NEXT from the plan."""
from __future__ import annotations

import asyncio

import pytest

from daedalus import native_tools, shell_center
from daedalus.build_loop import BuildLoop
from daedalus.native_tools import NATIVE_TOOL_SPECS, execute_native_tool, project_nav
from daedalus.tool_router import route

SNAKE = "Направи нова Playground игра snake: HTML canvas, стрелки. Една завършена среза."


@pytest.fixture
def playground(tmp_path, monkeypatch):
    from daedalus.paths import playground_projects_dir

    root = tmp_path / "projects"
    (root / "snake-game").mkdir(parents=True)
    (root / "snake-game" / "index.html").write_text("<canvas></canvas>", encoding="utf-8")
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(root))
    playground_projects_dir.cache_clear()
    yield root
    playground_projects_dir.cache_clear()


def _exec(name, args, session_id="playground:x"):
    return asyncio.run(
        execute_native_tool(name, args, memory=None, session_id=session_id, read_file_fn=lambda *a: {"ok": False, "error": "nope"})
    )


def test_relative_cwd_named_after_project_lands_in_the_project(playground):
    proj = (playground / "snake-game").resolve()
    assert shell_center.resolve_cwd("snake-game") == proj
    assert shell_center.resolve_cwd("/workspace/snake-game") == proj
    assert shell_center.resolve_cwd("snake-game/assets") == proj  # sub-dir missing → project root
    assert shell_center.resolve_cwd("no-such-project") == shell_center.product_root()


def test_project_nav_names_dir_preview_and_slug_tools(playground):
    nav = project_nav("snake-game", "game.js")
    assert str((playground / "snake-game").resolve()) in nav
    assert "/api/v1/playground/projects/snake-game/preview/game.js" in nav
    assert "slug=snake-game" in nav and "not cwd" in nav


def test_read_and_list_take_slug(playground):
    out = _exec("read_file", {"slug": "snake-game", "path": "index.html"})
    assert "read_file snake-game/index.html" in out and "<canvas>" in out
    missing = _exec("read_file", {"slug": "snake-game", "path": "game.js"})
    assert "FAILED" in missing and "slug=snake-game" in missing
    listing = _exec("list_dir", {"slug": "snake-game"})
    assert "index.html" in listing
    assert any(p["name"] == "read_file" and "slug" in p["input_schema"]["properties"] for p in NATIVE_TOOL_SPECS)
    assert any(p["name"] == "list_dir" and "slug" in p["input_schema"]["properties"] for p in NATIVE_TOOL_SPECS)


def test_harness_listing_in_playground_session_points_to_slug(playground):
    out = _exec("list_dir", {"path": "/workspace"})
    assert "harness listing refused" in out
    assert "slug=<project>" in out
    blocked = _exec("run_command", {"command": "find / -name x", "slug": "snake-game"})
    assert "BLOCKED" in blocked and "Your project:" in blocked


def test_failed_run_command_with_cwd_hints_slug(monkeypatch):
    monkeypatch.setattr(
        shell_center, "run_command",
        lambda cmd, cwd=None, slug="", timeout=None: {"ok": False, "exit_code": 1, "cwd": cwd, "stdout": "", "stderr": "boom"},
    )
    out = _exec("run_command", {"command": "ls", "cwd": "snake-game"}, session_id="s")
    assert "FAILED" in out and "slug=<project> instead of cwd" in out
    out2 = _exec("run_command", {"command": "ls", "slug": "snake-game"}, session_id="s")
    assert "instead of cwd" not in out2


def test_loop_prepare_fills_slug_and_drops_cwd():
    loop = BuildLoop(route(SNAKE), SNAKE, fix_rounds=1)
    args, note = loop.prepare("run_command", {"command": "ls", "cwd": "snake-game"})
    assert note == "" and "slug" not in args  # no slug known yet

    loop.observe("playground_new", {"name": "snake-game"}, "── playground_new OK slug=snake-game kind=static ──", ok=True)
    args, note = loop.prepare("run_command", {"command": "ls", "cwd": "snake-game"})
    assert args == {"command": "ls", "slug": "snake-game"} and "slug=snake-game" in note
    args, note = loop.prepare("run_command", {"command": "ls", "cwd": "/workspace/snake-game/assets"})
    assert args["slug"] == "snake-game" and "cwd" not in args
    args, note = loop.prepare("run_command", {"command": "ls", "cwd": "daedalus"})
    assert args == {"command": "ls", "cwd": "daedalus"} and note == ""
    args, note = loop.prepare("write_file", {"path": "game.js", "content": "x"})
    assert args["slug"] == "snake-game"
    args, note = loop.prepare("write_file", {"slug": "other", "path": "a"})
    assert args["slug"] == "other" and note == ""
    args, note = loop.prepare("math3d", {"op": "quat_slerp"})
    assert args == {"op": "quat_slerp"} and note == ""


def test_loop_annotate_points_next_then_stop():
    loop = BuildLoop(route(SNAKE), SNAKE, fix_rounds=1)
    loop.observe("playground_new", {"name": "snake-game"}, "── playground_new OK slug=snake-game ──", ok=True)
    text = loop.annotate("playground_new", "── playground_new OK slug=snake-game ──")
    assert "NEXT (plan): write_file slug=snake-game" in text
    loop.observe("write_file", {"slug": "snake-game", "path": "index.html"}, "── write_file OK ──", ok=True)
    text = loop.annotate("write_file", "── write_file OK ──")
    assert "PLAN: all required steps done" in text
    assert loop.annotate("write_file", text) == text  # idempotent


def test_native_pass_applies_prepare_and_annotate(monkeypatch):
    received: list[tuple[str, dict]] = []

    async def _fake_exec(name, args, **_kw):
        received.append((name, dict(args)))
        if name == "playground_new":
            return "── playground_new OK slug=snake-game kind=static ──"
        return f"── {name} OK ──"

    monkeypatch.setattr(native_tools, "execute_native_tool", _fake_exec)
    rounds = [
        [{"type": "tool_use", "id": "1", "name": "playground_new", "input": {"name": "snake-game"}}],
        [{"type": "tool_use", "id": "2", "name": "run_command", "input": {"command": "ls", "cwd": "snake-game"}},
         {"type": "tool_use", "id": "3", "name": "write_file", "input": {"path": "index.html", "content": "<canvas>"}}],
        [{"type": "text", "text": "Готово."}],
    ]
    sent: list[list[dict]] = []

    async def _model(system, messages, tools):
        sent.append(list(messages))
        return {"stop_reason": "x", "content": rounds.pop(0) if rounds else [{"type": "text", "text": "Готово."}]}

    loop = BuildLoop(route(SNAKE), SNAKE, fix_rounds=2)
    text, log = asyncio.run(native_tools.run_native_discovery_pass(
        "sys", SNAKE, _model, memory=None, session_id="", read_file_fn=lambda *a: {}, max_rounds=4, loop=loop,
    ))
    assert received[1] == ("run_command", {"command": "ls", "slug": "snake-game"})
    assert received[2][1]["slug"] == "snake-game"
    assert log[1]["input"] == {"command": "ls", "slug": "snake-game"}
    assert "NEXT (plan): write_file slug=snake-game" in text
    assert "PLAN: all required steps done" in text
    assert "(kernel: slug=snake-game filled in)" in text
    assert loop.stop_reason == "complete" and loop.nudges == 0
