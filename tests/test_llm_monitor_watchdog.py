"""LLM brain vs this-turn overlays, routing watchdog, build fallback."""
from __future__ import annotations

import pytest


def _budget_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    from daedalus import llm_config

    monkeypatch.setattr(
        llm_config,
        "_resolve_llm_config",
        lambda: {"provider": "openrouter", "model": "owner/pick", "api_key": "k", "base_url": "", "sdk": "openai",
                 "source": "db"},
    )
    monkeypatch.setattr(
        "daedalus.run_budget.applied_writer",
        lambda: {"model": "moonshotai/kimi-k3", "provider": "openrouter", "model_source": "run-budget",
                 "apply_model": True, "tier": "budget-20"},
    )


def test_run_budget_overlay_is_skipped_when_its_provider_has_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    # The overlay switches model only on a provider it can actually call (a key in the env). This used to pass
    # only because an earlier test leaked OPENROUTER_API_KEY into the process.
    from daedalus import llm_config

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    _budget_writer(monkeypatch)
    eff = llm_config.get_config()
    assert eff.get("model") == "owner/pick"
    assert eff.get("effort_skipped") == "no-atomic-route"


def test_get_brain_config_skips_run_budget_overlay(monkeypatch: pytest.MonkeyPatch) -> None:
    from daedalus import llm_config

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")     # the budget writer's provider must be callable
    monkeypatch.setattr(
        llm_config,
        "_resolve_llm_config",
        lambda: {
            "provider": "openrouter",
            "model": "owner/pick",
            "api_key": "k",
            "base_url": "",
            "sdk": "openai",
            "source": "db",
        },
    )
    monkeypatch.setattr(
        "daedalus.run_budget.applied_writer",
        lambda: {
            "model": "moonshotai/kimi-k3",
            "provider": "openrouter",
            "model_source": "run-budget",
            "apply_model": True,
            "tier": "budget-20",
        },
    )
    brain = llm_config.get_brain_config()
    assert brain.get("model") == "owner/pick"
    eff = llm_config.get_config()
    assert eff.get("model") == "moonshotai/kimi-k3"


def test_llm_status_includes_brain_and_overlays(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "daedalus.llm_config.get_brain_config",
        lambda: {"provider": "nvidia", "model": "qwen/x", "api_key": "k", "source": "db"},
    )
    monkeypatch.setattr(
        "daedalus.llm_config.get_config",
        lambda: {"provider": "openrouter", "model": "z/p", "api_key": "k", "source": "x", "effort": "epic"},
    )
    from app.main import app
    from fastapi.testclient import TestClient

    r = TestClient(app).get("/api/v1/llm/status")
    assert r.status_code == 200
    j = r.json()
    assert j["ok"] is True
    assert j["brain"]["provider"] == "nvidia"
    assert j["brain"]["model"] == "qwen/x"
    assert j["overlays"].get("this_turn") is not None


def test_routing_watchdog_surfaces_in_recommendations(monkeypatch: pytest.MonkeyPatch) -> None:
    from daedalus import connectivity, llm_routing_watchdog

    llm_routing_watchdog.note_failure(error="no creds", requested_model="poolside:x", source="t")
    rec = connectivity.recommendations()
    assert any(r.get("id") == "llm-routing-error" for r in rec)


def test_fallback_build_model_tag_uses_brain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "daedalus.llm_config.get_brain_config",
        lambda: {"provider": "novita", "model": "deepseek/deepseek-v3.2", "api_key": "k"},
    )
    from daedalus.build_prompts import fallback_build_model_tag

    tag, _ = fallback_build_model_tag()
    assert tag == "novita:deepseek/deepseek-v3.2"


def test_nvidia_free_policy_keeps_non_nvidia_db_brain(monkeypatch: pytest.MonkeyPatch) -> None:
    from daedalus import llm_config

    monkeypatch.setattr(llm_config, "is_nvidia_free_only", lambda: True)
    monkeypatch.setattr(llm_config, "resolve_provider_api_key", lambda _p: "nvk")
    monkeypatch.setattr(llm_config, "_read_db", lambda: {"provider": "openrouter", "model": "x/y"})

    out = llm_config._apply_nvidia_free_policy(
        {"provider": "openrouter", "model": "x/y", "api_key": "ork"},
    )
    assert out.get("provider") == "openrouter"
