"""Design Arena adapter: playable brief + pairwise playtest. No vendor dump."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def harness_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
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


def test_expand_brief_snake_and_racer():
    from daedalus.design_arena import expand_brief, lessons
    snake = expand_brief("направи змия")
    assert snake["ok"] is True
    assert snake["is_game"] is True
    assert "eat" in snake["win_lose"] or "score" in snake["win_lose"]
    assert "PLAYABLE BRIEF" in snake["prompt_block"]
    racer = expand_brief("F-Zero hover racer")
    assert "Three.js" in racer["art"] or "racer" in racer["art"]
    assert "campaign bible" in racer["feasibility"].lower() or "M0" in racer["feasibility"]
    app = expand_brief("todo dashboard with login")
    assert app["is_game"] is False
    snap = lessons()
    assert "designarena.ai" in snap["source"]
    assert any("vendor" in s.lower() or "Agent Runner" in s for s in snap["skip"])


def test_brief_enrichment_includes_arena_block():
    from daedalus.game_platform_guide import brief_enrichment
    text = brief_enrichment("vite", "snake", "browser snake game")
    assert "GAME PLATFORM GUIDE" in text
    assert "PLAYABLE BRIEF" in text


def test_compare_playtests_picks_the_loop(harness_tmp):
    from daedalus.paths import playground_projects_dir
    from daedalus.playtest import compare_playtests

    root = playground_projects_dir()
    play = root / "arena-play"
    empty = root / "arena-empty"
    play.mkdir(parents=True)
    empty.mkdir(parents=True)
    (play / "index.html").write_text(
        "<canvas></canvas><script>"
        "window.addEventListener('keydown',()=>{});"
        "let score=0; function update(){ requestAnimationFrame(update); }"
        "requestAnimationFrame(update);"
        "</script>",
        encoding="utf-8",
    )
    (empty / "README.md").write_text("coming soon", encoding="utf-8")

    out = asyncio.run(compare_playtests("arena-play", "arena-empty", n=2))
    assert out["ok"] is True
    assert out["winner"] == "arena-play"
    assert out["a"]["slug"] == "arena-play"


def test_design_arena_http(harness_tmp):
    from app.main import app
    from daedalus.paths import playground_projects_dir

    play = playground_projects_dir() / "a-play"
    empty = playground_projects_dir() / "b-empty"
    play.mkdir(parents=True)
    empty.mkdir(parents=True)
    (play / "index.html").write_text(
        "<canvas></canvas><script>"
        "window.addEventListener('keydown',()=>{});"
        "function update(){ requestAnimationFrame(update); }"
        "let score=0;"
        "requestAnimationFrame(update);"
        "</script>",
        encoding="utf-8",
    )
    (empty / "README.md").write_text("empty", encoding="utf-8")

    client = TestClient(app)
    lessons = client.get("/api/v1/daedalus/design-arena")
    assert lessons.status_code == 200
    assert lessons.json()["ok"] is True
    assert "keep" in lessons.json()

    brief = client.post(
        "/api/v1/daedalus/design-arena/brief",
        json={"prompt": "направи змия", "kind": "static"},
    )
    assert brief.status_code == 200
    assert brief.json()["is_game"] is True

    cmp_ = client.post(
        "/api/v1/daedalus/playtest/compare",
        json={"slug_a": "a-play", "slug_b": "b-empty", "n": 2},
    )
    assert cmp_.status_code == 200
    assert cmp_.json()["winner"] == "a-play"

    home = client.get("/")
    assert "ptestb" in home.text
    assert "DESIGN ARENA" in home.text or "A/B" in home.text


def test_radar_includes_design_arena():
    from daedalus.radar import RADAR_SOURCES
    ids = [s["id"] for s in RADAR_SOURCES]
    assert "github-designarena-runner" in ids
    assert "web-designarena-gamedev" in ids
