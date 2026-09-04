"""Senior game-engineer kit: math3d, game_lint, profiler, game_canon."""
from __future__ import annotations

import asyncio
import math

import pytest
from fastapi.testclient import TestClient

from daedalus.native_tools import NATIVE_TOOL_SPECS, execute_native_tool


@pytest.fixture
def harness_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_FULL_PERSONA", raising=False)
    from daedalus.paths import data_root, playground_projects_dir, product_root, repo_root
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    product_root.cache_clear()
    repo_root.cache_clear()
    from daedalus.store import reset_sqlite_pool
    reset_sqlite_pool()
    yield tmp_path
    reset_sqlite_pool()


def test_native_specs_include_senior_tools():
    names = {t["name"] for t in NATIVE_TOOL_SPECS}
    for n in ("math3d", "game_lint", "profiler", "game_canon"):
        assert n in names


def test_subagents_get_read_tools_not_write():
    from daedalus.subagents import tools_for_role

    for role in ("explorer", "verify"):
        tools = tools_for_role(role)
        assert "math3d" in tools
        assert "game_lint" in tools
        assert "profiler" in tools
        assert "game_canon" in tools
        assert "write_file" not in tools
    assert "write_file" in tools_for_role("coder")
    assert "math3d" in tools_for_role("coder")


def test_dot_cross_facing():
    from daedalus.math3d import run_op

    d = run_op("dot", {"a": [1, 0, 0], "b": [0, 1, 0]})
    assert d["ok"] is True
    assert abs(d["value"]) < 1e-9
    k = run_op("cross", {"a": [1, 0, 0], "b": [0, 1, 0]})
    assert k["value"] == [0.0, 0.0, 1.0]
    vis = run_op("facing", {"view": [0, 0, 1], "normal": [0, 0, 1]})
    assert vis["value"]["visible"] is True
    hidden = run_op("facing", {"view": [0, 0, -1], "normal": [0, 0, 1]})
    assert hidden["value"]["visible"] is False


def test_quat_slerp_and_claimed_mismatch():
    from daedalus.math3d import quat_from_axis_angle, run_op

    q0 = [0.0, 0.0, 0.0, 1.0]
    q1 = list(quat_from_axis_angle([0, 0, 1], math.pi / 2))
    half = run_op("quat_slerp", {"q0": q0, "q1": q1, "t": 0.5})
    assert half["ok"] is True
    x, y, z, w = half["value"]
    assert abs(x) < 1e-6 and abs(y) < 1e-6
    assert abs(z - math.sin(math.pi / 8)) < 1e-5
    assert abs(w - math.cos(math.pi / 8)) < 1e-5
    ends = run_op("quat_slerp", {"q0": q0, "q1": q1, "t": 0.0, "claimed": q0})
    assert ends["match"] is True
    bad = run_op("quat_slerp", {"q0": q0, "q1": q1, "t": 0.5, "claimed": [0, 0, 0, 1]})
    assert bad["verdict"] == "mismatch"
    assert bad["ok"] is True


def test_ray_triangle_hit_and_miss():
    from daedalus.math3d import run_op

    hit = run_op("ray_triangle", {
        "origin": [0, 0, 1],
        "direction": [0, 0, -1],
        "v0": [1, 1, 0],
        "v1": [-1, 1, 0],
        "v2": [0, -1, 0],
    })
    assert hit["value"]["hit"] is True
    assert abs(hit["value"]["t"] - 1.0) < 1e-5
    miss = run_op("ray_triangle", {
        "origin": [0, 0, 1],
        "direction": [0, 0, 1],
        "v0": [1, 1, 0], "v1": [-1, 1, 0], "v2": [0, -1, 0],
    })
    assert miss["value"]["hit"] is False


def test_look_at_places_eye_at_origin():
    from daedalus.math3d import mat4_transform, run_op

    m = run_op("look_at", {"eye": [0, 0, 5], "center": [0, 0, 0], "up": [0, 1, 0]})
    assert m["ok"] is True
    eye = mat4_transform(m["value"], [0, 0, 5])
    assert math.sqrt(sum(c * c for c in eye)) < 1e-5
    target = mat4_transform(m["value"], [0, 0, 0])
    assert target[2] < 0


def test_math3d_cli_json(capsys):
    from daedalus.math3d import main

    code = main(['{"op":"dot","a":[1,0,0],"b":[1,0,0]}'])
    out = capsys.readouterr().out
    assert code == 0
    assert '"value": 1.0' in out or '"value": 1' in out


def test_game_lint_cpp_leak_heuristic():
    from daedalus.game_lint import run_action

    src = "void f() { int* p = new int; int* q = new int[4]; }\n"
    r = run_action("check", path="src/leak.cpp", content=src)
    assert r["lang"] == "cpp"
    codes = {w["code"] for w in r.get("warnings") or []}
    assert "leak-heuristic-new" in codes
    clean = run_action("check", path="src/ok.cpp", content="void f() { auto p = std::make_unique<int>(1); }\n")
    codes2 = {w["code"] for w in clean.get("warnings") or []}
    assert "leak-heuristic-new" not in codes2


def test_game_lint_csharp_update_find(harness_tmp):
    from daedalus.game_lint import run_action

    src = """
using UnityEngine;
public class Bad : MonoBehaviour {
  void Update() { var x = FindObjectOfType<Player>(); }
}
"""
    r = run_action("check", path="Assets/Bad.cs", content=src)
    codes = {w["code"] for w in r.get("warnings") or []}
    assert "unity-find-in-update" in codes


def test_game_lint_gdscript_godot3():
    from daedalus.game_lint import run_action

    r = run_action("check", path="player.gd", content='func _ready():\n    connect("body_entered", self, "_on")\n')
    codes = {w["code"] for w in r.get("warnings") or []}
    assert "godot3-connect" in codes


def test_game_lint_python_ruff(harness_tmp):
    from daedalus.game_lint import run_action

    r = run_action("check", path="mod.py", content="def f():\n    return undefined_name\n")
    err = (r.get("backends") or {}).get("ruff", {}).get("error") or r.get("error") or ""
    if "ruff" in err.lower() and ("failed" in err.lower() or "not" in err.lower()):
        pytest.skip("ruff not installed")
    assert r["ok"] is False
    assert r["blocker_count"] >= 1


def test_profiler_nunit_and_chrome():
    from daedalus.profiler import parse_log
    import json

    xml = """<?xml version="1.0"?>
<test-run>
  <test-case name="Pass" result="Passed"/>
  <test-case name="Boom" result="Failed">
    <failure><message>expected 1</message></failure>
  </test-case>
</test-run>
"""
    nunit = parse_log(text=xml, kind="nunit")
    assert nunit["failed"] == 1
    assert nunit["ok"] is False
    assert any("Boom" in (c.get("name") or "") for c in nunit["cases"])

    trace = {
        "traceEvents": [
            {"ph": "X", "name": "Draw", "dur": 8000},
            {"ph": "X", "name": "Draw", "dur": 9000},
            {"ph": "X", "name": "GC", "dur": 25000},
        ]
    }
    chrome = parse_log(text=json.dumps(trace), kind="chrome")
    assert chrome["kind"] == "chrome"
    assert chrome["ok"] is False
    assert chrome["p95_ms"] > 16.7


def test_profiler_generic_fps():
    from daedalus.profiler import parse_log

    ok = parse_log(text="fps=60\nframe_time=16.0 ms\n", kind="generic")
    assert ok["ok"] is True
    assert ok["avg_fps"] == 60
    bad = parse_log(text="fps=12\nFAILED assertion\n", kind="generic")
    assert bad["ok"] is False


def test_profiler_unity_tests_honest_without_editor(harness_tmp):
    from daedalus.paths import playground_projects_dir
    from daedalus.profiler import run_action

    root = playground_projects_dir() / "arena"
    (root / "Assets").mkdir(parents=True)
    (root / "Packages").mkdir()
    (root / "Packages" / "manifest.json").write_text("{}", encoding="utf-8")
    r = run_action("unity_tests", slug="arena")
    assert r["ok"] is False
    assert r.get("need") == "unity_local" or "Unity" in (r.get("error") or "")


def test_game_canon_list_study_allowlist(harness_tmp):
    from daedalus.game_canon import CANON, run_action

    ids = {row["id"] for row in CANON}
    assert ids == {
        "doom3-bfg", "mindustry", "game-programming-patterns", "tinyrenderer", "stb",
    }
    listing = run_action("list")
    assert listing["ok"] is True
    assert len(listing["canon"]) == 5
    brief = run_action("study", id="doom3-bfg")
    assert "idLib" in " ".join(brief["study"])
    assert brief["do_not_copy"]
    denied = run_action("open", id="stb", path="../../etc/passwd")
    assert denied["ok"] is False
    denied2 = run_action("open", id="stb", path="secrets.bin")
    assert denied2["ok"] is False
    unknown = run_action("study", id="unreal-engine")
    assert unknown["ok"] is False


def test_knowledge_and_skills_exist():
    from daedalus.knowledge import _knowledge_sources
    from daedalus.learned_skills import list_skills, load_skill
    from daedalus.paths import package_root

    cats = {c for _, c in _knowledge_sources()}
    assert "game-eng" in cats
    know = package_root() / "knowledge"
    for name in ("data_oriented_design.md", "transform_math.md", "netcode.md", "game_canon.md"):
        text = (know / name).read_text(encoding="utf-8")
        assert len(text) > 400
        assert "Iyveris" not in text
        assert "ArgosDataNexus" not in text
    ids = {s["id"] for s in list_skills()}
    for sid in ("transform_math", "data_oriented_design", "netcode", "game_canon", "senior_tools"):
        assert sid in ids
        loaded = load_skill(sid)
        assert loaded and loaded["body"]
        assert loaded["meta"].get("name")


def test_select_skills_hits_math_and_netcode():
    from daedalus.improvements import select_skills

    math_hits = select_skills("verify quaternion slerp before C++", "code")
    assert "transform_math" in math_hits or "senior_tools" in math_hits
    net = select_skills("client-side prediction and lag compensation", "code")
    assert "netcode" in net
    dod = select_skills("fix cpu cache misses with SoA layout", "code")
    assert "data_oriented_design" in dod


def test_native_execute_math3d_and_canon(harness_tmp):
    math_text = asyncio.run(
        execute_native_tool(
            "math3d",
            {"action": "dot", "a": [0, 1, 0], "b": [0, 1, 0]},
            memory=None,
            session_id="t",
            read_file_fn=lambda *a, **k: {"ok": False},
        )
    )
    assert "FAILED" not in math_text
    assert "1.0" in math_text or "1" in math_text
    canon_text = asyncio.run(
        execute_native_tool(
            "game_canon",
            {"action": "list"},
            memory=None,
            session_id="t",
            read_file_fn=lambda *a, **k: {"ok": False},
        )
    )
    assert "tinyrenderer" in canon_text.lower()
    lint_text = asyncio.run(
        execute_native_tool(
            "game_lint",
            {"action": "status"},
            memory=None,
            session_id="t",
            read_file_fn=lambda *a, **k: {"ok": False},
        )
    )
    assert "FAILED" not in lint_text


def test_capabilities_and_persona(harness_tmp):
    from daedalus.identity import build_persona

    text = build_persona()
    assert "math3d" in text
    assert "game_canon" in text
    assert len(text) < 12_000
    assert text.count("\n") < 200
    from app.main import app
    client = TestClient(app)
    caps = client.get("/capabilities")
    names = caps.json()["capabilities"]
    for n in ("math3d", "game-lint", "profiler", "game-canon"):
        assert n in names
