"""ModelGateway — invoke / fallback / cheap ladder. No live LLM."""
from __future__ import annotations

import ast
import asyncio
import inspect
import sys
from pathlib import Path

import pytest

from daedalus.model_gateway import (
    FALLBACK_CHAIN,
    MAX_TOKENS,
    MODEL,
    CompleteRequest,
    CompleteResult,
    DaedalusError,
    ModelGateway,
    ToolRoundRequest,
    call_with_fallback,
    complete_cheap,
    complete_persona,
    complete_primary,
    complete_with_tools,
)

ROOT = Path(__file__).resolve().parents[1]


def _openai_client(text: str, hits: list[str] | None = None):
    class _Msg:
        content = text

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]
        usage = None
        model = "x"

    class _Completions:
        async def create(self, **_k):
            if hits is not None:
                hits.append("create")
            return _Resp()

    class _Chat:
        completions = _Completions()

    class _Client:
        def __init__(self, **_k):
            if hits is not None:
                hits.append("init")
            self.chat = _Chat()

    return _Client


def test_constants_match_legacy_agent_defaults():
    assert MODEL == "claude-sonnet-4-6"
    assert MAX_TOKENS == 8192
    assert len(FALLBACK_CHAIN) == 2
    assert FALLBACK_CHAIN[0][0] == "openrouter_claude"
    assert FALLBACK_CHAIN[1][0] == "novita"


def test_import_gateway_does_not_load_agent():
    env_code = (
        "import daedalus.model_gateway as mg\n"
        "import sys\n"
        "bad = [n for n in sys.modules if n == 'daedalus.agent' or n.startswith('daedalus.agent.')]\n"
        "assert not bad, bad\n"
        "assert mg.MODEL == 'claude-sonnet-4-6'\n"
        "assert mg.MAX_TOKENS == 8192\n"
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


def test_native_tools_and_legacy_adapter_do_not_import_gateway():
    for rel in ("daedalus/native_tools.py", "daedalus/legacy_adapter.py"):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "daedalus.model_gateway"
                    assert "model_gateway" not in alias.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module != "daedalus.model_gateway"
                assert not node.module.endswith(".model_gateway")
                assert "model_gateway" not in (node.module or "")
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    assert alias.name != "model_gateway"
                    assert alias.name != "ModelGateway"


def test_agent_reexports_facades():
    src = (ROOT / "daedalus" / "agent.py").read_text(encoding="utf-8")
    assert "complete_persona as _call_claude" in src
    assert "complete_primary as _call_claude_primary" in src
    assert "complete_with_tools as _call_claude_with_tools" in src
    from daedalus.agent import (
        DaedalusError as AgentError,
        _call_claude,
        _call_claude_primary,
        _call_claude_with_tools,
    )
    assert _call_claude is complete_persona
    assert _call_claude_primary is complete_primary
    assert _call_claude_with_tools is complete_with_tools
    assert AgentError is DaedalusError
    sig = inspect.signature(_call_claude)
    assert list(sig.parameters) == ["system", "messages", "use_cache_override"]
    sig_p = inspect.signature(_call_claude_primary)
    assert list(sig_p.parameters) == ["system", "messages", "use_cache_override"]
    sig_t = inspect.signature(_call_claude_with_tools)
    assert list(sig_t.parameters) == ["system", "messages", "tools"]


def test_complete_cheap_mesh_hit(monkeypatch):
    async def _mesh(**_k):
        return {"reply": "cheap-hello", "cost_usd": 0.001, "provider": "mesh", "model": "fast"}

    monkeypatch.setattr("daedalus.daedalus_mesh.use_mesh_router", lambda: True)
    monkeypatch.setattr("daedalus.daedalus_mesh.complete", _mesh)
    out = asyncio.run(complete_cheap(system="s", user="hi", intent="chat", max_tokens=300))
    assert isinstance(out, CompleteResult)
    assert out.text == "cheap-hello"
    assert out.routed_via == "mesh"
    assert out.cost_usd == 0.001


def test_complete_cheap_falls_to_master_config(monkeypatch):
    hits: list[str] = []

    async def _mesh(**_k):
        return {"reply": ""}

    monkeypatch.setattr("daedalus.daedalus_mesh.use_mesh_router", lambda: True)
    monkeypatch.setattr("daedalus.daedalus_mesh.complete", _mesh)
    monkeypatch.setattr(
        "daedalus.llm_config.get_config",
        lambda: {
            "provider": "openrouter",
            "model": "openai/gpt-oss-120b",
            "api_key": "sk-test",
            "sdk": "openai",
            "base_url": "https://example.invalid/v1",
        },
    )
    monkeypatch.setattr("openai.AsyncOpenAI", _openai_client("from-master", hits))
    out = asyncio.run(complete_cheap(system="s", user="hi"))
    assert out.text == "from-master"
    assert out.routed_via == "master-config"
    assert hits == ["init", "create"]


def test_complete_cheap_falls_to_cheap_llm(monkeypatch):
    hits: list[str] = []

    async def _mesh(**_k):
        return {}

    monkeypatch.setattr("daedalus.daedalus_mesh.use_mesh_router", lambda: True)
    monkeypatch.setattr("daedalus.daedalus_mesh.complete", _mesh)
    monkeypatch.setattr(
        "daedalus.llm_config.get_config",
        lambda: {"api_key": "", "sdk": "anthropic"},
    )
    monkeypatch.setattr(
        "daedalus.cheap_llm.resolve_cheap_openai",
        lambda: {
            "api_key": "sk-cheap",
            "base_url": "https://example.invalid/v1",
            "model": "nvidia/nemotron-3.5-lightning-30b-a3b",
        },
    )
    monkeypatch.setattr("openai.AsyncOpenAI", _openai_client("from-cheap", hits))
    out = asyncio.run(complete_cheap(system="s", user="hi"))
    assert out.text == "from-cheap"
    assert out.routed_via == "cheap"
    assert out.model == "nvidia/nemotron-3.5-lightning-30b-a3b"
    assert hits == ["init", "create"]


def test_complete_cheap_all_miss_empty(monkeypatch):
    monkeypatch.setattr("daedalus.daedalus_mesh.use_mesh_router", lambda: False)
    monkeypatch.setattr(
        "daedalus.llm_config.get_config",
        lambda: {"api_key": "", "sdk": "anthropic"},
    )
    monkeypatch.setattr("daedalus.cheap_llm.resolve_cheap_openai", lambda: None)
    out = asyncio.run(complete_cheap(system="s", user="hi"))
    assert out.text == ""
    assert out.routed_via == ""


def test_complete_cheap_wrap_swallows_mesh_error(monkeypatch):
    async def _boom(**_k):
        raise RuntimeError("mesh down")

    monkeypatch.setattr("daedalus.daedalus_mesh.use_mesh_router", lambda: True)
    monkeypatch.setattr("daedalus.daedalus_mesh.complete", _boom)
    monkeypatch.setattr("daedalus.cheap_llm.resolve_cheap_openai", lambda: None)
    out = asyncio.run(
        complete_cheap(
            system="s",
            user="hi",
            include_master_config=False,
            swallow_mesh_error=True,
        )
    )
    assert out.text == ""


def test_complete_cheap_chat_propagates_mesh_error(monkeypatch):
    async def _boom(**_k):
        raise RuntimeError("mesh down")

    monkeypatch.setattr("daedalus.daedalus_mesh.use_mesh_router", lambda: True)
    monkeypatch.setattr("daedalus.daedalus_mesh.complete", _boom)
    with pytest.raises(RuntimeError, match="mesh down"):
        asyncio.run(complete_cheap(system="s", user="hi"))


def test_gateway_complete_cheap_uses_last_user_message(monkeypatch):
    seen: list[str] = []

    async def _mesh(*, user, **_k):
        seen.append(user)
        return {"reply": "ok"}

    monkeypatch.setattr("daedalus.daedalus_mesh.use_mesh_router", lambda: True)
    monkeypatch.setattr("daedalus.daedalus_mesh.complete", _mesh)
    out = asyncio.run(
        ModelGateway().complete_cheap(
            CompleteRequest(
                system="sys",
                messages=[
                    {"role": "user", "content": "first"},
                    {"role": "assistant", "content": "mid"},
                    {"role": "user", "content": "second"},
                ],
                intent="chat",
                max_tokens=300,
            )
        )
    )
    assert seen == ["second"]
    assert out.text == "ok"


def test_complete_primary_does_not_consult_run_budget(monkeypatch):
    """Locked hole: persona primary ignores the envelope and still hits the SDK."""
    called: list[str] = []
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
    monkeypatch.setattr("openai.AsyncOpenAI", _openai_client("reached-provider", called))

    text = asyncio.run(complete_primary("sys", [{"role": "user", "content": "hi"}]))
    assert called == ["init", "create"]
    assert text == "reached-provider"
    src = inspect.getsource(complete_primary)
    assert "would_block" not in src


def test_call_with_fallback_prefixes_label(monkeypatch):
    async def _primary(*_a, **_k):
        raise RuntimeError("429 rate limited")

    hits: list[str] = []
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
    monkeypatch.setattr("openai.AsyncOpenAI", _openai_client("alt-ok", hits))
    text = asyncio.run(call_with_fallback(_primary, "sys", [{"role": "user", "content": "hi"}]))
    assert text == "[fallback:openrouter_claude] alt-ok"
    assert hits == ["init", "create"]


def test_call_with_fallback_reraises_non_rate_limit():
    async def _primary(*_a, **_k):
        raise RuntimeError("no endpoints found")

    with pytest.raises(RuntimeError, match="no endpoints"):
        asyncio.run(call_with_fallback(_primary, "sys", [{"role": "user", "content": "hi"}]))


def test_complete_with_tools_stops_before_network(monkeypatch):
    monkeypatch.setattr(
        "daedalus.llm_config.get_config",
        lambda: {
            "api_key": "sk-test",
            "provider": "openrouter",
            "model": "openai/gpt-oss-120b",
            "sdk": "openai",
            "base_url": "https://openrouter.ai/api/v1",
        },
    )
    monkeypatch.setattr("daedalus.run_budget.would_block", lambda provider=None: True)
    monkeypatch.setattr("daedalus.run_budget.stop_text", lambda: "envelope blocked")
    out = asyncio.run(complete_with_tools("sys", [{"role": "user", "content": "hi"}], []))
    assert out["budget_stopped"] is True
    assert "envelope blocked" in out["content"][0]["text"]
    gw = asyncio.run(
        ModelGateway().complete_tools(
            ToolRoundRequest(system="sys", messages=[{"role": "user", "content": "hi"}], tools=[])
        )
    )
    assert gw["budget_stopped"] is True


def test_complete_persona_retries_without_vision(monkeypatch):
    calls: list[bool] = []

    async def _primary(system, messages, **_k):
        from daedalus.llm_client import messages_have_images
        has = messages_have_images(messages)
        calls.append(has)
        if has:
            raise RuntimeError("No endpoints found that support image input")
        return "ok-no-vision"

    monkeypatch.setattr("daedalus.model_gateway.complete_primary", _primary)
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "RETURN TO WRITER"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,xx"}},
        ],
    }]
    text = asyncio.run(complete_persona("sys", messages))
    assert text == "ok-no-vision"
    assert calls == [True, False]


def test_agent_cheap_path_routing_stays_on_daedalus():
    src = (ROOT / "daedalus" / "agent.py").read_text(encoding="utf-8")
    assert "persona_via_master_enabled" in src
    assert "is_nvidia_free_only" in src
    assert "is_playground_work_session" in src
    assert "routed_to" in src
    assert "ModelGateway().complete_cheap" in src
    assert "process_mode_marker_only" in src
    gw = (ROOT / "daedalus" / "model_gateway.py").read_text(encoding="utf-8")
    assert "persona_via_master_enabled" not in gw
    assert "is_playground_work_session" not in gw
    assert "scan_need_markers" not in gw
    assert "NEED_BATCH" not in gw
