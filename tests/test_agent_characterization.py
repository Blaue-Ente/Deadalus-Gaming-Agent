"""Characterization of Daedalus.chat / _build_context_and_call — current behavior."""
from __future__ import annotations

import asyncio
import inspect

import pytest

from tests.characterization_fakes import (
    CHAT_RETURN_KEYS,
    PLAYGROUND_SID,
    ScriptedClaude,
    bare_daedalus,
    drain_background,
    isolate_paths,
    stub_navigate_build,
    stub_navigate_chat,
    stub_native_pass,
    stub_tools_quiet,
    user_text_from_messages,
)


@pytest.fixture
def char_env(tmp_path, monkeypatch):
    isolate_paths(tmp_path, monkeypatch)
    monkeypatch.setattr("daedalus.navigator.navigate", stub_navigate_build)
    monkeypatch.setattr("daedalus.agent.run_native_discovery_pass", stub_native_pass)
    monkeypatch.setattr("daedalus.agent._call_claude_with_tools", stub_tools_quiet)
    yield tmp_path


def _patch_claude(monkeypatch, script: ScriptedClaude) -> ScriptedClaude:
    monkeypatch.setattr("daedalus.agent._call_claude", script)
    return script


async def _chat(agent, **kwargs):
    try:
        return await agent.chat(**kwargs)
    finally:
        await drain_background(agent)


def test_chat_empty_message_raises_bad_request(char_env):
    from daedalus.agent import DaedalusBadRequest

    agent = bare_daedalus()
    with pytest.raises(DaedalusBadRequest, match="message cannot be empty"):
        asyncio.run(_chat(agent, message=""))
    with pytest.raises(DaedalusBadRequest, match="message cannot be empty"):
        asyncio.run(_chat(agent, message="   \n"))


def test_chat_assigns_session_id_prefix(char_env, monkeypatch):
    agent = bare_daedalus()
    _patch_claude(monkeypatch, ScriptedClaude(["ok"]))
    out = asyncio.run(_chat(agent, message="ping"))
    assert out["session_id"].startswith("chat-")
    assert out["agent"] == "Daedalus"


def test_chat_return_shape_keys(char_env, monkeypatch):
    agent = bare_daedalus()
    _patch_claude(monkeypatch, ScriptedClaude(["shape-ok"]))
    out = asyncio.run(_chat(agent, message="ping", session_id=PLAYGROUND_SID))
    for key in CHAT_RETURN_KEYS:
        assert key in out, f"missing {key}"
    assert out["for_self"] is False
    assert "shape-ok" in out["reply"]
    assert isinstance(out["context"], dict)
    assert "tool_calls" in out["context"]
    assert out["run_budget"] is not None


def test_chat_for_self_prefixes_approval_banner(char_env, monkeypatch):
    agent = bare_daedalus()
    script = _patch_claude(monkeypatch, ScriptedClaude(["noted"]))
    out = asyncio.run(
        _chat(agent, message="improve yourself", session_id=PLAYGROUND_SID, for_self=True)
    )
    blob = "\n".join(script.user_blobs())
    assert "SELF-IMPROVEMENT MODE" in blob
    assert "Owner approval required" in blob
    assert out["for_self"] is True


def test_chat_playground_session_skips_cheap_path(char_env, monkeypatch):
    cheap_hits = []

    async def _cheap_complete(**_k):
        cheap_hits.append(True)
        return {"reply": "should-not-run"}

    monkeypatch.setattr("daedalus.navigator.navigate", stub_navigate_chat)
    monkeypatch.setattr("daedalus.daedalus_mesh.use_mesh_router", lambda: True)
    monkeypatch.setattr("daedalus.daedalus_mesh.complete", _cheap_complete)
    agent = bare_daedalus()
    script = _patch_claude(monkeypatch, ScriptedClaude(["acted-on-playground"]))
    out = asyncio.run(_chat(agent, message="hi", session_id=PLAYGROUND_SID))
    assert cheap_hits == []
    assert script.calls, "_call_claude must run for playground sessions"
    assert "acted-on-playground" in out["reply"]


def test_chat_talk_or_navigator_chat_uses_cheap_path(char_env, monkeypatch):
    cheap_hits: list[str] = []

    async def _cheap_complete(**_k):
        cheap_hits.append("mesh")
        return {"reply": "cheap-hello"}

    monkeypatch.setattr("daedalus.llm_config.persona_via_master_enabled", lambda: False)
    monkeypatch.setattr("daedalus.llm_config.is_nvidia_free_only", lambda: False)
    monkeypatch.setattr("daedalus.navigator.navigate", stub_navigate_chat)
    monkeypatch.setattr("daedalus.mode.get_mode", lambda: "auto")
    monkeypatch.setattr("daedalus.daedalus_mesh.use_mesh_router", lambda: True)
    monkeypatch.setattr("daedalus.daedalus_mesh.complete", _cheap_complete)
    agent = bare_daedalus()
    script = _patch_claude(monkeypatch, ScriptedClaude(["full-path-should-not-run"]))
    out = asyncio.run(_chat(agent, message="hello there friend", session_id="chat-plain"))
    assert cheap_hits == ["mesh"]
    assert script.calls == []
    assert out["reply"] == "cheap-hello"
    assert out["context"].get("routed_to") in {"mesh_router_chat", "chat_fast_path"}


def test_build_context_strips_need_markers_from_reply(char_env, monkeypatch):
    agent = bare_daedalus()
    _patch_claude(
        monkeypatch,
        ScriptedClaude(["Here <NEED_FOO: bar> leftover"]),
    )
    raw, _summary = asyncio.run(
        agent._build_context_and_call(
            user_message="just chatting about leftovers",
            session_id=PLAYGROUND_SID,
        )
    )
    assert "<NEED_FOO" not in raw
    assert "NEED_FOO" not in raw
    assert "Here" in raw and "leftover" in raw


def test_build_context_need_read_then_final_reply(char_env, monkeypatch):
    agent = bare_daedalus()
    script = _patch_claude(
        monkeypatch,
        ScriptedClaude(
            [
                "<NEED_READ_FILE: tests/conftest.py>",
                "File looks fine.",
            ]
        ),
    )
    raw, summary = asyncio.run(
        agent._build_context_and_call(
            user_message="please read tests/conftest.py",
            session_id=PLAYGROUND_SID,
        )
    )
    assert "File looks fine." in raw
    assert "<NEED_" not in raw
    reads = [c for c in summary["tool_calls"] if c.get("tool") == "read_file"]
    assert reads
    assert len(script.calls) >= 2


def test_build_context_tool_reroll_failure_appends_fallback(char_env, monkeypatch):
    agent = bare_daedalus()
    _patch_claude(
        monkeypatch,
        ScriptedClaude(
            [
                "<NEED_READ_FILE: tests/conftest.py>",
                RuntimeError("llm down"),
            ]
        ),
    )
    raw, _summary = asyncio.run(
        agent._build_context_and_call(
            user_message="please read tests/conftest.py",
            session_id=PLAYGROUND_SID,
        )
    )
    assert "follow-up call failed" in raw
    assert "llm down" in raw


def test_build_context_native_pass_attaches_loop_summary(char_env, monkeypatch):
    seen: list[str] = []

    async def _claude(system, messages, **_k):
        seen.append(user_text_from_messages(messages))
        return "short summary of the slice"

    monkeypatch.setattr("daedalus.agent._call_claude", _claude)
    agent = bare_daedalus()
    raw, _summary = asyncio.run(
        agent._build_context_and_call(
            user_message=(
                "Направи нова Playground Godot 4 GDScript игра: "
                "една сцена и играч се движи."
            ),
            session_id=PLAYGROUND_SID,
        )
    )
    assert seen
    assert "BUILD LOOP (kernel)" in seen[0]
    assert "short summary of the slice" in raw


def test_chat_pending_proposal_parsed_from_reply(char_env, monkeypatch):
    agent = bare_daedalus()
    _patch_claude(
        monkeypatch,
        ScriptedClaude(
            [
                "Proposal ID : prop_deadbeef_aa11bb22\n"
                "File : daedalus/foo.py\n"
                "Ready."
            ]
        ),
    )
    out = asyncio.run(_chat(agent, message="show the proposal", session_id=PLAYGROUND_SID))
    assert out["pending_proposal"] == {
        "proposal_id": "prop_deadbeef_aa11bb22",
        "file_path": "daedalus/foo.py",
    }


def test_chat_includes_run_budget_status(char_env, monkeypatch):
    agent = bare_daedalus()
    _patch_claude(monkeypatch, ScriptedClaude(["ok"]))
    out = asyncio.run(_chat(agent, message="status?", session_id=PLAYGROUND_SID))
    budget = out["run_budget"]
    assert isinstance(budget, dict)
    assert "usd" in budget or "plan" in budget or "spent_usd" in budget or "active" in budget


def test_native_pass_honors_budget_stopped(char_env):
    from daedalus.build_loop import BuildLoop
    from daedalus.native_tools import run_native_discovery_pass
    from daedalus.tool_router import route

    async def _stopped(system, messages, tools):
        return {
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "envelope blocked"}],
            "budget_stopped": True,
        }

    ask = "Направи нова Playground игра snake на HTML canvas."
    loop = BuildLoop(route(ask), ask, fix_rounds=2)
    text, log = asyncio.run(
        run_native_discovery_pass(
            "sys",
            ask,
            _stopped,
            memory=None,
            session_id=PLAYGROUND_SID,
            read_file_fn=lambda *a, **k: {},
            max_rounds=4,
            tools=[{"name": "x"}],
            loop=loop,
        )
    )
    assert loop.stop_reason == "run budget exhausted"
    assert text == "envelope blocked"
    assert log == []


def test_call_claude_primary_does_not_consult_run_budget(char_env, monkeypatch):
    """Current hole: the persona call ignores the envelope and still hits the SDK."""
    from daedalus.agent import _call_claude_primary

    called: list[str] = []

    class _Msg:
        content = "reached-provider"

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]
        usage = None
        model = "x"

    class _Completions:
        async def create(self, **_k):
            called.append("create")
            return _Resp()

    class _Chat:
        completions = _Completions()

    class _Client:
        def __init__(self, **_k):
            called.append("init")
            self.chat = _Chat()

    monkeypatch.setattr(
        "daedalus.llm_config.get_config",
        lambda: {
            "provider": "openrouter",
            "model": "openai/gpt-oss-120b",
            "api_key": "sk-test",
            "sdk": "openai",
            "base_url": "https://example.invalid/v1",
            "use_cache": False,
        },
    )
    monkeypatch.setattr("daedalus.run_budget.would_block", lambda provider=None: True)
    monkeypatch.setattr("openai.AsyncOpenAI", _Client)

    text = asyncio.run(
        _call_claude_primary("sys", [{"role": "user", "content": "hi"}])
    )
    assert called == ["init", "create"]
    assert text == "reached-provider"
    src = inspect.getsource(_call_claude_primary)
    assert "would_block" not in src
