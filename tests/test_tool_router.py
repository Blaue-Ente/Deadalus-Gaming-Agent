"""Kernel tool catalog + playbook router — no live LLM."""
from __future__ import annotations

from daedalus.native_tools import NATIVE_TOOL_SPECS, playground_build_specs
from daedalus.playground_agent_routing import native_build_system
from daedalus.task_playbooks import PLAYBOOKS
from daedalus.tool_catalog import canonical, full_catalog, native_names
from daedalus.tool_router import match_playbook, route, run_consult, score_plan

SNAKE = (
    "Направи нова Playground игра snake: HTML canvas, стрелки, расте при ябълка, "
    "game over при стена/опашка. Една завършена среза."
)
SLERP = (
    "Направи нова Playground 3D demo: камера с quaternion slerp към цел и look-at."
)
CROWD = (
    "Направи Playground crowd physics: 80 bodies in 3D, octree, no nested collision loops."
)
GODOT = "Направи Godot 4 игра: CharacterBody2D, scenes/main.tscn, GDScript."


def test_catalog_covers_every_native_tool():
    names = {t["name"] for t in NATIVE_TOOL_SPECS}
    assert names == native_names()
    catalog_native = {e.name for e in full_catalog() if e.kind == "native"}
    assert names <= catalog_native
    assert any(e.kind == "skill" and e.name.startswith("skill:godot") for e in full_catalog())
    assert any(e.kind == "knowledge" for e in full_catalog())
    assert canonical("NEED_PLAYGROUND_NEW") == "playground_new"
    assert canonical("shell") == "run_command"
    assert canonical("math3d") == "math3d"


def test_playbooks_are_a_graph():
    ids = {pb.id for pb in PLAYBOOKS}
    assert "playground.math3d" in ids
    assert "playground.godot" in ids
    assert "playground.arcade" in ids
    assert "playground.mobile" in ids
    math_pb = next(pb for pb in PLAYBOOKS if pb.id == "playground.math3d")
    assert any(s.tool == "math3d" and s.required for s in math_pb.steps)
    assert any(s.depends_on for s in math_pb.steps)


def test_route_slerp_narrows_to_math_tools():
    rt = route(SLERP)
    assert rt.playbook.id == "playground.math3d"
    names = set(rt.tool_names)
    assert "math3d" in names
    assert "game_canon" in names
    assert "playground_new" in names
    assert "code_search" not in names
    assert "postgres_query" not in names
    specs = {t["name"] for t in playground_build_specs(SLERP)}
    assert "math3d" in specs
    assert "code_search" not in specs


def test_route_snake_does_not_require_math3d():
    rt = route(SNAKE)
    assert rt.playbook.id == "playground.arcade"
    assert "math3d" not in rt.tool_names
    assert "playground_new" in rt.tool_names
    specs = {t["name"] for t in playground_build_specs(SNAKE)}
    assert "math3d" not in specs
    assert "write_file" in specs


def test_route_crowd_and_godot():
    crowd = route(CROWD)
    assert crowd.playbook.id == "playground.crowd"
    assert "spatial" in crowd.tool_names
    godot = route(GODOT)
    assert godot.playbook.id == "playground.godot"
    assert godot.kind == "godot"
    assert "engine" in godot.tool_names
    assert "edit_file" in godot.tool_names
    assert "preflight" in godot.playbook.notes


def test_merged_slerp_and_octree_requires_both_tools():
    msg = "напиши quaternion slerp and octree broadphase"
    rt = route(msg)
    assert "math3d" in rt.playbook.id and "crowd" in rt.playbook.id
    miss = score_plan(rt, [{"tool": "spatial", "ok": True}, {"tool": "write_file", "ok": True}])
    assert miss.complete is False
    ok = score_plan(rt, [
        {"tool": "math3d", "ok": True},
        {"tool": "spatial", "ok": True},
        {"tool": "write_file", "ok": True},
    ])
    assert ok.complete is True


def test_game_ask_does_not_merge_harness_fix():
    msg = (
        "Направи нова Playground игра: top-down dungeon crawler на HTML canvas. "
        "Не прави змия, tetris или pong. Не чети daedalus/agent.py."
    )
    pb = match_playbook(msg)
    assert pb.id == "playground.arcade"
    assert "harness" not in pb.id
    harness = match_playbook("поправи daedalus/agent.py — native tools loop")
    assert harness.id == "harness.fix"
    pb = match_playbook("направи змия")
    assert pb.id == "playground.arcade"
    hello = match_playbook("здравей")
    assert hello.id == "talk"


def test_consult_runs_spatial_and_canon_without_llm():
    rt = route(SLERP)
    log_rows, extra = run_consult(rt)
    tools = [r["tool"] for r in log_rows]
    assert "task_plan" in tools
    assert "game_canon" in tools
    assert "GLM" in extra or "glm" in extra.lower()
    crowd = route(CROWD)
    clog, cextra = run_consult(crowd)
    assert any(r.get("tool") == "spatial" and r.get("ok") for r in clog)
    assert "advise" in cextra.lower() or "grid" in cextra.lower() or "octree" in cextra.lower()


def test_score_plan_requires_math3d_on_slerp():
    rt = route(SLERP)
    miss = score_plan(rt, [{"tool": "write_file", "ok": True}])
    assert miss.complete is False
    assert any("math3d" in m for m in miss.missing)
    ok = score_plan(rt, [
        {"tool": "math3d", "ok": True},
        {"tool": "write_file", "ok": True},
    ])
    assert ok.complete is True
    snake = score_plan(route(SNAKE), [{"tool": "write_file", "ok": True}])
    assert snake.complete is True


def test_job_contract_uses_plan():
    from daedalus.job_contract import evaluate

    snake = evaluate(SNAKE, "Готово.", tool_calls=[{"tool": "write_file", "ok": True}], kind="static")
    assert snake.complete is True
    slerp = evaluate(SLERP, "Готово.", tool_calls=[{"tool": "write_file", "ok": True}], kind="static")
    assert slerp.complete is False
    assert slerp.checks.get("task_plan") is False
    slerp_ok = evaluate(
        SLERP,
        "Готово.",
        tool_calls=[{"tool": "math3d", "ok": True}, {"tool": "write_file", "ok": True}],
        kind="static",
    )
    assert slerp_ok.complete is True


def test_native_build_system_contains_plan():
    sys = native_build_system(SLERP)
    assert "TASK PLAN" in sys
    assert "math3d" in sys
    assert "playground.math3d" in sys
    arcade = native_build_system(SNAKE)
    assert "playground.arcade" in arcade
    assert "REQUIRED" in arcade


def test_tools_http():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    cat = client.get("/api/v1/daedalus/tools")
    assert cat.status_code == 200
    body = cat.json()
    assert body["ok"] is True
    names = {t["name"] for t in body["tools"] if t["kind"] == "native"}
    assert "math3d" in names
    assert any(p["id"] == "playground.godot" for p in body["playbooks"])
    routed = client.get("/api/v1/daedalus/tools/route", params={"q": SLERP})
    assert routed.status_code == 200
    assert routed.json()["playbook"] == "playground.math3d"
