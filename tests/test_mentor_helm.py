"""Mentor director LLM — catalog, budget, JSON parse. No live LLM."""
from __future__ import annotations

import asyncio
import json

from daedalus.build_loop import BuildLoop
from daedalus.tool_router import route

from tests.test_mentor import LUMEN, SCAFFOLD


def _data(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path))
    monkeypatch.delenv("DAEDALUS_MENTOR_MODEL", raising=False)
    monkeypatch.delenv("DAEDALUS_MENTOR_PROVIDER", raising=False)
    monkeypatch.delenv("DAEDALUS_MENTOR_DAY_CAP", raising=False)
    monkeypatch.setenv("DAEDALUS_MENTOR_LLM", "1")
    from daedalus.paths import data_root
    data_root.cache_clear()


def test_catalog_has_recommended_and_rules():
    from daedalus import mentor_helm
    ids = {row["id"] for row in mentor_helm.CATALOG}
    assert "or-claude-sonnet" in ids
    assert "or-gpt-4o" in ids
    assert "novita-qwen-coder" in ids
    assert "rules" in ids
    assert "brain" in ids
    assert "auto" in ids
    rec = [r for r in mentor_helm.CATALOG if r.get("recommended")]
    assert rec[0]["id"] == "or-claude-sonnet"


def test_recommend_sonnet_when_openrouter(tmp_path, monkeypatch):
    _data(tmp_path, monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.delenv("NOVITA_API_KEY", raising=False)
    from daedalus import mentor_helm
    rec = mentor_helm.recommend()
    assert rec is not None
    assert rec["id"] == "or-claude-sonnet"
    choice = mentor_helm.get_choice()
    assert choice["id"] == "or-claude-sonnet"
    assert choice["source"] == "auto"


def test_set_choice_custom_and_unknown(tmp_path, monkeypatch):
    _data(tmp_path, monkeypatch)
    from daedalus import mentor_helm
    got = mentor_helm.set_choice({"provider": "openrouter", "model": "openai/gpt-4o"})
    assert got["id"] == "custom"
    assert got["model"] == "openai/gpt-4o"
    rules = mentor_helm.set_choice({"choice_id": "rules"})
    assert rules["id"] == "rules"
    try:
        mentor_helm.set_choice({"choice_id": "no-such-model"})
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "unknown mentor choice_id" in str(exc)


def test_day_cap_skips_llm(tmp_path, monkeypatch):
    _data(tmp_path, monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    from daedalus import mentor_helm
    mentor_helm.set_choice({"choice_id": "or-claude-sonnet"})
    mentor_helm.set_day_token_cap(10)
    mentor_helm.record_spend(10)
    inspect = {"flags": {}, "kind": "vite", "last_fails": []}
    rules = {
        "ok": True,
        "action": "call_tool",
        "next_tool": "browser",
        "next_args": {},
        "reason": "open",
        "forbid_tools": [],
        "blockers": [],
    }
    assert mentor_helm.skip_reason(inspect, rules, turn_calls=0) == "day_cap"


def test_llm_disabled_env(tmp_path, monkeypatch):
    _data(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_MENTOR_LLM", "0")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    from daedalus import mentor_helm
    mentor_helm.set_choice({"choice_id": "or-claude-sonnet"})
    inspect = {"flags": {}, "kind": "vite"}
    rules = {"ok": True, "action": "call_tool", "next_tool": "write_file", "next_args": {}, "reason": "x", "forbid_tools": [], "blockers": []}
    assert mentor_helm.skip_reason(inspect, rules, turn_calls=0) == "llm_disabled"


def test_parse_director_json_fenced_and_reason_cap():
    from daedalus import mentor_helm
    blob = '```json\n{"action":"call_tool","next_tool":"browser","next_args":{"slug":"x"},"reason":"' + ("n" * 200) + '","forbid_tools":["generate_image"],"blockers":["blank"]}\n```'
    parsed = mentor_helm.parse_director_json(blob)
    assert parsed is not None
    assert parsed["next_tool"] == "browser"
    assert parsed["next_args"]["slug"] == "x"
    assert len(parsed["reason"]) == 140
    assert parsed["forbid_tools"] == ["generate_image"]
    assert mentor_helm.parse_director_json("not json") is None


def test_thinking_warn():
    from daedalus import mentor_helm
    assert mentor_helm.thinking_warn("anthropic/claude-sonnet-4-5") is None
    assert mentor_helm.thinking_warn("moonshotai/kimi-k2-thinking") is not None


def test_scaffold_consult_does_not_call_llm(tmp_path, monkeypatch):
    _data(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_MENTOR", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    from daedalus import mentor, mentor_helm

    mentor_helm.set_choice({"choice_id": "or-claude-sonnet"})
    called: list[int] = []

    async def boom(*_a, **_k):
        called.append(1)
        raise AssertionError("director must not run on Vite scaffold")

    monkeypatch.setattr("daedalus.mentor_helm._complete_director", boom)
    slug = "lumen-bloom"
    root = tmp_path / slug
    (root / "src").mkdir(parents=True)
    (root / "index.html").write_text(
        '<!doctype html><canvas id="lumen-canvas"></canvas><script type="module" src="/src/main.js"></script>',
        encoding="utf-8",
    )
    (root / "src" / "main.js").write_text(SCAFFOLD, encoding="utf-8")
    monkeypatch.setattr("daedalus.playground.project_dir", lambda s: root)
    monkeypatch.setattr("daedalus.mentor._rag_hits", lambda q: [])
    loop = BuildLoop(route(LUMEN), LUMEN)
    loop.slug = slug
    loop.kind = "vite"
    inspect = mentor.inspect_digest(loop)
    rules = mentor.steer_payload(mentor.steer(loop))
    assert inspect["flags"]["scaffold_vite_app_div"] is True
    out = asyncio.run(mentor_helm.consult(inspect, rules))
    assert called == []
    assert out.get("llm_skip") == "rules_unambiguous"
    assert out.get("next_tool") == "write_file"


def test_consult_parses_llm_json_without_network(tmp_path, monkeypatch):
    _data(tmp_path, monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    from daedalus import mentor_helm

    mentor_helm.set_choice({"choice_id": "or-claude-sonnet"})

    async def fake(_provider, _model, _user):
        return json.dumps({
            "action": "call_tool",
            "next_tool": "browser",
            "next_args": {"slug": "lumen-bloom", "action": "goto"},
            "reason": "Open the running game.",
            "forbid_tools": ["generate_image"],
            "blockers": [],
        })

    monkeypatch.setattr("daedalus.mentor_helm._complete_director", fake)
    inspect = {
        "kind": "vite",
        "scaffold": False,
        "loop_ok": True,
        "has_src_main": True,
        "has_index_html": True,
        "play_url": "",
        "proof_blank": False,
        "flags": {"entry_boots": True},
        "last_fails": [],
        "native_hint": "",
    }
    rules = {
        "ok": True,
        "action": "call_tool",
        "next_tool": "write_file",
        "next_args": {"path": "src/main.js"},
        "reason": "keep writing",
        "forbid_tools": ["generate_image"],
        "blockers": [],
    }
    out = asyncio.run(mentor_helm.consult(inspect, rules))
    assert out["source"] == "llm"
    assert out["next_tool"] == "browser"
    assert "generate_image" in out["forbid_tools"]
    spend = mentor_helm.spend_today()
    assert spend["calls"] == 1
    assert spend["tokens"] > 0


def test_refresh_helm_caches_fingerprint(tmp_path, monkeypatch):
    _data(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_MENTOR", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    from daedalus import mentor, mentor_helm

    mentor_helm.set_choice({"choice_id": "or-claude-sonnet"})
    n = {"calls": 0}

    async def fake(_provider, _model, _user):
        n["calls"] += 1
        return '{"action":"call_tool","next_tool":"run_command","next_args":{"cmd":"npm run build"},"reason":"Build.","forbid_tools":[],"blockers":[]}'

    monkeypatch.setattr("daedalus.mentor_helm._complete_director", fake)
    loop = BuildLoop(route(LUMEN), LUMEN)
    loop.slug = "x"
    loop.kind = "vite"
    monkeypatch.setattr("daedalus.mentor.inspect_project", lambda *a, **k: mentor.ProjectView(
        slug="x", kind="vite", exists=True, index_html="<canvas>", main_js="new Phaser.Game({})",
        has_canvas=True, has_phaser_boot=True,
    ))
    monkeypatch.setattr("daedalus.mentor._rag_hits", lambda q: [])
    first = asyncio.run(mentor.refresh_helm(loop))
    second = asyncio.run(mentor.refresh_helm(loop))
    assert n["calls"] == 1
    assert first is second
    assert loop.mentor_llm_calls == 1
    assert loop.mentor_steer["next_tool"] == "run_command"


def test_http_choice_and_status_catalog(tmp_path, monkeypatch):
    _data(tmp_path, monkeypatch)
    monkeypatch.delenv("DAEDALUS_MENTOR", raising=False)
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    got = client.get("/api/v1/daedalus/mentor")
    assert got.status_code == 200
    body = got.json()
    assert "llm" in body
    assert isinstance(body["llm"]["catalog"], list)
    assert body["llm"]["spend"]["cap"] == 40000
    posted = client.post("/api/v1/daedalus/mentor", json={"choice_id": "rules"})
    assert posted.status_code == 200
    assert posted.json()["llm"]["choice"]["id"] == "rules"
    bad = client.post("/api/v1/daedalus/mentor", json={"choice_id": "nope"})
    assert bad.status_code == 400
    cap = client.post("/api/v1/daedalus/mentor", json={"day_token_cap": 5000})
    assert cap.status_code == 200
    assert cap.json()["llm"]["spend"]["cap"] == 5000
