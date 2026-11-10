"""F7 — Unity MCP adapter or an honest non-promise."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from daedalus.engine_session import run_action
from daedalus.game_studio_gates import run_gates, run_unity_check
from daedalus.native_tools import NATIVE_TOOL_SPECS
from daedalus.subagents import tools_for_role
from daedalus.unity_runtime import (
    looks_like_cloud_editor_claim,
    map_unity_mcp_tool,
    prove_unity,
    run_engine_action,
    status,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def harness_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_FULL_PERSONA", raising=False)
    monkeypatch.delenv("DAEDALUS_UNITY_MCP", raising=False)
    monkeypatch.delenv("UNITY_PATH", raising=False)
    monkeypatch.delenv("UNITY_EDITOR", raising=False)
    monkeypatch.setattr("daedalus.engine_runners.unity_bin", lambda: "")
    from daedalus.paths import data_root, playground_projects_dir, product_root, repo_root
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    product_root.cache_clear()
    repo_root.cache_clear()
    from daedalus.store import reset_sqlite_pool
    reset_sqlite_pool()
    yield tmp_path
    reset_sqlite_pool()


def _unity_tree(root: Path) -> None:
    (root / "Assets").mkdir(parents=True)
    (root / "Packages").mkdir(parents=True)
    (root / "ProjectSettings").mkdir(parents=True)
    (root / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
    (root / "ProjectSettings" / "ProjectVersion.txt").write_text(
        "m_EditorVersion: 2022.3.48f1\n", encoding="utf-8",
    )
    (root / "Assets" / "GameEntry.cs").write_text("class GameEntry {}\n", encoding="utf-8")


def _guided_racing_ctx() -> dict:
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


def test_bare_unity_is_not_a_cloud_claim():
    assert looks_like_cloud_editor_claim("unity URP mobile RPG") is False
    assert looks_like_cloud_editor_claim("open in Unity Hub locally") is False
    assert looks_like_cloud_editor_claim("unity editor in the cloud") is True
    assert looks_like_cloud_editor_claim("pressed play in unity") is True
    assert looks_like_cloud_editor_claim("не ни трябва unity hub") is True


def test_unity_no_editor_is_honest_non_promise(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DAEDALUS_UNITY_MCP", raising=False)
    monkeypatch.setattr("daedalus.engine_runners.unity_bin", lambda: "")
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path))
    from daedalus.paths import playground_projects_dir
    playground_projects_dir.cache_clear()
    slug = "hub"
    root = playground_projects_dir() / slug
    _unity_tree(root)
    out = asyncio.run(run_engine_action("run", slug))
    assert out.get("ok") is False
    assert out.get("honest") is True
    assert out.get("need") == "unity_local"
    assert out.get("play") is False
    err = out.get("error") or ""
    assert "vendor" in err.lower()
    st = status(root)
    assert st["promise"] == "local_hub"
    assert st["play"] is False
    routed = asyncio.run(run_action("run", slug))
    assert routed.get("honest") is True
    playground_projects_dir.cache_clear()


def test_cloud_editor_claim_fails_prove(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("daedalus.engine_runners.unity_bin", lambda: "")
    _unity_tree(tmp_path)
    proof = prove_unity(tmp_path, claim="unity editor in the cloud", kind="unity")
    assert proof["ok"] is False
    blob = " ".join(proof["errors"]).lower()
    assert "cloud" in blob or "vendor" in blob


def test_unity_check_does_not_block_racing():
    ctx = _guided_racing_ctx()
    row = run_unity_check(ctx)
    assert row["gate"] == "unity-check"
    assert row["passed"] is True
    assert row["blocking"] is False
    combined = run_gates(ctx)
    assert combined["blocking"] is False
    assert combined["gates"]["unity-check"]["passed"] is True
    assert combined["gates"]["production-check"]["passed"] is True
    assert combined["gates"]["eng-check"]["passed"] is True


def test_unity_mcp_tool_mapping():
    names = ["manage_editor", "read_console", "get_hierarchy"]
    assert map_unity_mcp_tool("play", names) == "manage_editor"
    assert map_unity_mcp_tool("errors", names) == "read_console"
    assert map_unity_mcp_tool("scene_tree", names) == "get_hierarchy"
    assert map_unity_mcp_tool("screenshot", names) == ""


def test_batchmode_is_not_play_mode(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("DAEDALUS_UNITY_MCP", raising=False)
    monkeypatch.setattr("daedalus.engine_runners.unity_bin", lambda: "/tmp/fake-unity")
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path))
    from daedalus.paths import playground_projects_dir
    playground_projects_dir.cache_clear()
    slug = "batch"
    root = playground_projects_dir() / slug
    _unity_tree(root)
    live = asyncio.run(run_engine_action("inspect", slug))
    assert live.get("ok") is False
    assert live.get("honest") is True
    assert live.get("play") is False
    st = status(root)
    assert st["promise"] == "batchmode"
    assert st["play"] is False
    playground_projects_dir.cache_clear()


def test_engine_spec_mentions_unity_honesty():
    spec = next(t for t in NATIVE_TOOL_SPECS if t["name"] == "engine")
    assert "Unity" in spec["description"]
    assert "do not vendor" in spec["description"]
    assert "engine" in tools_for_role("verify")
    assert "write_file" not in tools_for_role("explorer")


def test_racer_scaffold_untouched():
    src = (ROOT / "daedalus" / "playground_racer_scaffold.py").read_text(encoding="utf-8")
    assert "DAEDALUS_UNITY_MCP" not in src
    assert "src/layer/runtime" not in src


def test_http_unity_and_studio_copy(harness_tmp):
    from app.main import app

    client = TestClient(app)
    home = client.get("/")
    assert home.status_code == 200
    assert "not Editor in this cloud" in home.text or "Hub or MCP" in home.text
    js = client.get("/static/studio.js")
    assert "/unity" in js.text

    created = client.post(
        "/api/v1/playground/projects",
        json={"name": "Hub", "kind": "unity", "description": "unity slice"},
    )
    assert created.status_code == 200
    slug = created.json()["project"]["slug"]
    listed = client.get(f"/api/v1/playground/projects/{slug}/unity")
    assert listed.status_code == 200
    body = listed.json()
    assert body["ok"] is True
    assert body["unity"] is True
    assert body["promise"] == "local_hub"
    assert body["play"] is False

    wall = client.post(
        f"/api/v1/playground/projects/{slug}/unity",
        json={"action": "prove", "claim": "unity editor in the cloud"},
    )
    assert wall.status_code == 200
    assert wall.json()["ok"] is False

    refused = client.post(
        f"/api/v1/playground/projects/{slug}/unity",
        json={"action": "run"},
    )
    assert refused.status_code == 200
    assert refused.json().get("honest") is True
    assert refused.json().get("ok") is False

    # A6.5b: the game-studio page redirects to the Studio; its Unity row says the Editor is not in this cloud (EN and BG).
    gs = client.get("/admin/daedalus/game-studio", follow_redirects=False)
    assert gs.status_code == 302 and gs.headers["location"] == "/?panel=build"
    i18n = client.get("/static/i18n.js").text
    assert '"studio.unityHub": "Hub locally · not in this cloud"' in i18n and "не в този облак" in i18n
    assert 'let sub = t("studio.unityHub");' in client.get("/static/studio.js").text

    caps = client.get("/capabilities")
    assert "unity-mcp" in caps.json()["capabilities"]


def test_persona_still_compact_with_unity_line(monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.delenv("DAEDALUS_FULL_PERSONA", raising=False)
    from daedalus.identity import build_persona

    text = build_persona()
    assert "Unity" in text
    assert "DAEDALUS_UNITY_MCP" in text or "no Editor" in text
    assert len(text) < 12_000
    assert text.count("\n") < 200


def test_select_skills_for_unity_runtime():
    from daedalus import improvements

    improvements._SKILLS_LOADED = False
    improvements._SKILLS_CACHE.clear()
    names = improvements.select_skills(
        "unity editor in the cloud DAEDALUS_UNITY_MCP",
        "build",
    )
    assert "unity_runtime" in names


def test_bench_unity_honest_gate():
    from daedalus.game_agent_bench import status as bench_status

    st = bench_status()
    assert st["gates"]["unity_mcp_or_honest"] is True
    assert st["missing"] == []


def test_mcp_run_is_play(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_UNITY_MCP", "unity-local")
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path))
    from daedalus.paths import playground_projects_dir
    playground_projects_dir.cache_clear()
    slug = "live"
    _unity_tree(playground_projects_dir() / slug)

    async def fake_list(server: str):
        assert server == "unity-local"
        return {"ok": True, "tools": [{"name": "manage_editor"}, {"name": "read_console"}]}

    async def fake_call(server: str, tool: str, arguments=None):
        return {"ok": True, "result": {"playing": True}, "server": server, "tool": tool}

    monkeypatch.setattr("daedalus.mcp_client.list_tools", fake_list)
    monkeypatch.setattr("daedalus.mcp_client.call_tool", fake_call)
    out = asyncio.run(run_engine_action("run", slug))
    assert out.get("ok") is True
    assert out.get("via") == "mcp"
    assert out.get("play") is True
    playground_projects_dir.cache_clear()
