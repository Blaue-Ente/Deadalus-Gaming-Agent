"""Characterization of tdd_loop / NEED_TDD — current behavior."""
from __future__ import annotations

import asyncio

import pytest

from daedalus.tdd_loop import TDDResult, parse_tdd_marker, tdd_loop
from tests.characterization_fakes import (
    PLAYGROUND_SID,
    ScriptedClaude,
    bare_daedalus,
    isolate_paths,
    stub_navigate_build,
    stub_native_pass,
    stub_tools_quiet,
)


@pytest.fixture
def char_env(tmp_path, monkeypatch):
    isolate_paths(tmp_path, monkeypatch)
    monkeypatch.setattr("daedalus.navigator.navigate", stub_navigate_build)
    monkeypatch.setattr("daedalus.agent.run_native_discovery_pass", stub_native_pass)
    monkeypatch.setattr("daedalus.agent._call_claude_with_tools", stub_tools_quiet)
    yield tmp_path


def test_parse_tdd_marker_three_fields():
    raw = (
        "<NEED_TDD: tests/foo.py | assert add(1, 1) == 2 | def add(a, b): return 0>"
    )
    got = parse_tdd_marker(raw)
    assert got == {
        "file_path": "tests/foo.py",
        "test_code": "assert add(1, 1) == 2",
        "initial_code": "def add(a, b): return 0",
    }


def test_parse_tdd_marker_missing_returns_none():
    assert parse_tdd_marker("") is None
    assert parse_tdd_marker("no marker here") is None
    assert parse_tdd_marker("<NEED_TDD: x.py | short>") is None


def test_tdd_loop_pass_first_iter_no_propose_when_code_unchanged(char_env, monkeypatch):
    proposed = []

    def _propose(**kwargs):
        proposed.append(kwargs)
        return {"ok": True, "proposal_id": "prop_should_not"}

    monkeypatch.setattr("daedalus.self_improve.propose_patch_validated", _propose)
    result = asyncio.run(
        tdd_loop(
            file_path="tests/char_add.py",
            test_code="assert x == 1\n",
            initial_code="x = 1\n",
            auto_propose=True,
        )
    )
    assert result.ok is True
    assert result.proposal_id == ""
    assert proposed == []
    assert result.iterations_used == 1


def test_tdd_loop_pass_after_fix_calls_propose(char_env, monkeypatch):
    proposed = []

    def _propose(**kwargs):
        proposed.append(kwargs)
        return {"ok": True, "proposal_id": "prop_tdd_ok"}

    async def _fix(*_a, **_k):
        return "x = 1\n"

    monkeypatch.setattr("daedalus.self_improve.propose_patch_validated", _propose)
    monkeypatch.setattr("daedalus.tdd_loop._llm_fix", _fix)
    result = asyncio.run(
        tdd_loop(
            file_path="tests/char_add.py",
            test_code="assert x == 1\n",
            initial_code="x = 0\n",
            auto_propose=True,
            max_iterations=3,
        )
    )
    assert result.ok is True
    assert result.proposal_id == "prop_tdd_ok"
    assert proposed
    assert proposed[0]["file_path"] == "tests/char_add.py"
    assert result.iterations_used >= 2


def test_tdd_loop_unfixable_short_circuits(char_env, monkeypatch):
    async def _fix(*_a, **_k):
        return "# TDD_UNFIXABLE: cannot satisfy assertion\nx = 0\n"

    monkeypatch.setattr(
        "daedalus.self_improve.propose_patch_validated",
        lambda **k: (_ for _ in ()).throw(AssertionError("should not propose")),
    )
    monkeypatch.setattr("daedalus.tdd_loop._llm_fix", _fix)
    result = asyncio.run(
        tdd_loop(
            file_path="tests/char_add.py",
            test_code="assert x == 1\n",
            initial_code="x = 0\n",
            max_iterations=4,
        )
    )
    assert result.ok is False
    assert result.unfixable is True
    assert "cannot satisfy" in result.error


def test_tdd_loop_max_iter_failure_shape(char_env, monkeypatch):
    async def _fix(*_a, **_k):
        return "x = 2\n"

    monkeypatch.setattr("daedalus.tdd_loop._llm_fix", _fix)
    result = asyncio.run(
        tdd_loop(
            file_path="tests/char_add.py",
            test_code="assert x == 1\n",
            initial_code="x = 0\n",
            max_iterations=2,
            auto_propose=False,
        )
    )
    assert result.ok is False
    assert result.unfixable is False
    assert result.iterations_used == 2
    assert "Max 2 iterations" in result.error


def test_tdd_loop_llm_fix_error_keeps_current_code(char_env, monkeypatch):
    async def _fix(*_a, **_k):
        raise RuntimeError("coder slot down")

    monkeypatch.setattr("daedalus.tdd_loop._llm_fix", _fix)
    result = asyncio.run(
        tdd_loop(
            file_path="tests/char_add.py",
            test_code="assert x == 1\n",
            initial_code="x = 0\n",
            max_iterations=2,
            auto_propose=False,
        )
    )
    assert result.ok is False
    assert result.final_code == "x = 0\n"


def test_agent_need_tdd_records_tool_call(char_env, monkeypatch):
    async def _tdd(**kwargs):
        return TDDResult(
            ok=True,
            file_path=kwargs["file_path"],
            iterations_used=1,
            proposal_id="prop_from_agent",
        )

    monkeypatch.setattr("daedalus.tdd_loop.tdd_loop", _tdd)
    monkeypatch.setattr(
        "daedalus.agent._call_claude",
        ScriptedClaude(
            [
                "<NEED_TDD: tests/foo.py | assert add(1, 1) == 2 | def add(a, b): return 0>",
                "TDD finished.",
            ]
        ),
    )
    agent = bare_daedalus()
    raw, summary = asyncio.run(
        agent._build_context_and_call(
            user_message="run the tdd loop on tests/foo.py please",
            session_id=PLAYGROUND_SID,
        )
    )
    tdd_calls = [c for c in summary["tool_calls"] if c.get("tool") == "tdd_loop"]
    assert tdd_calls
    assert tdd_calls[0]["ok"] is True
    assert tdd_calls[0]["proposal_id"] == "prop_from_agent"
    assert "TDD LOOP" in raw or "TDD finished" in raw
