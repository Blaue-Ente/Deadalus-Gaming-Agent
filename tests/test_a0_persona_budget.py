"""A0-i: the persona call path obeys and feeds the run budget."""
from __future__ import annotations

import asyncio
import sys
import types

from a0_support import a0_tmp  # noqa: F401


def _exhaust(monkeypatch, cap=5):
    from daedalus import run_budget

    monkeypatch.setenv("DAEDALUS_RUN_BUDGET", str(cap))
    run_budget.note_usage({"cost_usd": cap + 1, "cost_known": True}, model="m", role="writer")
    assert run_budget.exhausted()
    return run_budget


def _cfg(provider="openrouter", sdk="openai"):
    return {"provider": provider, "model": "x/y", "api_key": "k", "base_url": "http://x", "sdk": sdk}


class _Boom:
    def __init__(self, *a, **k):
        raise AssertionError("provider SDK must not be constructed once the budget is spent")


def test_exhausted_budget_blocks_before_any_sdk(a0_tmp, monkeypatch):
    from daedalus import agent

    rb = _exhaust(monkeypatch)
    monkeypatch.setattr(agent, "get_config", lambda: _cfg())
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=_Boom))
    out = asyncio.run(agent._call_claude_primary("sys", [{"role": "user", "content": "hi"}]))
    assert out == rb.stop_text() and "blocked" in out


def test_local_provider_is_not_blocked(a0_tmp, monkeypatch):
    from daedalus import agent

    _exhaust(monkeypatch)
    monkeypatch.setattr(agent, "get_config", lambda: _cfg(provider="ollama"))

    class Msg:
        content = "local ok"

    class Resp:
        choices = [types.SimpleNamespace(message=Msg())]
        usage = None

    class Client:
        def __init__(self, *a, **k):
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self.create))

        async def create(self, **kw):
            return Resp()

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=Client))
    out = asyncio.run(agent._call_claude_primary("sys", [{"role": "user", "content": "hi"}]))
    assert out == "local ok"


def test_usage_is_metered_after_a_call(a0_tmp, monkeypatch):
    from daedalus import agent, run_budget

    monkeypatch.setenv("DAEDALUS_RUN_BUDGET", "20")
    monkeypatch.setattr(agent, "get_config", lambda: _cfg())
    metered = []
    monkeypatch.setattr("daedalus.llm_client._meter", lambda resp, *, model, role: metered.append((model, role)))

    class Resp:
        choices = [types.SimpleNamespace(message=types.SimpleNamespace(content="hello"))]
        usage = types.SimpleNamespace(prompt_tokens=3, completion_tokens=2)

    class Client:
        def __init__(self, *a, **k):
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self.create))

        async def create(self, **kw):
            return Resp()

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=Client))
    out = asyncio.run(agent._call_claude_primary("sys", [{"role": "user", "content": "hi"}]))
    assert out == "hello" and metered == [("x/y", "writer")]
    assert not run_budget.exhausted()


def test_fallback_chain_respects_spent_budget(a0_tmp, monkeypatch):
    from daedalus import improvements

    rb = _exhaust(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=_Boom))

    async def primary(system, messages, **kw):
        raise RuntimeError("429 rate limit")

    out = asyncio.run(improvements.call_with_fallback(primary, "sys", []))
    assert out == rb.stop_text()


def test_metering_failure_does_not_fail_a_successful_call(a0_tmp, monkeypatch):
    """Review fix: a billing-store error after a good provider reply must not turn it into an error."""
    from daedalus import agent

    monkeypatch.setattr(agent, "get_config", lambda: _cfg())

    def broken_meter(resp, *, model, role):
        raise OSError("budget store unavailable")

    monkeypatch.setattr("daedalus.llm_client._meter", broken_meter)

    class Resp:
        choices = [types.SimpleNamespace(message=types.SimpleNamespace(content="fine"))]
        usage = None

    class Client:
        def __init__(self, *a, **k):
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self.create))

        async def create(self, **kw):
            return Resp()

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=Client))
    assert asyncio.run(agent._call_claude_primary("sys", [{"role": "user", "content": "hi"}])) == "fine"
