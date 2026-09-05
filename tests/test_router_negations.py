"""S6 — negated clauses do not route; playbooks merge only on an explicit conjunction."""
from __future__ import annotations

from pathlib import Path

from daedalus.tool_router import format_system, match_playbook, route, strip_negations

SNAKE_MIXED = (
    "Направи нова Playground игра snake: HTML canvas, стрелки, расте при ябълка, "
    "game over при стена/опашка. Не прави 3D, quaternion или octree. Една завършена среза."
)
SLERP_CANVAS = (
    "Направи нова Playground 3D demo: камера с quaternion slerp към цел и look-at. "
    "Една завършена WebGL/canvas среза."
)


def test_strip_negations_cuts_the_clause_to_the_sentence_or_the_but():
    cleaned, dropped = strip_negations(SNAKE_MIXED)
    assert dropped == ["Не прави 3D, quaternion или octree"]
    assert "quaternion" not in cleaned and "snake" in cleaned and "Една завършена среза" in cleaned
    cleaned, dropped = strip_negations("Направи snake игра без 3D и quaternion, само canvas")
    assert dropped == ["без 3D и quaternion"] and "само canvas" in cleaned
    cleaned, dropped = strip_negations("crowd: 80 bodies, octree, no nested collision loops. Ship it.")
    assert dropped == ["no nested collision loops"] and "Ship it" in cleaned
    assert strip_negations("look-at camera with slerp") == ("look-at camera with slerp", [])
    assert strip_negations("Nothing here") == ("Nothing here", [])  # 'no' needs a word boundary


def test_negated_kinds_do_not_route():
    rt = route(SNAKE_MIXED)
    assert rt.playbook.id == "playground.arcade"
    assert "math3d" not in rt.tool_names and rt.dim == 2
    assert rt.negated == ["Не прави 3D, quaternion или octree"]
    assert route("Направи snake игра без 3D и quaternion, само canvas").playbook.id == "playground.arcade"
    assert route("Направи Godot игра и без Unity").playbook.id == "playground.godot"
    assert route("3D camera demo on canvas without octree").playbook.id == "playground.math3d"


def test_merge_only_on_explicit_conjunction():
    # a rendering-target mention is not a second game
    assert route(SLERP_CANVAS).playbook.id == "playground.math3d"
    assert match_playbook("3D camera demo on a canvas").id == "playground.math3d"
    # an explicit join is
    assert route("напиши quaternion slerp and octree broadphase").playbook.id == "playground.math3d+playground.crowd"
    assert route("quaternion slerp камера плюс crowd с octree").playbook.id == "playground.math3d+playground.crowd"
    assert route("змия и тетрис на canvas").playbook.id == "playground.arcade"
    # kind wins and joins
    assert match_playbook("snake with quaternion camera", kind="godot").id.startswith("playground.godot")


def test_plan_prompt_tells_the_model_what_the_owner_excluded():
    sys = format_system(route(SNAKE_MIXED), user_message=SNAKE_MIXED)
    assert "Owner excluded" in sys and "Не прави 3D, quaternion или octree" in sys
    assert "playground.arcade" in sys
    assert "Owner excluded" not in format_system(route("направи змия"), user_message="направи змия")


def test_route_api_exposes_negated():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    r = client.get("/api/v1/daedalus/tools/route", params={"q": SNAKE_MIXED})
    assert r.status_code == 200
    body = r.json()
    assert body["playbook"] == "playground.arcade"
    assert body["negated"] == ["Не прави 3D, quaternion или octree"]


def test_self_improvement_only_when_the_build_loop_gave_up():
    src = Path("daedalus/agent.py").read_text(encoding="utf-8")
    assert 'c.get("tool") == "build_loop"' in src
    assert '"exhausted" in str(_lr.get("stop_reason")' in src
