"""Evolution mode: idle gardener, radar rotation, GitHub study, no silent harness edits."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def evo_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_EVOLUTION", raising=False)
    monkeypatch.delenv("DAEDALUS_EVOLUTION_LLM", raising=False)
    monkeypatch.delenv("DAEDALUS_OFFLINE", raising=False)
    monkeypatch.delenv("DAEDALUS_PROVIDER", raising=False)
    monkeypatch.delenv("DAEDALUS_LOCAL_LLM", raising=False)
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


def test_default_off(evo_tmp):
    from daedalus.evolution import enabled, skip_reason
    assert enabled() is False
    assert skip_reason() == "disabled"


def test_enable_persists(evo_tmp):
    from daedalus.evolution import enabled, set_enabled, set_config, interval_s, _load_settings
    from daedalus.paths import data_root
    path = data_root() / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"auto_apply_enabled": true}', encoding="utf-8")
    snap = set_enabled(True)
    assert snap["enabled"] is True
    assert enabled() is True
    saved = _load_settings()
    assert saved.get("auto_apply_enabled") is True
    assert saved.get("evolution_enabled") is True
    set_config(interval=60)
    assert interval_s() == 300
    set_config(interval=1800, clone=True)
    from daedalus.evolution import clone_research
    assert clone_research() is True
    set_enabled(False)
    assert enabled() is False
    assert _load_settings().get("auto_apply_enabled") is True


def test_env_kill_switch(evo_tmp, monkeypatch):
    from daedalus.evolution import enabled, set_enabled
    set_enabled(True)
    monkeypatch.setenv("DAEDALUS_EVOLUTION", "0")
    assert enabled() is False
    monkeypatch.setenv("DAEDALUS_EVOLUTION", "1")
    assert enabled() is True


def test_skip_offline_and_busy(evo_tmp, monkeypatch):
    from daedalus.evolution import set_enabled, skip_reason, run_cycle
    set_enabled(True)
    from daedalus.llm_wardrobe import set_offline
    set_offline(True)
    assert skip_reason() == "offline"
    assert skip_reason(force=True) == "offline"
    set_offline(False)
    monkeypatch.setattr(
        "daedalus.evolution.machine_headroom",
        lambda: {"idle": False, "load1": 99, "cores": 1, "load_ratio": 9, "mem_avail_mb": 50},
    )
    assert skip_reason() == "machine_busy"
    assert skip_reason(force=True) is None
    out = asyncio.run(run_cycle(force=False))
    assert out["skipped"] is True
    assert out["reason"] == "machine_busy"


def test_radar_rotation():
    from daedalus.radar import RADAR_SOURCES, rotate_source_ids, sources_by_ids
    assert len(RADAR_SOURCES) >= 9
    ids = [s["id"] for s in RADAR_SOURCES]
    assert "github-memory" in ids
    assert "github-local-llm" in ids
    first, nxt = rotate_source_ids(0, 2)
    assert len(first) == 2
    second, nxt2 = rotate_source_ids(nxt, 2)
    assert first != second or len(ids) <= 2
    subset = sources_by_ids(first)
    assert [s["id"] for s in subset] == first


def test_parse_and_study_without_network(evo_tmp):
    from daedalus.evolution import parse_github_url, study_repo, journal_tail, github_urls_from_radar

    assert parse_github_url("https://github.com/nousresearch/hermes-agent") == (
        "nousresearch",
        "hermes-agent",
    )
    assert parse_github_url("not-a-url") is None

    def fake_analyzer(url: str) -> dict:
        return {
            "ok": True,
            "slug": "nousresearch/hermes-agent",
            "url": url,
            "name": "hermes-agent",
            "description": "tool-using agent",
            "stars": 1,
            "license": "MIT",
            "primary_language": "Python",
            "readme": "hello",
        }

    studied = study_repo(
        "https://github.com/nousresearch/hermes-agent",
        analyzer=fake_analyzer,
        clone=False,
    )
    assert studied["ok"] is True
    assert studied["owner"] == "nousresearch"
    assert studied["clone"]["cloned"] is False
    assert studied.get("learned_skill")

    def agpl_analyzer(url: str) -> dict:
        return {**fake_analyzer(url), "license": "AGPL-3.0", "slug": "nikmcfly/MiroFish-Offline"}

    from daedalus.evolution import set_config
    set_config(clone=True)
    blocked = study_repo(
        "https://github.com/nikmcfly/MiroFish-Offline",
        analyzer=agpl_analyzer,
        clone=True,
    )
    assert blocked["clone"]["cloned"] is False
    assert blocked["clone"]["reason"] == "license_blocked"

    urls = github_urls_from_radar({
        "sources": [{
            "urls": [
                "https://github.com/foo/bar",
                "https://example.com/blog",
                "https://github.com/foo/bar",
            ],
        }],
    })
    assert urls == ["https://github.com/foo/bar"]
    journal_tail(1)  # does not throw


def test_cycle_mocked(evo_tmp):
    from daedalus.evolution import run_cycle, set_enabled, _load_state

    set_enabled(True)

    async def fake_radar(agent, source_ids=None, for_self=False):
        assert for_self is True
        assert source_ids
        return {
            "ok": True,
            "total_found": 1,
            "new_discoveries": 1,
            "sources": [{"id": source_ids[0], "urls": ["https://github.com/acme/memory-kit"]}],
        }

    def fake_study(url, **kwargs):
        return {
            "ok": True,
            "slug": "acme/memory-kit",
            "url": url,
            "clone": {"cloned": False, "reason": "clone_research_off"},
        }

    class Dummy:
        memory = None

        async def chat(self, *args, **kwargs):
            return {"reply": ""}

    out = asyncio.run(run_cycle(
        force=True,
        daedalus=Dummy(),
        radar_fn=fake_radar,
        study_fn=fake_study,
        reach_fn=lambda *a, **k: {"ok": True, "channel": "web", "results": []},
    ))
    assert out["skipped"] is False
    assert out["studied"]["slug"] == "acme/memory-kit"
    state = _load_state()
    assert state["cycle_count"] == 1
    assert "https://github.com/acme/memory-kit" in state["seen_urls"]


def test_auto_apply_never_includes_harness():
    from daedalus.auto_apply import AUTO_APPLY_ALLOWED_SKILLS
    assert "edit_modules" not in AUTO_APPLY_ALLOWED_SKILLS
    assert "edit_core" not in AUTO_APPLY_ALLOWED_SKILLS
    assert "write_routes" not in AUTO_APPLY_ALLOWED_SKILLS


def test_evolution_http_and_studio(evo_tmp, monkeypatch):
    from app.main import app

    async def fake_cycle(**kwargs):
        return {"ok": True, "skipped": False, "sources": ["github-memory"], "studied": None}

    monkeypatch.setattr("daedalus.evolution.run_cycle", fake_cycle)

    client = TestClient(app)
    got = client.get("/api/v1/daedalus/evolution")
    assert got.status_code == 200
    body = got.json()
    assert body["enabled"] is False
    assert body["interval_s"] >= 300

    on = client.post("/api/v1/daedalus/evolution", json={"enabled": True, "interval_s": 1800})
    assert on.status_code == 200
    assert on.json()["enabled"] is True

    off = client.post("/api/v1/daedalus/evolution", json={"enabled": False})
    assert off.json()["enabled"] is False

    cyc = client.post("/api/v1/daedalus/evolution/cycle", json={"force": True})
    assert cyc.status_code == 200
    assert cyc.json().get("ok") is True

    home = client.get("/")
    assert "Еволюция" in home.text
    caps = client.get("/capabilities")
    assert "evolution" in caps.json()["capabilities"]


def test_cycle_studies_github_from_reach(evo_tmp):
    from daedalus.evolution import github_urls_from_reach, run_cycle, set_enabled

    set_enabled(True)
    assert github_urls_from_reach([
        {"urls": ["https://example.com/x", "https://github.com/acme/reach-kit"]},
    ]) == ["https://github.com/acme/reach-kit"]

    async def fake_radar(agent, source_ids=None, for_self=False):
        return {"ok": True, "total_found": 0, "new_discoveries": 0, "sources": []}

    async def fake_reach(query, **kwargs):
        return {
            "ok": True,
            "channel": kwargs.get("channel") or "web",
            "query": query,
            "results": [{"title": "kit", "url": "https://github.com/acme/reach-kit"}],
        }

    def fake_study(url, **kwargs):
        return {
            "ok": True,
            "slug": "acme/reach-kit",
            "url": url,
            "clone": {"cloned": False, "reason": "clone_research_off"},
        }

    class Dummy:
        memory = None

        async def chat(self, *args, **kwargs):
            return {"reply": ""}

    out = asyncio.run(run_cycle(
        force=True,
        daedalus=Dummy(),
        radar_fn=fake_radar,
        study_fn=fake_study,
        reach_fn=fake_reach,
    ))
    assert out["studied"]["slug"] == "acme/reach-kit"
    assert any(row.get("ok") for row in out["reach"])
