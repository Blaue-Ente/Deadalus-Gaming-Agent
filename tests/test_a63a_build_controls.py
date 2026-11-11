"""A6.3a: Build run controls (autopilot pause/resume, resume info, plan clear/create/expand) and Full Auto M0→M8 (API level)."""
from __future__ import annotations

import asyncio
import json

import pytest
from a0_support import a0_tmp  # noqa: F401
from fastapi.testclient import TestClient

B = "/api/v1/harness/build"
PLAN = {"title": "Space", "stages": [{"id": 1, "title": "a", "status": "done"}, {"id": 2, "title": "b", "status": "pending"},
                                     {"id": 3, "title": "c", "status": "pending"}]}


@pytest.fixture
def client(a0_tmp):  # noqa: F811
    from app.main import app
    from daedalus.harness import full_auto

    full_auto._runs_by_slug.clear()
    return TestClient(app)


@pytest.fixture
def slug(client):
    from daedalus import playground as pg

    return asyncio.run(pg.create_project("Orbit", kind="static", description="a small space game"))["slug"]


def _save(slug, plan):
    from daedalus import playground as pg

    asyncio.run(pg.save_autopilot_plan(slug, plan))


def _load(slug):
    from daedalus import playground as pg

    return asyncio.run(pg.load_autopilot_plan(slug))


# ------------------------------------------------------------------------------------------------ controls
def test_state_reports_the_plan_and_what_a_resume_would_continue(client, slug):
    data = client.get(f"{B}/{slug}/controls").json()
    assert data["ok"] is True and data["plan"] is None and data["resume"] is None and data["autopilot"]["running"] is False and data["game_studio"] is False
    _save(slug, PLAN)
    data = client.get(f"{B}/{slug}/controls").json()
    assert data["plan"] == {"title": "Space", "stages": 3, "done": 1, "ultra": False, "studio": False}
    assert data["resume"] is None                                   # the saved plan is NOT what a resumed autopilot continues
    from daedalus.run_engine import save_checkpoint

    ckpt_plan = {"title": "Autopilot plan", "stages": [{"title": "s1"}, {"title": "s2"}, {"title": "s3"}, {"title": "s4"}]}
    save_checkpoint("run_x", {"stage": 2, "slug": slug, "plan": ckpt_plan}, slug=slug)
    data = client.get(f"{B}/{slug}/controls").json()
    assert data["resume"] == {"plan_title": "Autopilot plan", "done_stages": 2, "total_stages": 4, "next_stage": "s3"}
    save_checkpoint("run_y", {"stage": 4, "slug": slug, "plan": ckpt_plan}, slug=slug)
    assert client.get(f"{B}/{slug}/controls").json()["resume"] is None           # it had finished every stage
    assert client.get(f"{B}/nope/controls").status_code == 404


def test_pause_and_resume_say_when_there_is_nothing_to_pause_or_resume(client, slug, monkeypatch):
    assert client.post(f"{B}/{slug}/autopilot/pause").json() == {"ok": False, "error": "not_running"}
    assert client.post(f"{B}/{slug}/autopilot/resume").status_code == 409
    calls = []

    async def pause(s):
        calls.append(("pause", s))
        return True

    async def resume(s):
        calls.append(("resume", s))
        return True

    monkeypatch.setattr("daedalus.autopilot_jobs.pause_active", pause)
    monkeypatch.setattr("daedalus.autopilot_jobs.resume_active", resume)
    assert client.post(f"{B}/{slug}/autopilot/pause").json() == {"ok": True, "paused": True}
    assert client.post(f"{B}/{slug}/autopilot/resume").json() == {"ok": True, "paused": False}
    assert calls == [("pause", slug), ("resume", slug)]


def test_clearing_the_plan_asks_and_is_refused_while_a_build_owns_the_project(client, slug, monkeypatch):
    _save(slug, PLAN)
    assert client.post(f"{B}/{slug}/plan/clear", json={}).status_code == 400 and _load(slug)
    monkeypatch.setattr("daedalus.harness.full_auto._other_owner", lambda s: "guided")
    busy = client.post(f"{B}/{slug}/plan/clear", json={"confirm": True})
    assert busy.status_code == 409 and busy.json()["owner"] == "guided" and _load(slug)
    monkeypatch.setattr("daedalus.harness.full_auto._other_owner", lambda s: "")
    assert client.post(f"{B}/{slug}/plan/clear", json={"confirm": True}).json() == {"ok": True} and _load(slug) is None


def _fake_llm(monkeypatch, reply=None, exc=None):
    seen = []

    async def complete(system, user, intent="", max_tokens=0):
        seen.append(user)
        if exc:
            raise exc
        return {"reply": reply}

    monkeypatch.setattr("daedalus.llm_client.complete", complete)
    return seen


def test_a_failed_plan_call_saves_nothing_and_says_so(client, slug, monkeypatch):
    _fake_llm(monkeypatch, exc=RuntimeError("model down"))
    res = client.post(f"{B}/{slug}/plan/create", json={"description": "a space shooter"})
    assert res.status_code == 502 and res.json() == {"ok": False, "error": "plan_failed"} and _load(slug) is None
    _fake_llm(monkeypatch, reply="I cannot make a plan")                              # no stages is not a plan either
    assert client.post(f"{B}/{slug}/plan/create", json={"description": "x"}).status_code == 502 and _load(slug) is None


def test_creating_a_plan_saves_the_models_plan_and_asks_before_replacing_one(client, slug, monkeypatch):
    seen = _fake_llm(monkeypatch, reply=json.dumps({"title": "Orbit plan", "stages": [{"id": 1, "title": "Ship", "instructions": "x"},
                                                                                     {"id": 2, "title": "Enemies", "instructions": "y"}]}))
    assert client.post(f"{B}/{slug}/plan/create", json={"description": ""}).status_code == 400
    made = client.post(f"{B}/{slug}/plan/create", json={"description": "a space shooter"}).json()
    assert made["ok"] is True and made["plan"]["stages"] == 2 and seen == ["a space shooter"]
    assert [s["title"] for s in _load(slug)["stages"]] == ["Ship", "Enemies"]
    again = client.post(f"{B}/{slug}/plan/create", json={"description": "something else"})
    assert again.status_code == 400 and again.json()["error"] == "confirm_required" and again.json()["replaces"]["stages"] == 2
    assert len(seen) == 1                                                             # no model call before the answer
    assert client.post(f"{B}/{slug}/plan/create", json={"description": "something else", "confirm": True}).json()["ok"] is True


def test_the_old_route_keeps_its_stand_in_plan(client, slug, monkeypatch):
    _fake_llm(monkeypatch, exc=RuntimeError("down"))
    res = client.post(f"/admin/daedalus/playground/api/projects/{slug}/plan/create", json={"description": "x"}).json()
    assert res["ok"] is True and res["plan"]["stages"][0]["title"] == "Scaffold"            # unchanged legacy behaviour


def test_expanding_the_saved_plan_asks_and_needs_a_plan(client, slug):
    assert client.post(f"{B}/{slug}/plan/expand", json={"confirm": True}).json() == {"ok": False, "error": "no_plan"}
    _save(slug, {"title": "t", "stages": [{"id": 1, "title": "Build the whole game", "instructions": "everything", "status": "pending"}]})
    assert client.post(f"{B}/{slug}/plan/expand", json={}).status_code == 400
    res = client.post(f"{B}/{slug}/plan/expand", json={"confirm": True}).json()
    assert res["ok"] is True and res["before"]["stages"] == 1 and res["plan"]["ultra"] is True
    assert _load(slug)["ultra"] is True


# ------------------------------------------------------------------------------------------------ Full Auto
class Job:
    def __init__(self, id, finals):
        self.id, self.status, self._finals = id, "running", list(finals)

    async def mark_interrupted(self, reason):
        self.status = "interrupted"


class World:
    """A fake Game Studio: milestones M0..Mn, each with pending stages until its Guided job has run."""

    def __init__(self, milestones=2, verify=None, guided_final="done", plan=True, gates=True):
        self.milestones = [f"M{i}" for i in range(milestones)]
        self.index = 0
        self.built: set[str] = set()
        self.verify = verify or {}
        self.guided_final = guided_final
        self.plan, self.gates = plan, gates
        self.calls: list = []
        self.jobs: dict[str, Job] = {}
        self.hold = None                                                             # an Event a Guided job waits on

    def install(self, monkeypatch):
        from daedalus import game_studio, guided_jobs

        monkeypatch.setattr(game_studio, "is_game_studio_slug", lambda s: True)
        monkeypatch.setattr(game_studio, "get_workspace_context", self.ctx)
        monkeypatch.setattr(game_studio, "start_milestone_guided", self.start)
        monkeypatch.setattr(game_studio, "advance_project_milestone", self.advance)
        monkeypatch.setattr("daedalus.game_studio_verify.run_milestone_verify", self.run_verify)
        monkeypatch.setattr(guided_jobs, "get_job", lambda jid: self.jobs.get(jid))
        monkeypatch.setattr(guided_jobs, "stop_active", self.stop)
        monkeypatch.setattr("daedalus.harness.full_auto.POLL_SECONDS", 0.01)

    async def ctx(self, slug):
        done = self.index >= len(self.milestones)
        cur = self.milestones[min(self.index, len(self.milestones) - 1)]
        stages = [{"milestone_id": m, "status": "done" if m in self.built else "pending"} for m in self.milestones]
        return {"project_complete": done, "plan": {"stages": stages} if self.plan else None, "can_guided": self.gates,
                "current_milestone": cur, "milestone_summary": {}}

    async def start(self, slug, mid, auto_approve=False):
        self.calls.append(("guided", mid, auto_approve))
        job = Job(f"job_{mid}", [])
        self.jobs[job.id] = job

        async def finish():
            if self.hold:
                await self.hold.wait()
            if job.status == "running":
                job.status = self.guided_final
                if self.guided_final == "done":
                    self.built.add(mid)

        asyncio.get_running_loop().create_task(finish())
        return {"job_id": job.id}

    async def run_verify(self, slug, mid, on_log=None):
        self.calls.append(("verify", mid))
        return {"ok": self.verify.get(mid, True), "runtime_ok": True}

    async def advance(self, slug, **kw):
        self.calls.append(("advance", kw))
        prev = self.milestones[self.index]
        self.index += 1
        return {"previous": prev, "current_milestone": self.milestones[self.index] if self.index < len(self.milestones) else None}

    async def stop(self, slug):
        self.calls.append(("stop_guided",))
        for job in self.jobs.values():
            if job.status == "running":
                job.status = "interrupted"
        if self.hold:
            self.hold.set()
        return True


def _drive(slug, world, monkeypatch, *, stop_after=None):
    from daedalus.harness import full_auto

    world.install(monkeypatch)

    async def scenario():
        started = await full_auto.start(slug, True)
        if stop_after:
            while not world.calls or world.calls[-1][0] != stop_after:
                await asyncio.sleep(0.01)
            await full_auto.stop(slug)
        await full_auto._runs_by_slug[slug].task
        return started, full_auto.status(slug)["run"]

    return asyncio.run(scenario())


def test_full_auto_needs_a_confirm_a_game_studio_project_and_a_free_project(client, slug, monkeypatch):
    assert client.post(f"{B}/{slug}/full-auto", json={}).status_code == 400
    assert client.post(f"{B}/nope/full-auto", json={"confirm": True}).status_code == 404
    assert client.post(f"{B}/{slug}/full-auto", json={"confirm": True}).json()["error"] == "not_game_studio"
    monkeypatch.setattr("daedalus.game_studio.is_game_studio_slug", lambda s: True)
    monkeypatch.setattr("daedalus.harness.full_auto._other_owner", lambda s: "autopilot")
    busy = client.post(f"{B}/{slug}/full-auto", json={"confirm": True})
    assert busy.status_code == 409 and busy.json() == {"ok": False, "error": "build_running", "owner": "autopilot"}
    assert client.get(f"{B}/{slug}/full-auto").json() == {"ok": True, "run": None}
    assert client.post(f"{B}/{slug}/full-auto/stop").status_code == 409


def test_full_auto_builds_verifies_and_advances_each_milestone_and_never_forces(client, slug, monkeypatch):
    world = World(milestones=2)
    started, run = _drive(slug, world, monkeypatch)
    assert started["ok"] is True and run["state"] == "succeeded" and run["reason"] == ""
    assert world.calls == [("guided", "M0", True), ("verify", "M0"), ("advance", {}),
                           ("guided", "M1", True), ("verify", "M1"), ("advance", {})]          # no force, no waiver
    assert [e["type"] for e in run["events"]] == ["milestone_build", "verify", "verified", "advanced",
                                                  "milestone_build", "verify", "verified", "advanced", "ended"]
    from daedalus.harness import runs

    ledger = runs.list_runs(slug=slug, kind="build")[0]
    assert ledger["state"] == "succeeded" and ledger["summary"] == "Full Auto M0→M8"


def test_a_milestone_that_does_not_verify_stops_the_run_instead_of_advancing(client, slug, monkeypatch):
    world = World(milestones=3, verify={"M1": False})
    _, run = _drive(slug, world, monkeypatch)
    assert run["state"] == "blocked" and run["reason"] == "verify_failed" and run["milestone"] == "M1"
    assert [c for c in world.calls if c[0] == "advance"] == [("advance", {})]                 # only M0 advanced
    from daedalus.harness import runs

    assert runs.list_runs(slug=slug, kind="build")[0]["state"] == "stopped"                    # blocked is not success


@pytest.mark.parametrize("world,reason", [(World(plan=False), "no_plan"), (World(gates=False), "gates_not_passed")])
def test_no_plan_or_closed_gates_block_before_any_build(client, slug, monkeypatch, world, reason):
    _, run = _drive(slug, world, monkeypatch)
    assert run["state"] == "blocked" and run["reason"] == reason and world.calls == []


def test_a_failed_guided_job_fails_the_run(client, slug, monkeypatch):
    _, run = _drive(slug, World(guided_final="error"), monkeypatch)
    assert run["state"] == "failed" and run["reason"] == "guided_error"


def test_stop_cancels_the_running_guided_job_and_ends_as_stopped(client, slug, monkeypatch):
    world = World(milestones=2)
    from daedalus.harness import full_auto

    world.install(monkeypatch)

    async def scenario():
        world.hold = asyncio.Event()
        await full_auto.start(slug, True)
        while not world.calls:
            await asyncio.sleep(0.01)
        again = await full_auto.start(slug, True)
        stopped = await full_auto.stop(slug)
        await full_auto._runs_by_slug[slug].task
        return again, stopped, full_auto.status(slug)["run"]

    again, stopped, run = asyncio.run(scenario())
    assert again == {"ok": False, "error": "already_running"} and stopped == {"ok": True, "state": "stopping"}
    assert run["state"] == "stopped" and ("stop_guided",) in world.calls
    assert not any(c[0] == "verify" for c in world.calls)


def test_a_run_that_never_completes_stops_at_the_step_limit(client, slug, monkeypatch):
    monkeypatch.setattr("daedalus.harness.full_auto.MAX_STEPS", 3)
    world = World(milestones=10)
    _, run = _drive(slug, world, monkeypatch)
    assert run["state"] == "blocked" and run["reason"] == "step_limit" and len([c for c in world.calls if c[0] == "advance"]) == 3


def test_status_returns_only_new_events_after_a_sequence_number(client, slug, monkeypatch):
    _drive(slug, World(milestones=1), monkeypatch)
    full = client.get(f"{B}/{slug}/full-auto").json()["run"]["events"]
    tail = client.get(f"{B}/{slug}/full-auto?after={full[-2]['seq']}").json()["run"]["events"]
    assert [e["type"] for e in tail] == ["ended"]


# ------------------------------------------------------------------------------------------------ UI (real clicks)
import re  # noqa: E402

from test_a61c_maintenance import _dialogs  # noqa: E402
from test_a62a_review import _record, _until  # noqa: E402
from test_a62b_planner import _status_has, _uncovered  # noqa: E402
from test_studio_phase2_workspace import _fulfill, studio_server  # noqa: E402,F401  (fixture and helpers)

G = "orbit-ui"
CTL = {"ok": True, "game_studio": True, "autopilot": {"running": False, "paused": False, "job_id": None}, "busy": "",
       "plan": {"title": "Orbit plan", "stages": 5, "done": 2, "ultra": False, "studio": True},
       "resume": {"plan_title": "Orbit plan", "done_stages": 2, "total_stages": 5, "next_stage": "Enemies"}}


def _seq(answers):
    calls = {"n": 0}

    def handler(route):
        if route.request.method != "GET":
            return route.fallback()
        body = answers[min(calls["n"], len(answers) - 1)]
        calls["n"] += 1
        _fulfill(route, 200, body)
    return handler, calls


def _build_page(page, ctl=None, fa=None):
    page.route(re.compile(rf".*/game-studio/api/projects/{G}$"),
               lambda r: _fulfill(r, 200, {"project": {"slug": G}, "can_guided": True, "can_autopilot": True, "milestones": []})
               if r.request.method == "GET" else r.fallback())
    page.route(f"**/playground/api/projects/{G}/guided/active", lambda r: _fulfill(r, 200, {"active": False}))
    page.route(re.compile(rf".*/api/v1/playground/projects/{G}/autopilot$"),
               lambda r: _fulfill(r, 200, {"ok": True, "job": None}) if r.request.method == "GET" else r.fallback())
    ctl_handler, ctl_calls = _seq(ctl if isinstance(ctl, list) else [ctl or CTL])
    fa_handler, fa_calls = _seq(fa if isinstance(fa, list) else [fa or {"ok": True, "run": None}])
    page.route(re.compile(rf".*/api/v1/harness/build/{G}/controls$"), ctl_handler)
    page.route(re.compile(rf".*/api/v1/harness/build/{G}/full-auto(\?.*)?$"), fa_handler)
    return ctl_calls, fa_calls


_made: dict[str, str] = {}


def _game(server):
    """A real project on the real server (the Studio drops a slug it cannot find); its workspace reads stay stubbed."""
    from test_studio_phase2_workspace import _api

    if _made.get("server") != server["url"]:
        _made["server"] = server["url"]
        _made["slug"] = _api(server, "POST", "/api/v1/playground/projects", {"name": "Orbit UI", "kind": "static"})["project"]["slug"]
    return _made["slug"]


def _open_build(page, server, lang="en"):
    assert _game(server) == G
    page.add_init_script(f"try {{ localStorage.setItem('dx.lang', '{lang}'); }} catch (e) {{}}")
    page.goto(server["url"] + f"/?panel=build&slug={G}", wait_until="domcontentloaded")
    page.wait_for_selector("#build-controls:not([hidden]) button[data-ctl]")


def _browser(pw, size=(1280, 800)):
    browser = pw.chromium.launch()
    return browser, browser.new_page(viewport={"width": size[0], "height": size[1]})


def _ctl(role):
    return f"#build-ctl-body button[data-ctl='{role}']"


def test_the_controls_show_the_saved_plan_and_what_a_resume_continues(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        _build_page(page)
        _open_build(page, studio_server)
        text = page.inner_text("#build-controls")
        assert "Saved plan “Orbit plan”: 2 of 5 stages done. Next: Enemies." in text and "Full Auto is not running." in text
        assert page.locator(_ctl("continue")).count() == 1 and page.locator(_ctl("pause")).count() == 0
        for role in ("plan-create", "plan-expand", "plan-clear", "fa-start"):
            assert page.locator(_ctl(role)).is_enabled(), role
        browser.close()


def test_pause_and_resume_follow_the_autopilot(studio_server):
    from playwright.sync_api import sync_playwright

    running = {**CTL, "autopilot": {"running": True, "paused": False, "job_id": "j1"}, "resume": None, "busy": "autopilot"}
    paused = {**running, "autopilot": {"running": True, "paused": True, "job_id": "j1"}}
    with sync_playwright() as pw:
        browser, page = _browser(pw)
        _build_page(page)
        state = {"paused": False}
        pauses, resumes = [], []

        def controls(route):                        # the answer follows the posted state, however often the page reads it
            _fulfill(route, 200, paused if state["paused"] else running)

        def act(seen, value):
            def handler(route):
                seen.append(json.loads(route.request.post_data or "{}"))
                state["paused"] = value
                _fulfill(route, 200, {"ok": True, "paused": value})
            return handler

        page.route(re.compile(rf".*/api/v1/harness/build/{G}/controls$"), controls)
        page.route(f"**/api/v1/harness/build/{G}/autopilot/pause", act(pauses, True))
        page.route(f"**/api/v1/harness/build/{G}/autopilot/resume", act(resumes, False))
        _open_build(page, studio_server)
        assert "autopilot owns this project" in page.inner_text("#build-controls").lower()
        assert page.locator(_ctl("plan-clear")).is_disabled() and page.locator(_ctl("fa-start")).is_disabled()
        page.locator(_ctl("pause")).click()
        _status_has(page, "build-ctl-status", "Paused. A model call already under way finishes first.")
        page.wait_for_selector(_ctl("resume"))
        assert pauses == [{}]
        page.locator(_ctl("resume")).click()
        _status_has(page, "build-ctl-status", "Resumed.")
        assert resumes == [{}]
        page.wait_for_selector(_ctl("pause"))
        assert page.locator(_ctl("plan-clear")).is_disabled()                              # still owned after an unlock
        browser.close()


def test_continue_from_there_starts_the_autopilot_with_resume(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        _build_page(page)
        starts = []

        def start(route):
            if route.request.method != "POST":
                return route.fallback()
            starts.append(json.loads(route.request.post_data or "{}"))
            _fulfill(route, 200, {"ok": True, "job": None})

        page.route(re.compile(rf".*/api/v1/playground/projects/{G}/autopilot$"), start)
        _open_build(page, studio_server)
        page.locator(_ctl("continue")).click()
        assert _until(page, lambda: starts)
        assert starts[0]["resume"] is True and starts[0]["mode"] == "full"
        page.locator("#build-autopilot").evaluate("el => el.disabled = false")
        page.locator("#build-autopilot").click()
        assert _until(page, lambda: len(starts) == 2)
        assert starts[1]["resume"] is False                                                   # the plain button starts fresh
        browser.close()


def test_creating_a_plan_needs_the_idea_asks_and_says_when_replacing(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        _build_page(page)
        creates = _record(page, f"**/api/v1/harness/build/{G}/plan/create",
                          [(502, {"ok": False, "error": "plan_failed"}), (200, {"ok": True, "plan": {"stages": 7}})])
        _open_build(page, studio_server)
        page.locator(_ctl("plan-create")).click()
        _status_has(page, "build-ctl-status", "Write the idea first.")
        page.locator("#build-idea").fill("a space shooter")
        asked = _dialogs(page, [False, True, True])
        page.locator("#build-ctl-body input[data-role='ultra']").check()
        page.locator(_ctl("plan-create")).click()
        assert _until(page, lambda: asked)
        page.wait_for_timeout(200)
        assert creates == [] and "saved plan is lost" in asked[0]
        page.locator(_ctl("plan-create")).click()
        _status_has(page, "build-ctl-status", "The model did not return a usable plan. Nothing was saved.")
        assert page.locator("#build-ctl-status").get_attribute("data-state") == "error"
        page.locator(_ctl("plan-create")).click()
        _status_has(page, "build-ctl-status", "Plan saved: 7 stages.")
        assert creates[0] == {"description": "a space shooter", "ultra": True, "studio": True, "confirm": True}
        browser.close()


def test_expanding_and_clearing_ask_first(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        _build_page(page)
        expands = _record(page, f"**/api/v1/harness/build/{G}/plan/expand",
                          [(200, {"ok": True, "before": {"stages": 5}, "plan": {"stages": 14}})])
        clears = _record(page, f"**/api/v1/harness/build/{G}/plan/clear", [(200, {"ok": True})])
        _open_build(page, studio_server)
        asked = _dialogs(page, [False, True, False, True])
        page.locator(_ctl("plan-expand")).click()
        assert _until(page, lambda: asked)
        page.wait_for_timeout(150)
        assert expands == [] and "No model call" in asked[0]
        page.locator(_ctl("plan-expand")).click()
        _status_has(page, "build-ctl-status", "Plan expanded: 5 stages became 14.")
        page.wait_for_selector(_ctl("plan-clear") + ":enabled")
        page.locator(_ctl("plan-clear")).click()
        assert _until(page, lambda: len(asked) == 3)
        page.wait_for_timeout(150)
        assert clears == []
        page.locator(_ctl("plan-clear")).click()
        _status_has(page, "build-ctl-status", "Saved plan cleared.")
        assert expands == [{"confirm": True}] and clears == [{"confirm": True}]
        browser.close()


def test_full_auto_asks_runs_on_the_server_and_shows_where_it_stopped(studio_server):
    from playwright.sync_api import sync_playwright

    live = {"ok": True, "run": {"id": "r1", "state": "running", "milestone": "M1", "reason": "", "events": [
        {"seq": 1, "type": "milestone_build", "milestone": "M0"}, {"seq": 2, "type": "verify", "milestone": "M0"},
        {"seq": 3, "type": "verified", "milestone": "M0", "ok": True}, {"seq": 4, "type": "advanced", "previous": "M0", "current": "M1"}]}}
    ended = {"ok": True, "run": {**live["run"], "state": "blocked", "reason": "verify_failed", "events": live["run"]["events"] + [
        {"seq": 5, "type": "verified", "milestone": "M1", "ok": False}, {"seq": 6, "type": "ended", "state": "blocked", "reason": "verify_failed"}]}}
    with sync_playwright() as pw:
        browser, page = _browser(pw)
        _build_page(page)
        starts = []
        reads = {"after_start": 0}

        def full_auto(route):                       # the server's answer follows the start: none, then live twice, then ended
            if route.request.method == "POST":
                starts.append(json.loads(route.request.post_data or "{}"))
                return _fulfill(route, 200, {"ok": True, "run": live["run"]})
            if not starts:
                return _fulfill(route, 200, {"ok": True, "run": None})
            reads["after_start"] += 1
            _fulfill(route, 200, live if reads["after_start"] <= 2 else ended)

        page.route(re.compile(rf".*/api/v1/harness/build/{G}/full-auto(\?.*)?$"), full_auto)
        _open_build(page, studio_server)
        asked = _dialogs(page, [False, True])
        page.locator(_ctl("fa-start")).click()
        assert _until(page, lambda: asked)
        page.wait_for_timeout(150)
        assert starts == [] and "advances only when Verify passes" in asked[0]
        page.locator(_ctl("fa-start")).click()
        _status_has(page, "build-ctl-status", "Full Auto started on the server.")
        assert starts == [{"confirm": True}]
        page.wait_for_selector(_ctl("fa-stop"))
        assert "Advanced M0 → M1." in page.inner_text("#build-controls .fa-log")
        page.wait_for_selector("#build-controls .fa-log li[data-type='ended'][data-state='blocked']", timeout=15000)
        assert "Full Auto stopped and needs you: this milestone did not verify." in page.inner_text("#build-controls .fa-log")
        assert page.locator(_ctl("fa-start")).count() == 1 and reads["after_start"] >= 3
        browser.close()


def test_stopping_full_auto_sends_stop(studio_server):
    from playwright.sync_api import sync_playwright

    live = {"ok": True, "run": {"id": "r1", "state": "running", "milestone": "M0", "reason": "", "events": []}}
    with sync_playwright() as pw:
        browser, page = _browser(pw)
        _build_page(page, ctl=[{**CTL, "busy": "full_auto"}], fa=[live])
        stops = _record(page, f"**/api/v1/harness/build/{G}/full-auto/stop", [(200, {"ok": True, "state": "stopping"})])
        _open_build(page, studio_server)
        assert "Full Auto owns this project" in page.inner_text("#build-controls")
        page.locator(_ctl("fa-stop")).click()
        _status_has(page, "build-ctl-status", "Stopping")
        assert stops == [{}]
        browser.close()


def test_a_project_outside_game_studio_has_no_full_auto(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        _build_page(page, ctl={**CTL, "game_studio": False})
        _open_build(page, studio_server)
        assert page.locator(_ctl("fa-start")).count() == 0 and page.locator(_ctl("plan-create")).count() == 1
        browser.close()


def test_bulgarian_shows_the_run_controls_without_raw_keys(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw, (910, 640))
        _build_page(page)
        _open_build(page, studio_server, lang="bg")
        text = page.inner_text("#build-controls")
        low = text.lower()
        for expected in ("управление на изпълнението", "запазен план", "продължи оттам", "създай план", "разгъни", "изчисти плана",
                         "пълен автопилот"):
            assert expected in low, expected
        assert not re.search(r"\bstudio\.[A-Za-z]", text) and "saved plan" not in low
        browser.close()


@pytest.mark.parametrize("size", [(1280, 800), (1366, 768), (910, 640)])
def test_run_controls_fit_and_are_really_clickable(studio_server, size):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw, size)
        _build_page(page)
        _open_build(page, studio_server)
        over = page.evaluate("""() => { const p = document.querySelector('[data-panel=build]');
          return {sw: p.scrollWidth, cw: p.clientWidth, docw: document.documentElement.scrollWidth, vw: window.innerWidth}; }""")
        assert over["sw"] <= over["cw"] + 1 and over["docw"] <= over["vw"] + 1, over
        for role in ("continue", "plan-create", "plan-expand", "plan-clear", "fa-start"):
            page.locator(_ctl(role)).first.evaluate("el => el.scrollIntoView({block: 'center'})")
            _uncovered(page, _ctl(role))
        browser.close()


# ------------------------------------------------------------------------------------------------ review fixes
def test_a_milestone_whose_stages_were_skipped_goes_to_verify_not_to_a_build(client, slug, monkeypatch):
    world = World(milestones=1)
    original = world.ctx

    async def ctx(s):
        data = await original(s)
        for st in data["plan"]["stages"]:
            st["status"] = "skipped"
        return data

    world.ctx = ctx
    _, run = _drive(slug, world, monkeypatch)
    assert run["state"] == "succeeded" and world.calls == [("verify", "M0"), ("advance", {})]


def test_stop_pressed_while_the_build_is_being_started_still_stops_it(client, slug, monkeypatch):
    from daedalus.harness import full_auto

    world = World(milestones=2)
    world.install(monkeypatch)
    gate = {}
    original = world.start

    async def slow_start(s, mid, auto_approve=False):
        await gate["go"].wait()                                  # the job id is not known while this waits
        return await original(s, mid, auto_approve)

    monkeypatch.setattr("daedalus.game_studio.start_milestone_guided", slow_start)

    async def scenario():
        gate["go"] = asyncio.Event()
        world.hold = asyncio.Event()
        await full_auto.start(slug, True)
        await asyncio.sleep(0.05)
        await full_auto.stop(slug)
        gate["go"].set()
        await asyncio.wait_for(asyncio.shield(full_auto._runs_by_slug[slug].task), 5)   # a Stop that is ignored fails, never hangs
        return full_auto.status(slug)["run"]

    run = asyncio.run(scenario())
    assert run["state"] == "stopped" and ("stop_guided",) in world.calls and not any(c[0] == "verify" for c in world.calls)


def test_stop_during_an_auto_approved_checkpoint_interrupts_the_job(client, slug, monkeypatch):
    from daedalus.harness import full_auto

    world = World(milestones=1)
    world.install(monkeypatch)

    async def start(s, mid, auto_approve=False):
        job = Job("job_cp", [])
        job.status = "checkpoint"
        world.jobs[job.id] = job
        return {"job_id": job.id}

    monkeypatch.setattr("daedalus.game_studio.start_milestone_guided", start)

    async def scenario():
        await full_auto.start(slug, True)
        await asyncio.sleep(0.05)
        await full_auto.stop(slug)
        await asyncio.wait_for(asyncio.shield(full_auto._runs_by_slug[slug].task), 5)
        return full_auto.status(slug)["run"], world.jobs["job_cp"].status

    run, job_status = asyncio.run(scenario())
    assert run["state"] == "stopped" and job_status == "interrupted"


def test_a_refused_step_blocks_with_a_code_and_keeps_the_message_apart(client, slug, monkeypatch):
    world = World(milestones=1)

    async def refuse(s, mid, auto_approve=False):
        raise ValueError("milestone M0 has no stages — прегенерирай")

    world.start = refuse
    _, run = _drive(slug, world, monkeypatch)
    assert run["state"] == "blocked" and run["reason"] == "studio_refused" and "no stages" in run["detail"]
    from daedalus.harness import runs

    assert "studio_refused" in runs.list_runs(slug=slug, kind="build")[0]["error"] + str(runs.events(runs.list_runs(slug=slug, kind="build")[0]["id"]))


@pytest.mark.parametrize("method,path", [
    ("post", "/admin/daedalus/playground/api/projects/{slug}/guided/start"),
    ("post", "/admin/daedalus/playground/api/projects/{slug}/guided/continue"),
    ("post", "/admin/daedalus/game-studio/api/projects/{slug}/verify"),
    ("post", "/admin/daedalus/game-studio/api/projects/{slug}/milestones/advance"),
    ("post", "/admin/daedalus/game-studio/api/projects/{slug}/build/guided"),
    ("post", "/admin/daedalus/playground/api/projects/{slug}/autopilot"),
    ("post", "/api/v1/playground/projects/{slug}/autopilot"),
])
def test_manual_build_steps_are_refused_while_full_auto_drives_the_project(client, slug, monkeypatch, method, path):
    monkeypatch.setattr("daedalus.harness.full_auto.owns", lambda s: s == slug)
    res = getattr(client, method)(path.format(slug=slug), json={"description": "x"})
    assert res.status_code == 409 and "full_auto_running" in res.text


def test_ultra_expand_keeps_each_micro_stage_in_its_parents_milestone():
    from daedalus.playground_ultra_plan import ultra_expand_plan

    plan = {"title": "t", "stages": [{"id": 1, "title": "Core", "milestone_id": "M2", "files": ["a.js", "b.js", "c.js"], "status": "pending"},
                                     {"id": 2, "title": "Menu", "milestone_id": "M3", "files": ["m.js", "n.js"], "status": "pending"}]}
    out = ultra_expand_plan(plan, kind="static", is_game=True)
    assert len(out["stages"]) > 2 and all(s.get("milestone_id") in ("M2", "M3") for s in out["stages"])
    assert {s["milestone_id"] for s in out["stages"] if s.get("ultra_parent_title") == "Core"} == {"M2"}


def test_status_tail_returns_only_the_last_events(client, slug, monkeypatch):
    _drive(slug, World(milestones=2), monkeypatch)
    data = client.get(f"{B}/{slug}/full-auto?tail=2").json()["run"]
    assert [e["type"] for e in data["events"]] == ["advanced", "ended"] and data["last_seq"] == 9


def test_a_full_auto_project_disables_the_manual_build_buttons_and_explains_a_refusal(studio_server):
    from playwright.sync_api import sync_playwright

    live = {"ok": True, "run": {"id": "r1", "state": "running", "milestone": "M0", "reason": "", "events": []}}
    with sync_playwright() as pw:
        browser, page = _browser(pw)
        _build_page(page, ctl=[{**CTL, "busy": "full_auto"}], fa=[live])
        page.route(f"**/game-studio/api/projects/{G}/verify", lambda r: _fulfill(r, 409, {"ok": False, "error": "full_auto_running"}))
        _open_build(page, studio_server)
        page.wait_for_function("() => ['build-guided', 'build-autopilot', 'build-verify', 'build-advance'].every((id) => document.getElementById(id).disabled)")
        page.locator("#build-verify").evaluate("el => el.disabled = false")       # a stale page that still offers it
        page.locator("#build-verify").click()
        _status_has(page, "build-status", "Full Auto is driving this project. Stop Full Auto first.")
        browser.close()


def test_a_second_identical_read_does_not_replace_the_buttons(studio_server):
    """The Build panel reads its controls twice when it opens. Repainting the same answer replaced every button, so a
    scroll or click under way landed on a node that had just been thrown away (seen in CI at 910x640)."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw, (910, 640))
        calls, _ = _build_page(page)
        _open_build(page, studio_server)
        page.locator(_ctl("plan-create")).evaluate("el => { el.__first = true; }")
        page.evaluate("() => window.dxStudio.selectSlug(window.dxStudio.getSlug())")   # one more read of the same answer
        assert _until(page, lambda: calls["n"] >= 2)
        page.wait_for_timeout(300)
        assert page.locator(_ctl("plan-create")).evaluate("el => el.__first === true")
        browser.close()


def test_a_workspace_repaint_keeps_the_manual_steps_off_while_full_auto_owns_the_project(studio_server):
    """Seen in CI: the controls paint switched the manual steps off, a later workspace paint switched them on again,
    and the next (identical) controls answer was not repainted, so nothing switched them off."""
    from playwright.sync_api import sync_playwright

    live = {"ok": True, "run": {"id": "r1", "state": "running", "milestone": "M0", "reason": "", "events": []}}
    with sync_playwright() as pw:
        browser, page = _browser(pw)
        calls, _ = _build_page(page, ctl=[{**CTL, "busy": "full_auto"}], fa=[live])
        _open_build(page, studio_server)
        ids = "['build-guided', 'build-autopilot', 'build-verify', 'build-advance']"
        page.wait_for_function(f"() => {ids}.every((id) => document.getElementById(id).disabled)")
        before = calls["n"]
        page.evaluate("() => window.dxStudio.selectSlug(window.dxStudio.getSlug())")   # workspace repaint + the same controls answer
        assert _until(page, lambda: calls["n"] > before)
        page.wait_for_timeout(400)
        assert page.evaluate(f"() => {ids}.every((id) => document.getElementById(id).disabled)")
        browser.close()


def test_an_unparsable_plan_reply_is_tried_once_more(client, slug, monkeypatch):
    """Found live (2026-11-11): the same request failed once with an unparsable reply and passed the next time."""
    good = json.dumps({"title": "Comet plan", "stages": [{"id": 1, "title": "Ship", "instructions": "x"}]})
    replies = ["Sure! Here is the plan: (no json)", good]
    systems = []

    async def fake(**kw):
        systems.append(kw.get("system", ""))
        return {"reply": replies.pop(0)}

    monkeypatch.setattr("daedalus.llm_client.complete", fake)
    made = client.post(f"{B}/{slug}/plan/create", json={"description": "a game"}).json()
    assert made["ok"] is True and made["plan"]["stages"] == 1 and _load(slug)["stages"][0]["title"] == "Ship"
    assert len(systems) == 2 and "not valid plan JSON" in systems[1]
