"""Ollama /api/show probe, context budget, and the slim local prompt.

No live Ollama daemon. A loopback fake server returns the show/ps payloads.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        return

    def _send(self, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path == "/v1/models":
            self._send({"data": [{"id": "qwen2.5-coder:7b"}]})
            return
        if path == "/api/ps":
            self._send({
                "models": [{
                    "name": "qwen2.5-coder:7b",
                    "context_length": self.server.ctx,  # type: ignore[attr-defined]
                }],
            })
            return
        self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        path = self.path.split("?", 1)[0]
        if path == "/api/show":
            self._send(self.server.show)  # type: ignore[attr-defined]
            return
        self.send_error(404)


def _serve(show: dict, ctx: int) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    httpd.show = show  # type: ignore[attr-defined]
    httpd.ctx = ctx  # type: ignore[attr-defined]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def _stop(httpd: ThreadingHTTPServer) -> None:
    httpd.shutdown()
    httpd.server_close()


def _point_at(monkeypatch, httpd: ThreadingHTTPServer, tmp_path) -> str:
    port = httpd.server_address[1]
    host = f"http://127.0.0.1:{port}"
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PROVIDER", "ollama")
    monkeypatch.setenv("DAEDALUS_MODEL", "qwen2.5-coder:7b")
    monkeypatch.setenv("OLLAMA_HOST", host)
    monkeypatch.delenv("OLLAMA_CONTEXT_LENGTH", raising=False)
    monkeypatch.delenv("DAEDALUS_OFFLINE", raising=False)
    monkeypatch.delenv("DAEDALUS_LOCAL_LLM", raising=False)
    from daedalus.local_llm import clear_probe_cache
    from daedalus.local_prompt import clear_warning_log
    clear_probe_cache()
    clear_warning_log()
    return host


def _build_prompt() -> tuple[str, list]:
    from daedalus.tool_router import format_system, native_specs, route
    message = "направи змия на Phaser, kind=phaser"
    routed = route(message, kind="phaser")
    system = format_system(routed, user_message=message)
    tools = native_specs(routed) or []
    return system, tools


def test_show_parser_reads_tools_and_num_ctx():
    from daedalus.local_llm import effective_context, parse_ollama_show
    parsed = parse_ollama_show({
        "capabilities": ["completion", "tools"],
        "parameters": "num_ctx 4096\ntemperature 0.2",
        "model_info": {"qwen2.context_length": 32768},
    })
    assert parsed["tools"] is True
    assert parsed["parameter_num_ctx"] == 4096
    assert parsed["model_context"] == 32768
    ctx, source = effective_context(parsed, ps_context=None, env_ctx=None)
    assert ctx == 4096
    assert "num_ctx" in source


def test_unloaded_model_assumes_ollama_default_4096():
    from daedalus.local_llm import OLLAMA_DEFAULT_NUM_CTX, effective_context, parse_ollama_show
    parsed = parse_ollama_show({
        "capabilities": ["completion", "tools"],
        "parameters": "",
        "model_info": {"qwen2.context_length": 32768},
    })
    ctx, source = effective_context(parsed, ps_context=None, env_ctx=None)
    assert ctx == OLLAMA_DEFAULT_NUM_CTX == 4096
    assert "4096" in source
    wide, wide_source = effective_context(parsed, ps_context=32768, env_ctx=None)
    assert wide == 32768
    assert "ps" in wide_source


def test_fake_ollama_4096_warns_and_slims(tmp_path, monkeypatch, caplog):
    from daedalus.local_prompt import (
        REPLY_RESERVE_TOKENS,
        SLIM_LOCAL_SYSTEM,
        fit_ollama_turn,
    )
    from daedalus.scratch_journal import estimate_tokens

    show = {
        "capabilities": ["completion", "tools"],
        "parameters": "num_ctx 4096",
        "model_info": {"qwen2.context_length": 32768},
    }
    httpd = _serve(show, 4096)
    try:
        host = _point_at(monkeypatch, httpd, tmp_path)
        system, tools = _build_prompt()
        full = estimate_tokens(system) + estimate_tokens(json.dumps(tools))
        assert full + REPLY_RESERVE_TOKENS > 4096
        user = "x" * 20000
        with caplog.at_level(logging.WARNING):
            fitted = fit_ollama_turn(
                system,
                tools,
                messages=[{"role": "user", "content": user}],
                model="qwen2.5-coder:7b",
                base_url=host + "/v1",
                max_tokens=12288,
            )
        assert fitted["slim"] is True
        assert fitted["trimmed"] is True
        assert fitted["system"] == SLIM_LOCAL_SYSTEM
        assert len(fitted["system"]) < len(system)
        names = [t["name"] for t in fitted["tools"]]
        assert "write_file" in names
        assert len(names) < len(tools)
        assert len(json.dumps(fitted["tools"])) < len(json.dumps(tools))
        assert "trimmed to fit local context" in fitted["messages"][0]["content"]
        assert len(fitted["messages"][0]["content"]) < len(user)
        assert fitted["max_tokens"] < 4096
        blob = " ".join(fitted["warnings"])
        assert "4096" in blob
        assert "slim local profile" in blob
        assert "slim local profile" in caplog.text

        from daedalus.connectivity import recommendations
        ids = [row["id"] for row in recommendations()]
        assert "ollama-small-context" in ids
        assert "ollama-down" not in ids
        ctx_row = next(row for row in recommendations() if row["id"] == "ollama-small-context")
        assert "4096" in ctx_row["body"]
        assert "slim local profile" in ctx_row["body"]
    finally:
        _stop(httpd)


def test_missing_tools_capability_warns(tmp_path, monkeypatch, caplog):
    show = {
        "capabilities": ["completion"],
        "parameters": "num_ctx 32768",
        "model_info": {"qwen2.context_length": 32768},
    }
    httpd = _serve(show, 32768)
    try:
        _point_at(monkeypatch, httpd, tmp_path)
        from daedalus.connectivity import recommendations
        with caplog.at_level(logging.WARNING):
            rows = recommendations()
        ids = [row["id"] for row in rows]
        assert "ollama-no-tools" in ids
        assert "ollama-small-context" not in ids
        body = next(row for row in rows if row["id"] == "ollama-no-tools")["body"]
        assert "does not advertise the tools capability" in body
        assert "does not advertise the tools capability" in caplog.text
        assert "completion" in body
    finally:
        _stop(httpd)


def test_large_context_keeps_full_prompt(tmp_path, monkeypatch):
    from daedalus.local_prompt import fit_ollama_turn
    show = {
        "capabilities": ["completion", "tools", "insert"],
        "parameters": "num_ctx 32768",
        "model_info": {"qwen2.context_length": 32768},
    }
    httpd = _serve(show, 32768)
    try:
        host = _point_at(monkeypatch, httpd, tmp_path)
        system, tools = _build_prompt()
        fitted = fit_ollama_turn(
            system,
            tools,
            messages=[{"role": "user", "content": "build a snake"}],
            model="qwen2.5-coder:7b",
            base_url=host + "/v1",
            max_tokens=12288,
        )
        assert fitted["slim"] is False
        assert fitted["system"] == system
        assert [t["name"] for t in fitted["tools"]] == [t["name"] for t in tools]
        assert fitted["probe"]["tools"] is True
        assert fitted["probe"]["context_length"] == 32768
        assert not any("slim local profile" in w for w in fitted["warnings"])
    finally:
        _stop(httpd)


def test_epic_uses_cloud_without_changing_wardrobe_model(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ALLTOKEN_API_KEY", raising=False)
    monkeypatch.delenv("NOVITA_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DAEDALUS_OFFLINE", raising=False)
    from daedalus.llm_wardrobe import CATALOG, activate, load
    from daedalus.local_llm import _DEFAULTS
    from daedalus.task_effort import EPIC_LOCAL_WARNING, resolve_model

    assert next(row["ollama"] for row in CATALOG if row["id"] == "code-7b") == "qwen2.5-coder:7b"
    assert _DEFAULTS["ollama"]["default_model"] == "qwen2.5-coder:7b"
    activate("code-7b")
    monkeypatch.setattr("daedalus.llm_wardrobe.installed_ollama_models", lambda: [])

    decision = {"apply_model": True, "tier": "epic", "role": "reason"}
    local = resolve_model(decision)
    assert local["provider"] == "ollama"
    assert local["model"] == "qwen2.5-coder:7b"
    assert local["provider_path"] == "local"
    assert local["warning"] == EPIC_LOCAL_WARNING
    assert load()["active"] == "qwen2.5-coder:7b"

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr(
        "daedalus.model_registry.select_for_task",
        lambda *args, **kwargs: {"model_id": "cloud-epic", "provider": "openrouter"},
    )
    cloud = resolve_model(decision)
    assert cloud["provider_path"] == "epic-cloud"
    assert cloud["provider"] == "openrouter"
    assert cloud["model"] == "cloud-epic"
    assert cloud["model_source"] == "epic-cloud"
    assert load()["active"] == "qwen2.5-coder:7b"
    assert next(row["ollama"] for row in CATALOG if row["id"] == "code-7b") == "qwen2.5-coder:7b"

    monkeypatch.setenv("DAEDALUS_OFFLINE", "1")
    stayed = resolve_model(decision)
    assert stayed["provider"] == "ollama"
    assert stayed["model"] == "qwen2.5-coder:7b"
    assert stayed["provider_path"] == "local"

    whisper = resolve_model({"apply_model": True, "tier": "whisper", "role": "talk"})
    assert whisper["provider_path"] == "whisper-local"
    assert whisper["provider"] == "ollama"


def test_call_with_tools_sends_slim_prompt(tmp_path, monkeypatch):
    from daedalus.local_prompt import SLIM_LOCAL_SYSTEM

    show = {
        "capabilities": ["completion", "tools"],
        "parameters": "num_ctx 4096",
        "model_info": {"qwen2.context_length": 32768},
    }
    httpd = _serve(show, 4096)
    captured: dict = {}

    class _Msg:
        content = "ok"
        tool_calls = None

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]
        usage = None
        model = "qwen2.5-coder:7b"

    class _Completions:
        async def create(self, **kwargs):
            captured["kwargs"] = kwargs
            return _Resp()

    class _Chat:
        def __init__(self):
            self.completions = _Completions()

    class _Client:
        def __init__(self, *args, **kwargs):
            self.chat = _Chat()

    try:
        _point_at(monkeypatch, httpd, tmp_path)
        monkeypatch.setattr("openai.AsyncOpenAI", _Client)
        from daedalus.llm_client import call_with_tools
        system, tools = _build_prompt()
        result = asyncio.run(call_with_tools(
            system,
            [{"role": "user", "content": "build the snake"}],
            tools,
            max_tokens=12288,
        ))
        assert result["stop_reason"] == "end_turn"
        sent = captured["kwargs"]
        assert sent["messages"][0]["content"] == SLIM_LOCAL_SYSTEM
        assert sent["max_tokens"] < 4096
        names = [t["function"]["name"] for t in sent["tools"]]
        assert "write_file" in names
        assert len(names) < len(tools)
    finally:
        _stop(httpd)
