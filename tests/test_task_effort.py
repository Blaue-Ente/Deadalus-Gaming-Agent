"""Effort classifier and world kit — no live LLM."""
from __future__ import annotations

from fastapi.testclient import TestClient
import pytest


def test_classify_whisper_craft_epic():
    from daedalus.task_effort import classify
    w = classify("здравей")
    assert w["tier"] == "whisper"
    assert w["apply_model"] is True
    c = classify("направи змия на Phaser")
    assert c["tier"] == "craft"
    assert c["apply_model"] is False
    e = classify("искам необятен свят с кампания, персонажи и различни нива")
    assert e["tier"] == "epic"
    assert e["world_kit"] is True
    assert e["min_stages"] >= 12
    forced = classify("здравей", requested="epic")
    assert forced["tier"] == "epic"
    assert forced["source"] == "owner"
    assert classify("hello world")["tier"] != "epic"
    assert classify("build hello world")["tier"] == "craft"


def test_bind_does_not_leak():
    from daedalus.task_effort import applied_model, bind, classify
    dec = classify("епос с сюжет и фракции")
    dec["model"] = "unit-test-model"
    dec["apply_model"] = True
    assert applied_model() is None
    with bind(dec):
        assert applied_model()["model"] == "unit-test-model"
    assert applied_model() is None


def test_slugify_bulgarian():
    from daedalus.playground import slugify
    assert slugify("Северна сага") == "severna-saga"
    assert slugify("Snake") == "snake"


def test_world_kit_writes_once(tmp_path):
    from daedalus.world_kit import needed, status, write
    assert needed("open world campaign with characters") is True
    assert needed("здравей") is False
    written = write(tmp_path, name="Лабиринт", slug="lab", description="епос")
    assert "world/bible.md" in written
    assert "world/quests.json" in written
    assert (tmp_path / "CAMPAIGN.md").is_file()
    again = write(tmp_path, name="Лабиринт", slug="lab", description="епос")
    assert again == []
    st = status(tmp_path)
    assert st["ready"] is True


def test_min_stages_epic():
    from daedalus.playground_plan_guide import enrich_plan, min_stages_for_complexity
    assert min_stages_for_complexity("static", is_game=True, description="кампания с нива") >= 12
    plan = {"title": "W", "stages": [{"title": "loop", "files": ["src/game.js"]}]}
    out = enrich_plan(plan, "static", "необятен свят с персонажи")
    assert out["effort"] == "epic"
    files = " ".join(" ".join(s.get("files") or []) for s in out["stages"])
    assert "world/bible.md" in files
    assert "world/quests.json" in files


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_USE_ADN_DB", raising=False)
    from daedalus.paths import data_root, playground_projects_dir
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    from daedalus.store import reset_sqlite_pool
    reset_sqlite_pool()
    yield tmp_path
    reset_sqlite_pool()


def test_effort_http_and_studio_assets(isolated, monkeypatch):
    monkeypatch.setenv("DAEDALUS_DATA", str(isolated / "data"))
    from app.main import app
    client = TestClient(app)
    caps = client.get("/capabilities")
    assert "effort-routing" in caps.json()["capabilities"]
    assert "world-kit" in caps.json()["capabilities"]
    ev = client.get("/api/v1/daedalus/effort", params={"q": "здравей"})
    assert ev.json()["tier"] == "whisper"
    page = client.get("/")
    assert page.status_code == 200
    assert "studio.css" in page.text
    assert "data-effort" in page.text
    css = client.get("/static/studio.css")
    js = client.get("/static/studio.js")
    assert css.status_code == 200
    assert js.status_code == 200
    created = client.post(
        "/api/v1/playground/projects",
        json={"name": "Сага", "kind": "static", "description": "кампания", "effort": "epic"},
    )
    assert created.status_code == 200
    slug = created.json()["project"]["slug"]
    tree = client.get(f"/api/v1/playground/projects/{slug}")
    payload = tree.json()
    raw_files = payload.get("files") or []
    paths = " ".join(
        f.get("path", "") if isinstance(f, dict) else str(f) for f in raw_files
    )
    blob = paths + " " + str(payload)
    assert "world/bible.md" in blob or "bible" in blob
    world = client.get(f"/api/v1/playground/projects/{slug}/world")
    assert world.status_code == 200
    assert world.json()["world"]["ready"] is True
    assert world.json()["world"]["files"]["bible"] is True
    assert world.json()["world"]["runtime"] is True
    page = client.get("/")
    assert "worldkit" in page.text
    assert "pdesc" in page.text


def test_select_skills_for_campaign():
    from daedalus import improvements
    improvements._SKILLS_LOADED = False
    improvements._SKILLS_CACHE.clear()
    names = improvements.select_skills(
        "искам кампания с необятен свят и персонажи",
        "build",
    )
    assert set(names) & {"world_kit", "campaign", "narrative"}
