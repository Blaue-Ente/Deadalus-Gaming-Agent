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
    ToolContext,
    ToolRegistry,
    default_registry,
    registry_enabled,
    reset_registry_for_tests,
)
from tests.characterization_fakes import PLAYGROUND_SID, isolate_paths

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
    assert reg.resolve("write_file") is None
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


def test_resolve_unknown_is_none():
    reset_registry_for_tests()
    assert default_registry().resolve("not_a_real_tool") is None
    assert default_registry().resolve("") is None


def test_describe_matches_live_specs():
    reset_registry_for_tests()
    by = {s.name: s for s in live_specs()}
    reg = default_registry()
    for name in READ_TOOLS:
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


def test_unregistered_stays_on_ladder(tmp_path, monkeypatch):
    isolate_paths(tmp_path, monkeypatch)
    reset_registry_for_tests()
    monkeypatch.setenv("DAEDALUS_TOOL_REGISTRY", "1")
    assert default_registry().get_exact("write_file") is None
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
