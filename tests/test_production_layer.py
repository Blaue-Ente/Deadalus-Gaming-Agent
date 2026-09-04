"""F6 — production layer vs the prototype wall."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from daedalus.game_studio_gates import run_gates, run_production_check
from daedalus.native_tools import NATIVE_TOOL_SPECS, execute_native_tool
from daedalus.production_layer import (
    empty_map,
    looks_like_ship_claim,
    promote,
    prove_production,
    seed_map,
    write_map,
    write_runtime,
)
from daedalus.subagents import tools_for_role

ROOT = Path(__file__).resolve().parents[1]


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


def _guided_ctx() -> dict:
    return {
        "gdd": "GDD " + ("x" * 80),
        "config": {
            "studio_preset": "racing",
            "kind": "vite",
            "brainstorm": {"genre": "racing", "idea": "F-Zero night city"},
            "build_mode": "guided",
        },
        "plan": {"stages": [{"title": "boot", "goal": "menu"}] * 3},
        "project": {"kind": "vite", "slug": "mist"},
    }


def test_racer_ship_is_not_a_store_claim():
    assert looks_like_ship_claim("one hover ship on a spline") is False
    assert looks_like_ship_claim("steam in the GDD") is False
    assert looks_like_ship_claim("store-ready") is True
    assert looks_like_ship_claim("production-ready") is True
    assert looks_like_ship_claim("готово за стора") is True
    assert looks_like_ship_claim("not a prototype") is True
    assert looks_like_ship_claim("upload to steam") is True


def test_empty_map_is_not_production(tmp_path: Path):
    (tmp_path / "index.html").write_text("<canvas></canvas>", encoding="utf-8")
    write_map(tmp_path, empty_map(layer="production", systems=[]))
    proof = prove_production(tmp_path)
    assert proof["ok"] is False
    assert any("empty systems map" in e for e in proof["errors"])

    write_map(tmp_path, empty_map(layer="prototype", systems=[]))
    proof = prove_production(tmp_path, claim="store-ready for the store")
    assert proof["ok"] is False
    assert any("empty systems map" in e for e in proof["errors"])


def test_pre_build_does_not_block(tmp_path: Path):
    proof = prove_production(tmp_path / "empty-pre-build")
    assert proof["ok"] is True
    assert proof["wall"] == "prototype"
    ctx = _guided_ctx()
    row = run_production_check(ctx)
    assert row["gate"] == "production-check"
    assert row["passed"] is True
    assert row["blocking"] is False
    combined = run_gates(ctx)
    assert combined["blocking"] is False
    assert combined["gates"]["production-check"]["passed"] is True


def test_honest_prototype_with_planned_systems(tmp_path: Path):
    (tmp_path / "index.html").write_text("<canvas></canvas>", encoding="utf-8")
    seed_map(tmp_path, systems=["handling", "track"], layer="prototype")
    proof = prove_production(tmp_path, claim="F-Zero night city with one hover ship")
    assert proof["ok"] is True, proof.get("errors")
    assert proof["wall"] == "prototype"
    ctx = {
        "workspace": str(tmp_path),
        "gdd": "GDD " + ("x" * 80),
        "config": {
            "studio_preset": "racing",
            "kind": "vite",
            "brainstorm": {"genre": "racing", "idea": "one hover ship on a spline"},
            "build_mode": "guided",
        },
        "project": {"kind": "vite", "slug": "mist"},
    }
    row = run_production_check(ctx)
    assert row["passed"] is True
    assert row["blocking"] is False


def test_claimed_missing_files_fail(tmp_path: Path):
    (tmp_path / "index.html").write_text("<canvas></canvas>", encoding="utf-8")
    write_map(
        tmp_path,
        empty_map(
            layer="prototype",
            systems=[{"id": "save", "status": "implemented", "files": ["src/missing.js"]}],
        ),
    )
    proof = prove_production(tmp_path)
    assert proof["ok"] is False
    blob = " ".join(proof["errors"])
    assert "missing" in blob
    assert "src/missing.js" in blob


def test_promote_requires_evidence(tmp_path: Path):
    (tmp_path / "index.html").write_text("<canvas></canvas>", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "loop.js").write_text("export function tick() {}\n", encoding="utf-8")
    seed_map(
        tmp_path,
        systems=[{"id": "loop", "files": ["src/loop.js"], "status": "implemented"}],
        layer="prototype",
    )
    write_runtime(tmp_path)
    result = promote(tmp_path)
    assert result["ok"] is True, result
    proof = prove_production(tmp_path, require_production=True)
    assert proof["ok"] is True, proof.get("errors")
    assert proof["wall"] == "clear"
    assert proof["layer"] == "production"


def test_promote_blocked_without_evidence(tmp_path: Path):
    (tmp_path / "index.html").write_text("<canvas></canvas>", encoding="utf-8")
    seed_map(tmp_path, systems=["pause"], layer="prototype")
    result = promote(tmp_path)
    assert result["ok"] is False
    proof = prove_production(tmp_path, require_production=True)
    assert proof["ok"] is False


def test_native_production_and_explorer_role():
    spec = next(t for t in NATIVE_TOOL_SPECS if t["name"] == "production")
    assert "empty systems map" in spec["description"]
    assert "production" in tools_for_role("verify")
    assert "production" in tools_for_role("explorer")
    assert "production" in tools_for_role("coder")
    assert "write_file" not in tools_for_role("explorer")
    assert "write_file" not in tools_for_role("verify")


def test_native_production_execute_status(harness_tmp, monkeypatch):
    from daedalus.paths import playground_projects_dir

    slug = "layer"
    root = playground_projects_dir() / slug
    root.mkdir(parents=True)
    (root / "index.html").write_text("<p>m0</p>", encoding="utf-8")
    text = asyncio.run(
        execute_native_tool(
            "production",
            {"action": "status", "slug": slug},
            memory=None,
            session_id="t",
            read_file_fn=lambda *a, **k: {"ok": False},
        )
    )
    assert "production" in text
    assert "prototype" in text


def test_racer_scaffold_does_not_get_layer_runtime():
    src = (ROOT / "daedalus" / "playground_racer_scaffold.py").read_text(encoding="utf-8")
    assert "src/layer/runtime" not in src
    gs = (ROOT / "daedalus" / "game_studio.py").read_text(encoding="utf-8")
    assert "seed_map" in gs
    assert "write_runtime" not in gs


def test_http_production_and_studio_copy(harness_tmp):
    from app.main import app

    client = TestClient(app)
    home = client.get("/")
    assert home.status_code == 200
    assert "прототип" in home.text
    assert "продукционен слой" in home.text
    js = client.get("/static/studio.js")
    assert "/production" in js.text

    created = client.post(
        "/api/v1/playground/projects",
        json={"name": "Mist", "kind": "static", "description": "slice"},
    )
    assert created.status_code == 200
    slug = created.json()["project"]["slug"]
    listed = client.get(f"/api/v1/playground/projects/{slug}/production")
    assert listed.status_code == 200
    body = listed.json()
    assert body["ok"] is True
    assert body["layer"] == "prototype"

    seeded = client.post(
        f"/api/v1/playground/projects/{slug}/production",
        json={"action": "seed", "systems": ["handling", "track"]},
    )
    assert seeded.status_code == 200
    assert seeded.json()["ok"] is True

    wall = client.post(
        f"/api/v1/playground/projects/{slug}/production",
        json={"action": "prove", "claim": "store-ready"},
    )
    assert wall.status_code == 200
    assert wall.json()["ok"] is False

    gs = client.get("/admin/daedalus/game-studio")
    assert gs.status_code == 200
    assert "Слой" in gs.text
    assert "не билд за стора" in gs.text

    caps = client.get("/capabilities")
    assert "production-layer" in caps.json()["capabilities"]


def test_persona_still_compact_with_production_tool(monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.delenv("DAEDALUS_FULL_PERSONA", raising=False)
    from daedalus.identity import build_persona

    text = build_persona()
    assert "production" in text
    assert "prototype" in text
    assert len(text) < 12_000
    assert text.count("\n") < 200


def test_select_skills_for_production_layer():
    from daedalus import improvements

    improvements._SKILLS_LOADED = False
    improvements._SKILLS_CACHE.clear()
    names = improvements.select_skills(
        "искам продукционен слой и store-ready, не прототип",
        "build",
    )
    assert "production_layer" in names


def test_bench_production_layer_gate():
    from daedalus.game_agent_bench import status

    st = status()
    assert st["gates"]["production_layer"] is True
    assert st["missing"] == []
