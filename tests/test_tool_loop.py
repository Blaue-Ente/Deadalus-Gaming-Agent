"""ToolLoop — native + leftover round drivers. No live LLM."""
from __future__ import annotations

import ast
import asyncio
import inspect
import sys
from pathlib import Path

import pytest

from daedalus.native_tools import run_native_discovery_pass
from daedalus.tool_loop import (
    MAX_TOOL_ITERATIONS,
    LeftoverExecResult,
    LeftoverRoundRequest,
    NativeRoundRequest,
    ToolLoop,
)

ROOT = Path(__file__).resolve().parents[1]


def test_import_tool_loop_does_not_load_agent():
    env_code = (
        "import daedalus.tool_loop as tl\n"
        "import sys\n"
        "bad = [n for n in sys.modules if n == 'daedalus.agent' or n.startswith('daedalus.agent.')]\n"
        "assert not bad, bad\n"
        "assert tl.MAX_TOOL_ITERATIONS == 30\n"
    )
    import os
    import subprocess

    env = os.environ.copy()
    env["DAEDALUS_STANDALONE"] = "1"
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env.get("PYTHONPATH", ""))
    result = subprocess.run(
        [sys.executable, "-c", env_code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_native_tools_reexports_run_native_discovery_pass():
    from daedalus import native_tools, tool_loop

    sig = inspect.signature(run_native_discovery_pass)
    assert "loop" in sig.parameters
    src = inspect.getsource(native_tools.run_native_discovery_pass)
    assert "ToolLoop().run_native" in src
    assert inspect.signature(tool_loop.ToolLoop.run_native)


def test_agent_calls_tool_loop_for_leftover():
    src = (ROOT / "daedalus" / "agent.py").read_text(encoding="utf-8")
    assert "from .tool_loop import LeftoverRoundRequest, ToolLoop" in src
    assert "ToolLoop().run_leftover" in src
    assert "async def _execute_leftover_markers" in src
    assert "if search_q:" in src
    assert '_denied = _perm_gate("web_search", "NEED_SEARCH")' in src
    assert "from .native_tools import native_tools_enabled, run_native_discovery_pass" in src
    assert "run_native_discovery_pass(" in src


def test_tool_loop_does_not_own_verification():
    src = (ROOT / "daedalus" / "tool_loop.py").read_text(encoding="utf-8")
    assert "review_gate_critique" not in src
    assert "job_contract" not in src
    assert "empty-reply" not in src
    assert "record_outcome" not in src
    agent = (ROOT / "daedalus" / "agent.py").read_text(encoding="utf-8")
    assert "review_gate_critique" in agent
    assert "empty-reply guard" in agent
    assert "from .job_contract import evaluate" in agent


def test_tool_loop_does_not_construct_build_loop():
    src = (ROOT / "daedalus" / "tool_loop.py").read_text(encoding="utf-8")
    assert "from .build_loop import BuildLoop" not in src
    assert "BuildLoop(" not in src
    agent = (ROOT / "daedalus" / "agent.py").read_text(encoding="utf-8")
    assert "from .build_loop import BuildLoop" in agent


def test_legacy_adapter_does_not_import_tool_loop():
    tree = ast.parse((ROOT / "daedalus" / "legacy_adapter.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "daedalus.tool_loop"
                assert "tool_loop" not in alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module != "daedalus.tool_loop"
            assert not node.module.endswith(".tool_loop")
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                assert alias.name != "tool_loop"
                assert alias.name != "ToolLoop"


def test_tool_loop_does_not_import_agent():
    tree = ast.parse((ROOT / "daedalus" / "tool_loop.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "daedalus.agent"
                assert not alias.name.startswith("daedalus.agent.")
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module != "daedalus.agent"
            assert not (node.module or "").startswith("daedalus.agent")


def test_breaker_hole_and_reroll_query_hole_live_in_leaf():
    src = (ROOT / "daedalus" / "tool_loop.py").read_text(encoding="utf-8")
    assert 'messages.append({"role": "user", "content": tool_block})' in src
    assert 'f"{c[\'tool\']}({c[\'query\']!r}): {c[\'result_count\']} results"' in src
    assert "итерация {_tool_iter + 1}/20" in src


def test_max_tool_iterations_constant():
    assert MAX_TOOL_ITERATIONS == 30


def test_run_native_via_toolloop_budget_stopped():
    from daedalus.build_loop import BuildLoop
    from daedalus.tool_router import route
    from tests.characterization_fakes import PLAYGROUND_SID

    async def _stopped(system, messages, tools):
        return {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "envelope blocked"}],
            "budget_stopped": True,
        }

    ask = "Направи нова Playground игра snake на HTML canvas."
    loop = BuildLoop(route(ask), ask, fix_rounds=2)
    result = asyncio.run(
        ToolLoop().run_native(
            NativeRoundRequest(
                system="sys",
                user_content=ask,
                call_with_tools_fn=_stopped,
                memory=None,
                session_id=PLAYGROUND_SID,
                read_file_fn=lambda *a, **k: {},
                max_rounds=4,
                tools=[{"name": "x"}],
                loop=loop,
            )
        )
    )
    assert loop.stop_reason == "run budget exhausted"
    assert result.text == "envelope blocked"
    assert result.tool_log == []


def test_run_native_blocked_counts_as_ok(monkeypatch):
    from daedalus import native_tools
    from tests.characterization_fakes import PLAYGROUND_SID, ScriptedTools, _text, _use

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
        run_native_discovery_pass(
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
    assert by["code_search"]["ok"] is True
    assert by["run_command"]["ok"] is False
    assert by["run_command"]["blocked"] is False


def test_leftover_quiet_raw_passes_through():
    async def _complete(*_a, **_k):
        raise AssertionError("quiet leftover must not re-call")

    async def _exec(*_a, **_k):
        raise AssertionError("quiet leftover must not execute if-arms")

    async def _keepalive(*_a, **_k):
        raise AssertionError("no keepalive")

    out = asyncio.run(
        ToolLoop().run_leftover(
            LeftoverRoundRequest(
                raw="Just a conversational reply.",
                user_content="hi",
                persona="sys",
                session_id="chat-quiet",
                user_message="hi",
                expect_json=False,
                propose_result_text="",
                complete_fn=_complete,
                execute_leftover_fn=_exec,
                keepalive=_keepalive,
                set_live=lambda *a, **k: None,
                memory=None,
                read_file_fn=lambda *a, **k: {},
            )
        )
    )
    assert out.raw == "Just a conversational reply."
    assert out.tool_calls == []
    assert out.broke_with_fallback is False
    assert out.iterations == 0


def test_leftover_executes_callback_then_rerolls():
    hits: list[str] = []

    async def _complete(system, messages, **_k):
        hits.append("complete")
        return "File looks fine."

    async def _exec(raw, hit, **_k):
        hits.append(hit.read_file_path or "")
        return LeftoverExecResult(
            parts=["━━━ TOOL RESULTS — READ FILE ━━━\nok"],
            calls=[{"tool": "read_file", "ok": True}],
        )

    class _Keep:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    def _keepalive(*_a, **_k):
        return _Keep()

    out = asyncio.run(
        ToolLoop().run_leftover(
            LeftoverRoundRequest(
                raw="<NEED_READ_FILE: tests/conftest.py>",
                user_content="please read tests/conftest.py",
                persona="sys",
                session_id="chat-read",
                user_message="please read tests/conftest.py",
                expect_json=False,
                propose_result_text="",
                complete_fn=_complete,
                execute_leftover_fn=_exec,
                keepalive=_keepalive,
                set_live=lambda *a, **k: None,
                memory=None,
                read_file_fn=lambda *a, **k: {},
            )
        )
    )
    assert hits == ["tests/conftest.py", "complete"]
    assert out.raw == "File looks fine."
    assert out.tool_calls[0]["tool"] == "read_file"
    assert out.iterations == 1


def test_leftover_reroll_fallback_keyerror_when_rows_lack_query():
    async def _complete(*_a, **_k):
        raise RuntimeError("llm down")

    async def _exec(*_a, **_k):
        return LeftoverExecResult(
            parts=["read ok"],
            calls=[{"tool": "read_file", "ok": True}],
        )

    class _Keep:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

    def _keepalive(*_a, **_k):
        return _Keep()

    with pytest.raises(KeyError, match="query"):
        asyncio.run(
            ToolLoop().run_leftover(
                LeftoverRoundRequest(
                    raw="<NEED_READ_FILE: tests/conftest.py>",
                    user_content="please read tests/conftest.py",
                    persona="sys",
                    session_id="chat-hole",
                    user_message="please read tests/conftest.py",
                    expect_json=False,
                    propose_result_text="",
                    complete_fn=_complete,
                    execute_leftover_fn=_exec,
                    keepalive=_keepalive,
                    set_live=lambda *a, **k: None,
                    memory=None,
                    read_file_fn=lambda *a, **k: {},
                )
            )
        )
