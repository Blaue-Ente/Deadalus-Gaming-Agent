"""The audience test: simulated players judge frames from actually playing the game (owner, 2026-11-12: "can Daedalus
test its games to find out what the opinions will be"). Before, every persona only read the source, and the builder's
own playtest tool ran with no model at all."""
from __future__ import annotations

import asyncio
import json

import pytest
from a0_support import a0_tmp  # noqa: F401

from daedalus import playtest

REPLY = json.dumps({"loop_clarity": 4, "controls": 4, "first_30s": 3, "win_lose": 4, "frustration": 2,
                    "would_quit": False, "quote": "I got it in five seconds", "fix": "bigger restart button"})


@pytest.fixture
def game(a0_tmp):  # noqa: F811
    from daedalus import playground as pg

    slug = asyncio.run(pg.create_project("Hop", kind="static"))["slug"]
    pg.write_file(slug, "index.html", "<canvas></canvas><script>addEventListener('keydown',()=>{});"
                                      "requestAnimationFrame(()=>{}); let score=0</script>")
    return slug


def _frames(monkeypatch, n):
    from daedalus.harness import gateway as gw

    async def play_frames(slug, kind="static"):
        parts = [gw.image_part(media_type="image/png", data=bytes([i]) * 80) for i in range(n)]
        return parts, [p["sha256"] for p in parts]

    monkeypatch.setattr("daedalus.mentor_lead.play_frames", play_frames)


def test_players_see_the_frames_of_playing_it(game, monkeypatch):
    _frames(monkeypatch, 4)
    seen = []

    async def see(system, user, images):
        seen.append((system, len(images)))
        return REPLY

    r = asyncio.run(playtest.run_playtest(game, n=3, see_fn=see))
    assert r["mode"] == "played" and r["saw_frames"] == 4 and len(r["frame_sha256s"]) == 4
    assert [n for _, n in seen] == [4, 4, 4] and "PLAYED" in seen[0][0]
    assert all(t["mode"] == "played" for t in r["personas"]) and r["scores"]["controls"] == 4.0
    assert "(played, 4 frames)" in r["summary"] and r["ship_ready"]


def test_without_frames_they_read_the_source_and_say_so(game, monkeypatch):
    _frames(monkeypatch, 0)

    async def see(system, user, images):
        assert images == [] and "PLAYED" not in system
        return REPLY

    r = asyncio.run(playtest.run_playtest(game, n=2, see_fn=see))
    assert r["mode"] == "source" and r["saw_frames"] == 0


def test_with_no_model_it_is_heuristic_only_and_says_so(game):
    r = asyncio.run(playtest.run_playtest(game, n=2))
    assert r["mode"] == "heuristic" and "heuristic-only" in r["summary"]


def test_half_quitting_blocks_shipping_and_is_an_activity_row(game, monkeypatch):
    from daedalus import activity
    from daedalus.harness import runs

    _frames(monkeypatch, 3)
    quit_reply = json.loads(REPLY) | {"would_quit": True, "quote": "nothing happens when I press keys"}

    async def see(system, user, images):
        return json.dumps(quit_reply)

    runs.reset()
    rid = runs.open_run("chat", slug=game, summary="t")
    tok = runs.bind(rid)
    try:
        r = asyncio.run(playtest.run_playtest(game, n=2, see_fn=see))
    finally:
        runs.unbind(tok)
    assert not r["ship_ready"] and any("would quit" in b for b in r["blockers"])
    row = [x for x in activity.since(rid, 0)[0] if x["type"] == "playtest"][0]
    assert (row["mode"], row["frames"], row["players"], row["would_quit"]) == ("played", 3, 2, 2)
    assert "nothing happens when I press keys" in row["quotes"][0]


def test_the_builders_tool_lets_the_players_play(game, monkeypatch):
    from daedalus.native_tools import execute_native_tool

    got = {}

    async def fake(slug, **kw):
        got.update(kw)
        return {"summary": "ok", "personas": [], "scores": {}, "blockers": [], "quotes": [], "fixes": []}

    monkeypatch.setattr(playtest, "run_playtest", fake)
    asyncio.run(execute_native_tool("playtest", {"slug": game}, memory=None, session_id="s",
                                    read_file_fn=lambda *a: {}))
    assert got["see_fn"] is playtest.gateway_see


# ── the audience gate: a game is done only when players who played it would keep playing ────────────────────────
class _Loop:
    def __init__(self, slug, playbook="playground.arcade"):
        import types
        self.slug, self.kind, self.lead_accepted, self.lead_fix_used = slug, "static", True, 0
        self.route = types.SimpleNamespace(playbook=types.SimpleNamespace(id=playbook))


def _report(ship_ready, mode="played"):
    async def fake(slug, **kw):
        assert kw["see_fn"] is playtest.gateway_see
        return {"mode": mode, "ship_ready": ship_ready, "summary": "playtest x (played, 4 frames): quit=3/4",
                "quotes": ["I could not tell what hit me"], "fixes": ["flash the car that hits the frog"],
                "saw_frames": 4}
    return fake


def test_players_who_would_quit_send_the_game_back_with_their_fixes(game, monkeypatch):
    from daedalus import mentor_lead

    monkeypatch.setattr(playtest, "run_playtest", _report(False))
    loop = _Loop(game)
    nudge = asyncio.run(mentor_lead.audience_gate(loop, "make a frog game"))
    assert "AUDIENCE TEST: NOT YET" in nudge and "flash the car that hits the frog" in nudge
    assert loop.lead_fix_used == 1 and loop.lead_accepted is False                   # reviewed again after the fix
    assert "most players would still quit" in " ".join(mentor_lead.summary_lines(loop))


def test_a_game_players_would_keep_is_done(game, monkeypatch):
    from daedalus import mentor_lead

    monkeypatch.setattr(playtest, "run_playtest", _report(True))
    loop = _Loop(game)
    assert asyncio.run(mentor_lead.audience_gate(loop, "make a frog game")) is None and loop.audience_passed
    assert "taken WHILE PLAYING" in " ".join(mentor_lead.summary_lines(loop))   # live: the reply doubted it
    assert asyncio.run(mentor_lead.audience_gate(loop, "make a frog game")) is None   # runs once once passed


@pytest.mark.parametrize("change", [
    {"lead_accepted": False},                     # the lead has not accepted: its own review comes first
    {"lead_fix_used": 2},                         # no fix round left: report, do not loop
    {"kind": "godot"},                            # no browser play for this kind
])
def test_the_gate_stays_out_of_the_way(game, monkeypatch, change):
    from daedalus import mentor_lead

    monkeypatch.setattr(playtest, "run_playtest", _report(False))
    loop = _Loop(game)
    for k, v in change.items():
        setattr(loop, k, v)
    assert asyncio.run(mentor_lead.audience_gate(loop, "make a frog game")) is None


def test_an_app_is_not_played_by_an_audience(game, monkeypatch):
    from daedalus import mentor_lead

    monkeypatch.setattr(playtest, "run_playtest", _report(False))
    assert asyncio.run(mentor_lead.audience_gate(_Loop(game, "playground.vite"), "a budget tracker app")) is None
    assert mentor_lead.is_game(_Loop(game, "playground.vite"), "направи игра с жаба")


def test_whisper_never_runs_an_audience(game, monkeypatch):
    from daedalus import mentor_lead, task_effort

    monkeypatch.setattr(playtest, "run_playtest", _report(False))
    with task_effort.bind({"tier": "whisper"}):
        assert asyncio.run(mentor_lead.audience_gate(_Loop(game), "make a frog game")) is None
