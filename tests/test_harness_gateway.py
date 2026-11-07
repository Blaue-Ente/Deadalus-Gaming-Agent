"""A2: the LLM gateway - resolution, budget, images, typed errors, trace."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import sys
import types

import pytest
from a0_support import a0_tmp  # noqa: F401

PNG = b"\x89PNG\r\n\x1a\n" + b"gateway-test-frame" * 4
PNG_SHA = hashlib.sha256(PNG).hexdigest()
PNG_B64 = base64.b64encode(PNG).decode()

BRAIN = {"provider": "openrouter", "model": "owner/pick", "api_key": "k", "base_url": "http://x",
         "sdk": "openai", "source": "db"}


@pytest.fixture
def gw(a0_tmp, monkeypatch):
    from daedalus.harness import gateway

    monkeypatch.setattr("daedalus.llm_config.get_config", lambda: dict(BRAIN))
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("ALLTOKEN_API_KEY", "k")
    for name in ("DAEDALUS_MODEL", "DAEDALUS_RUN_BUDGET"):
        monkeypatch.delenv(name, raising=False)
    return gateway


def _openai_client(record, text="hello", usage=None, exc=None):
    class Client:
        def __init__(self, **kw):
            record["init"] = kw
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self.create))

        async def create(self, **kw):
            record["body"] = kw
            if exc:
                raise exc
            msg = types.SimpleNamespace(content=text)
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)], usage=usage)

    monkeypatch_target = types.SimpleNamespace(AsyncOpenAI=Client)
    return monkeypatch_target


def _install_openai(monkeypatch, **kw):
    record: dict = {}
    monkeypatch.setitem(sys.modules, "openai", _openai_client(record, **kw))
    return record


def run(coro):
    return asyncio.run(coro)


# ---- resolution ------------------------------------------------------------------------------

def test_default_resolution_matches_todays_models(gw):
    """Per-intent snapshot: with no override every intent resolves exactly as before."""
    assert gw.resolve("persona").model == "owner/pick" and gw.resolve("persona").source == "brain"
    for intent in ("writer", "mentor", "critic", "planner", "navigator", "worker", "tools", "summary"):
        r = gw.resolve(intent)
        assert (r.provider, r.model, r.source) == ("openrouter", "owner/pick", "brain"), intent
    # the two intents that hard-coded a model keep that exact model, now declared in one place
    for intent in ("scout", "asset"):
        r = gw.resolve(intent)
        assert r.source == "registry-default" and r.model == "deepseek-v3.2", (intent, r)


def test_legacy_intent_names_map(gw):
    assert gw.canonical_intent("vision") == "critic"
    assert gw.canonical_intent("chat") == "persona"
    assert gw.canonical_intent("plan") == "planner"
    assert gw.canonical_intent("") == "persona"


def test_owner_override_applies_to_core_intents_only(gw):
    with pytest.raises(ValueError):
        gw.set_override("scout", "openrouter:foo/bar")
    with pytest.raises(ValueError):
        gw.set_override("critic", "not-a-tag")
    gw.set_override("critic", "openrouter:foo/bar")
    r = gw.resolve("critic")
    assert (r.model, r.source) == ("foo/bar", "owner-override")
    assert gw.resolve("vision").model == "foo/bar"          # legacy name resolves to the critic
    assert gw.resolve("planner").model == "owner/pick"       # other intents untouched
    gw.set_override("writer", "openrouter:w/model")
    assert gw.resolve("persona").model == "w/model"          # persona shares the writer selector
    assert gw.get_overrides() == {"critic": "openrouter:foo/bar", "writer": "openrouter:w/model"}
    gw.set_override("critic", "")                            # clearing restores the brain
    assert gw.resolve("critic").source == "brain"


def test_env_lock_beats_override_and_refuses_writes(gw, monkeypatch):
    from daedalus.env_lock import EnvLockedError

    gw.set_override("mentor", "openrouter:foo/bar")
    monkeypatch.setenv("DAEDALUS_MODEL", "pinned/model")
    r = gw.resolve("mentor")
    assert r.source == "env-lock" and r.locked is True
    with pytest.raises(EnvLockedError):
        gw.set_override("mentor", "openrouter:x/y")


def test_call_site_hint_and_slot_route(gw):
    assert gw.resolve("worker", model_hint="openrouter:hint/model").source == "call-site"
    slot = {"provider": "novita", "model": "slot/m", "api_key": "sk", "base_url": "http://n", "sdk": "openai"}
    r = gw.resolve("worker", route=slot)
    assert (r.provider, r.model, r.source) == ("novita", "slot/m", "slot")


def test_unreachable_explicit_model_is_a_routing_error(gw, monkeypatch):
    monkeypatch.delenv("ALLTOKEN_API_KEY", raising=False)
    monkeypatch.setattr("daedalus.llm_config.resolve_provider_api_key", lambda *a, **k: "", raising=False)
    result = run(gw.call("scout", [{"role": "user", "content": "x"}]))
    assert result.ok is False and result.error.type == "routing"


# ---- budget ------------------------------------------------------------------------------------

def test_exhausted_budget_blocks_every_intent_before_any_sdk(gw, monkeypatch):
    from daedalus import run_budget

    monkeypatch.setenv("DAEDALUS_RUN_BUDGET", "5")
    run_budget.note_usage({"cost_usd": 6, "cost_known": True}, model="m", role="writer")
    assert run_budget.exhausted()

    class Boom:
        def __init__(self, *a, **k):
            raise AssertionError("SDK must not be built once the budget is spent")

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=Boom))
    for intent in gw.INTENTS:
        out = run(gw.call(intent, [{"role": "user", "content": "hi"}]))
        assert out.ok is False and out.budget_stopped is True, intent
        assert out.error.type == "budget_blocked" and out.text == ""


def test_local_provider_is_not_blocked_by_budget(gw, monkeypatch):
    from daedalus import run_budget

    monkeypatch.setenv("DAEDALUS_RUN_BUDGET", "5")
    run_budget.note_usage({"cost_usd": 6, "cost_known": True}, model="m", role="writer")
    monkeypatch.setattr("daedalus.llm_config.get_config", lambda: {**BRAIN, "provider": "ollama", "api_key": ""})
    _install_openai(monkeypatch, text="local ok")
    out = run(gw.call("persona", [{"role": "user", "content": "hi"}]))
    assert out.ok is True and out.text == "local ok"


def test_unknown_cost_is_counted_and_flagged(gw, monkeypatch):
    from daedalus import run_budget

    monkeypatch.setenv("DAEDALUS_RUN_BUDGET", "20")
    _install_openai(monkeypatch, usage=types.SimpleNamespace(prompt_tokens=3, completion_tokens=2))
    out = run(gw.call("persona", [{"role": "user", "content": "hi"}]))
    assert out.ok and out.cost_known is False and out.cost_usd is None
    assert out.usage == {"prompt_tokens": 3, "completion_tokens": 2}
    ledger = run_budget.ledger()
    assert ledger["calls"] == 1 and ledger.get("unknown_cost_calls") == 1 and ledger["spent_usd"] == 0


def test_metering_failure_does_not_fail_the_call(gw, monkeypatch):
    monkeypatch.setattr("daedalus.run_budget.note_usage", lambda *a, **k: (_ for _ in ()).throw(OSError("store")))
    _install_openai(monkeypatch)
    assert run(gw.call("persona", [{"role": "user", "content": "hi"}])).ok is True


# ---- images --------------------------------------------------------------------------------------

def test_normalise_accepts_every_image_shape_and_keeps_bytes(gw):
    shapes = [
        gw.image_part(media_type="image/png", data=PNG_B64),
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PNG_B64}"}},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG_B64}},
    ]
    for shape in shapes:
        content = gw.normalise_content([gw.text_part("look"), shape])
        assert [p["type"] for p in content] == ["text", "image"]
        assert content[1]["sha256"] == PNG_SHA and base64.b64decode(content[1]["data"]) == PNG


def test_text_only_lists_collapse_and_remote_urls_are_not_fetched(gw):
    assert gw.normalise_content([gw.text_part("a"), gw.text_part("b")]) == "a\nb"
    assert gw.normalise_content("plain") == "plain"
    out = gw.normalise_content([{"type": "image_url", "image_url": {"url": "https://example.test/x.png"}},
                                gw.text_part("t")])
    assert out == "t"  # a URL the gateway does not hold bytes for is dropped, never stringified into text


def test_image_round_trip_openai_and_hash_reaches_trace(gw, monkeypatch):
    record = _install_openai(monkeypatch, text="I see a frame")
    events = []
    gw.add_trace_sink(events.append)
    try:
        msg = [{"role": "user", "content": [gw.text_part("what is on screen?"), gw.image_part(data=PNG_B64)]}]
        out = run(gw.call("critic", msg, system="You are a critic."))
    finally:
        gw.remove_trace_sink(events.append)
    assert out.ok and out.text == "I see a frame" and out.image_sha256 == [PNG_SHA]
    sent = record["body"]["messages"]
    assert sent[0] == {"role": "system", "content": "You are a critic."}
    parts = sent[1]["content"]
    assert parts[0] == {"type": "text", "text": "what is on screen?"}
    url = parts[1]["image_url"]["url"]
    assert url.startswith("data:image/png;base64,") and base64.b64decode(url.split(",", 1)[1]) == PNG
    assert "str(" not in repr(sent) and "{'type'" not in str(sent[1]["content"][0]["text"])
    assert events and events[0]["image_sha256"] == [PNG_SHA] and events[0]["intent"] == "critic"
    assert "PNG" not in repr(events[0])  # the trace carries hashes, never image bytes


def test_image_round_trip_anthropic_with_prompt_cache(gw, monkeypatch):
    seen = {}

    class Msgs:
        async def create(self, **kw):
            seen.update(kw)
            return types.SimpleNamespace(content=[types.SimpleNamespace(type="text", text="ok")], usage=None)

    class Anth:
        def __init__(self, **kw):
            self.messages = Msgs()

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(AsyncAnthropic=Anth))
    monkeypatch.setattr("daedalus.llm_config.get_config", lambda: {**BRAIN, "provider": "anthropic", "sdk": "anthropic"})
    msg = [{"role": "user", "content": [gw.text_part("look"), {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PNG_B64}"}}]}]
    out = run(gw.call("mentor", msg, system="sys"))
    assert out.ok and out.text == "ok"
    block = seen["messages"][0]["content"][1]
    assert block == {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PNG_B64}}
    assert seen["system"][0]["cache_control"] == {"type": "ephemeral"}
    run(gw.call("mentor", msg, system="sys", use_cache=False))
    assert seen["system"] == "sys"


# ---- typed errors --------------------------------------------------------------------------------

@pytest.mark.parametrize("exc,etype", [
    (RuntimeError("Error code: 401 - invalid_api_key"), "auth"),
    (RuntimeError("429 Too Many Requests"), "quota"),
    (RuntimeError("insufficient credits"), "quota"),
    (asyncio.TimeoutError(), "timeout"),
    (RuntimeError("request timed out"), "timeout"),
    (RuntimeError("this model does not support image input"), "unsupported_modality"),
    (RuntimeError("boom"), "provider_error"),
])
def test_provider_failures_become_typed_errors_not_fake_text(gw, monkeypatch, exc, etype):
    _install_openai(monkeypatch, exc=exc)
    out = run(gw.call("persona", [{"role": "user", "content": "hi"}]))
    assert out.ok is False and out.error.type == etype and out.text == ""
    assert etype in gw.ERROR_TYPES


def test_missing_key_is_an_auth_error_without_calling_a_provider(gw, monkeypatch):
    monkeypatch.setattr("daedalus.llm_config.get_config", lambda: {**BRAIN, "api_key": ""})

    class Boom:
        def __init__(self, *a, **k):
            raise AssertionError("no provider call without a key")

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=Boom))
    out = run(gw.call("persona", [{"role": "user", "content": "hi"}]))
    assert out.error.type == "auth"


# ---- trace, sync bridge, overlay, shims --------------------------------------------------------------

def test_trace_event_per_call_and_broken_sink_is_harmless(gw, monkeypatch):
    _install_openai(monkeypatch)
    events = []

    def broken(_e):
        raise RuntimeError("sink down")

    gw.add_trace_sink(broken)
    gw.add_trace_sink(events.append)
    try:
        out = run(gw.call("planner", [{"role": "user", "content": "hi"}], slug="s1", run_id="r1", client_id="t"))
    finally:
        gw.remove_trace_sink(broken)
        gw.remove_trace_sink(events.append)
    assert out.ok and len(events) == 1
    ev = events[0]
    assert (ev["intent"], ev["model"], ev["slug"], ev["run_id"], ev["ok"]) == ("planner", "owner/pick", "s1", "r1", True)
    assert "hi" not in repr(ev)  # message content is never traced


def test_call_sync_works_with_and_without_a_running_loop(gw, monkeypatch):
    _install_openai(monkeypatch, text="sync")
    assert gw.call_sync("persona", [{"role": "user", "content": "x"}]).text == "sync"

    async def inside():
        return gw.call_sync("persona", [{"role": "user", "content": "x"}]).text

    assert run(inside()) == "sync"


def test_explicit_overlay_is_applied_without_a_contextvar(gw, monkeypatch):
    _install_openai(monkeypatch)
    out = run(gw.call("writer", [{"role": "user", "content": "x"}],
                      overlay={"model": "ov/model", "provider": "openrouter", "tier": "epic"}))
    assert out.model == "ov/model"


def test_legacy_shims(gw, monkeypatch):
    _install_openai(monkeypatch, text="reply")
    ok = run(gw.call("persona", [{"role": "user", "content": "x"}]))
    assert gw.to_text_or_raise(ok) == "reply"
    legacy = gw.to_legacy_openai(ok)
    assert legacy["ok"] and legacy["reply"] == "reply" and legacy["choices"][0]["message"]["content"] == "reply"

    _install_openai(monkeypatch, exc=RuntimeError("429 rate limit"))
    bad = run(gw.call("persona", [{"role": "user", "content": "x"}]))
    with pytest.raises(gw.GatewayCallError) as info:
        gw.to_text_or_raise(bad)
    assert "429" in str(info.value) and info.value.error.type == "quota"  # old rate-limit string check still works

    from daedalus import run_budget
    monkeypatch.setenv("DAEDALUS_RUN_BUDGET", "5")
    run_budget.note_usage({"cost_usd": 6, "cost_known": True}, model="m", role="writer")
    stopped = run(gw.call("persona", [{"role": "user", "content": "x"}]))
    assert gw.to_text_or_raise(stopped) == run_budget.stop_text()  # legacy: the stop text is the reply
    assert gw.to_legacy_openai(stopped)["budget_stopped"] is True


def test_path_flag(gw, monkeypatch):
    assert gw.path_enabled("mesh") is True
    monkeypatch.setenv("DAEDALUS_GATEWAY_MESH", "0")
    assert gw.path_enabled("mesh") is False


# ---- migration 1: mesh.route keeps images in standalone mode -----------------------------------------

def test_mesh_route_sends_images_as_image_parts(gw, monkeypatch):
    from daedalus import daedalus_mesh as mesh

    monkeypatch.setattr(mesh, "use_mesh_router", lambda: False)
    record = _install_openai(monkeypatch, text="critic reply")
    msgs = [{"role": "system", "content": "judge"},
            {"role": "user", "content": [gw.text_part("rate this"), {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PNG_B64}"}}]}]
    out = run(mesh.route(messages=msgs, intent="vision", max_tokens=300))
    assert out["ok"] and out["reply"] == "critic reply"
    sent = record["body"]["messages"]
    assert sent[0] == {"role": "system", "content": "judge"}
    assert sent[1]["content"][1]["image_url"]["url"] == f"data:image/png;base64,{PNG_B64}"  # bytes intact
    assert record["body"]["max_tokens"] == 300


def test_mesh_route_text_only_still_uses_llm_client(gw, monkeypatch):
    from daedalus import daedalus_mesh as mesh

    monkeypatch.setattr(mesh, "use_mesh_router", lambda: False)
    seen = {}

    async def fake_complete(**kw):
        seen.update(kw)
        return {"ok": True, "reply": "plain"}

    monkeypatch.setattr("daedalus.llm_client.complete", fake_complete)
    out = run(mesh.route(messages=[{"role": "user", "content": "hi"}], intent="chat"))
    assert out["reply"] == "plain" and seen["user"] == "hi"


def test_mesh_route_flag_off_restores_the_old_path(gw, monkeypatch):
    from daedalus import daedalus_mesh as mesh

    monkeypatch.setattr(mesh, "use_mesh_router", lambda: False)
    monkeypatch.setenv("DAEDALUS_GATEWAY_MESH", "0")
    seen = {}

    async def fake_complete(**kw):
        seen.update(kw)
        return {"ok": True, "reply": "old"}

    monkeypatch.setattr("daedalus.llm_client.complete", fake_complete)
    msgs = [{"role": "user", "content": [gw.text_part("rate this"), gw.image_part(data=PNG_B64)]}]
    run(mesh.route(messages=msgs, intent="vision"))
    assert "image omitted" in seen["user"] and "{'type'" not in seen["user"]  # readable, never a repr


def test_split_messages_never_dumps_a_python_repr(gw):
    from daedalus.daedalus_mesh import _split_messages

    system, user = _split_messages([
        {"role": "system", "content": [{"type": "text", "text": "sys"}]},
        {"role": "user", "content": [{"type": "text", "text": "hello"}, {"type": "image_url", "image_url": {"url": "data:x"}}]},
    ])
    assert system == "sys" and user.startswith("hello") and "{" not in user


# ---- migration 2: the persona path ----------------------------------------------------------------------

def test_persona_path_uses_the_owner_override_and_sends_images(gw, monkeypatch):
    from daedalus import agent

    record = _install_openai(monkeypatch, text="persona reply")
    gw.set_override("writer", "openrouter:writer/pick")
    msgs = [{"role": "user", "content": [gw.text_part("hi"), {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PNG_B64}"}}]}]
    out = run(agent._call_claude_primary("persona system", msgs))
    assert out == "persona reply"
    assert record["body"]["model"] == "writer/pick"
    assert record["body"]["messages"][1]["content"][1]["image_url"]["url"].endswith(PNG_B64)


def test_persona_missing_key_still_raises_daedalus_error(gw, monkeypatch):
    from daedalus import agent

    monkeypatch.setattr("daedalus.llm_config.get_config", lambda: {**BRAIN, "api_key": ""})
    with pytest.raises(agent.DaedalusError):
        run(agent._call_claude_primary("s", [{"role": "user", "content": "x"}]))


def test_persona_rate_limit_still_triggers_the_fallback_chain(gw, monkeypatch):
    from daedalus import agent, improvements

    _install_openai(monkeypatch, exc=RuntimeError("Error code: 429 rate limit exceeded"))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("NOVITA_API_KEY", raising=False)
    # primary fails with a 429-looking error; with no alternates configured the chain reports exhaustion
    with pytest.raises(RuntimeError, match="Rate-limited by primary and all fallbacks failed"):
        run(improvements.call_with_fallback(agent._call_claude_primary, "s", [{"role": "user", "content": "x"}]))


def test_persona_no_image_model_is_retried_without_frames(gw, monkeypatch):
    from daedalus import agent

    calls = []

    class Client:
        def __init__(self, **kw):
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self.create))

        async def create(self, **kw):
            calls.append(kw["messages"])
            if any(isinstance(m["content"], list) for m in kw["messages"]):
                raise RuntimeError("this model does not support image input")
            msg = types.SimpleNamespace(content="text only ok")
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)], usage=None)

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=Client))
    msgs = [{"role": "user", "content": [gw.text_part("hi"), gw.image_part(data=PNG_B64)]}]
    assert run(agent._call_claude("sys", msgs)) == "text only ok"
    assert len(calls) == 2  # first with the frame, then without: explicit, one retry


# ---- migrations 3-6: fast path, navigator, continuity, reviewer -----------------------------------------------

def test_cheap_route_is_provider_aware_and_none_without_keys(gw, monkeypatch):
    for k in ("NVIDIA_API_KEY", "OPENROUTER_API_KEY", "NOVITA_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert gw.cheap_route() is None
    monkeypatch.setenv("NOVITA_API_KEY", "n")
    r = gw.cheap_route()
    assert (r["provider"], r["model"], r["api_key"]) == ("novita", "deepseek/deepseek-v3.2", "n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "o")
    assert gw.cheap_route()["provider"] == "openrouter"
    monkeypatch.setenv("NVIDIA_API_KEY", "v")
    assert gw.cheap_route()["model"] == "nvidia/nemotron-3.5-lightning-30b-a3b"


def test_cheap_helpers_obey_the_run_budget(gw, monkeypatch):
    """The wrap-up/navigator helpers used to bypass the budget; through the gateway they stop."""
    from daedalus import navigator, run_budget

    monkeypatch.setenv("NOVITA_API_KEY", "n")
    monkeypatch.setenv("DAEDALUS_RUN_BUDGET", "5")
    run_budget.note_usage({"cost_usd": 6, "cost_known": True}, model="m", role="writer")

    class Boom:
        def __init__(self, *a, **k):
            raise AssertionError("no provider call once the budget is spent")

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=Boom, OpenAI=Boom))
    assert navigator._call_haiku_sync("sys", "user") == ""


def test_navigator_cheap_call_uses_the_gateway_and_flag_restores_old_path(gw, monkeypatch):
    from daedalus import navigator

    for k in ("NVIDIA_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NOVITA_API_KEY", "n")
    record = _install_openai(monkeypatch, text="nav reply")
    assert navigator._call_haiku_sync("sys", "user text") == "nav reply"
    assert record["body"]["model"] == "deepseek/deepseek-v3.2"
    assert record["init"]["base_url"] == "https://api.novita.ai/v3/openai"

    # rollback flag: the original sync SDK client is used instead
    monkeypatch.setenv("DAEDALUS_GATEWAY_NAVIGATOR", "0")
    seen = {}

    class Sync:
        def __init__(self, **kw):
            seen["init"] = kw
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self.create))

        def create(self, **kw):
            seen["body"] = kw
            msg = types.SimpleNamespace(content="legacy nav")
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)])

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=Sync, AsyncOpenAI=None))
    assert navigator._call_haiku_sync("sys", "user text") == "legacy nav"


def test_continuity_keeps_its_exact_models_through_the_gateway(gw, monkeypatch):
    from daedalus import continuity

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("NOVITA_API_KEY", "n")
    record = _install_openai(monkeypatch, text="digest")
    assert continuity._llm_call("sys", "user") == "digest"
    assert record["body"]["model"] == "inclusionai/ling-2.6-flash" and record["body"]["max_tokens"] == 1024
    monkeypatch.delenv("NOVITA_API_KEY")
    monkeypatch.setenv("OPENROUTER_API_KEY", "o")
    continuity._llm_call("sys", "user")
    assert record["body"]["model"] == "meta-llama/llama-3.3-70b-instruct"
    monkeypatch.delenv("OPENROUTER_API_KEY")
    assert continuity._llm_call("sys", "user") == ""  # no provider configured: empty, as before


def test_review_gate_uses_the_gateway_and_skips_on_budget(gw, monkeypatch):
    from daedalus import improvements, run_budget

    monkeypatch.setenv("OPENROUTER_API_KEY", "o")
    monkeypatch.delenv("NOVITA_API_KEY", raising=False)
    record = _install_openai(monkeypatch, text='{"ok": false, "issue": "promised, not done", "suggested_fix": "do it"}')
    out = run(improvements.review_gate_critique("do x", "done", [{"tool": "t", "ok": True}], 1.0))
    assert out["ok"] is False and out["issue"] == "promised, not done"
    assert record["body"]["model"] == "anthropic/claude-haiku-4-5"

    monkeypatch.setenv("DAEDALUS_RUN_BUDGET", "5")
    run_budget.note_usage({"cost_usd": 6, "cost_known": True}, model="m", role="writer")
    skipped = run(improvements.review_gate_critique("do x", "done", [], 1.0))
    assert skipped["ok"] is True and skipped["skipped"] == "budget"


def test_every_raw_provider_client_in_the_chat_path_sits_behind_a_gateway_flag(gw):
    """Structural guard for the fast path and wrap-up in agent.py: a raw AsyncOpenAI(...) may only remain
    inside the rollback branches, so the gateway (budget, metering, owner model) cannot be bypassed."""
    import ast
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "daedalus" / "agent.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    raw = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "AsyncOpenAI"]
    assert len(raw) >= 4
    unguarded = []
    for call in raw:
        guarded, node = False, call
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in ("_call_claude_primary",):
                guarded = True  # rollback body behind path_enabled("persona") at the top of the function
                break
            if isinstance(node, (ast.If, ast.IfExp)) and any(
                tok in ast.unparse(node.test) for tok in ("_gw_fast", "_gw2", "path_enabled")
            ):
                guarded = True
                break
        if not guarded:
            unguarded.append(call.lineno)
    assert unguarded == []


# ---- migrations 4-5: mentor director and worker slots ---------------------------------------------------------------

def test_mentor_director_goes_through_the_gateway_with_json_mode(gw, monkeypatch):
    from daedalus import mentor_helm

    monkeypatch.setattr("daedalus.llm_config.resolve_provider_api_key", lambda p: "mkey")
    record = _install_openai(monkeypatch, text='{"action": "continue"}')
    out = run(mentor_helm._complete_director("openrouter", "mentor/model", '{"digest": 1}'))
    assert out == '{"action": "continue"}'
    body = record["body"]
    assert body["model"] == "mentor/model" and body["temperature"] == 0.1
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["role"] == "system" and body["messages"][1] == {"role": "user", "content": '{"digest": 1}'}
    assert record["init"]["base_url"] == "https://openrouter.ai/api/v1"  # the provider's default endpoint


def test_mentor_brain_choice_uses_the_configured_endpoint(gw, monkeypatch):
    from daedalus import mentor_helm

    monkeypatch.setattr(mentor_helm, "get_choice", lambda: {"id": "brain"})
    record = _install_openai(monkeypatch)
    run(mentor_helm._complete_director("openrouter", "owner/pick", "{}"))
    assert record["init"]["base_url"] == "http://x" and record["init"]["api_key"] == "k"  # BRAIN config, not the default URL


def test_mentor_receives_frames_as_real_image_parts(gw, monkeypatch):
    from daedalus import mentor_helm

    monkeypatch.setattr("daedalus.llm_config.resolve_provider_api_key", lambda p: "mkey")
    record = _install_openai(monkeypatch, text="{}")
    run(mentor_helm._complete_director("openrouter", "m/m", '{"digest": 1}', frames=[gw.image_part(data=PNG_B64)]))
    content = record["body"]["messages"][1]["content"]
    assert content[0] == {"type": "text", "text": '{"digest": 1}'}
    assert base64.b64decode(content[1]["image_url"]["url"].split(",", 1)[1]) == PNG


def test_mentor_budget_stop_raises_and_flag_restores_old_path(gw, monkeypatch):
    from daedalus import mentor_helm, run_budget

    monkeypatch.setattr("daedalus.llm_config.resolve_provider_api_key", lambda p: "mkey")
    monkeypatch.setenv("DAEDALUS_RUN_BUDGET", "5")
    run_budget.note_usage({"cost_usd": 6, "cost_known": True}, model="m", role="writer")
    with pytest.raises(gw.GatewayCallError) as info:
        run(mentor_helm._complete_director("openrouter", "m/m", "{}"))
    assert info.value.error.type == "budget_blocked"

    monkeypatch.setenv("DAEDALUS_RUN_BUDGET", "0")
    monkeypatch.setenv("DAEDALUS_GATEWAY_MENTOR", "0")
    record = _install_openai(monkeypatch, text="legacy")
    assert run(mentor_helm._complete_director("openrouter", "m/m", "{}")) == "legacy"
    assert record["body"]["response_format"] == {"type": "json_object"}


def _slot(model="worker/m", provider="openrouter"):
    return {"model": model, "provider": provider, "sdk": "openai", "api_key": "wk", "base_url": "http://w", "max_tokens": 500}


def test_worker_direct_branch_goes_through_the_gateway(gw, monkeypatch):
    from daedalus import daedalus_mesh as dm, workers

    monkeypatch.setattr(dm, "should_route_worker_via_mesh", lambda provider, model: False)
    record = _install_openai(monkeypatch, text="worker answer")
    out = run(workers._call_worker_single("deep", "do it", _slot()))
    assert out["ok"] is True and out["result"] == "worker answer"
    assert record["body"]["max_tokens"] == 500 and record["init"]["base_url"] == "http://w"
    assert record["body"]["messages"][1] == {"role": "user", "content": "do it"}

    run(workers._call_worker_single("analysis", "think", _slot("o3-mini")))
    assert record["body"]["max_completion_tokens"] == 2000 and "max_tokens" not in record["body"]  # reasoning budget


def test_worker_rate_limit_retries_then_succeeds_and_failure_is_not_ok(gw, monkeypatch):
    from daedalus import daedalus_mesh as dm, workers

    monkeypatch.setattr(dm, "should_route_worker_via_mesh", lambda provider, model: False)
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr(workers.asyncio, "sleep", fake_sleep)
    attempts = {"n": 0}

    class Client:
        def __init__(self, **kw):
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self.create))

        async def create(self, **kw):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise RuntimeError("Error 429: rate limit")
            msg = types.SimpleNamespace(content="finally")
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)], usage=None)

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=Client))
    out = run(workers._call_worker_single("deep", "t", _slot()))
    assert out["ok"] and out["result"] == "finally" and sleeps == [8, 16] and attempts["n"] == 3

    _install_openai(monkeypatch, exc=RuntimeError("boom"))
    bad = run(workers._call_worker_single("deep", "t", _slot()))
    assert bad["ok"] is False and "boom" in bad["result"]


def test_worker_obeys_the_run_budget_and_flag_restores_old_path(gw, monkeypatch):
    from daedalus import daedalus_mesh as dm, run_budget, workers

    monkeypatch.setattr(dm, "should_route_worker_via_mesh", lambda provider, model: False)
    monkeypatch.setenv("DAEDALUS_RUN_BUDGET", "5")
    run_budget.note_usage({"cost_usd": 6, "cost_known": True}, model="m", role="writer")

    class Boom:
        def __init__(self, *a, **k):
            raise AssertionError("no provider call once the budget is spent")

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=Boom))
    out = run(workers._call_worker_single("deep", "t", _slot()))
    assert out["ok"] is False and "blocked" in out["result"]

    monkeypatch.setenv("DAEDALUS_RUN_BUDGET", "0")
    monkeypatch.setenv("DAEDALUS_GATEWAY_WORKERS", "0")
    record = _install_openai(monkeypatch, text="old path")
    assert run(workers._call_worker_single("deep", "t", _slot()))["result"] == "old path"
    assert record["body"]["max_tokens"] == 500
