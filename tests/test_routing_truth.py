"""PR2 routing truth: owner overrides really apply, and the local fallback is opt-in and announced."""
from __future__ import annotations

import asyncio
import sys

import pytest
from a0_support import a0_tmp  # noqa: F401

BRAIN = {"provider": "openrouter", "model": "owner/brain", "api_key": "k", "base_url": "http://x",
         "sdk": "openai", "source": "db"}
LOCAL = {"provider": "ollama", "model": "qwen-local", "api_key": "", "base_url": "http://localhost:11434/v1",
         "sdk": "openai", "local": True}
OVERRIDABLE = ("writer", "mentor", "critic", "planner", "tools", "scout", "asset")


@pytest.fixture
def env(a0_tmp, monkeypatch):  # noqa: F811
    from daedalus.harness import gateway

    monkeypatch.setattr("daedalus.llm_config.get_config", lambda: dict(BRAIN))
    for name in ("DAEDALUS_MODEL", "DAEDALUS_RUN_BUDGET", "DAEDALUS_FALLBACK", "ALLTOKEN_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("GROQ_API_KEY", "g")
    return gateway


def _capture(monkeypatch, fail_first=None):
    """Replace the provider call; record each cfg used. ``fail_first`` is raised for non-local configs."""
    seen: list[dict] = []

    async def fake(cfg, **kw):
        seen.append(dict(cfg))
        if fail_first is not None and not cfg.get("local"):
            raise RuntimeError(fail_first)
        return {"reply": "ok", "provider": cfg.get("provider"), "model": cfg.get("model")}, {}

    monkeypatch.setattr("daedalus.llm_client._complete_with_cfg", fake)
    monkeypatch.setattr("daedalus.local_llm.resolve_local_config", lambda *a, **k: dict(LOCAL))
    return seen


def _complete(**kw):
    from daedalus import llm_client

    return asyncio.run(llm_client.complete(system="s", user="u", **kw))


def test_planner_override_changes_the_cfg_used_by_json_calls(env, monkeypatch):
    seen = _capture(monkeypatch)
    _complete(intent="json")
    assert seen[-1]["model"] == "owner/brain"
    env.set_override("planner", "groq:llama-planner")
    _complete(intent="json")
    assert (seen[-1]["provider"], seen[-1]["model"]) == ("groq", "llama-planner")
    _complete(intent="json", model="openrouter:explicit/one")           # an explicit model still wins
    assert seen[-1]["model"] == "explicit/one"
    _complete(intent="chat")                                              # chat keeps following the brain
    assert seen[-1]["model"] == "owner/brain"


def test_planner_override_is_ignored_under_the_env_lock(env, monkeypatch):
    seen = _capture(monkeypatch)
    env.set_override("planner", "groq:llama-planner")
    monkeypatch.setenv("DAEDALUS_MODEL", "pinned/model")
    _complete(intent="json")
    assert seen[-1]["model"] == "owner/brain"


def test_critic_override_is_honoured_on_the_improvements_path(env, monkeypatch):
    from daedalus import improvements

    calls: list[dict] = []

    async def fake_call(intent, messages, **kw):
        calls.append({"intent": intent, **kw})
        from daedalus.harness.gateway import GatewayResult
        return GatewayResult(ok=True, text='{"ok": true}')

    monkeypatch.setattr(env, "call", fake_call)
    monkeypatch.setattr(improvements, "_cred_env", lambda name, default="": "k" if name == "OPENROUTER_API_KEY" else default)
    asyncio.run(improvements.review_gate_critique("do x", "done", [], 1.0))
    assert calls[-1]["route"] is not None                                   # the cheap reviewer default
    env.set_override("critic", "groq:critic-model")
    asyncio.run(improvements.review_gate_critique("do x", "done", [], 1.0))
    assert calls[-1]["intent"] == "critic" and calls[-1]["route"] is None   # gateway resolves the owner's choice
    assert env.resolve("critic").model == "critic-model"


def test_tools_override_reaches_tool_rounds(env):
    from daedalus import llm_client

    assert llm_client._override_model_tag("tools") == ""
    env.set_override("tools", "groq:tool-model")
    assert llm_client._override_model_tag("tools") == "groq:tool-model"


def test_fallback_off_never_retries_locally(env, monkeypatch):
    seen = _capture(monkeypatch, fail_first="HTTP 402 insufficient credits")
    with pytest.raises(RuntimeError):
        _complete(intent="chat", model="groq:big")
    assert [c["provider"] for c in seen] == ["groq"]                        # no local retry
    from daedalus import routing_fallback
    assert routing_fallback.mode() == "off"


def test_fallback_auto_retries_on_credit_errors_and_says_so(env, monkeypatch):
    from daedalus import settings_file

    settings_file.update({"fallback_mode": "auto"})
    seen = _capture(monkeypatch, fail_first="Error code: 402 - insufficient credit")
    out = _complete(intent="chat", model="groq:big")
    assert [c["provider"] for c in seen] == ["groq", "ollama"]
    assert out["routing_fallback"] == {"from": "groq:big", "to": "ollama:qwen-local", "reason": "credits"}


def test_fallback_auto_ignores_unrelated_errors_and_other_triggers(env, monkeypatch):
    from daedalus import settings_file

    settings_file.update({"fallback_mode": "auto"})
    seen = _capture(monkeypatch, fail_first="JSON schema violated by the prompt")
    with pytest.raises(RuntimeError):
        _complete(intent="chat", model="groq:big")
    assert len(seen) == 1
    settings_file.update({"fallback_triggers": "offline"})
    seen.clear()
    _capture_seen = _capture(monkeypatch, fail_first="429 quota exceeded")
    with pytest.raises(RuntimeError):
        _complete(intent="chat", model="groq:big")
    assert len(_capture_seen) == 1                                          # credits error, offline-only trigger
    _capture_seen = _capture(monkeypatch, fail_first="Connection error: name resolution failed")
    out = _complete(intent="chat", model="groq:big")
    assert out["routing_fallback"]["reason"] == "offline"


def test_fallback_ask_does_not_retry_but_reports_availability(env, monkeypatch):
    from daedalus import settings_file

    settings_file.update({"fallback_mode": "ask"})
    seen = _capture(monkeypatch, fail_first="401 unauthorized")
    with pytest.raises(RuntimeError) as info:
        _complete(intent="chat", model="groq:big")
    assert len(seen) == 1
    assert info.value.fallback_available is True and info.value.result["fallback_reason"] == "credits"


def test_fallback_rows_are_in_the_config_view_and_env_locks_the_mode(env, monkeypatch):
    from daedalus.harness import config_view

    rows = {r["key"]: r for r in config_view.config_payload()["settings"]}
    assert rows["fallback.mode"]["effective"] == "off" and rows["fallback.mode"]["choices"] == ["off", "ask", "auto"]
    assert rows["fallback.triggers"]["effective"] == "both"
    config_view.set_value("fallback.mode", "auto")
    assert config_view.view_one(config_view._BY_KEY["fallback.mode"])["effective"] == "auto"
    with pytest.raises(ValueError):
        config_view.set_value("fallback.mode", "sometimes")
    monkeypatch.setenv("DAEDALUS_FALLBACK", "off")
    row = config_view.view_one(config_view._BY_KEY["fallback.mode"])
    assert row["env_locked"] is True and row["effective"] == "off"


def test_env_lock_gives_409_for_every_overridable_intent(env, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    assert set(env.OVERRIDABLE) == set(OVERRIDABLE)
    monkeypatch.setenv("DAEDALUS_MODEL", "pinned/model")
    client = TestClient(app)
    for intent in OVERRIDABLE:
        if intent == "mentor":      # the mentor keeps its own choice (DAEDALUS_MENTOR_MODEL lock), not the brain pin
            continue
        res = client.post("/api/v1/harness/intents", json={"intent": intent, "model": "openrouter:a/b"})
        assert res.status_code == 409, intent
        assert res.json()["error"] == "env_locked"
    assert env.get_overrides() == {}


def test_scout_default_falls_back_to_the_brain_without_an_alltoken_key(env, monkeypatch):
    assert env.model_tag_for("scout") is None and env.model_tag_or_empty("asset") == ""
    res = env.resolve("scout")
    assert res.source == "brain" and res.model == "owner/brain"
    monkeypatch.setenv("ALLTOKEN_API_KEY", "k")
    assert env.model_tag_for("scout") == "alltoken:deepseek-v3.2"
    assert env.resolve("scout").source == "registry-default"
    env.set_override("scout", "groq:mine")
    assert env.model_tag_for("scout") == "groq:mine"
