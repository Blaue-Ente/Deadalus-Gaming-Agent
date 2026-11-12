"""A project's bible carries a game across sessions (owner, 2026-11-12: The-Labirinth keeps a game bible every
session starts from; a Daedalus build planned each turn from scratch)."""
from __future__ import annotations

import asyncio
import types

import pytest
from a0_support import a0_tmp  # noqa: F401

from daedalus import project_bible as bible

PLAN = {"brief": "Snake Garden: steer a snake to eat fruit.", "checklist": ["arrow keys steer", "fruit grows the snake"],
        "look": ["warm garden colours"], "steps": ["x"], "model": "m"}


@pytest.fixture
def game(a0_tmp):  # noqa: F811
    from daedalus import playground as pg
    return asyncio.run(pg.create_project("Snake Garden", kind="static"))["slug"]


def _loop(slug, **kw):
    loop = types.SimpleNamespace(slug=slug, lead_plan=PLAN, user_message="make a snake game",
                                 route=types.SimpleNamespace(playbook=types.SimpleNamespace(id="playground.arcade")))
    for k, v in kw.items():
        setattr(loop, k, v)
    return loop


def test_the_first_plan_becomes_the_bible_and_each_turn_is_logged(game):
    loop = _loop(game, lead_review={"accept": False, "missing": ["game over panel never shows"]},
                 audience={"summary": "x", "ship_ready": False, "fixes": ["show what killed you"]},
                 project_tests={"summary": "logic 16 passed, 0 failed; no baseline yet", "regressions": []})
    bible.record_turn(loop, "make a snake game")
    p = bible.path(game)
    assert p.name == bible.GAME_BIBLE
    body = p.read_text()
    assert "## Vision\nSnake Garden" in body and "- arrow keys steer" in body and "- warm garden colours" in body
    assert "lead: not accepted" in body and "audience: most would quit" in body and "logic 16 passed" in body
    assert "game over panel never shows" in body and "players: show what killed you" in body
    bible.record_turn(_loop(game, lead_review={"accept": True, "missing": []}), "add a pause key")
    body = p.read_text()
    assert body.count("### ") == 2 and "add a pause key" in body and "lead accepted" in body
    assert body.count("## Vision") == 1                          # created once, then only the log grows


def test_the_owners_edits_are_kept(game):
    bible.record_turn(_loop(game), "make a snake game")
    p = bible.path(game)
    p.write_text(p.read_text().replace("warm garden colours", "night garden, glowing fruit"))
    bible.record_turn(_loop(game), "faster snake")
    assert "night garden, glowing fruit" in p.read_text()


def test_the_prompt_gets_the_head_and_the_latest_log(game):
    bible.record_turn(_loop(game), "first")
    for i in range(80):
        bible.log_turn(game, f"turn {i} " + "x" * 80, _loop(game))
    t = bible.text(game, max_chars=1500)
    assert len(t) <= 1600 and "## Vision" in t and "turn 79" in t and "turn 0 " not in t


def test_a_revived_repo_and_an_app_get_what_fits(game, a0_tmp):  # noqa: F811
    from daedalus import playground as pg

    root = pg.project_dir(game)
    (root / ".daedalus").mkdir(exist_ok=True)
    (root / ".daedalus" / "upstream_manifest.json").write_text("{}")
    bible.record_turn(_loop(game), "make a snake game")
    assert bible.path(game) is None                              # the upstream's docs are not ours to add to
    app = asyncio.run(pg.create_project("Budget", kind="static"))["slug"]
    pg.write_file(app, "index.html", "<form><input></form>")
    bible.record_turn(_loop(app, route=types.SimpleNamespace(playbook=types.SimpleNamespace(id="playground.vite"))),
                      "a budget tracker")
    assert bible.path(app).name == bible.PROJECT_BIBLE


def test_the_next_plan_reads_the_bible(game, monkeypatch):
    from daedalus import mentor_lead

    bible.record_turn(_loop(game), "make a snake game")
    seen = {}

    async def ask(system, user, **kw):
        seen["user"] = user
        return {"brief": "b", "checklist": ["c"], "look": [], "steps": []}, {"model": "m"}

    monkeypatch.setattr(mentor_lead, "_ask", ask)
    monkeypatch.setattr(mentor_lead, "unavailable_reason", lambda: "")
    asyncio.run(mentor_lead.kickoff("add a pause key", slug=game, kind="static"))
    assert "PROJECT BIBLE" in seen["user"] and "Snake Garden: steer a snake" in seen["user"]
