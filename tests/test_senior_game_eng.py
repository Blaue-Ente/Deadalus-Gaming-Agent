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
    for n in ("math3d", "spatial", "game_lint", "profiler", "game_canon"):
        assert n in names


def test_subagents_get_read_tools_not_write():
    from daedalus.subagents import tools_for_role

    for role in ("explorer", "verify"):
        tools = tools_for_role(role)
        assert "math3d" in tools
        assert "spatial" in tools
        assert "game_lint" in tools
        assert "profiler" in tools
        assert "game_canon" in tools
        assert "write_file" not in tools
    assert "write_file" in tools_for_role("coder")
    assert "math3d" in tools_for_role("coder")
    assert "spatial" in tools_for_role("coder")


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
        "glm", "directxmath", "tracy",
        "milo-path", "game-program-books", "cis-roadmap", "awesome-engine",
        "engine-arch-samples", "unity-chop",
    }
    listing = run_action("list")
    assert listing["ok"] is True
    assert len(listing["canon"]) == 14
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
    for name in (
        "data_oriented_design.md", "transform_math.md", "netcode.md", "game_canon.md",
        "game_dev_agent_knowledge_base.md", "game_roadmaps.md",
    ):
        text = (know / name).read_text(encoding="utf-8")
        assert len(text) > 400
        assert "Iyveris" not in text
        assert "ArgosDataNexus" not in text
    ids = {s["id"] for s in list_skills()}
    for sid in ("transform_math", "data_oriented_design", "netcode", "game_canon", "senior_tools", "spatial_simd"):
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
    arch = select_skills("chop chop event channel game architecture", "code")
    assert "game_canon" in arch


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
    assert "spatial" in text
    assert "game_canon" in text
    assert len(text) < 12_000
    assert text.count("\n") < 200
    from app.main import app
    client = TestClient(app)
    caps = client.get("/capabilities")
    names = caps.json()["capabilities"]
    for n in ("math3d", "game-lint", "profiler", "game-canon", "spatial"):
        assert n in names


def test_spatial_advise_and_aabb():
    from daedalus.spatial import run_action

    tiny = run_action("advise", n=10, dim=3)
    assert tiny["ok"] is True
    assert tiny["recommend"] == "brute_force"
    crowd = run_action("advise", n=500, dim=3, moving=True)
    assert crowd["recommend"] in ("uniform_grid", "octree")
    rays = run_action("advise", n=80, dim=3, rays=True)
    assert rays["recommend"] == "bvh"
    hit = run_action("aabb", a_min=[0, 0, 0], a_max=[1, 1, 1], b_min=[0.5, 0.5, 0.5], b_max=[2, 2, 2])
    assert hit["hit"] is True
    miss = run_action("aabb", a_min=[0, 0, 0], a_max=[1, 1, 1], b_min=[2, 2, 2], b_max=[3, 3, 3])
    assert miss["hit"] is False
    nested = run_action(
        "scan",
        text="for (const a of ents) { for (const b of ents) { a.distanceTo(b) } }",
        path="src/loop.js",
    )
    assert nested["warnings"]


def test_tracy_csv_parse():
    from daedalus.profiler import parse_log

    csv_text = "Name,Duration\nPhysicsUpdate,12.4\nDraw,1.1\nAI,2.0\n"
    row = parse_log(text=csv_text, kind="tracy")
    assert row["kind"] == "tracy"
    assert row["ok"] is True
    names = [z["name"] for z in row["heaviest"]]
    assert "PhysicsUpdate" in names
    slow = parse_log(text="Name,Duration\nSpike,40.0\n", kind="tracy")
    assert slow["ok"] is False
    ns = parse_log(text="name,total_ns\nPhysics,16400000\n", kind="tracy")
    assert ns["kind"] == "tracy"
    assert ns["heaviest"][0]["ms"] == 16.4


def test_canon_includes_glm_tracy():
    from daedalus.game_canon import list_canon

    ids = {row["id"] for row in list_canon()["canon"]}
    for cid in ("glm", "directxmath", "tracy", "tinyrenderer", "milo-path", "unity-chop"):
        assert cid in ids


def test_game_jobs_wire_senior_tools_not_just_rag():
    from daedalus.game_engineer import extra_skill_ids, prefer_skill_id, prompt_block, missing_eng_tools
    from daedalus.learned_skills import disclose

    q = "Направи 3D игра с octree physics"
    extra = extra_skill_ids(q, "build")
    assert "senior_tools" in extra
    assert "spatial_simd" in extra
    pref = prefer_skill_id(q, intent="build")
    assert pref == "senior_tools"
    block = disclose(q, extra_ids=extra, prefer_id=pref)
    assert "SKILL BODY:" in block
    assert "math3d" in block
    assert "spatial" in block.lower()
    contract = prompt_block(q)
    assert "GAME ENGINEER CONTRACT" in contract
    assert missing_eng_tools("simple snake", [{"tool": "write_file"}]) == ""
    assert missing_eng_tools("quaternion slerp in the camera", [])
    assert not missing_eng_tools("quaternion slerp in the camera", [{"tool": "math3d"}])


def test_job_contract_heavy_eng_requires_tool():
    from daedalus.job_contract import evaluate

    snake = evaluate(
        "напиши snake игра",
        "Готово.",
        tool_calls=[{"tool": "write_file", "ok": True}, {"tool": "run_command", "ok": True}],
    )
    assert snake.complete is True
    heavy = evaluate(
        "напиши quaternion slerp and octree broadphase",
        "Готово.",
        intent="code",
        tool_calls=[{"tool": "write_file", "ok": True}, {"tool": "run_command", "ok": True}],
    )
    assert heavy.complete is False
    assert any("math3d" in b or "spatial" in b for b in heavy.blockers)
    ok = evaluate(
        "напиши quaternion slerp and octree broadphase",
        "Готово.",
        intent="code",
        tool_calls=[
            {"tool": "write_file", "ok": True},
            {"tool": "run_command", "ok": True},
            {"tool": "spatial", "ok": True},
        ],
    )
    assert ok.complete is True


def test_eng_check_warns_not_blocks_racing():
    from daedalus.game_studio_gates import run_eng_check, run_gates
    from daedalus.game_agent_bench import _guided_racing_ctx

    ctx = _guided_racing_ctx()
    row = run_eng_check(ctx)
    assert row["gate"] == "eng-check"
    assert row["passed"] is True
    assert row["blocking"] is False
    combined = run_gates(ctx)
    assert combined["blocking"] is False
    assert combined["gates"]["eng-check"]["passed"] is True


def test_native_execute_spatial(harness_tmp):
    text = asyncio.run(
        execute_native_tool(
            "spatial",
            {"action": "advise", "n": 400, "dim": 2},
            memory=None,
            session_id="t",
            read_file_fn=lambda *a, **k: {"ok": False},
        )
    )
    assert "FAILED" not in text
    assert "quadtree" in text.lower() or "uniform_grid" in text.lower() or "grid" in text.lower()


def test_canon_roadmaps_refuse_books_wiki_and_chunk(harness_tmp):
    from daedalus.game_canon import (
        CANON,
        chunk_cpp,
        chunk_markdown,
        folder_context,
        local_dir,
        raw_url,
        run_action,
    )

    books = run_action("clone", id="game-program-books")
    assert books["ok"] is False
    err = (books.get("error") or "").lower()
    assert "ebook" in err or "copyright" in err
    assert run_action("open", id="game-program-books")["ok"] is False
    assert run_action("chunk", id="game-program-books")["ok"] is False
    assert run_action("search", id="game-program-books", query="primer")["ok"] is False

    milo = run_action("study", id="milo-path")
    assert milo["ok"] is True
    assert "isbn.csv" in milo["files"]
    assert "readme.md" in milo["files"]
    chop = run_action("study", id="unity-chop")
    assert chop["ok"] is True
    assert any("wiki/" in f for f in chop["files"])
    assert any("Event" in s or "wiki" in s.lower() for s in chop["study"])

    item = next(row for row in CANON if row["id"] == "unity-chop")
    wiki = raw_url(item, "wiki/Event-system.md")
    assert wiki == (
        "https://raw.githubusercontent.com/wiki/UnityTechnologies/open-project-1/Event-system.md"
    )
    assert raw_url(item, "README.md").endswith("/main/README.md")

    src = (
        "class EventInterface {\npublic:\n  void Raise();\n};\n"
        "struct EventDelegate {\n  int id;\n};\n"
    )
    pieces = chunk_cpp(src, "SampleEventSystem/SampleEvent/Event.h")
    names = {p["name"] for p in pieces}
    assert names == {"EventInterface", "EventDelegate"}
    assert all("// Context: Event System Pattern" in p["text"] for p in pieces)
    assert folder_context("SampleArchitecture/SampleECS/Main.cpp") == "Engine Architecture / ECS"

    md = (
        "# Title\n\nIntro paragraph that is long enough to keep as a RAG chunk.\n\n"
        "## Events\n\nEvent channels connect scenes without a singleton manager.\n"
    )
    heads = chunk_markdown(md, "README.md", context="Engine architecture")
    assert len(heads) >= 1
    assert all("Context:" in h["text"] for h in heads)

    dest = local_dir("engine-arch-samples")
    header = dest / "SampleEventSystem" / "SampleEvent" / "Event.h"
    header.parent.mkdir(parents=True, exist_ok=True)
    header.write_text(src, encoding="utf-8")
    chunked = run_action(
        "chunk",
        id="engine-arch-samples",
        path="SampleEventSystem/SampleEvent/Event.h",
    )
    assert chunked["ok"] is True
    assert chunked["count"] >= 2
    assert any(c["name"] == "EventInterface" for c in chunked["chunks"])

