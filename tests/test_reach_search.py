"""Reach search: DuckDuckGo web + Reddit fallback, no vendor dump."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient


def test_reach_search_urls_and_infer():
    from daedalus.reach import (
        GARDEN_QUERIES,
        _hits_from_reddit_listing,
        infer_channel,
        normalize_subreddit,
        reddit_search_url,
        reddit_web_query,
        rotate_garden_queries,
    )

    assert normalize_subreddit("/r/gamedev/") == "gamedev"
    assert "old.reddit.com/search.json" in reddit_search_url("local llm")
    assert "r/gamedev/search.json" in reddit_search_url("npc", subreddit="gamedev")
    assert reddit_web_query("phaser", subreddit="gamedev") == "site:reddit.com/r/gamedev phaser"
    assert infer_channel("anything", "web") == "web"
    assert infer_channel("reddit local llm", "auto") == "reddit"
    assert infer_channel("anything", "reddit") == "reddit"
    picked, nxt = rotate_garden_queries(0, 2)
    assert len(picked) == 2
    assert any(q["channel"] == "reddit" for q in GARDEN_QUERIES)
    listing = {
        "data": {
            "children": [{
                "data": {
                    "title": "hello",
                    "permalink": "/r/gamedev/comments/abc/hello/",
                    "subreddit": "gamedev",
                    "selftext": "hi",
                }
            }]
        }
    }
    hits = _hits_from_reddit_listing(listing)
    assert hits[0]["url"] == "https://www.reddit.com/r/gamedev/comments/abc/hello/"


def test_reddit_search_falls_back_to_ddg(monkeypatch):
    import daedalus.reach as R

    async def fake_web(query, *, limit=6):
        return {
            "ok": True,
            "backend": "duckduckgo",
            "query": query,
            "results": [{"title": "t", "url": "https://www.reddit.com/r/x/y", "snippet": "s"}],
        }

    monkeypatch.setattr(R, "web_search_reach", fake_web)
    monkeypatch.setattr(R, "_reddit_via_rdt", lambda *a, **k: None)
    async def _no_json(*_a, **_k):
        return None

    monkeypatch.setattr(R, "_reddit_via_json", _no_json)
    out = asyncio.run(R.reddit_search("phaser"))
    assert out["ok"] is True
    assert out["backend"] == "duckduckgo-site"
    assert "site:reddit.com" in (out.get("ddg_query") or "")


def test_reach_status_lists_search_and_reddit():
    from daedalus.reach import reach_status
    st = reach_status()
    assert st["channels"]["web"]["ready"] is True
    assert st["channels"]["search"]["backend"] == "duckduckgo"
    assert st["channels"]["reddit"]["ready"] is True
    assert st["channels"]["reddit"]["backend"] == "duckduckgo-site"


def test_reach_search_http_mocked(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    from daedalus.paths import data_root, playground_projects_dir, product_root, repo_root
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    product_root.cache_clear()
    repo_root.cache_clear()

    async def fake(query, *, channel="auto", subreddit="", limit=6):
        return {
            "ok": True,
            "channel": "reddit",
            "backend": "duckduckgo-site",
            "query": query,
            "subreddit": subreddit,
            "results": [{
                "title": "thread",
                "url": "https://www.reddit.com/r/gamedev/comments/x",
                "snippet": "ok",
            }],
        }

    monkeypatch.setattr("daedalus.reach.reach_search", fake)
    from app.main import app
    client = TestClient(app)
    r = client.post(
        "/api/v1/daedalus/reach/search",
        json={"query": "phaser", "channel": "reddit", "subreddit": "gamedev"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["channel"] == "reddit"
