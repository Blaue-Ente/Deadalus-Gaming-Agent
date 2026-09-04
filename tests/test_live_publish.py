"""Live publish: backend chain, URL helpers, Studio button — no real Netlify/GitLab."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def live_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("NETLIFY_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("NETLIFY_TOKEN", raising=False)
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    monkeypatch.delenv("GitLabToken", raising=False)
    monkeypatch.delenv("DAEDALUS_OFFLINE", raising=False)
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


def test_pages_url_and_parse():
    from daedalus.live_publish import extract_https, pages_url, parse_json_blob
    assert pages_url("Blaue-Ente", "daedalus-snake") == "https://blaue-ente.gitlab.io/daedalus-snake/"
    blob = parse_json_blob('note\n{"url":"https://cool-site.netlify.app","site_id":"abc"}\n')
    assert blob["url"] == "https://cool-site.netlify.app"
    assert blob["site_id"] == "abc"
    assert extract_https("open https://x.netlify.app/ now") == "https://x.netlify.app/"


def test_pick_chain_offline_and_tokens(live_tmp, monkeypatch):
    from daedalus.live_publish import pick_chain
    monkeypatch.setenv("DAEDALUS_OFFLINE", "1")
    assert pick_chain("auto") == ["local"]
    monkeypatch.delenv("DAEDALUS_OFFLINE")
    monkeypatch.setattr("daedalus.live_publish._npx", lambda: "/usr/bin/npx")
    monkeypatch.setenv("NETLIFY_AUTH_TOKEN", "nlt_test")
    monkeypatch.setattr("daedalus.playground_gitlab.is_configured", lambda: True)
    chain = pick_chain("auto")
    assert chain[0] == "netlify"
    assert "gitlab_pages" in chain
    assert "netlify_anon" in chain
    assert pick_chain("gitlab_pages") == ["gitlab_pages"]


def test_stage_pages(live_tmp):
    from daedalus.live_publish import PAGES_CI, _stage_pages
    src = live_tmp / "site"
    src.mkdir()
    (src / "index.html").write_text("<html>hi</html>", encoding="utf-8")
    dest = _stage_pages(src, "snake")
    assert (dest / "public" / "index.html").read_text(encoding="utf-8") == "<html>hi</html>"
    assert (dest / ".gitlab-ci.yml").read_text(encoding="utf-8") == PAGES_CI


def test_publish_live_local_then_remote(live_tmp):
    from daedalus.live_publish import get_site, publish_live

    async def local_fn(slug: str):
        return {
            "ok": True,
            "static": True,
            "dir": live_tmp / "out",
            "local_url": f"/apps/{slug}/",
        }

    (live_tmp / "out").mkdir()
    (live_tmp / "out" / "index.html").write_text("x", encoding="utf-8")

    async def deploy_fn(name, static_dir, slug):
        assert name == "netlify_anon"
        assert static_dir == live_tmp / "out"
        return {"ok": True, "backend": "netlify_anon", "url": "https://tmp.netlify.app", "expires": "1h claim"}

    out = asyncio.run(publish_live(
        "maze",
        backend="netlify_anon",
        local_fn=local_fn,
        deploy_fn=deploy_fn,
    ))
    assert out["ok"] is True
    assert out["local_url"] == "/apps/maze/"
    assert out["url"] == "https://tmp.netlify.app"
    assert out["backend"] == "netlify_anon"
    saved = get_site("maze")
    assert saved and saved["url"] == "https://tmp.netlify.app"


def test_publish_live_fallback_when_remote_fails(live_tmp, monkeypatch):
    from daedalus.live_publish import publish_live
    monkeypatch.setattr("daedalus.live_publish.pick_chain", lambda requested="auto": ["netlify"])

    async def local_fn(slug: str):
        return {"ok": True, "dir": live_tmp, "local_url": f"/apps/{slug}/"}

    async def deploy_fn(name, static_dir, slug):
        raise RuntimeError("boom")

    out = asyncio.run(publish_live("x", backend="auto", local_fn=local_fn, deploy_fn=deploy_fn))
    assert out["local_url"] == "/apps/x/"
    assert out["url"] is None
    assert out["errors"]


def test_http_and_studio(live_tmp, monkeypatch):
    async def fake_live(slug, backend="auto"):
        return {
            "ok": True,
            "slug": slug,
            "local_url": f"/apps/{slug}/",
            "url": "https://demo.netlify.app",
            "backend": "netlify",
            "note": "ok",
        }

    monkeypatch.setattr("daedalus.live_publish.publish_live", fake_live)
    from app.main import app
    client = TestClient(app)
    caps = client.get("/capabilities")
    assert "live-publish" in caps.json()["capabilities"]
    catalog = client.get("/api/v1/daedalus/live")
    assert catalog.status_code == 200
    assert "chain" in catalog.json()
    home = client.get("/")
    assert "Live" in home.text
    i18n = client.get("/static/i18n.js")
    assert "Наживо" in i18n.text
    created = client.post(
        "/api/v1/playground/projects",
        json={"name": "Live Lab", "kind": "static"},
    )
    slug = created.json()["project"]["slug"]
    posted = client.post(f"/api/v1/playground/projects/{slug}/live", json={"backend": "auto"})
    assert posted.status_code == 200
    assert posted.json()["url"] == "https://demo.netlify.app"
