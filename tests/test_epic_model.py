"""Epic must not swap the owner's model for a weaker or tool-less one (live 2026-11-12: with Haiku 5.5 chosen, epic
picked DeepSeek V3.2, an older catalog model without tool calling, for a build that works through tools)."""
from __future__ import annotations

import pytest

from daedalus import task_effort as te


@pytest.fixture
def cloud(monkeypatch):
    monkeypatch.setattr("daedalus.llm_wardrobe.wants_local", lambda: False)
    monkeypatch.setattr("daedalus.llm_config.resolve_provider_api_key", lambda p: "k")
    monkeypatch.delenv("DAEDALUS_EPIC_MODEL", raising=False)
    monkeypatch.delenv("DAEDALUS_OFFLINE", raising=False)

    def owner(model):
        monkeypatch.setattr("daedalus.llm_config.get_brain_config", lambda: {"provider": "openrouter", "model": model})
    return owner


def _cands(monkeypatch, rows):
    monkeypatch.setattr("daedalus.model_registry.iter_candidates_for_task", lambda *a, **k: rows)


def test_a_model_without_tool_calling_is_never_the_epic_writer(cloud, monkeypatch):
    cloud("")
    _cands(monkeypatch, [{"model_id": "deepseek/deepseek-v3.2", "provider": "openrouter", "supports_tools": False},
                         {"model_id": "anthropic/claude-sonnet-4-5", "provider": "openrouter", "supports_tools": True}])
    assert te.resolve_model({"apply_model": True, "tier": "epic"})["model"] == "anthropic/claude-sonnet-4-5"


def test_an_owner_model_newer_than_the_catalog_is_kept(cloud, monkeypatch):
    cloud("anthropic/claude-haiku-9")                                   # newer than anything the benchmark knows
    _cands(monkeypatch, [{"model_id": "anthropic/claude-sonnet-4-5", "provider": "openrouter", "supports_tools": True}])
    d = te.classify("build an epic rpg world", requested="epic")
    assert d["model"] is None and d["model_source"] == "owner-default" and d["note"] == te.KEPT_OWNER_NOTE
    assert d["world_kit"] is True and d["min_stages"] == 12            # epic still means more work, not a weaker model


def test_a_known_stronger_model_replaces_a_known_weaker_one(cloud, monkeypatch):
    from daedalus.llm_benchmark import _METRICS, _composite_capability

    ranked = sorted(_METRICS, key=lambda m: _composite_capability(_METRICS[m]))
    weak, strong = ranked[0], ranked[-1]
    cloud(weak)
    _cands(monkeypatch, [{"model_id": strong, "provider": "openrouter", "supports_tools": True}])
    assert te.resolve_model({"apply_model": True, "tier": "epic"})["model"] == strong
    cloud(strong)
    _cands(monkeypatch, [{"model_id": weak, "provider": "openrouter", "supports_tools": True}])
    assert te.resolve_model({"apply_model": True, "tier": "epic"})["model"] is None


def test_the_owner_can_name_the_epic_model(cloud, monkeypatch):
    cloud("anthropic/claude-haiku-5.5")
    monkeypatch.setenv("DAEDALUS_EPIC_MODEL", "anthropic/claude-sonnet-5.5")
    monkeypatch.setenv("DAEDALUS_EPIC_PROVIDER", "openrouter")
    out = te.resolve_model({"apply_model": True, "tier": "epic"})
    assert (out["model"], out["provider"], out["model_source"]) == ("anthropic/claude-sonnet-5.5", "openrouter", "owner-epic")
    monkeypatch.delenv("DAEDALUS_EPIC_PROVIDER")
    assert te.resolve_model({"apply_model": True, "tier": "epic"})["provider"] == "openrouter"   # the owner's provider
    monkeypatch.setenv("DAEDALUS_OFFLINE", "1")
    assert te.resolve_model({"apply_model": True, "tier": "epic"}).get("model_source") != "owner-epic"


def test_model_facing_briefings_do_not_pull_the_reply_into_bulgarian():
    """Live 2026-11-12: an English request got a Bulgarian reply; every turn carried a Bulgarian effort briefing."""
    import re

    from daedalus.tool_router import format_system, route

    for tier in te.TIERS:
        assert not re.search(r"[А-Яа-я]", te.briefing({"tier": tier}))
    sys = format_system(route("Build a small web game with a frog", kind="static"), user_message="Build a frog game")
    assert "in English (the owner's request)" in sys


def test_the_quality_critic_answers_in_the_language_of_the_task():
    """Live 2026-11-12: an English Autopilot task stopped with a Bulgarian critic finding in its error."""
    from daedalus import playground_quality as pq

    msgs = pq.build_critic_messages(description="A maze game", kind="static", plan_goals=[], dom_excerpt="",
                                    code_index="", screenshot_b64="")
    assert "in the language of the user's task" in msgs[-1]["content"][0]["text"]


def test_a_known_weaker_owner_model_gets_the_current_stronger_one(cloud, monkeypatch):
    """With Haiku 5.5 known to the benchmark (2026-11-12), epic lifts the writer to Sonnet 5.5, as it promises."""
    from daedalus.model_defaults import OPENROUTER_DEFAULT_MODEL, OPENROUTER_FAST_MODEL

    cloud(OPENROUTER_FAST_MODEL)
    _cands(monkeypatch, [{"model_id": OPENROUTER_DEFAULT_MODEL, "provider": "openrouter", "supports_tools": True}])
    assert te.resolve_model({"apply_model": True, "tier": "epic"})["model"] == OPENROUTER_DEFAULT_MODEL
