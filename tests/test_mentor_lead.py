"""Mentor as lead programmer: plans the build, then accepts it or sends it back. It never writes code.

Owner's design (2026-11-11): the Mentor is the stronger, more expensive model and supervises like a lead programmer.
The Daedalus model does the work. Found live the same day: a build passed every kernel check with half the request
missing, and the director, never told the project existed, advised playground_new."""
from __future__ import annotations

import asyncio

import pytest
from a0_support import a0_tmp  # noqa: F401

from daedalus import mentor_lead, native_tools
from daedalus.build_loop import BuildLoop
from daedalus.tool_router import route

GAME = ("Направи игра Star Catcher: кораб долу със стрелките хваща падащи звезди, метеорите се избягват, "
        "3 живота, Game Over с рестарт с Enter.")


class Calls:
    """Stands in for the Mentor model: answers in order, records what it was asked."""

    def __init__(self, *answers):
        self.answers, self.seen = list(answers), []

    async def __call__(self, system, user, *, max_tokens, images=None, client_id):
        self.seen.append({"system": system, "user": user, "images": len(images or []), "client": client_id})
        return self.answers.pop(0), {"model": "lead-model", "provider": "openrouter", "ms": 5}


_REAL_PLAY_FRAMES = mentor_lead.play_frames


@pytest.fixture(autouse=True)
def _no_browser_play(monkeypatch):
    """Reviews in these tests judge source only; one test below drives real Chromium on purpose."""
    async def none(slug, kind="static"):
        return [], []

    monkeypatch.setattr(mentor_lead, "play_frames", none)


@pytest.fixture
def lead_on(monkeypatch):
    monkeypatch.setattr(mentor_lead, "unavailable_reason", lambda: None)


def test_the_lead_plans_from_the_owners_words(lead_on, monkeypatch):
    calls = Calls({"brief": "Arcade catcher.", "checklist": ["ship moves with arrows", "meteors to dodge",
                                                             "3 lives", "Enter restarts"],
                   "look": ["neon glow"], "steps": ["write index.html"]})
    monkeypatch.setattr(mentor_lead, "_ask", calls)
    plan = asyncio.run(mentor_lead.kickoff(GAME, slug="", kind="static"))
    assert plan["checklist"][1] == "meteors to dodge" and plan["model"] == "lead-model"
    assert "OWNER REQUEST" in calls.seen[0]["user"] and "метеорите" in calls.seen[0]["user"]
    assert "never write code" in calls.seen[0]["system"]
    block = mentor_lead.plan_block(plan)
    assert "LEAD PLAN" in block and "[ ] meteors to dodge" in block and "1. write index.html" in block


def test_no_lead_means_no_call_and_no_block(monkeypatch):
    monkeypatch.setenv("DAEDALUS_MENTOR", "0")

    async def never(*a, **k):
        raise AssertionError("the Mentor model must not be called when the Mentor is off")

    monkeypatch.setattr(mentor_lead, "_ask", never)
    assert mentor_lead.unavailable_reason() == "mentor_off"
    assert asyncio.run(mentor_lead.kickoff(GAME)) is None
    loop = BuildLoop(route(GAME), GAME)
    loop.slug = "star"
    assert asyncio.run(mentor_lead.acceptance_nudge(loop, GAME)) is None
    assert loop.lead_accepted is None                              # no review happened, and none is claimed


def test_a_bad_answer_is_no_plan(lead_on, monkeypatch):
    monkeypatch.setattr(mentor_lead, "_ask", Calls(None))
    assert asyncio.run(mentor_lead.kickoff(GAME)) is None
    monkeypatch.setattr(mentor_lead, "_ask", Calls({"brief": "x", "checklist": []}))
    assert asyncio.run(mentor_lead.kickoff(GAME)) is None


@pytest.fixture
def project(a0_tmp):  # noqa: F811
    from daedalus import playground as pg

    slug = asyncio.run(pg.create_project("Star", kind="static"))["slug"]
    pg.write_file(slug, "index.html", "<canvas id=c></canvas><script>/* stars */</script>\n")
    return slug


def test_the_lead_sends_back_what_is_missing_then_accepts(lead_on, monkeypatch, project):
    calls = Calls(
        {"accept": False, "met": ["3 lives"], "missing": ["meteors to dodge"],
         "fixes": ["index.html: spawn meteors that cost a life"], "observations": ["stars fall"]},
        {"accept": True, "met": ["3 lives", "meteors to dodge"], "missing": []},
    )
    monkeypatch.setattr(mentor_lead, "_ask", calls)
    loop = BuildLoop(route(GAME), GAME)
    loop.slug, loop.stop_reason = project, "complete"
    loop.lead_plan = {"checklist": ["3 lives", "meteors to dodge"]}
    nudge = asyncio.run(mentor_lead.acceptance_nudge(loop, GAME))
    assert "LEAD REVIEW" in nudge and "[ ] meteors to dodge" in nudge and "spawn meteors" in nudge
    # the kernel's verdict stands; the lead's own verdict is separate (review 2026-11-11)
    assert loop.stop_reason == "complete" and loop.lead_fix_used == 1 and loop.lead_accepted is None
    assert "=== index.html ===" in calls.seen[0]["user"] and "- meteors to dodge" in calls.seen[0]["user"]
    assert asyncio.run(mentor_lead.acceptance_nudge(loop, GAME)) is None
    assert loop.lead_accepted is True
    assert any("ACCEPTED" in line for line in mentor_lead.summary_lines(loop))


def test_the_lead_cannot_hold_a_turn_forever(lead_on, monkeypatch, project):
    no = {"accept": False, "missing": ["particles"], "fixes": ["add particles"]}
    monkeypatch.setattr(mentor_lead, "_ask", Calls(*[dict(no) for _ in range(5)]))
    loop = BuildLoop(route(GAME), GAME)
    loop.slug = project
    nudges = [asyncio.run(mentor_lead.acceptance_nudge(loop, GAME)) for _ in range(5)]
    assert sum(1 for n in nudges if n) == mentor_lead.LEAD_FIX_ROUNDS
    assert loop.lead_reviews == mentor_lead.MAX_REVIEWS and loop.lead_accepted is False
    assert any("NOT accepted" in line and "particles" in line for line in mentor_lead.summary_lines(loop))


def test_observations_without_frames_are_dropped(lead_on, monkeypatch, project):
    monkeypatch.setattr(mentor_lead, "_ask", Calls({"accept": True, "observations": ["a ship"], "missing": []}))
    loop = BuildLoop(route(GAME), GAME)
    loop.slug = project
    asyncio.run(mentor_lead.acceptance_nudge(loop, GAME))
    assert loop.lead_review["saw_frames"] == 0 and loop.lead_review["observations"] == []


def test_the_build_loop_asks_the_lead_before_it_ends(lead_on, monkeypatch, project):
    """Kernel says complete -> lead reviews -> one more writer round -> lead accepts."""
    from daedalus.visual_proof import ProofResult

    async def _exec(name, args, **_kw):
        return f"── {name} OK {args.get('slug')}/{args.get('path')} ──"

    async def _proof(slug, kind="static", **_k):
        return ProofResult(ok=True, skipped=False, kind="static", slug=slug, tool="browser", summary="ok", frames=[])

    monkeypatch.setattr(native_tools, "execute_native_tool", _exec)
    monkeypatch.setattr("daedalus.visual_proof.run_visual_proof", _proof)
    monkeypatch.setattr("daedalus.playable.loop_wants_computer_play", lambda loop: False)
    calls = Calls({"accept": False, "missing": ["meteors"], "fixes": ["index.html: add meteors"]},
                  {"accept": True, "missing": []})
    monkeypatch.setattr(mentor_lead, "_ask", calls)

    async def _no_tests_gate(loop, request):       # the project-tests gate has its own tests (test_project_tests)
        return None

    monkeypatch.setattr(mentor_lead, "tests_gate", _no_tests_gate)

    def use(tool, **args):
        return {"type": "tool_use", "id": f"c{len(seen)}{tool}", "name": tool, "input": args}

    seen: list = []
    rounds = [
        [use("write_file", slug=project, path="index.html", content="<canvas>")],
        [use("run_command", command="ls", slug=project)],
        [{"type": "text", "text": "Готово."}],
        [use("write_file", slug=project, path="index.html", content="<canvas> meteors")],
        [{"type": "text", "text": "Готово, с метеори."}],
    ]

    async def model(system, messages, tools):
        seen.append(messages[-1])
        r = rounds.pop(0) if rounds else [{"type": "text", "text": "done"}]
        return {"stop_reason": "tool_use" if r[0]["type"] == "tool_use" else "end_turn", "content": r}

    loop = BuildLoop(route(GAME), GAME, fix_rounds=3)
    loop.slug = project
    asyncio.run(native_tools.run_native_discovery_pass(
        "sys", GAME, model, memory=None, session_id="", read_file_fn=lambda *a, **k: {},
        max_rounds=4, tools=[{"name": "x"}], loop=loop))
    assert len(calls.seen) == 2 and loop.lead_accepted is True and loop.stop_reason == "complete"
    sent_back = [m for m in seen if "LEAD REVIEW" in str(m.get("content"))]
    assert len(sent_back) == 1 and "add meteors" in str(sent_back[0]["content"])


def test_director_never_steers_an_existing_project_to_playground_new():
    from daedalus.mentor_graph import guard_steer

    out = guard_steer({"action": "call_tool", "next_tool": "playground_new", "forbid_tools": []},
                      {"slug": "star", "project_exists": True, "graph": {"tools": ["playground_new", "write_file"]}},
                      {"next_tool": "write_file"})
    assert out["next_tool"] == "write_file" and "playground_new" in out["forbid_tools"]


def test_the_director_digest_names_the_project_and_the_request(project, monkeypatch):
    from daedalus import mentor, mentor_helm

    loop = BuildLoop(route(GAME), "add meteors please")
    loop.slug = project
    loop.lead_plan = {"checklist": ["meteors to dodge"]}
    digest = mentor.inspect_digest(loop)
    assert digest["slug"] == project and digest["project_exists"] is True
    assert digest["owner_request"] == "add meteors please" and digest["checklist"] == ["meteors to dodge"]
    prompt = mentor_helm.build_user_prompt(digest)
    assert '"project_exists":true' in prompt and "add meteors please" in prompt


def test_a_failed_look_is_not_a_failed_build():
    from daedalus.job_contract import unrecovered_failure_indexes

    calls = [{"tool": "read_file", "ok": False}, {"tool": "list_dir", "ok": False},
             {"tool": "write_file", "ok": True}]
    assert unrecovered_failure_indexes(calls) == []
    assert unrecovered_failure_indexes([{"tool": "write_file", "ok": False}]) == [0]


def test_owner_request_is_the_owners_words_not_the_prompt():
    assert mentor_lead.request_text("fallback text") == "fallback text"
    with mentor_lead.owner_request("make it red"):
        assert mentor_lead.request_text("[PLAYGROUND x]\n<files>\n\nmake it red") == "make it red"


def test_kernel_status_files_do_not_hide_the_proof_frame(project):
    """The kernel rewrites README.md and the verify screenshot right after the proof; the frame must still count."""
    import os
    import time

    import struct
    import zlib

    from daedalus import playground as pg
    from daedalus.harness import perception

    def png(w: int, h: int) -> bytes:                     # a gradient RGB PNG without PIL (CI has no Pillow)
        rows = b"".join(b"\x00" + bytes(v for x in range(w) for v in (x * 4, y * 5, 120)) for y in range(h))

        def chunk(tag: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))

    root = pg.project_dir(project)
    proof = root / "studio" / "proof"
    proof.mkdir(parents=True, exist_ok=True)
    (proof / "frame_000.png").write_bytes(png(64, 48))
    later = time.time() + 5
    for rel in ("README.md", "assets/_verify_screenshot.png"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(b"status")
        os.utime(root / rel, (later, later))
    assert [r.source for r in perception.latest_frames(project).refs] == ["visual_proof"]
    os.utime(root / "index.html", (later, later))                     # a real source edit still makes it stale
    assert perception.latest_frames(project).refs == []


def test_director_never_opens_a_game_nothing_was_written_for():
    from daedalus.mentor_graph import guard_steer

    out = guard_steer({"action": "call_tool", "next_tool": "browser", "forbid_tools": []},
                      {"slug": "moss", "project_exists": True, "wrote_source": False,
                       "graph": {"tools": ["browser", "write_file"]}}, {"next_tool": "browser"})
    assert out["next_tool"] == "write_file"
    kept = guard_steer({"action": "call_tool", "next_tool": "browser", "forbid_tools": []},
                       {"slug": "moss", "project_exists": True, "wrote_source": True,
                        "graph": {"tools": ["browser", "write_file"]}}, {"next_tool": "browser"})
    assert kept["next_tool"] == "browser"


def test_a_no_without_a_defect_is_not_a_rejection(lead_on, monkeypatch, project):
    """Found live: the lead said accept=false with nothing missing; the writer got a round with nothing to do."""
    monkeypatch.setattr(mentor_lead, "_ask", Calls(
        {"accept": False, "missing": [], "unverified": ["3 levels: levels.js not shown"]},
        {"accept": True, "missing": ["Enter does not restart"], "fixes": ["game.js: restart on Enter"]}))
    monkeypatch.setattr(mentor_lead, "play_frames", _no_frames)
    loop = BuildLoop(route(GAME), GAME)
    loop.slug = project
    assert asyncio.run(mentor_lead.acceptance_nudge(loop, GAME)) is None and loop.lead_accepted is True
    assert loop.lead_review["unverified"] == ["3 levels: levels.js not shown"]
    loop.lead_reviews = 0
    nudge = asyncio.run(mentor_lead.acceptance_nudge(loop, GAME))
    assert nudge and "Enter does not restart" in nudge


async def _no_frames(slug, kind="static"):
    return [], []


def test_the_lead_plays_a_web_game_before_judging_it(project):
    """Start screen, then frames after pressing start keys and moving (real Chromium)."""
    pytest.importorskip("playwright")
    from daedalus import playground as pg

    pg.write_file(project, "index.html", (
        "<canvas id=c width=320 height=200></canvas><script>let s='title',x=10;"
        "addEventListener('keydown',e=>{if(e.key==='Enter')s='play';if(e.key==='ArrowRight')x+=40});"
        "const g=c.getContext('2d');(function f(){g.fillStyle=s==='play'?'#204':'#000';g.fillRect(0,0,320,200);"
        "g.fillStyle='#0f0';g.fillRect(x,100,20,20);requestAnimationFrame(f)})()</script>"))
    try:
        parts, shas = asyncio.run(_REAL_PLAY_FRAMES(project, "static"))
    except Exception as exc:  # pragma: no cover - only when Chromium itself cannot start here
        pytest.skip(f"chromium unavailable: {exc}")
    if not parts:
        pytest.skip("chromium unavailable in this environment")
    assert len(parts) == 4 and len(set(shas)) >= 2          # play changed the picture
    assert all(p["type"] == "image" for p in parts)


def test_a_crossing_game_that_moves_only_up_is_seen_moving(project):
    """Live 2026-11-12: the frog of a crossing game never moved under a platformer-only script."""
    pytest.importorskip("playwright")
    from daedalus import playground as pg

    pg.write_file(project, "index.html", (
        "<canvas id=c width=320 height=200></canvas><script>let y=180;"
        "addEventListener('keydown',e=>{if(e.key==='ArrowUp')y-=30});"
        "const g=c.getContext('2d');(function f(){g.fillStyle='#000';g.fillRect(0,0,320,200);"
        "g.fillStyle='#0f0';g.fillRect(150,y,20,20);requestAnimationFrame(f)})()</script>"))
    parts, shas = asyncio.run(_REAL_PLAY_FRAMES(project, "static"))
    if not parts:
        pytest.skip("chromium unavailable in this environment")
    assert shas[1] != shas[0]                                # the mid-play frame shows the hops


def test_the_owners_words_are_taken_once():
    """A nested turn inside the first one (subagent, planner) is routed on its own text (review 2026-11-11)."""
    with mentor_lead.owner_request("make a game"):
        assert mentor_lead.take_request() == "make a game"
        assert mentor_lead.take_request() == ""
    assert mentor_lead.take_request() == ""


def test_a_crash_while_planning_never_stops_the_build(lead_on, monkeypatch):
    def boom(slug, limit=30):
        raise FileNotFoundError("gone between walk and stat")

    monkeypatch.setattr(mentor_lead, "project_files", boom)
    assert asyncio.run(mentor_lead.kickoff(GAME, slug="x")) is None


def test_project_files_skip_node_modules(project):
    from daedalus import playground as pg

    nm = pg.project_dir(project) / "node_modules" / "phaser"
    nm.mkdir(parents=True)
    (nm / "phaser.js").write_text("x")
    assert all("node_modules" not in p for p, _ in mentor_lead.project_files(project))


def test_an_unbuilt_vite_game_is_not_played(project):
    from daedalus import playground as pg

    pg.write_file(project, "src/main.js", "console.log(1)")
    assert asyncio.run(_REAL_PLAY_FRAMES(project, "vite")) == ([], [])


def test_a_review_cut_at_the_output_limit_is_asked_again_with_more_room(monkeypatch):
    """Live 2026-11-12: a 10-item review was cut mid-JSON at 700 tokens and the acceptance silently failed (bad_json)."""
    import asyncio as _aio

    from daedalus import mentor_lead as ml
    from daedalus.harness import gateway as gw

    asked = []

    async def call(intent, messages, *, max_tokens, **kw):
        asked.append(max_tokens)
        if len(asked) == 1:
            return gw.GatewayResult(True, text='{"met": ["a", "b"', usage={"completion_tokens": max_tokens})
        return gw.GatewayResult(True, text='{"met": ["a"], "missing": []}', usage={"completion_tokens": 20})

    monkeypatch.setattr(gw, "call", call)
    monkeypatch.setattr("daedalus.mentor_helm._resolve_endpoint", lambda: ("openrouter", "m"))
    monkeypatch.setattr("daedalus.mentor_helm.record_spend", lambda n: None)
    data, meta = _aio.run(ml._ask("sys", "user", max_tokens=ml.REVIEW_MAX_OUT, client_id="t"))
    assert data == {"met": ["a"], "missing": []} and asked == [ml.REVIEW_MAX_OUT, ml.REVIEW_MAX_OUT * 2]
    asked.clear()

    async def bad(intent, messages, *, max_tokens, **kw):
        asked.append(max_tokens)
        return gw.GatewayResult(True, text="not json", usage={"completion_tokens": 5})

    monkeypatch.setattr(gw, "call", bad)
    data, meta = _aio.run(ml._ask("sys", "user", max_tokens=100, client_id="t"))
    assert data is None and meta["error"] == "bad_json" and asked == [100]       # not cut: no second call


def test_no_second_retry_when_the_gateway_already_retried(monkeypatch):
    """Review 2026-11-12: summed usage after the gateway's own retry looked like a cut reply: up to four calls."""
    import asyncio as _aio

    from daedalus import mentor_lead as ml
    from daedalus.harness import gateway as gw

    asked = []

    async def call(intent, messages, *, max_tokens, **kw):
        asked.append(max_tokens)
        return gw.GatewayResult(True, text="not json", usage={"completion_tokens": max_tokens * 5},
                                retried_max_tokens=max_tokens * 4)

    monkeypatch.setattr(gw, "call", call)
    monkeypatch.setattr("daedalus.mentor_helm._resolve_endpoint", lambda: ("openrouter", "m"))
    monkeypatch.setattr("daedalus.mentor_helm.record_spend", lambda n: None)
    data, meta = _aio.run(ml._ask("sys", "user", max_tokens=700, client_id="t"))
    assert data is None and meta["error"] == "bad_json" and asked == [700]


def test_a_click_game_is_seen_reacting_to_clicks(project):
    """Live 2026-11-12: a click game (pop the bubbles) saw one centre click; its players said nothing reacted."""
    pytest.importorskip("playwright")
    from daedalus import playground as pg

    pg.write_file(project, "index.html", (
        "<canvas id=c width=960 height=640></canvas><script>let hits=[];"
        "c.addEventListener('click',e=>hits.push([e.offsetX,e.offsetY]));"
        "const g=c.getContext('2d');(function f(){g.fillStyle='#000';g.fillRect(0,0,960,640);g.fillStyle='#f0f';"
        "for(const[x,y]of hits)g.fillRect(x-10,y-10,20,20);requestAnimationFrame(f)})()</script>"))
    parts, shas = asyncio.run(_REAL_PLAY_FRAMES(project, "static"))
    if not parts:
        pytest.skip("chromium unavailable in this environment")
    assert shas[1] != shas[0] and shas[3] != shas[1]          # clicks across the field show up mid-play and later


def test_the_click_sweep_never_presses_a_link_or_a_button(project):
    """Review 2026-11-12: a sweep click on a help link or a pause button would change what the frames show."""
    pytest.importorskip("playwright")
    from daedalus import playground as pg

    pg.write_file(project, "index.html", (
        "<style>body{margin:0}a,button{position:absolute;width:120px;height:60px}</style>"
        "<a href='help.html' style='left:240px;top:230px'>help</a>"
        "<button style='left:600px;top:230px'>pause</button>"
        "<canvas id=c width=960 height=640></canvas><script>document.title='ok'</script>"))
    interactive = []

    async def run():
        from playwright.async_api import async_playwright

        from daedalus import mentor_lead
        from daedalus.playground_browser import _serve_static
        base, stop = _serve_static(pg.project_dir(project))
        try:
            async with async_playwright() as p:
                b = await p.chromium.launch()
                page = await b.new_page(viewport={"width": 960, "height": 640})
                await page.goto(base + "/")
                for xy in ([300, 260], [660, 260]):
                    interactive.append(await page.evaluate(mentor_lead._INTERACTIVE_AT, xy))
                interactive.append(await page.evaluate(mentor_lead._INTERACTIVE_AT, [480, 500]))   # the canvas
                await b.close()
        finally:
            stop()
    try:
        asyncio.run(run())
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"chromium unavailable: {exc}")
    assert interactive == [True, True, False]     # link and button skipped; the canvas is clicked
