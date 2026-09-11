"""Characterization of journal resume, task-state, and dead phase-resume."""
from __future__ import annotations

import asyncio
import inspect
import time

import pytest

from tests.characterization_fakes import (
    PLAYGROUND_SID,
    ScriptedClaude,
    bare_daedalus,
    drain_background,
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


def test_is_resume_request_triggers():
    from daedalus.scratch_journal import is_resume_request

    assert is_resume_request("продължи") is True
    assert is_resume_request("Resume") is True
    assert is_resume_request("please continue") is True


def test_is_resume_request_rejects_long_message():
    from daedalus.scratch_journal import is_resume_request

    assert is_resume_request("продължи " + ("x" * 80)) is False
    assert is_resume_request("") is False
    assert is_resume_request("hello") is False


def test_build_resume_recap_last_steps(char_env):
    from daedalus.scratch_journal import build_resume_recap, write_step

    sid = "playground:char-recap"
    write_step(sid, "one", "first", task_name="maze")
    write_step(sid, "two", "second")
    write_step(sid, "three", "third")
    recap = build_resume_recap(sid)
    assert recap.startswith("RESUME: 'maze'")
    assert "three: third" in recap
    assert "НЕ започвай отначало" in recap
    assert len(recap) <= 280


def test_chat_resume_injects_journal_recap(char_env, monkeypatch):
    from daedalus.scratch_journal import write_step

    sid = "playground:char-resume"
    write_step(sid, "scaffold", "wrote maze.html", task_name="maze")
    agent = bare_daedalus()
    script = ScriptedClaude(["continuing"])
    monkeypatch.setattr("daedalus.agent._call_claude", script)

    async def _run():
        try:
            return await agent.chat(message="продължи", session_id=sid)
        finally:
            await drain_background(agent)

    out = asyncio.run(_run())
    blob = "\n".join(script.user_blobs())
    assert "[JOURNAL RESUME]" in blob
    assert "RESUME:" in blob
    assert "maze" in blob
    assert "continuing" in out["reply"]


def test_should_pause_on_token_threshold(char_env):
    from daedalus.scratch_journal import should_pause, write_step

    sid = "playground:char-pause"
    write_step(sid, "a", "hi", task_name="long")
    paused, reason = should_pause(sid, extra_tokens_used=200_000)
    assert paused is True
    assert reason.startswith("token_budget_")
    none, empty = should_pause("", extra_tokens_used=200_000)
    assert none is False
    assert empty == ""


def test_task_state_context_block_and_done_clears(char_env):
    from daedalus import task_state as ts

    assert ts.get_context_block() == ""
    ts.save(goal="build veins", phase="coding", remaining_steps="write,proof")
    block = ts.get_context_block()
    assert "АКТИВНА ЗАДАЧА" in block
    assert "build veins" in block
    assert "coding" in block
    assert ts.is_active() is True
    cleared = ts.save(goal="update", phase="done")
    assert cleared.get("status") == "cleared"
    assert ts.get_context_block() == ""
    assert ts.is_active() is False


def test_phase_save_load_are_noop_nameerror(char_env, monkeypatch):
    from daedalus.agent import Daedalus

    src_save = inspect.getsource(Daedalus._phase_save)
    src_load = inspect.getsource(Daedalus._phase_load)
    src_clear = inspect.getsource(Daedalus._phase_clear)
    assert "DaedalusAgent" in src_save
    assert "DaedalusAgent" in src_load
    assert "DaedalusAgent" in src_clear

    class _Boom(dict):
        def __setitem__(self, key, value):
            raise AssertionError(f"phase_save wrote {key}")

        def get(self, key, default=None):
            raise AssertionError(f"phase_load read {key}")

        def __delitem__(self, key):
            raise AssertionError(f"phase_clear deleted {key}")

    import daedalus.kv as kv

    monkeypatch.setattr(kv, "db", _Boom())
    Daedalus._phase_save("sess", 0.4, "task", "midway")
    assert Daedalus._phase_load("sess") is None
    Daedalus._phase_clear("sess")
    # TTL constant still exists on the live class (unused while NameError stands).
    assert Daedalus._PHASE_TTL_SECONDS == 72 * 3600
    assert time.time() > 0
