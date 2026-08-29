"""Command Center URLs: planner, radar, run-background — not the stub."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def standalone_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_USE_ADN_DB", raising=False)
    monkeypatch.delenv("DAEDALUS_USE_MESH_ROUTER", raising=False)
    from daedalus.paths import data_root, playground_projects_dir, product_root, repo_root
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    product_root.cache_clear()
    repo_root.cache_clear()
    from daedalus.store import reset_sqlite_pool
    reset_sqlite_pool()
    yield tmp_path
    reset_sqlite_pool()


def test_planner_radar_and_background_http(standalone_tmp):
    from app.main import app

    client = TestClient(app)
    listed = client.get("/admin/daedalus/planner/plans")
    assert listed.status_code == 200
    body = listed.json()
    assert "plans" in body
    assert body.get("wired") is not False

    missing_title = client.post("/admin/daedalus/planner/plans", json={"description": "x"})
    assert missing_title.status_code == 400

    created = client.post(
        "/admin/daedalus/planner/plans",
        json={"title": "Labyrinth", "description": "build"},
    )
    assert created.status_code == 200
    pid = created.json()["plan_id"]
    step = client.post(
        f"/admin/daedalus/planner/plans/{pid}/steps",
        json={"title": "Scaffold"},
    )
    assert step.status_code == 200
    assert step.json().get("ok") is True

    got = client.get(f"/admin/daedalus/planner/plans/{pid}")
    assert got.status_code == 200
    assert len(got.json()["steps"]) == 1

    prog = client.get(f"/admin/daedalus/planner/plans/{pid}/progress")
    assert prog.status_code == 200
    assert prog.json()["plan_id"] == pid

    radar = client.get("/admin/daedalus/radar/sources")
    assert radar.status_code == 200
    sources = radar.json()["sources"]
    assert len(sources) >= 1
    assert sources[0].get("name")

    state = client.get("/admin/daedalus/radar/state")
    assert state.status_code == 200

    bg = client.post("/admin/daedalus/run-background", json={})
    assert bg.status_code == 400
    gh = client.post("/admin/daedalus/github-search", json={})
    assert gh.status_code == 400
    auto = client.post("/admin/daedalus/planner/plans/auto", json={})
    assert auto.status_code == 400
    web = client.post("/admin/daedalus/web-search", json={})
    assert web.status_code == 400

    journal = client.get("/admin/daedalus/journal/list")
    assert journal.status_code == 200
    assert journal.json().get("ok") is True
    assert "journals" in journal.json()

    stats = client.get("/admin/daedalus/clarification-stats")
    assert stats.status_code == 200
    assert stats.json().get("ok") is True

    bundles = client.get("/admin/daedalus/bundles/api")
    assert bundles.status_code == 200
    assert "bundles" in bundles.json()

    apply_empty = client.post("/admin/daedalus/playground/api/projects/missing/chat/apply", json={})
    assert apply_empty.status_code == 400

    grant = client.post("/admin/daedalus/blockers/grant-recommended", json={"hours": 1})
    assert grant.status_code == 200
    assert grant.json().get("ok") is True

    mode = client.post("/admin/daedalus/mode", json={"mode": "build"})
    assert mode.status_code == 200
    assert mode.json().get("mode") == "build"
    got_mode = client.get("/admin/daedalus/mode")
    assert got_mode.json().get("mode") == "build"

    from daedalus.plan_lock import acquire, release
    lock = acquire("maze-plan")
    assert lock.get("ok") is True
    release("maze-plan")

    created_pg = client.post(
        "/admin/daedalus/playground/api/projects",
        json={"name": "Diff Lab", "kind": "static"},
    )
    assert created_pg.status_code == 200
    slug = created_pg.json()["project"]["slug"]
    diff = client.post(
        f"/admin/daedalus/playground/api/projects/{slug}/diff-preview",
        json={"files": [{"path": "index.html", "content": "<p>hi</p>"}]},
    )
    assert diff.status_code == 200
    assert diff.json().get("ok") is True
    assert diff.json().get("diffs")


def test_github_repos_and_skill_creator_are_wired(standalone_tmp, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_INTEGRATION_TOKEN", raising=False)
    from app.main import app

    client = TestClient(app)
    repos = client.get("/admin/daedalus/playground/api/github/repos")
    assert repos.status_code == 400
    assert repos.json().get("repos") == []
    assert repos.json().get("wired") is not False

    branches = client.get("/admin/daedalus/playground/api/github/repos/octo/hello/branches")
    assert branches.status_code == 400
    assert branches.json().get("wired") is not False

    compat = client.post("/admin/daedalus/playground/api/github/compat-check", json={})
    assert compat.status_code == 400
    assert "url" in (compat.json().get("error") or "")

    refine = client.post("/admin/daedalus/playground/api/skill-creator/refine", json={})
    assert refine.status_code == 400
    generate = client.post("/admin/daedalus/playground/api/skill-creator/generate", json={})
    assert generate.status_code == 400
    gist = client.post("/admin/daedalus/playground/api/skill-creator/github-gist", json={})
    assert gist.status_code == 400

    missing_bundle = client.get("/admin/daedalus/bundles/api/no-such-bundle")
    assert missing_bundle.status_code == 404
    assert missing_bundle.json().get("bundle") is None
    assert missing_bundle.json().get("wired") is not False

    sessions = client.get("/admin/daedalus/sessions")
    assert sessions.status_code == 200
    assert "sessions" in sessions.json()
    mem = client.get("/admin/daedalus/memory/stats")
    assert mem.status_code == 200
    disc = client.get("/admin/daedalus/discoveries")
    assert disc.status_code == 200

    from daedalus.scratch_journal import list_active_journals, write_step
    write_step("sess-labyrinth", "scaffold", "wrote maze.html", task_name="maze")
    listed = client.get("/admin/daedalus/journal/list")
    assert listed.status_code == 200
    assert listed.json().get("ok") is True
    journals = listed.json().get("journals") or []
    assert any(j.get("session_id") == "sess-labyrinth" for j in journals)
    assert any(j.get("session_id") == "sess-labyrinth" for j in list_active_journals())

    apps = client.get("/apps/")
    assert apps.status_code == 200
    menu = client.get("/apps/menu.json")
    assert menu.status_code == 200
    put = client.post("/apps/maze/db/score", json={"value": 9})
    assert put.status_code == 200
    assert put.json().get("ok") is True
    got = client.get("/apps/maze/db/score")
    assert got.status_code == 200
    assert got.json().get("value") == 9
    keys = client.get("/apps/maze/db/")
    assert "score" in (keys.json().get("keys") or [])

    from daedalus.playground_skill_creator import get_session, save_session
    import daedalus.playground_skill_creator as sc
    save_session({"id": "skill-lab", "platform": "generic", "history": []})
    sc._sessions.clear()
    loaded = get_session("skill-lab")
    assert loaded and loaded["id"] == "skill-lab"


def test_project_actions_and_canonical_navigation_are_wired(standalone_tmp):
    from app.main import app

    client = TestClient(app)
    created = client.post(
        "/admin/daedalus/playground/api/projects",
        json={"name": "Published Maze", "kind": "static"},
    )
    assert created.status_code == 200
    slug = created.json()["project"]["slug"]

    from daedalus import playground as pg

    pg.write_file(slug, "index.html", "<main>maze</main>")
    pg.write_file(slug, "assets/mark.svg", "<svg xmlns='http://www.w3.org/2000/svg'/>")

    asset = client.get(
        f"/admin/daedalus/playground/api/projects/{slug}/assets/raw",
        params={"path": "assets/mark.svg"},
    )
    assert asset.status_code == 200
    escaped = client.get(
        f"/admin/daedalus/playground/api/projects/{slug}/assets/raw",
        params={"path": "../pyproject.toml"},
    )
    assert escaped.status_code == 400

    upstream = client.get(
        f"/admin/daedalus/playground/api/projects/{slug}/upstream-diff"
    )
    assert upstream.status_code == 200
    assert upstream.json()["has_upstream"] is False

    published = client.post(
        f"/admin/daedalus/playground/api/projects/{slug}/publish",
        json={"icon": "🎮"},
    )
    assert published.status_code == 200
    assert published.json()["url"] == f"/apps/{slug}/"
    unpublish = client.post(
        f"/admin/daedalus/playground/api/projects/{slug}/unpublish"
    )
    assert unpublish.status_code == 200

    projects = client.get("/admin/daedalus/projects")
    assert projects.status_code == 200
    assert "/admin/daedalus/agent" in projects.text
    assert "/admin/daedalus/review" in projects.text
    assert "/admin/daedalus/settings" in projects.text
    assert "/admin/static/tailwind.css" in projects.text
    assert "cdn.tailwindcss.com" not in projects.text
    assert "/admin/static/daedalus-playground.js" in projects.text
    assert '_daedalus_nav.html' not in projects.text
    assert client.get("/admin/static/tailwind.css").status_code == 200
    assert client.get("/admin/static/daedalus-playground.js").status_code == 200
    assert client.get("/admin/daedalus/settings").status_code == 200

    legacy = client.get("/admin/daedalus/playground", follow_redirects=False)
    assert legacy.status_code == 307
    assert legacy.headers["location"] == "/admin/daedalus/projects"
