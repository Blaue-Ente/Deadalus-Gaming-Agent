"""A tool call cut off by the output limit is refused with the fix, not run with half its arguments.

Found live (2026-11-11): asked for a full game, the writer put it all in one write_file, hit 8192 output tokens, and
the cut call failed with no reason the model could act on."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from daedalus import llm_client, native_tools


def test_only_a_cut_call_is_marked():
    assert llm_client.mark_cut_tool_input({"path": "a"}, cut=False, max_tokens=8192) == {"path": "a"}
    out = llm_client.mark_cut_tool_input({"raw": '{"path": "index.html", "content": "<html'}, cut=True,
                                         max_tokens=8192)
    assert out["_cut_at_output_limit"] == 8192 and out["raw"].startswith('{"path"')


def test_a_cut_call_is_refused_with_the_fix(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a cut call must not reach the playground")

    monkeypatch.setattr("daedalus.playground.write_file", boom)
    text = asyncio.run(native_tools.execute_native_tool(
        "write_file", {"raw": "{", "_cut_at_output_limit": 8192}, memory=None, session_id="pg-x",
        read_file_fn=lambda *a, **k: {}))
    assert "NOT RUN" in text and "8192" in text and "several smaller files" in text


def test_openai_compatible_length_finish_marks_the_call(monkeypatch):
    """The OpenAI-compatible path (OpenRouter and the rest) reads finish_reason == "length"."""
    whole = SimpleNamespace(id="t0", function=SimpleNamespace(name="write_file",
                                                              arguments='{"path": "game.js", "content": "ok"}'))
    tc = SimpleNamespace(id="t1", function=SimpleNamespace(name="write_file", arguments='{"path": "index.html", "con'))
    resp = SimpleNamespace(choices=[SimpleNamespace(finish_reason="length",
                                                    message=SimpleNamespace(content=None, tool_calls=[whole, tc]))],
                           usage=None)

    class Client:
        def __init__(self, **kw):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        async def create(self, **kw):
            return resp

    monkeypatch.setattr("openai.AsyncOpenAI", Client)
    monkeypatch.setattr(llm_client, "_meter", lambda *a, **k: {})
    monkeypatch.setenv("DAEDALUS_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-real")
    monkeypatch.setenv("DAEDALUS_MODEL", "test/model")
    out = asyncio.run(llm_client.call_with_tools("sys", [{"role": "user", "content": "hi"}], [{"name": "write_file", "description": "w"}],
                                                 max_tokens=8192))
    use = [b for b in out["content"] if b["type"] == "tool_use"]
    # found live: marking every call of a cut reply refused five whole files; only the interrupted last one is cut
    assert use[0]["input"] == {"path": "game.js", "content": "ok"}
    assert use[1]["input"]["_cut_at_output_limit"] == 8192 and out["cut_at_output_limit"] == 8192


def test_a_reply_cut_with_no_tool_call_is_explained_not_ended():
    """Some providers return no tool call at all when the limit cuts it; the loop must say why and go on."""
    replies = [
        {"stop_reason": "end_turn", "content": [{"type": "text", "text": "<!doctype html>..."}],
         "cut_at_output_limit": 8192},
        {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "w1", "name": "read_file",
                                                  "input": {"path": "x"}}]},
        {"stop_reason": "end_turn", "content": [{"type": "text", "text": "done"}]},
    ]
    seen = []

    async def model(system, messages, tools):
        seen.append(messages[-1])
        return replies.pop(0)

    asyncio.run(native_tools.run_native_discovery_pass(
        "sys", "make a game", model, memory=None, session_id="", read_file_fn=lambda *a, **k: {"content": ""},
        max_rounds=2, tools=[{"name": "read_file"}]))
    told = [m for m in seen if "output limit" in str(m.get("content"))]
    assert len(told) == 1 and "8192" in str(told[0]["content"]) and "smaller files" in str(told[0]["content"])
    assert not replies                                   # the cut round did not use up the two real rounds
