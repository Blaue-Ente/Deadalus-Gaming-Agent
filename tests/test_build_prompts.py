"""Build model resolution for game / repair paths."""
from __future__ import annotations

import pytest


def test_game_build_respects_daedalus_build_model_bare_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAEDALUS_BUILD_MODEL", "qwen2.5-coder:7b")
    monkeypatch.delenv("DAEDALUS_BUILD_PROVIDER", raising=False)
    monkeypatch.setattr("daedalus.build_prompts._adn_admin", lambda: None)
    monkeypatch.setattr("daedalus.llm_wardrobe.wants_local", lambda: False)

    from daedalus.build_prompts import resolve_build_model_for_project

    tag, label = resolve_build_model_for_project("platformer", is_game=True)
    assert tag == "ollama:qwen2.5-coder:7b"
    assert "DAEDALUS_BUILD_MODEL" in label


def test_game_auto_skips_cloud_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DAEDALUS_BUILD_MODEL", raising=False)
    monkeypatch.setattr("daedalus.build_prompts._adn_admin", lambda: None)
    monkeypatch.setattr("daedalus.llm_wardrobe.wants_local", lambda: False)
    monkeypatch.setattr("daedalus.build_prompts._tag_from_get_config", lambda: (None, ""))
    monkeypatch.setattr("daedalus.model_registry.get_auto_mode", lambda: True)
    monkeypatch.setattr(
        "daedalus.model_registry.iter_candidates_for_task",
        lambda *a, **k: [
            {"provider": "poolside", "model_id": "poolside/laguna-m.1", "display_name": "Poolside"},
            {"provider": "novita", "model_id": "deepseek/deepseek-v3.2", "display_name": "Novita"},
        ],
    )

    def _key(p: str) -> str:
        return "k" if (p or "").strip().lower() == "novita" else ""

    monkeypatch.setattr("daedalus.llm_config.resolve_provider_api_key", _key)
    monkeypatch.setattr("daedalus.local_llm.active_ollama_model", lambda: "fallback-local:7b")

    from daedalus.build_prompts import resolve_build_model_for_project

    tag, _ = resolve_build_model_for_project("browser game", is_game=True)
    assert tag == "novita:deepseek/deepseek-v3.2"


def test_game_auto_all_cloud_without_key_falls_back_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DAEDALUS_BUILD_MODEL", raising=False)
    monkeypatch.setattr("daedalus.build_prompts._adn_admin", lambda: None)
    monkeypatch.setattr("daedalus.llm_wardrobe.wants_local", lambda: False)
    monkeypatch.setattr("daedalus.build_prompts._tag_from_get_config", lambda: (None, ""))
    monkeypatch.setattr("daedalus.model_registry.get_auto_mode", lambda: True)
    monkeypatch.setattr(
        "daedalus.model_registry.iter_candidates_for_task",
        lambda *a, **k: [
            {"provider": "poolside", "model_id": "poolside/laguna-m.1", "display_name": "Poolside"},
        ],
    )
    monkeypatch.setattr("daedalus.llm_config.resolve_provider_api_key", lambda _p: "")
    monkeypatch.setattr("daedalus.local_llm.active_ollama_model", lambda: "qwen2.5-coder:7b")

    from daedalus.build_prompts import resolve_build_model_for_project

    tag, label = resolve_build_model_for_project("browser game", is_game=True)
    assert tag == "ollama:qwen2.5-coder:7b"
    assert "fallback" in label.lower()


def test_game_nvidia_free_only_beats_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DAEDALUS_BUILD_MODEL", raising=False)
    monkeypatch.setattr("daedalus.build_prompts._adn_admin", lambda: None)
    monkeypatch.setattr("daedalus.llm_config.is_nvidia_free_only", lambda: True)
    monkeypatch.setattr(
        "daedalus.llm_config.get_brain_config",
        lambda: {"provider": "nvidia", "model": "qwen/qwen3-next-80b-a3b-instruct"},
    )

    from daedalus.build_prompts import resolve_build_model_for_project

    tag, label = resolve_build_model_for_project("browser game", is_game=True)
    assert tag == "nvidia:qwen/qwen3-next-80b-a3b-instruct"
    assert "nvidia" in label.lower()


def test_resolve_build_model_auto_skips_poolside_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("daedalus.build_prompts._adn_admin", lambda: None)
    monkeypatch.setattr("daedalus.llm_config.is_nvidia_free_only", lambda: False)
    monkeypatch.setattr("daedalus.model_registry.get_auto_mode", lambda: True)
    monkeypatch.setattr(
        "daedalus.model_registry.select_for_task",
        lambda *a, **k: {
            "provider": "poolside",
            "model_id": "poolside/laguna-m.1",
            "display_name": "Poolside",
        },
    )
    monkeypatch.setattr("daedalus.llm_config.resolve_provider_api_key", lambda _p: "")

    from daedalus.build_prompts import resolve_build_model

    model, label = resolve_build_model("fix typescript")
    assert model is None
    assert "Auto" in label
