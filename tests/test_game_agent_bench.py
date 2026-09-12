"""F8 — game_agent_bench as a real in-process honesty suite."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from daedalus.game_agent_bench import (
    CASES,
    GAME_TASK_COUNT,
    GAME_TASKS,
    HONESTY_CASE_COUNT,
    LIVE_TASKS,
    P0_22_HOOKS,
    allowed,
    evaluate,
    missing_gates,
    run_game_suite,
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

    gs = client.get("/admin/daedalus/game-studio")
    assert gs.status_code == 200
    assert "Honesty" in gs.text
    assert 'data-i18n="gs.honesty"' in gs.text
    assert "not a bake-off" in gs.text.lower()

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


def test_game_tasks_are_exactly_twenty_hermetic():
    assert HONESTY_CASE_COUNT == 15
    assert len(CASES) == HONESTY_CASE_COUNT
    assert GAME_TASK_COUNT == 20
    assert len(GAME_TASKS) == GAME_TASK_COUNT
    families = {name: 0 for name in ("godot", "unity", "blender", "build")}
    for spec in GAME_TASKS.values():
        assert spec.mode == "hermetic"
        families[spec.family] += 1
    assert families == {"godot": 10, "unity": 4, "blender": 3, "build": 3}
    assert set(LIVE_TASKS) == {
        "godot_live_bridge",
        "unity_live_batchmode",
        "blender_live_execute",
    }
    assert all(spec.mode == "live" for spec in LIVE_TASKS.values())
    assert P0_22_HOOKS["live_required_for_release"] is False
    assert P0_22_HOOKS["game_task_count"] == 20
    assert P0_22_HOOKS["honesty_case_count"] == 15


def test_run_game_suite_hermetic_twenty(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.delenv("DAEDALUS_BENCH_LIVE", raising=False)
    monkeypatch.delenv("GODOT_BIN", raising=False)
    monkeypatch.delenv("UNITY_PATH", raising=False)
    monkeypatch.delenv("BLENDER_BIN", raising=False)
    from daedalus.paths import data_root
    data_root.cache_clear()

    report = run_game_suite(persist=False, live=False)
    assert report["ok"] is True, report["failed"]
    assert report["bake_off"] is False
    assert report["suite"] == "game"
    assert report["layer"] == "game_v1"
    assert report["live"] is False
    assert report["total"] == GAME_TASK_COUNT
    assert report["passed"] == GAME_TASK_COUNT
    assert report["failed"] == []
    assert [row["id"] for row in report["cases"]] == list(GAME_TASKS)
    assert "p0_22_hooks" in report
    assert report["p0_22_hooks"]["live_required_for_release"] is False


def test_cli_suite_game_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.delenv("DAEDALUS_BENCH_LIVE", raising=False)
    from daedalus.paths import data_root
    data_root.cache_clear()
    from daedalus.game_agent_bench import main

    code = main(["--suite", "game", "--no-persist"])
    out = capsys.readouterr().out
    assert code == 0
    assert "game_agent_bench game ok" in out
    assert "20/20" in out
    assert "bake_off=false" in out
    assert "live=false" in out


def test_cli_live_without_bins_fail_closed(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.delenv("DAEDALUS_BENCH_LIVE", raising=False)
    monkeypatch.delenv("GODOT_BIN", raising=False)
    monkeypatch.delenv("GODOT_BINARY", raising=False)
    monkeypatch.delenv("UNITY_PATH", raising=False)
    monkeypatch.delenv("UNITY_EDITOR", raising=False)
    monkeypatch.delenv("BLENDER_BIN", raising=False)
    monkeypatch.setattr("daedalus.godot_runtime_probe.godot_bin", lambda: "")
    monkeypatch.setattr("daedalus.engine_runners.unity_bin", lambda: "")
    monkeypatch.setattr("daedalus.blender_jobs.blender_bin", lambda: "")
    from daedalus.paths import data_root
    data_root.cache_clear()
    from daedalus.game_agent_bench import main

    code = main(["--suite", "game", "--live", "--no-persist"])
    out = capsys.readouterr().out
    assert code == 1
    assert "live=true" in out
    assert "godot_live_bridge" in out
    assert "unity_live_batchmode" in out
    assert "blender_live_execute" in out


def test_evaluate_suite_dispatch(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.delenv("DAEDALUS_BENCH_LIVE", raising=False)
    from daedalus.paths import data_root
    data_root.cache_clear()

    honesty = evaluate(persist=False)
    assert honesty["suite"] == "honesty"
    assert honesty["total"] == HONESTY_CASE_COUNT
    assert honesty["ok"] is True
    assert "path" not in honesty

    game = evaluate(persist=False, suite="game")
    assert game["suite"] == "game"
    assert game["total"] == GAME_TASK_COUNT
    assert game["ok"] is True

    combined = evaluate(persist=False, suite="all")
    assert combined["suite"] == "all"
    assert combined["total"] == HONESTY_CASE_COUNT + GAME_TASK_COUNT
    assert combined["ok"] is True
    assert combined["layers"]["honesty"]["total"] == HONESTY_CASE_COUNT
    assert combined["layers"]["game_v1"]["total"] == GAME_TASK_COUNT


def test_http_suite_game_query(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_BENCH_LIVE", raising=False)
    from daedalus.paths import data_root, playground_projects_dir, product_root, repo_root
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    product_root.cache_clear()
    repo_root.cache_clear()
    from daedalus.store import reset_sqlite_pool
    reset_sqlite_pool()

    from app.main import app

    client = TestClient(app)
    listed = client.get("/api/v1/daedalus/bench")
    assert listed.status_code == 200
    body = listed.json()
    assert body["suite"] == "honesty"
    assert body["total"] == HONESTY_CASE_COUNT

    game = client.get("/api/v1/daedalus/bench", params={"suite": "game"})
    assert game.status_code == 200
    payload = game.json()
    assert payload["ok"] is True
    assert payload["suite"] == "game"
    assert payload["total"] == GAME_TASK_COUNT
    assert payload["bake_off"] is False
    reset_sqlite_pool()


def test_leftover_ladder_not_deleted():
    from daedalus.game_agent_bench import run_task

    row = run_task("build_leftover_ladder_stays")
    assert row["ok"] is True
    loop = (ROOT / "daedalus" / "tool_loop.py").read_text(encoding="utf-8")
    assert "execute_leftover_fn" in loop
    assert "NEED_PROPOSE_START" in loop
