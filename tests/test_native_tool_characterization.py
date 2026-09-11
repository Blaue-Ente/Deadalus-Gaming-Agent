"""Characterization of execute_native_tool / Godot native path — current behavior."""
from __future__ import annotations

import asyncio

import pytest

from daedalus import native_tools
from daedalus.build_loop import BuildLoop
from daedalus.tool_router import route
from tests.characterization_fakes import (
    PLAYGROUND_SID,
    ScriptedTools,
    _text,
    _use,
    isolate_paths,
)


@pytest.fixture
def char_env(tmp_path, monkeypatch):
    isolate_paths(tmp_path, monkeypatch)
    yield tmp_path


def _exec(name, args=None, **kwargs):
    return asyncio.run(
        native_tools.execute_native_tool(
            name,
            args or {},
            memory=None,
            session_id=kwargs.pop("session_id", PLAYGROUND_SID),
            read_file_fn=kwargs.pop("read_file_fn", lambda *a, **k: {"ok": False, "error": "no"}),
            **kwargs,
        )
    )


def test_unknown_tool_message(char_env):
    text = _exec("not_a_real_tool", {})
    assert text == "── unknown native tool: not_a_real_tool ──"


def test_tool_exception_becomes_error_line(char_env):
    def _boom(*_a, **_k):
        raise RuntimeError("disk exploded")

    text = _exec("read_file", {"path": "x.py"}, read_file_fn=_boom)
    assert "── read_file ERROR:" in text
    assert "disk exploded" in text


def test_permission_denied_returns_denial_without_running(char_env, monkeypatch):
    ran = []

    async def _ws(*_a, **_k):
        ran.append(True)
        return {"results": []}

    monkeypatch.setattr("daedalus.outbox.check_permission", lambda *_a, **_k: False)
    monkeypatch.setattr("daedalus.tools.web_search_tool", _ws)
    text = _exec("web_search", {"query": "x"})
    assert "PERMISSION DENIED" in text
    assert "web_search" in text
    assert ran == []


def test_permission_check_fail_open(char_env, monkeypatch):
    ran = []

    async def _ws(*_a, **_k):
        ran.append(True)
        return {"results": [{"title": "t", "url": "http://x", "snippet": "s"}]}

    def _raise(_perm):
        raise RuntimeError("perms db down")

    monkeypatch.setattr("daedalus.outbox.check_permission", _raise)
    monkeypatch.setattr("daedalus.tools.web_search_tool", _ws)
    text = _exec("web_search", {"query": "x"})
    assert ran == [True]
    assert "PERMISSION DENIED" not in text
    assert "web_search" in text


def test_offline_denies_web_search(char_env, monkeypatch):
    ran = []

    async def _ws(*_a, **_k):
        ran.append(True)
        return {"results": []}

    monkeypatch.setenv("DAEDALUS_OFFLINE", "1")
    monkeypatch.setattr("daedalus.tools.web_search_tool", _ws)
    text = _exec("web_search", {"query": "x"})
    assert "OFFLINE KIT" in text
    assert "web_search" in text
    assert ran == []


def test_failed_vs_blocked_ok_classification(char_env, monkeypatch):
    async def _exec_stub(name, args, **_k):
        if name == "code_search":
            return "── code_search BLOCKED ──\nPLAYGROUND JAIL"
        if name == "run_command":
            return "── run_command FAILED exit=1 ──"
        return f"── {name} OK ──"

    model = ScriptedTools(
        [
            [_use("code_search", query="x"), _use("run_command", command="boom")],
            [_text("done")],
        ]
    )
    monkeypatch.setattr(native_tools, "execute_native_tool", _exec_stub)
    _text_out, log = asyncio.run(
        native_tools.run_native_discovery_pass(
            "sys",
            "x",
            model,
            memory=None,
            session_id=PLAYGROUND_SID,
            read_file_fn=lambda *a, **k: {},
            max_rounds=3,
            tools=[{"name": "x"}],
            loop=None,
        )
    )
    by = {row["tool"]: row for row in log}
    assert by["code_search"]["blocked"] is True
    assert by["code_search"]["ok"] is True  # current heuristic: BLOCKED counts as ok
    assert by["run_command"]["ok"] is False
    assert by["run_command"]["blocked"] is False


def test_playground_new_godot_words_coerce_kind(char_env, monkeypatch):
    monkeypatch.setattr("daedalus.outbox.check_permission", lambda *_a, **_k: True)
    text = _exec(
        "playground_new",
        {"name": "Veins", "kind": "static", "description": "Godot 4 GDScript"},
    )
    assert "playground_new OK" in text
    assert "kind=godot" in text


def test_write_file_gd_path_autocreates_godot_project(char_env, monkeypatch):
    monkeypatch.setattr("daedalus.outbox.check_permission", lambda *_a, **_k: True)
    from daedalus import playground as pg

    text = _exec(
        "write_file",
        {
            "slug": "veins-gd",
            "path": "scripts/mob.gd",
            "content": "extends Node\nfunc _ready():\n\tpass\n",
        },
    )
    assert "write_file OK veins-gd/scripts/mob.gd" in text
    proj = asyncio.run(pg.get_project("veins-gd"))
    assert proj is not None
    assert proj.get("kind") == "godot"
    pdir = pg.project_dir("veins-gd")
    assert (pdir / "scripts" / "mob.gd").is_file()
    assert (pdir / "project.godot").is_file()


def test_engine_missing_project_failed_via_execute_native_tool(char_env):
    text = _exec("engine", {"action": "run", "slug": "not-godot"})
    assert "── engine run FAILED not-godot ──" in text
    assert "FAILED" in text


def test_godot_route_scripted_native_pass_uses_engine_subset(char_env, monkeypatch):
    ask = "Направи Godot 4 GDScript игра: една сцена и player.gd"
    rt = route(ask)
    assert rt.playbook.id == "playground.godot"
    assert rt.kind == "godot"
    assert "engine" in rt.tool_names
    assert "edit_file" in rt.tool_names

    async def _exec_stub(name, args, **_k):
        if name == "playground_new":
            return "── playground_new OK slug=veins kind=godot ──"
        if name == "engine":
            return "── engine pipeline FAILED veins ──\n{\"ok\": false}"
        return f"── {name} OK ──"

    monkeypatch.setattr(native_tools, "execute_native_tool", _exec_stub)
    monkeypatch.setenv("DAEDALUS_MENTOR", "0")
    loop = BuildLoop(rt, ask, fix_rounds=1)
    model = ScriptedTools(
        [
            [_use("playground_new", name="veins", kind="godot")],
            [_use("engine", action="pipeline", slug="veins")],
            [_text("stopped")],
        ]
    )
    _out, log = asyncio.run(
        native_tools.run_native_discovery_pass(
            "sys",
            ask,
            model,
            memory=None,
            session_id=PLAYGROUND_SID,
            read_file_fn=lambda *a, **k: {},
            max_rounds=4,
            tools=[{"name": t} for t in rt.tool_names],
            loop=loop,
        )
    )
    tools = [row.get("tool") for row in log]
    assert "playground_new" in tools
    assert "engine" in tools
    engine_row = next(r for r in log if r.get("tool") == "engine")
    assert engine_row["ok"] is False
