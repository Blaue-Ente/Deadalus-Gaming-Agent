"""ToolRegistry — P0.04 read-tool dual-path. No live LLM."""
from __future__ import annotations

import ast
import asyncio
import os
import subprocess
import sys
from pathlib import Path

from daedalus.contracts import ErrorClass, ToolRequest
from daedalus.native_tools import _HARNESS_JAIL, execute_native_tool
from daedalus.tool_adapt import live_specs
from daedalus.tool_registry import (
    READ_TOOLS,
    REGISTERED_TOOLS,
    WRITE_COMMAND_TOOLS,
    ToolContext,
    ToolRegistry,
    default_registry,
    registry_enabled,
    reset_registry_for_tests,
)
from tests.characterization_fakes import PLAYGROUND_SID, isolate_paths
from tests.test_write_gate import CLEAN_CANVAS, SNAKE_DUP

ROOT = Path(__file__).resolve().parents[1]


def _exec(name, args=None, **kwargs):
    return asyncio.run(
        execute_native_tool(
            name,
            args or {},
            memory=None,
            session_id=kwargs.pop("session_id", PLAYGROUND_SID),
            read_file_fn=kwargs.pop(
                "read_file_fn", lambda *a, **k: {"ok": False, "error": "no"},
            ),
        )
    )


def test_register_resolve_describe_read_tools():
    reset_registry_for_tests()
    reg = default_registry()
    for name in sorted(READ_TOOLS):
        hit = reg.resolve(name)
        assert hit is not None
        assert hit.name == name
        spec = reg.describe(name)
        assert spec is not None
        assert spec.name == name
        assert spec.side_effect.value == "read"
        assert spec.risk.value == "low"
        assert spec.permission is None
    assert reg.resolve("write_file") is not None
    assert reg.describe("engine") is None
    assert reg.get_exact("cat") is None


def test_resolve_aliases():
    reset_registry_for_tests()
    reg = default_registry()
    assert reg.resolve("NEED_READ_FILE").name == "read_file"
    assert reg.resolve("NEED_PLAYGROUND_READ").name == "read_file"
    assert reg.resolve("cat").name == "read_file"
    assert reg.resolve("ls").name == "list_dir"
    assert reg.resolve("NEED_CODE_SEARCH").name == "code_search"
    assert reg.resolve("NEED_PLAYGROUND_WRITE").name == "write_file"
    assert reg.resolve("playground_write").name == "write_file"
    assert reg.resolve("NEED_PLAYGROUND_EDIT").name == "edit_file"
    assert reg.resolve("shell").name == "run_command"
    assert reg.get_exact("NEED_PLAYGROUND_WRITE") is None
    assert reg.get_exact("shell") is None


def test_resolve_unknown_is_none():
    reset_registry_for_tests()
    assert default_registry().resolve("not_a_real_tool") is None
    assert default_registry().resolve("") is None


def test_describe_matches_live_specs():
    reset_registry_for_tests()
    by = {s.name: s for s in live_specs()}
    reg = default_registry()
    for name in REGISTERED_TOOLS:
        assert reg.describe(name) == by[name]


def test_flag_on_off_read_file_ok_identical(tmp_path, monkeypatch):
    isolate_paths(tmp_path, monkeypatch)
    reset_registry_for_tests()
    reader = lambda *a, **k: {  # noqa: E731
        "ok": True,
        "path": "playground/projects/slerp-lookat/index.html",
        "content": "<canvas></canvas>",
    }
    args = {"path": "index.html", "slug": "slerp-lookat"}
    monkeypatch.setenv("DAEDALUS_TOOL_REGISTRY", "1")
    on = _exec("read_file", args, read_file_fn=reader)
    monkeypatch.setenv("DAEDALUS_TOOL_REGISTRY", "0")
    off = _exec("read_file", args, read_file_fn=reader)
    assert on == off
    assert "read_file FAILED" not in on
    assert "playground/projects/slerp-lookat/" in on
    assert "NEXT: call write_file" in on


def test_flag_on_off_read_file_failed_identical(tmp_path, monkeypatch):
    isolate_paths(tmp_path, monkeypatch)
    reset_registry_for_tests()
    args = {"path": "index.html", "slug": "dungeon-keys"}
    monkeypatch.setenv("DAEDALUS_TOOL_REGISTRY", "1")
    on = _exec("read_file", args)
    monkeypatch.setenv("DAEDALUS_TOOL_REGISTRY", "0")
    off = _exec("read_file", args)
    assert on == off
    assert "read_file FAILED" in on
    assert "playground/projects/dungeon-keys/" in on
    assert "NEXT:" in on


def test_flag_on_off_list_dir_harness_refused_identical(tmp_path, monkeypatch):
    isolate_paths(tmp_path, monkeypatch)
    reset_registry_for_tests()
    args = {"path": "/workspace"}
    monkeypatch.setenv("DAEDALUS_TOOL_REGISTRY", "1")
    on = _exec("list_dir", args)
    monkeypatch.setenv("DAEDALUS_TOOL_REGISTRY", "0")
    off = _exec("list_dir", args)
    assert on == off
    assert "harness listing refused" in on
    assert "playground/projects" in on
    assert "NEXT:" in on


def test_flag_on_off_code_search_blocked_identical(tmp_path, monkeypatch):
    isolate_paths(tmp_path, monkeypatch)
    reset_registry_for_tests()
    args = {"query": "x"}
    monkeypatch.setenv("DAEDALUS_TOOL_REGISTRY", "1")
    on = _exec("code_search", args)
    monkeypatch.setenv("DAEDALUS_TOOL_REGISTRY", "0")
    off = _exec("code_search", args)
    assert on == off
    assert on == f"── code_search BLOCKED ──\n{_HARNESS_JAIL}"


def test_execute_typed_blocked_code_search(tmp_path, monkeypatch):
    isolate_paths(tmp_path, monkeypatch)
    reset_registry_for_tests()
    result = asyncio.run(
        default_registry().execute(
            ToolRequest(name="code_search", args={"query": "x"}),
            ToolContext(session_id=PLAYGROUND_SID),
        )
    )
    assert result.ok is True
    assert result.blocked is True
    assert result.error is not None
    assert result.error.error_class is ErrorClass.TOOL_BLOCKED
    assert "code_search BLOCKED" in result.text


def test_authorize_read_tools_allow(tmp_path, monkeypatch):
    isolate_paths(tmp_path, monkeypatch)
    reset_registry_for_tests()
    ctx = ToolContext(session_id=PLAYGROUND_SID)
    for name in READ_TOOLS:
        decision = default_registry().authorize(name, ctx)
        assert decision.allowed is True
        assert decision.reason == ""
        assert decision.perm_class == ""


def test_register_describe_write_command_tools():
    reset_registry_for_tests()
    reg = default_registry()
    write = reg.describe("write_file")
    edit = reg.describe("edit_file")
    run = reg.describe("run_command")
    assert write is not None and edit is not None and run is not None
    assert write.side_effect.value == "write" and write.risk.value == "high"
    assert edit.side_effect.value == "write" and edit.risk.value == "high"
    assert run.side_effect.value == "shell" and run.risk.value == "high"
    assert write.permission is None and edit.permission is None
    assert run.permission is None
    assert WRITE_COMMAND_TOOLS <= REGISTERED_TOOLS


def test_authorize_write_command_tools_allow(tmp_path, monkeypatch):
    isolate_paths(tmp_path, monkeypatch)
    reset_registry_for_tests()
    ctx = ToolContext(session_id=PLAYGROUND_SID)
    for name in WRITE_COMMAND_TOOLS:
        decision = default_registry().authorize(name, ctx)
        assert decision.allowed is True
        assert decision.reason == ""
        assert decision.perm_class == ""


def test_unregistered_stays_on_ladder(tmp_path, monkeypatch):
    isolate_paths(tmp_path, monkeypatch)
    reset_registry_for_tests()
    monkeypatch.setenv("DAEDALUS_TOOL_REGISTRY", "1")
    assert default_registry().get_exact("write_file") is not None
    assert default_registry().get_exact("engine") is None
    assert default_registry().get_exact("playground_new") is None
    unknown = _exec("not_a_real_tool", {})
    assert unknown == "── unknown native tool: not_a_real_tool ──"
    write = _exec("write_file", {})
    assert "write_file FAILED" in write
    engine = _exec("engine", {"action": "run", "slug": "not-godot"})
    assert "── engine run FAILED not-godot ──" in engine


def test_registry_enabled_flag(monkeypatch):
    monkeypatch.delenv("DAEDALUS_TOOL_REGISTRY", raising=False)
    assert registry_enabled() is True
    monkeypatch.setenv("DAEDALUS_TOOL_REGISTRY", "1")
    assert registry_enabled() is True
    monkeypatch.setenv("DAEDALUS_TOOL_REGISTRY", "0")
    assert registry_enabled() is False
    monkeypatch.setenv("DAEDALUS_TOOL_REGISTRY", "off")
    assert registry_enabled() is False


def test_native_tools_and_agent_import_rules():
    for rel, ban_registry in (
        ("daedalus/agent.py", True),
        ("daedalus/native_tools.py", False),
        ("daedalus/tool_protocol.py", True),
    ):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            modules: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules.append(node.module or "")
                names = [alias.name for alias in node.names]
            blob = " ".join(names + modules)
            assert "tool_adapt" not in blob
            if ban_registry:
                assert "tool_registry" not in blob


def test_registry_import_does_not_load_agent():
    env = os.environ.copy()
    env["DAEDALUS_STANDALONE"] = "1"
    env["PYTHONPATH"] = str(ROOT) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else ""
    )
    code = (
        "import daedalus.tool_registry\n"
        "import sys\n"
        "bad = [n for n in sys.modules if n == 'daedalus.agent' or n.startswith('daedalus.agent.')]\n"
        "assert not bad, bad\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_local_register_does_not_require_native_bootstrap():
    async def _fake(args, ctx):
        return f"── fake OK ── {args.get('q')}"

    from daedalus.tool_adapt import spec_from_native

    reg = ToolRegistry()
    spec = spec_from_native({"name": "read_file", "description": "x"})
    reg.register("read_file", _fake, spec)
    assert reg.resolve("read_file") is not None
    assert reg.resolve("list_dir") is None
    result = asyncio.run(
        reg.execute(ToolRequest(name="read_file", args={"q": "hi"}), ToolContext())
    )
    assert result.text == "── fake OK ── hi"
    assert result.ok is True


def _flag_pair(monkeypatch, name, args, **kwargs):
    reset_registry_for_tests()
    monkeypatch.setenv("DAEDALUS_TOOL_REGISTRY", "1")
    on = _exec(name, args, **kwargs)
    reset_registry_for_tests()
    monkeypatch.setenv("DAEDALUS_TOOL_REGISTRY", "0")
    off = _exec(name, args, **kwargs)
    return on, off


def _stub_static_write(monkeypatch):
    async def fake_get(slug):
        return {"slug": slug, "kind": "static"}

    monkeypatch.setattr("daedalus.playground.get_project", fake_get)
    monkeypatch.setattr("daedalus.playground.write_file", lambda *a, **k: None)
    monkeypatch.setattr("daedalus.project_status.record_built", lambda *a, **k: None)


def test_flag_on_off_write_file_missing_args_identical(tmp_path, monkeypatch):
    isolate_paths(tmp_path, monkeypatch)
    on, off = _flag_pair(monkeypatch, "write_file", {})
    assert on == off
    assert on.startswith("── write_file FAILED: slug and path required ──")
    assert "Got keys=(none)." in on
    assert "Example: write_file slug=<project> path=index.html content=…" in on


def test_flag_on_off_write_file_filename_alias_identical(tmp_path, monkeypatch):
    isolate_paths(tmp_path, monkeypatch)
    _stub_static_write(monkeypatch)
    args = {"project": "dungeon-keys", "filename": "index.html", "code": CLEAN_CANVAS}
    on, off = _flag_pair(monkeypatch, "write_file", args)
    assert on == off
    assert "write_file OK dungeon-keys/index.html" in on
    assert "FAILED" not in on
    assert "playground/projects/dungeon-keys/index.html" in on
    assert "visual proof" in on.lower()


def test_flag_on_off_write_file_gate_failed_identical(tmp_path, monkeypatch):
    isolate_paths(tmp_path, monkeypatch)
    _stub_static_write(monkeypatch)
    args = {"slug": "snake-game", "path": "index.html", "content": SNAKE_DUP}
    on, off = _flag_pair(monkeypatch, "write_file", args)
    assert on == off
    assert "write_file FAILED snake-game/index.html" in on
    assert "js-duplicate-binding" in on
    assert "Wrote playground/projects/snake-game/index.html" in on
    assert "NEXT: call write_file with slug=snake-game" in on


def test_flag_on_off_write_file_gate_off_identical(tmp_path, monkeypatch):
    isolate_paths(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_WRITE_GATE", "0")
    _stub_static_write(monkeypatch)
    args = {"slug": "snake-game", "path": "index.html", "content": SNAKE_DUP}
    on, off = _flag_pair(monkeypatch, "write_file", args)
    assert on == off
    assert "write_file OK snake-game/index.html" in on
    assert "FAILED" not in on


def test_flag_on_off_write_file_gd_autocreate_identical(tmp_path, monkeypatch):
    isolate_paths(tmp_path, monkeypatch)
    monkeypatch.setattr("daedalus.outbox.check_permission", lambda *_a, **_k: True)
    args = {
        "slug": "veins-gd",
        "path": "scripts/mob.gd",
        "content": "extends Node\nfunc _ready():\n\tpass\n",
    }
    on, off = _flag_pair(monkeypatch, "write_file", args)
    assert on == off
    assert "write_file OK veins-gd/scripts/mob.gd" in on
    from daedalus import playground as pg
    proj = asyncio.run(pg.get_project("veins-gd"))
    assert proj is not None
    assert proj.get("kind") == "godot"
    pdir = pg.project_dir("veins-gd")
    assert (pdir / "scripts" / "mob.gd").is_file()
    assert (pdir / "project.godot").is_file()


def test_flag_on_off_edit_file_missing_args_identical(tmp_path, monkeypatch):
    isolate_paths(tmp_path, monkeypatch)
    on, off = _flag_pair(monkeypatch, "edit_file", {})
    assert on == off
    assert on == (
        "── edit_file FAILED: slug and path required ──\n"
        "Example: edit_file slug=<project> path=scripts/player.gd "
        "search=… replace=…"
    )


def test_flag_on_off_edit_file_missing_project_identical(tmp_path, monkeypatch):
    isolate_paths(tmp_path, monkeypatch)
    args = {"slug": "no-such", "path": "index.html", "search": "a", "replace": "b"}
    on, off = _flag_pair(monkeypatch, "edit_file", args)
    assert on == off
    assert on == (
        "── edit_file FAILED no-such/index.html ──\n"
        "Project does not exist. Call playground_new first, or write_file for a new file."
    )


def test_flag_on_off_edit_file_search_miss_identical(tmp_path, monkeypatch):
    isolate_paths(tmp_path, monkeypatch)
    from daedalus import playground as pg

    asyncio.run(pg.create_project(
        "patch-me", kind="static", source="built",
        description="edit char", slug="patch-me",
    ))
    pg.write_file("patch-me", "index.html", "hello world")
    args = {
        "slug": "patch-me", "path": "index.html",
        "search": "missing-token", "replace": "x",
    }
    on, off = _flag_pair(monkeypatch, "edit_file", args)
    assert on == off
    assert "── edit_file FAILED patch-me/index.html ──" in on
    assert "search текстът не е намерен" in on
    assert "NEXT: read_file slug=patch-me path=index.html then edit_file" in on


def test_flag_on_off_edit_file_ok_identical(tmp_path, monkeypatch):
    isolate_paths(tmp_path, monkeypatch)
    from daedalus import playground as pg

    asyncio.run(pg.create_project(
        "patch-ok", kind="static", source="built",
        description="edit char", slug="patch-ok",
    ))
    pg.write_file("patch-ok", "index.html", "hello world")
    args = {
        "project": "patch-ok", "file": "index.html",
        "old": "hello", "new": "howdy",
    }
    reset_registry_for_tests()
    monkeypatch.setenv("DAEDALUS_TOOL_REGISTRY", "1")
    on = _exec("edit_file", args)
    pg.write_file("patch-ok", "index.html", "hello world")
    reset_registry_for_tests()
    monkeypatch.setenv("DAEDALUS_TOOL_REGISTRY", "0")
    off = _exec("edit_file", args)
    assert on == off
    assert "── edit_file OK patch-ok/index.html ──" in on
    assert "playground/projects/patch-ok/index.html" in on
    assert "Keep edit_file / write_file with slug=patch-ok." in on
    assert (pg.project_dir("patch-ok") / "index.html").read_text() == "howdy world"


def test_flag_on_off_run_command_find_blocked_identical(tmp_path, monkeypatch):
    isolate_paths(tmp_path, monkeypatch)
    on, off = _flag_pair(
        monkeypatch, "run_command", {"command": "find /workspace"},
        session_id=PLAYGROUND_SID,
    )
    assert on == off
    assert on == f"── run_command BLOCKED ──\n{_HARNESS_JAIL}"


def test_flag_on_off_run_command_ok_stub_identical(tmp_path, monkeypatch):
    isolate_paths(tmp_path, monkeypatch)

    def fake_run(cmd, *, cwd=None, slug="", timeout=None):
        return {
            "ok": True,
            "exit_code": 0,
            "cwd": "/data/playground/snake-game",
            "stdout": "HTML OK\n",
            "stderr": "",
            "ran": cmd,
        }

    monkeypatch.setattr("daedalus.shell_center.run_command", fake_run)
    args = {"command": "ls", "slug": "snake-game"}
    on, off = _flag_pair(monkeypatch, "run_command", args)
    assert on == off
    assert on.startswith("── run_command OK exit=0 cwd=/data/playground/snake-game ──")
    assert "HTML OK" in on
    assert "Keep tools on slug=snake-game." in on


def test_flag_on_off_run_command_failed_meta_identical(tmp_path, monkeypatch):
    isolate_paths(tmp_path, monkeypatch)

    def fake_run(cmd, *, cwd=None, slug="", timeout=None):
        return {
            "ok": False,
            "exit_code": 126,
            "cwd": "",
            "stdout": "",
            "stderr": "",
            "error": "shell metacharacters refused (pipes, redirection, and &&)",
            "ran": cmd,
        }

    monkeypatch.setattr("daedalus.shell_center.run_command", fake_run)
    args = {"command": "echo hi | wc", "slug": "snake-game"}
    on, off = _flag_pair(monkeypatch, "run_command", args)
    assert on == off
    assert "── run_command FAILED exit=126 cwd= ──" in on
    assert "NEXT: retry run_command without | && or 2>&1." in on
    assert 'command="npm install" slug=snake-game' in on
    assert "NEXT: fix the command (plain argv, slug=snake-game)" in on


def test_flag_on_off_run_command_peeled_hint_identical(tmp_path, monkeypatch):
    isolate_paths(tmp_path, monkeypatch)

    def fake_run(cmd, *, cwd=None, slug="", timeout=None):
        return {
            "ok": True,
            "exit_code": 0,
            "cwd": "snake-game",
            "stdout": "ok",
            "stderr": "",
            "ran": "npm install",
        }

    monkeypatch.setattr("daedalus.shell_center.run_command", fake_run)
    args = {"command": "npm install 2>&1 | tail", "slug": "snake-game"}
    on, off = _flag_pair(monkeypatch, "run_command", args)
    assert on == off
    assert "Kernel dropped pipes/&& and ran: npm install" in on
