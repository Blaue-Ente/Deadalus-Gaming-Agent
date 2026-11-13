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


# ── review fixes, wave 1 ─────────────────────────────────────────────────────────────────────────────────────────
def _as_member(monkeypatch, role="designer"):
    from daedalus import team

    monkeypatch.setattr(team, "current", lambda: team.Member("d1", "Dee", role))


def test_a_members_override_never_uses_the_owners_env_key(env, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "owner-secret")
    _as_member(monkeypatch)
    seen = _capture(monkeypatch)
    out = _complete(intent="json", model="openrouter:x/y")               # caller-chosen: refused, never the owner's key
    assert not seen and out.get("routing_failed") is True
    env.set_override("planner", "openrouter:x/y")
    out = _complete(intent="json")                                        # override: dropped, the brain cfg is kept
    assert seen and all(c.get("api_key") != "owner-secret" for c in seen)
    assert seen[-1]["model"] == "owner/brain"


def test_a_members_tool_override_is_dropped_without_their_key(env, monkeypatch):
    from daedalus import llm_client

    _as_member(monkeypatch)
    over = {"provider": "openrouter", "model": "x/y", "api_key": "owner-secret", "base_url": "http://x", "sdk": "openai"}
    got = llm_client._member_route(over)
    assert got.get("routing_error") and got.get("api_key") != "owner-secret"


def test_the_owner_keeps_the_override_route(env, monkeypatch):
    seen = _capture(monkeypatch)
    env.set_override("planner", "groq:llama-planner")
    _complete(intent="json")
    assert seen[-1]["api_key"] == "g"


def test_a_dead_override_falls_back_to_the_brain_instead_of_failing(env, monkeypatch):
    seen = _capture(monkeypatch)
    env.set_override("planner", "groq:llama-planner")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    out = _complete(intent="json")
    assert not out.get("routing_failed") and out["reply"] == "ok"
    assert seen[-1]["model"] == "owner/brain"
    out = _complete(intent="json", model="groq:explicit")                # a caller-chosen dead model still reports
    assert out.get("routing_failed") is True


def test_fallback_codes_match_whole_numbers_only():
    from daedalus import routing_fallback as rf

    assert rf.classify("you requested 14029 tokens") is None
    assert rf.classify("max 5030 reached") is None
    assert rf.classify("HTTP 429 too many") == "credits"
    assert rf.classify("error code: 503") == "offline"
    assert rf.classify("the quota is gone") == "credits"


def test_an_unparsable_enum_env_pin_does_not_lock(env, monkeypatch):
    from daedalus.harness import config_view

    def row():
        return next(r for r in config_view.config_payload()["settings"] if r["key"] == "fallback.mode")

    monkeypatch.setenv("DAEDALUS_FALLBACK", "yes")
    assert row()["env_locked"] is False and row()["writable"] is True
    monkeypatch.setenv("DAEDALUS_FALLBACK", "auto")
    assert row()["env_locked"] is True and row()["writable"] is False


def test_a_saved_critic_without_a_route_still_runs_the_cheap_reviewer(env, monkeypatch):
    from daedalus import improvements

    calls: list[dict] = []

    async def fake_call(intent, messages, **kw):
        calls.append({"intent": intent, **kw})
        from daedalus.harness.gateway import GatewayResult
        return GatewayResult(ok=True, text='{"ok": true}')

    monkeypatch.setattr(env, "call", fake_call)
    monkeypatch.setattr(improvements, "_cred_env", lambda name, default="": "k" if name == "OPENROUTER_API_KEY" else default)
    env.set_override("critic", "groq:critic-model")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    asyncio.run(improvements.review_gate_critique("do x", "done", [], 1.0))
    assert calls[-1]["route"] is not None


def test_click_handlers_read_currentTarget_before_their_first_await():
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "app" / "static" / "studio-workspace.js").read_text(encoding="utf-8")
    bad = []
    for m in re.finditer(r'addEventListener\("click", async \(ev\) => \{', src):
        end = src.find("\n  });", m.end())
        body = src[m.end():end if end > 0 else len(src)]
        first_await = body.find("await ")
        for c in re.finditer(r"ev\.currentTarget", body):
            if first_await != -1 and c.start() > first_await:
                bad.append(src[:m.start()].count("\n") + 1)
    assert bad == [], f"ev.currentTarget used after an await in handlers at lines {bad}"
