"""Tests that stay with each game (owner, 2026-11-12: games a team keeps building on, like The-Labirinth, whose pure
simulation is tested and verified before every push)."""
from __future__ import annotations

import asyncio
import json
import shutil
import types

import pytest
from a0_support import a0_tmp  # noqa: F401

from daedalus import mentor_lead, project_tests

LOGIC = ("export function hop(s){ return {...s, y: s.y - 1, score: s.y - 1 === 0 ? s.score + 1 : s.score}; }\n"
         "export function hit(s){ return {...s, lives: s.lives - 1, over: s.lives - 1 <= 0}; }\n")
TESTS_OK = ("import test from 'node:test'; import assert from 'node:assert/strict';\n"
            "import {hop, hit} from '../logic.mjs';\n"
            "test('reaching the top scores', () => assert.equal(hop({y:1, score:0}).score, 1));\n"
            "test('the third hit ends the game', () => assert.equal(hit({lives:1}).over, true));\n")
node_needed = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js not installed")


@pytest.fixture
def game(a0_tmp):  # noqa: F811
    from daedalus import playground as pg

    slug = asyncio.run(pg.create_project("Frog", kind="static"))["slug"]
    pg.write_file(slug, "logic.mjs", LOGIC)
    return slug


def _write(slug, rel, text):
    from daedalus import playground as pg
    pg.write_file(slug, rel, text)


@node_needed
def test_passing_logic_tests_are_counted(game):
    _write(game, "tests/logic.test.mjs", TESTS_OK)
    r = asyncio.run(project_tests.run_logic_tests(game))
    assert r["ran"] and r["ok"] and r["passed"] == 2 and r["failed"] == 0


@node_needed
def test_a_failing_rule_is_named(game):
    _write(game, "tests/logic.test.mjs", TESTS_OK.replace("hit({lives:1}).over, true", "hit({lives:2}).over, true"))
    r = asyncio.run(project_tests.run_logic_tests(game))
    assert r["ran"] and not r["ok"] and r["failed"] == 1 and "the third hit ends the game" in r["failures"][0]


@node_needed
def test_a_broken_import_is_not_a_pass(game):
    _write(game, "tests/logic.test.mjs", "import {nope} from '../missing.mjs';\n")
    r = asyncio.run(project_tests.run_logic_tests(game))
    assert r["ran"] and r["ok"] is False


def test_no_tests_is_said_not_passed(game):
    r = asyncio.run(project_tests.run_logic_tests(game))
    assert r["ran"] is False and r["ok"] is None and "no tests" in r["note"]


def test_what_worked_at_acceptance_and_fails_now_is_a_regression(game):
    play_then = {"checks": [{"id": "canvas", "ok": True}, {"id": "keyboard_input", "ok": True}]}
    project_tests.record_baseline(game, {"ok": True, "passed": 2, "files": ["tests/logic.test.mjs"]}, play_then)
    base = project_tests.load_baseline(game)
    assert base["play_passing"] == ["canvas", "keyboard_input"] and base["logic_passed"] == 2
    play_now = {"checks": [{"id": "canvas", "ok": True}, {"id": "keyboard_input", "ok": False}]}
    regs = project_tests.regressions(base, {"ok": True, "passed": 2}, play_now)
    assert regs == ["play check keyboard_input passed at acceptance and fails now"]
    assert any("fail now" in r for r in project_tests.regressions(base, {"ok": False, "passed": 1}, play_then))
    assert any("removed" in r for r in project_tests.regressions(base, {"ok": True, "passed": 1}, play_then))


# ── the gate ─────────────────────────────────────────────────────────────────────────────────────────────────────
def _loop(slug, playbook="playground.arcade", **kw):
    loop = types.SimpleNamespace(slug=slug, kind="static", lead_accepted=True, lead_fix_used=0,
                                 route=types.SimpleNamespace(playbook=types.SimpleNamespace(id=playbook)),
                                 play_bot_raw={"checks": [{"id": "canvas", "ok": True}]})
    for k, v in kw.items():
        setattr(loop, k, v)
    return loop


@node_needed
def test_an_accepted_game_with_passing_tests_records_its_baseline(game):
    _write(game, "tests/logic.test.mjs", TESTS_OK)
    loop = _loop(game)
    assert asyncio.run(mentor_lead.tests_gate(loop, "make a frog game")) is None
    assert project_tests.load_baseline(game)["logic_passed"] == 2 and loop.project_tests["ok"]


@node_needed
def test_failing_tests_send_the_game_back_once_per_round(game):
    _write(game, "tests/logic.test.mjs", TESTS_OK.replace("score, 1", "score, 7"))
    loop = _loop(game)
    nudge = asyncio.run(mentor_lead.tests_gate(loop, "make a frog game"))
    assert "PROJECT TESTS: FAILING" in nudge and "reaching the top scores" in nudge
    assert loop.lead_fix_used == 1 and loop.lead_accepted is False
    assert "(failing)" in " ".join(mentor_lead.summary_lines(loop))


def test_a_game_without_tests_is_asked_for_them_once(game):
    loop = _loop(game)
    nudge = asyncio.run(mentor_lead.tests_gate(loop, "make a frog game"))
    assert "PROJECT TESTS: NONE YET" in nudge and "logic.mjs" in nudge
    loop.lead_accepted = True
    assert asyncio.run(mentor_lead.tests_gate(loop, "make a frog game")) is None      # asked once, not a loop


@pytest.mark.parametrize("change", [{"lead_accepted": False}, {"lead_fix_used": 2}])
def test_the_gate_stays_out_of_the_way(game, change):
    _write(game, "tests/logic.test.mjs", "import x from 'nope';\n")
    assert asyncio.run(mentor_lead.tests_gate(_loop(game, **change), "make a frog game")) is None


def test_apps_and_whisper_are_not_asked_for_game_tests(game):
    from daedalus import playground as pg
    from daedalus import task_effort

    pg.write_file(game, "index.html", "<form><input></form>")
    assert asyncio.run(mentor_lead.tests_gate(_loop(game, "playground.vite"), "a budget tracker")) is None
    with task_effort.bind({"tier": "whisper"}):
        assert asyncio.run(mentor_lead.tests_gate(_loop(game), "make a frog game")) is None


def test_the_writer_is_told_for_games_only():
    from daedalus.tool_router import format_system, route

    game_sys = format_system(route("Build a small web game with a frog", kind="static"), user_message="frog game")
    app_sys = format_system(route("Build a budget tracker app", kind="vite-react"), user_message="budget tracker app")
    assert "tests/logic.test.mjs" in game_sys and "tests/logic.test.mjs" not in app_sys


def test_the_studio_shows_a_tests_row():
    from daedalus import activity

    row = activity.compact({"seq": 4, "ts": 1.0, "type": "project_tests",
                            "payload": {"ok": False, "ran": True, "passed": 3, "failed": 1, "failures": ["x"],
                                        "regressions": ["play check canvas passed at acceptance and fails now"],
                                        "baseline": True}})
    assert row["passed"] == 3 and row["regressions"] and row["baseline"] is True
    assert json.dumps(row)


@node_needed
def test_when_the_rounds_are_spent_the_tests_still_run_for_the_report(game):
    """Live 2026-11-12: lead and audience used both rounds; 16 logic tests passed and the owner never saw it."""
    _write(game, "tests/logic.test.mjs", TESTS_OK)
    loop = _loop(game, lead_accepted=False, lead_fix_used=2)
    assert asyncio.run(mentor_lead.tests_gate(loop, "make a frog game")) is None
    assert loop.project_tests["ok"] and "logic 2 passed" in loop.project_tests["summary"]
    assert project_tests.load_baseline(game) is None                    # no baseline for an unaccepted game


def test_epic_gets_more_fix_rounds():
    from daedalus import task_effort

    assert mentor_lead.fix_rounds() == mentor_lead.LEAD_FIX_ROUNDS
    with task_effort.bind({"tier": "epic"}):
        assert mentor_lead.fix_rounds() == mentor_lead.EPIC_FIX_ROUNDS > mentor_lead.LEAD_FIX_ROUNDS


def test_the_self_check_knows_what_this_turn_wrote(monkeypatch):
    """Live 2026-11-12: it accused the reply of naming files 'not in the file tree' taken before the turn."""
    from daedalus import improvements

    seen = {}

    async def call(intent, messages, *, system, **kw):
        from daedalus.harness import gateway as gw
        seen["system"], seen["user"] = system, messages[0]["content"]
        return gw.GatewayResult(True, text='{"ok": true}')

    monkeypatch.setattr("daedalus.harness.gateway.call", call)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    asyncio.run(improvements.review_gate_critique(
        "FILE TREE: index.html\nmake a frog game", "Built logic.mjs and game.js",
        [{"tool": "write_file", "ok": True, "input": {"path": "logic.mjs"}}], 30.0))
    assert "write_file logic.mjs" in seen["user"] and "taken BEFORE this turn" in seen["system"]
    assert "in English" in seen["system"]
