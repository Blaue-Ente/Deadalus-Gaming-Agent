"""Atomic model route helpers for explicit provider:model tags."""
from __future__ import annotations

from daedalus.model_router import config_for_explicit_model, parse_provider_model_tag


def test_parse_provider_tag_ollama_colon_preserved() -> None:
    assert parse_provider_model_tag("qwen2.5-coder:7b") == (None, None)


def test_parse_provider_tag_novita() -> None:
    assert parse_provider_model_tag("novita:deepseek/deepseek-v3.2") == (
        "novita", "deepseek/deepseek-v3.2",
    )


def test_config_for_explicit_resolves_nvidia_when_key(monkeypatch) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    base = {
        "provider": "openrouter",
        "model": "anthropic/claude-sonnet-4-5",
        "api_key": "or-key",
        "base_url": "https://openrouter.ai/api/v1",
        "sdk": "openai",
    }
    out = config_for_explicit_model(base, "nvidia:qwen/qwen3-next-80b-a3b-instruct")
    assert out.get("provider") == "nvidia"
    assert "qwen/qwen3-next" in (out.get("model") or "")
    assert out.get("api_key")


def test_config_for_explicit_missing_key_sets_routing_error(monkeypatch) -> None:
    monkeypatch.delenv("POOLSIDE_API", raising=False)
    base = {"provider": "openrouter", "model": "x", "api_key": "k"}
    out = config_for_explicit_model(base, "poolside:poolside/laguna-m.1")
    assert out.get("routing_error")
