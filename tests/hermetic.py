"""Shared hermetic CI helpers. No live LLM keys, no Chroma, no docker."""
from __future__ import annotations

from typing import Any

PROVIDER_KEY_NAMES = (
    "ALLTOKEN_API_KEY",
    "OPENROUTER_API_KEY",
    "NOVITA_API_KEY",
    "NVIDIA_API_KEY",
    "DAEDALUS_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OMNIROUTE_API_KEY",
)

HERMETIC_LLM_CONFIG: dict[str, Any] = {
    "provider": "test",
    "model": "test-model",
    "api_key": "",
    "base_url": "",
    "sdk": "openai",
    "use_cache": False,
    "source": "hermetic-test",
}


def strip_provider_keys(monkeypatch) -> None:
    for name in PROVIDER_KEY_NAMES:
        monkeypatch.delenv(name, raising=False)


def stub_hermetic_llm_config(monkeypatch) -> None:
    """finish_chat reads run_finalizer.get_config (imported name), not only llm_config."""

    def _cfg() -> dict[str, Any]:
        return dict(HERMETIC_LLM_CONFIG)

    monkeypatch.setattr("daedalus.llm_config.get_config", _cfg)
    monkeypatch.setattr("daedalus.run_finalizer.get_config", _cfg)
    monkeypatch.setattr("daedalus.agent.get_config", _cfg, raising=False)
