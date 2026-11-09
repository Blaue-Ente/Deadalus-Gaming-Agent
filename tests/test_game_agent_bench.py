"""F8 — game_agent_bench as a real in-process honesty suite."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from daedalus.game_agent_bench import (
    CASES,
    allowed,
    evaluate,
    missing_gates,
    run_suite,
    status,
)

ROOT = Path(__file__).resolve().parents[1]


def test_gates_still_allow_measurement():
    st = status()
    assert missing_gates() == []
    assert allowed() is True
    assert st["allowed"] is True
    assert st["bake_off"] is False
    assert st["gates"]["honesty_suite"] is True
    assert st["gates"]["unity_mcp_or_honest"] is True
    assert st["gates"]["production_layer"] is True
    assert "playtest" in st["note"].lower()
    assert "vendor bake-off" in st["note"].lower()


def test_run_suite_passes_without_editors(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.delenv("DAEDALUS_FULL_PERSONA", raising=False)
    monkeypatch.delenv("DAEDALUS_QUALITY", raising=False)
    from daedalus.paths import data_root
    data_root.cache_clear()

    report = run_suite(persist=True)
    assert report["ok"] is True
    assert report["bake_off"] is False
    assert report["failed"] == []
    assert report["total"] == len(CASES)
    assert report["passed"] == len(CASES)
    ids = {row["id"] for row in report["cases"]}
    assert "skip_is_not_ok" in ids
    assert "unity_without_editor_honest" in ids
    assert "racing_gates_not_blocking" in ids
    assert "not_vendor_bakeoff" in ids
    path = Path(report["path"])
    assert path.is_file()
    assert "latest.json" in path.name

    alias = evaluate(persist=False)
    assert alias["ok"] is True
    assert "path" not in alias


def test_cli_main_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.delenv("DAEDALUS_QUALITY", raising=False)
    from daedalus.paths import data_root
    data_root.cache_clear()
    from daedalus.game_agent_bench import main

    code = main(["--no-persist"])
    out = capsys.readouterr().out
    assert code == 0
    assert "game_agent_bench ok" in out
    assert "bake_off=false" in out


def test_http_bench_and_studio_copy(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_FULL_PERSONA", raising=False)
    monkeypatch.delenv("DAEDALUS_QUALITY", raising=False)
    from daedalus.paths import data_root, playground_projects_dir, product_root, repo_root
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    product_root.cache_clear()
    repo_root.cache_clear()
    from daedalus.store import reset_sqlite_pool
    reset_sqlite_pool()

    from app.main import app

    client = TestClient(app)
    home = client.get("/")
    assert home.status_code == 200
    assert "not a bake-off" in home.text or "Honesty is a suite" in home.text
    js = client.get("/static/studio.js")
    assert "/api/v1/daedalus/bench" in js.text
    assert "studio.honesty" in js.text
    i18n = client.get("/static/i18n.js")
    assert "не bake-off" in i18n.text

    listed = client.get("/api/v1/daedalus/bench")
    assert listed.status_code == 200
    body = listed.json()
    assert body["ok"] is True
    assert body["bake_off"] is False
    assert body["failed"] == []
    assert "path" not in body

    posted = client.post("/api/v1/daedalus/bench")
    assert posted.status_code == 200
    saved = posted.json()
    assert saved["ok"] is True
    assert saved.get("path")

    # A6.5b: the game-studio page redirects to the Studio's Build panel; the Studio says the same honest thing.
    gs = client.get("/admin/daedalus/game-studio", follow_redirects=False)
    assert gs.status_code == 302 and gs.headers["location"] == "/?panel=build"
    i18n = client.get("/static/i18n.js").text
    assert '"studio.notBakeoff": "not a bake-off"' in i18n
    assert 'itemEl(t("studio.honesty"), passed + "/" + total + " · " + t("studio.notBakeoff")' in client.get("/static/studio.js").text

    caps = client.get("/capabilities")
    assert "game-agent-bench" in caps.json()["capabilities"]
    reset_sqlite_pool()


def test_persona_mentions_honesty_suite(monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.delenv("DAEDALUS_FULL_PERSONA", raising=False)
    from daedalus.identity import build_persona

    text = build_persona()
    assert "game_agent_bench" in text
    assert "bake-off" in text.lower()
    assert len(text) < 12_000
    assert text.count("\n") < 200


def test_select_skills_for_honesty_suite():
    from daedalus import improvements

    improvements._SKILLS_LOADED = False
    improvements._SKILLS_CACHE.clear()
    names = improvements.select_skills(
        "game agent bench vendor bake-off honesty suite",
        "build",
    )
    assert "game_agent_bench" in names


def test_racer_scaffold_still_untouched():
    src = (ROOT / "daedalus" / "playground_racer_scaffold.py").read_text(encoding="utf-8")
    assert "game_agent_bench" not in src
    assert "src/layer/runtime" not in src
