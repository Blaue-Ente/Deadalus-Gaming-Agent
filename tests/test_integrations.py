"""Adapters: Reach, playtest, MiroFish, MCP, slim persona, sub-agents."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def harness_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_FULL_PERSONA", raising=False)
    monkeypatch.delenv("DAEDALUS_USE_ADN_DB", raising=False)
    from daedalus.paths import data_root, playground_projects_dir, product_root, repo_root
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    product_root.cache_clear()
    repo_root.cache_clear()
    from daedalus.store import reset_sqlite_pool
    reset_sqlite_pool()
    yield tmp_path
    reset_sqlite_pool()


def test_standalone_persona_is_compact(monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.delenv("DAEDALUS_FULL_PERSONA", raising=False)
    from daedalus.identity import build_persona
    text = build_persona()
    assert "NEED_PLAYGROUND_WRITE" in text
    assert "web_read" in text
    assert "spawn_subagent" in text
    assert "Argos Vault — central encrypted" not in text
    assert len(text) < 12_000
    assert text.count("\n") < 200


def test_full_persona_opt_in(monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_FULL_PERSONA", "1")
    from daedalus.identity import DAEDALUS_PERSONA, build_persona
    text = build_persona()
    assert "Argos Vault" in text
    assert len(text) > len(DAEDALUS_PERSONA) // 2


def test_jina_reader_url_idempotent():
    from daedalus.reach import jina_reader_url, looks_like_rss
    assert jina_reader_url("https://example.com/a") == "https://r.jina.ai/https://example.com/a"
    already = "https://r.jina.ai/https://example.com/a"
    assert jina_reader_url(already) == already
    assert looks_like_rss("https://example.com/feed.xml")
    assert not looks_like_rss("https://example.com/about")


def test_reach_status_zero_config():
    from daedalus.reach import reach_status
    st = reach_status()
    assert st["channels"]["web"]["ready"] is True
    assert st["channels"]["web"]["backend"] == "jina-reader"


def test_playtest_catalog_and_heuristic(harness_tmp):
    from daedalus.paths import playground_projects_dir
    from daedalus.playtest import list_personas, run_playtest
    import asyncio

    ids = {p["id"] for p in list_personas()}
    assert "casual_bg" in ids and "hardcore" in ids and "kids" in ids
    groups = {"background", "psychology", "capability", "behavior", "lifestyle"}
    assert groups <= set(list_personas()[0])

    root = playground_projects_dir() / "snake"
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.html").write_text(
        "<canvas></canvas><script>"
        "window.addEventListener('keydown',()=>{});"
        "function update(){}"
        "requestAnimationFrame(update);"
        "let score=0; let gameover=false;"
        "</script>",
        encoding="utf-8",
    )
    report = asyncio.run(run_playtest("snake", n=3, llm_fn=None))
    assert report["ok"] is True
    assert len(report["personas"]) == 3
    assert report["heuristic"]["signals"]["canvas"] is True
    assert report["heuristic"]["signals"]["win_lose"] is True


def test_mirofish_form_and_default_url(monkeypatch):
    monkeypatch.delenv("MIROFISH_URL", raising=False)
    from daedalus.mirofish_client import api_base, press_release_form
    assert api_base() == "http://127.0.0.1:5001"
    form, files = press_release_form("# My Game\nA snake.", project_name="Snake")
    assert "simulation_requirement" in form
    name, blob, ctype = files["files"]
    assert name.endswith(".md")
    assert b"snake" in blob.lower()
    assert "markdown" in ctype


def test_mcp_framing_roundtrip():
    from daedalus.mcp_client import decode_buffer, encode_message, rpc
    msg = rpc("tools/list", {}, id=7)
    framed = encode_message(msg)
    assert framed.startswith(b"Content-Length:")
    parsed, rest = decode_buffer(framed)
    assert rest == b""
    assert parsed[0]["id"] == 7
    assert parsed[0]["method"] == "tools/list"


def test_mcp_status_empty(harness_tmp):
    from daedalus.mcp_client import load_servers, status
    assert load_servers() == {}
    st = status()
    assert st["ok"] is True
    assert st["servers"] == {}


def test_subagent_marker_parse():
    from daedalus.subagents import parse_subagent_markers
    raw = "<NEED_SUBAGENT: snake | add a pause key>\n<NEED_WORKER: fast | ignore>"
    calls = parse_subagent_markers(raw)
    assert calls == [{"slug": "snake", "task": "add a pause key"}]


def test_browser_close_without_session():
    import asyncio
    from daedalus.browser_session import close
    r = asyncio.run(close())
    assert r["ok"] is True


def test_native_tool_catalog_includes_integrations():
    from daedalus.native_tools import NATIVE_TOOL_SPECS, openai_tool_specs
    names = {t["name"] for t in NATIVE_TOOL_SPECS}
    for n in ("web_read", "reach_status", "browser", "mcp_call", "spawn_subagent", "playtest", "mirofish", "play_lab", "dissect_game", "engine", "world", "assets", "skill", "production", "git_forge"):
        assert n in names
    oa_names = {s["function"]["name"] for s in openai_tool_specs()}
    assert "web_read" in oa_names


def test_connectors_and_playtest_http(harness_tmp):
    from app.main import app
    client = TestClient(app)
    home = client.get("/")
    assert "Свят" in home.text

    reach = client.get("/api/v1/daedalus/reach")
    assert reach.status_code == 200
    assert reach.json()["channels"]["web"]["ready"] is True

    personas = client.get("/api/v1/daedalus/playtest/personas")
    assert len(personas.json()["personas"]) >= 4

    pt = client.post("/api/v1/daedalus/playtest", json={"slug": "missing-game", "n": 2})
    assert pt.status_code == 200
    assert pt.json()["ok"] is True

    mcp = client.get("/api/v1/daedalus/mcp")
    assert mcp.status_code == 200

    caps = client.get("/capabilities")
    body = caps.json()["capabilities"]
    assert "agent-reach" in body
    assert "playtest" in body
    assert "dissection" in body
    assert "mirofish-sidecar" in body
    assert "mcp" in body
