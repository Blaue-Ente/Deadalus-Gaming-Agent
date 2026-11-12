"""Gap 5 (owner, 2026-11-12): longer, goal-directed audience tests, and what real players do with a published game."""
from __future__ import annotations

import asyncio
import json
import types

import pytest
from a0_support import a0_tmp  # noqa: F401

from daedalus import goal_play, play_analytics, playtest

# A game a player can win and lose in a few inputs: Right scores, Left loses a life (3 lives), R restarts.
GAME = """<!doctype html><html><head><title>Dot</title></head><body style="margin:0;background:#123">
<canvas id=c width=960 height=600></canvas><div id=hud style="color:#fff;font:24px sans-serif">score 0 lives 3</div>
<script>
let s = {score: 0, lives: 3, over: false};
const hud = document.getElementById('hud'), g = document.getElementById('c').getContext('2d');
function draw(){ g.fillStyle = s.over ? '#600' : '#123'; g.fillRect(0,0,960,600); g.fillStyle='#ff0';
  g.fillRect(100 + s.score * 40, 300, 30, 30);
  hud.textContent = s.over ? 'GAME OVER score ' + s.score : 'score ' + s.score + ' lives ' + s.lives; }
addEventListener('keydown', e => {
  if (e.code === 'KeyR') s = {score: 0, lives: 3, over: false};
  else if (!s.over && e.code === 'ArrowRight') s.score++;
  else if (!s.over && e.code === 'ArrowLeft') { s.lives--; s.over = s.lives <= 0; }
  draw(); });
draw();
</script></body></html>"""


def _browser_ok() -> bool:
    try:
        from daedalus.playground_browser import chromium_executable  # noqa: F401
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch().close()
        return True
    except Exception:
        return False


browser_needed = pytest.mark.skipif(not _browser_ok(), reason="Chromium/Playwright not available")


@pytest.fixture
def game(a0_tmp):  # noqa: F811
    from daedalus import playground as pg

    slug = asyncio.run(pg.create_project("Dot", kind="static"))["slug"]
    pg.write_file(slug, "index.html", GAME)
    return slug


# ── real players ────────────────────────────────────────────────────────────────────────────────────────────────
def test_a_visit_record_keeps_only_clamped_numbers_and_capped_text():
    row = play_analytics.clean({"ms": 10**12, "first_input_ms": -5, "keys": "7", "clicks": None,
                                "errors": ["x" * 999, "b", "c", "d"], "events": {"score": 3, "": 1, "bad": "z"},
                                "ip": "1.2.3.4", "ua": "Mozilla"})
    assert row["ms"] == 6 * 3600 * 1000 and row["first_input_ms"] == 0 and row["keys"] == 7 and row["clicks"] == 0
    assert len(row["errors"]) == 3 and len(row["errors"][0]) == 160 and row["events"] == {"score": 3}
    assert "ip" not in row and "ua" not in row
    assert play_analytics.clean({"keys": 3}) is None and play_analytics.clean([1]) is None


def test_records_are_capped_rate_limited_and_summarised(game, monkeypatch):
    monkeypatch.setattr(play_analytics, "_recent", {})
    assert play_analytics.record(game, b"x" * 5000) == (False, "too_large")
    assert play_analytics.record(game, b"not json") == (False, "bad_record")
    visits = [{"ms": 4000, "first_input_ms": None, "keys": 0, "clicks": 0},
              {"ms": 60000, "first_input_ms": 2000, "keys": 40, "clicks": 1, "events": {"score": 5}},
              {"ms": 90000, "first_input_ms": 3000, "keys": 70, "clicks": 0, "errors": ["boom is not defined"],
               "events": {"score": 9, "game_over": 1}}]
    for v in visits:
        assert play_analytics.record(game, json.dumps(v).encode()) == (True, "")
    s = play_analytics.summary(game)
    assert s["sessions"] == 3 and s["median_s"] == 60.0 and s["left_within_10s_pct"] == 33
    assert s["no_input_pct"] == 33 and s["median_first_input_s"] == 2.5 and s["error_sessions_pct"] == 33
    assert s["top_errors"] == [{"message": "boom is not defined", "sessions": 1}]
    assert s["events_per_session"] == {"score": 4.67, "game_over": 0.33}
    text = play_analytics.format_for_prompt(game)
    assert "REAL PLAYERS" in text and "33% left within 10s" in text and "boom is not defined" in text
    monkeypatch.setattr(play_analytics, "RATE_PER_MINUTE", 3)
    monkeypatch.setattr(play_analytics, "_recent", {})
    for _ in range(3):
        play_analytics.record(game, b'{"ms": 1}')
    assert play_analytics.record(game, b'{"ms": 1}') == (False, "rate_limited")
    monkeypatch.setattr(play_analytics, "MAX_SESSIONS", 4)
    monkeypatch.setattr(play_analytics, "_recent", {})
    play_analytics.record(game, b'{"ms": 2}')
    assert len(play_analytics.sessions(game)) == 4


def test_too_few_visits_say_nothing(game):
    play_analytics.record(game, b'{"ms": 1000}')
    assert play_analytics.format_for_prompt(game) == ""


def test_a_published_game_reports_its_visits_and_only_the_owner_reads_them(game, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from daedalus import playground_publish as pub

    monkeypatch.setattr(play_analytics, "_recent", {})
    client = TestClient(app)
    assert client.post(f"/apps/{game}/analytics", content=b'{"ms": 1}').status_code == 404      # not published
    assert asyncio.run(pub.publish(game))["ok"]
    page = client.get(f"/apps/{game}/").text
    assert "PlaygroundAnalytics" in page and f"/apps/' + encodeURIComponent(slug) + '/analytics" in page
    assert client.post(f"/apps/{game}/analytics", content=json.dumps({"ms": 5000, "keys": 3}).encode()).json() == \
        {"ok": True}
    assert client.post(f"/apps/{game}/analytics", content=b"{").status_code == 400
    assert client.post(f"/apps/{game}/analytics", content=b"x" * 5000).status_code == 413
    assert play_analytics.summary(game)["sessions"] == 1
    assert client.get(f"/apps/api/{game}/analytics").status_code == 401


@browser_needed
def test_a_real_visit_sends_one_record_when_the_page_is_left(game):
    """The injected script itself: keys counted, a game event tracked, one beacon on leaving, nothing with DNT."""
    from playwright.sync_api import sync_playwright

    from daedalus import playground as pg
    from daedalus.playground_browser import _serve_static
    from daedalus.playground_router import _inject_playground_scripts

    html = GAME.replace("draw();\n</script>", "draw(); PlaygroundAnalytics.track('level');\n</script>")
    pg.write_file(game, "index.html", _inject_playground_scripts(html, game))
    base, stop = _serve_static(pg.project_dir(game))
    bodies: list[dict] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            for dnt in (False, True):
                ctx = browser.new_context(extra_http_headers={"DNT": "1"} if dnt else {})
                if dnt:
                    ctx.add_init_script("Object.defineProperty(navigator, 'doNotTrack', {get: () => '1'})")
                ctx.route("**/analytics", lambda r: (bodies.append(json.loads(r.request.post_data or "{}")),
                                                     r.fulfill(status=200, body="{}")))
                page = ctx.new_page()
                page.goto(base + "/")
                page.keyboard.press("ArrowRight")
                page.keyboard.press("ArrowRight")
                page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")   # still visible: no send
                page.evaluate("window.dispatchEvent(new Event('pagehide')); window.dispatchEvent(new Event('pagehide'))")
                page.wait_for_timeout(500)
                ctx.close()
            browser.close()
    finally:
        stop()
    assert len(bodies) == 1, bodies                                   # once per visit, and none with Do Not Track
    assert bodies[0]["keys"] == 2 and bodies[0]["first_input_ms"] is not None and bodies[0]["events"] == {"level": 1}


# ── the goal-directed session ───────────────────────────────────────────────────────────────────────────────────
def test_only_known_inputs_inside_the_screen_are_played():
    acts = goal_play.actions_from({"actions": [
        {"key": "ArrowRight", "hold_ms": 99999}, {"key": "rm -rf"}, {"click": [10, 20]}, {"click": [5000, 1]},
        {"click": ["a", 1]}, "Space", {"key": "Space"}, {"key": "Enter"}, {"key": "KeyR"}]})
    assert acts == [("key", ("ArrowRight", 1500)), ("click", (10, 20)), ("key", ("Space", 0)), ("key", ("Enter", 0)),
                    ("key", ("KeyR", 0))]
    assert goal_play.actions_from({"actions": "ArrowRight"}) == []
    assert goal_play._kept(3) == [0, 1, 2] and goal_play._kept(10) == [0, 2, 4, 5, 7, 9]


@browser_needed
def test_a_player_plays_toward_goals_and_its_frames_follow_the_play(game):
    plan = [  # what the "model" sees and does each turn
        {"actions": [{"key": "Enter"}], "reached": ["start the game"]},
        {"actions": [{"key": "ArrowRight"}, {"key": "ArrowRight"}], "reached": []},
        {"actions": [{"key": "ArrowLeft"}, {"key": "ArrowLeft"}, {"key": "ArrowLeft"}],
         "reached": ["score at least once"], "note": "the dot moved right and the score went up"},
        {"actions": [{"key": "KeyR"}], "reached": ["lose a life or reach game over", "not a goal"]},
        {"actions": [], "reached": ["restart and play again"]},
        {"actions": [{"key": "ArrowRight"}], "reached": []},
    ]
    seen: list[tuple[str, int]] = []

    async def see(system, user, images):
        seen.append((user, len(images)))
        return json.dumps(plan[len(seen) - 1])

    r = asyncio.run(goal_play.goal_play(game, "static", see=see, steps=8))
    assert r["ran"] and r["turns"] == 5 and r["missed"] == [] and r["reached"] == list(goal_play.GOALS)
    assert all(n == 1 for _, n in seen) and "(reached)" in seen[2][0] and "turn 2: ArrowRight, ArrowRight" in seen[2][0]
    assert 2 <= len(r["frames"]) <= goal_play.FRAMES_KEPT and len(r["shas"]) == len(r["frames"])
    assert "[frame 1] turn 1: Enter" in r["inputs"] and "all goals reached" in r["inputs"]
    assert r["notes"] == ["the dot moved right and the score went up"] and r["errors"] == []


@browser_needed
def test_a_stopped_model_keeps_what_was_played(game):
    calls = []

    async def see(system, user, images):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("budget reached")
        return '{"actions": [{"key": "ArrowRight"}], "reached": ["start the game"]}'

    r = asyncio.run(goal_play.goal_play(game, "static", see=see, steps=8))
    assert r["turns"] == 1 and r["stopped"] == "budget reached" and r["frames"]
    assert r["missed"] == list(goal_play.GOALS[1:])


def test_no_build_no_session(game):
    from daedalus import playground as pg

    (pg.project_dir(game) / "index.html").unlink()

    async def see(*a):
        raise AssertionError("no model call without a game to play")

    r = asyncio.run(goal_play.goal_play(game, "static", see=see))
    assert r["ran"] is False and r["stopped"] == "no playable build" and r["frames"] == []


def test_the_players_judge_the_session_they_played(game):
    session = {"ran": True, "turns": 6, "goals": list(goal_play.GOALS), "reached": ["start the game"],
               "missed": ["score at least once"], "frames": [{"type": "image", "sha256": "ab"}], "shas": ["ab"],
               "inputs": "[frame 1] turn 1: Enter", "errors": ["x is not defined"]}
    systems: list[str] = []

    async def see(system, user, images):
        systems.append(system)
        assert images == session["frames"]
        return json.dumps({"loop_clarity": 2, "controls": 2, "first_30s": 2, "win_lose": 2, "frustration": 4,
                           "would_quit": True, "quote": "nothing scored", "fix": "show how to score"})

    r = asyncio.run(playtest.run_playtest(game, n=2, see_fn=see, session=session))
    assert r["mode"] == "played" and r["goals"]["missed"] == ["score at least once"]
    assert "6 turns" in systems[0] and "You never managed: score at least once" in systems[0]
    assert any("never managed: score at least once" in b for b in r["blockers"])


def test_epic_audience_plays_a_goal_session_and_craft_does_not(game, monkeypatch):
    from daedalus import mentor_lead, task_effort

    sessions, reports = [], []

    async def fake_goal_play(slug, kind, *, see, **kw):
        sessions.append(slug)
        return {"ran": True, "turns": 8, "frames": [{"sha256": "f"}], "shas": ["f"], "goals": ["start the game"],
                "reached": [], "missed": ["start the game"], "errors": ["boom"], "inputs": ""}

    async def fake_playtest(slug, **kw):
        reports.append(kw)
        goals = kw.get("session") and {"turns": 8, "missed": ["start the game"], "errors": ["boom"]}
        return {"mode": "played", "many_quit": True, "summary": "s", "quotes": ["q"], "fixes": ["f"],
                "goals": goals}

    monkeypatch.setattr("daedalus.goal_play.goal_play", fake_goal_play)
    monkeypatch.setattr("daedalus.playtest.run_playtest", fake_playtest)

    def loop():
        return types.SimpleNamespace(slug=game, kind="static", lead_accepted=True, lead_fix_used=0,
                                     lead_play_frames=([{"sha256": "lead"}], ["lead"]),
                                     route=types.SimpleNamespace(playbook=types.SimpleNamespace(id="playground.arcade")))

    with task_effort.bind({"tier": "epic"}):
        nudge = asyncio.run(mentor_lead.audience_gate(loop(), "make a dot game"))
    assert sessions == [game] and reports[0]["session"]["turns"] == 8
    assert "8-turn goal session the player never managed: start the game" in nudge and "boom" in nudge
    asyncio.run(mentor_lead.audience_gate(loop(), "make a dot game"))
    assert sessions == [game] and reports[1]["session"] is None and reports[1]["frames"][1] == ["lead"]


def test_the_studio_row_shows_the_goals():
    from daedalus import activity

    row = activity.compact({"seq": 1, "ts": 1.0, "type": "playtest", "payload": {
        "mode": "played", "frames": 6, "players": 4, "would_quit": 1,
        "goals": {"turns": 8, "reached": ["start the game"], "missed": ["score at least once"]}}})
    assert row["goals"] == {"turns": 8, "reached": ["start the game"], "missed": ["score at least once"]}
