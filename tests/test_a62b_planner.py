"""A6.2b: planner and discovery actions in Review (API level, real planner storage)."""
from __future__ import annotations

import asyncio
import types

import pytest
from a0_support import a0_tmp  # noqa: F401
from fastapi.testclient import TestClient

P = "/api/v1/harness/review/planner/plans"


@pytest.fixture
def client(a0_tmp):  # noqa: F811
    from app.main import app

    return TestClient(app)


def _plan(client, status="draft", steps=1):
    """A plan with ``steps`` steps in the given status (set directly: only the loop and Approve may activate one)."""
    plan_id = client.post(P, json={"title": "Ship it", "description": "demo"}).json()["plan_id"]
    ids = []
    for i in range(steps):
        ids.append(client.post(f"{P}/{plan_id}/steps", json={"title": f"step {i + 1}", "description": f"do {i + 1}"}).json()["step_id"])
    if status != "draft":
        from daedalus import planner

        asyncio.run(planner.update_plan_status(plan_id, status))
    return plan_id, ids


def _status(client, plan_id):
    return client.get(f"{P}/{plan_id}/progress").json()


# ------------------------------------------------------------------------------------------------ plans
def test_a_manual_plan_is_a_draft_so_nothing_runs_until_it_is_approved(client):
    out = client.post(P, json={"title": "  My plan ", "description": "why"}).json()
    assert out["ok"] is True and out["status"] == "draft"
    assert _status(client, out["plan_id"])["plan_status"] == "draft" and _status(client, out["plan_id"])["title"] == "My plan"
    assert client.post(P, json={"title": "   "}).json() == {"ok": False, "error": "title_required"}
    assert client.post(P, json={"title": "x" * 201}).json()["error"] == "too_long"
    assert client.post(P, json={"title": "ok", "description": "d" * 4001}).status_code == 400


# ------------------------------------------------------------------------------------------------ steps
def test_adding_a_step_to_an_active_plan_needs_a_confirm_because_it_goes_into_the_queue(client):
    plan_id, _ = _plan(client, status="active", steps=0)
    res = client.post(f"{P}/{plan_id}/steps", json={"title": "queued"})
    assert res.status_code == 400 and res.json() == {"ok": False, "error": "confirm_required", "queues_work": True}
    assert _status(client, plan_id)["steps"] == []                                   # nothing was added
    out = client.post(f"{P}/{plan_id}/steps", json={"title": "queued", "confirm": True}).json()
    assert out["ok"] is True and len(_status(client, plan_id)["steps"]) == 1


def test_step_validation(client, monkeypatch):
    plan_id, (first,) = _plan(client, steps=1)
    ok = client.post(f"{P}/{plan_id}/steps", json={"title": "second", "depends_on": [first], "max_retries": 99}).json()
    assert ok["ok"] is True
    bad = client.post(f"{P}/{plan_id}/steps", json={"title": "x", "depends_on": ["step_nope"]})
    assert bad.status_code == 400 and bad.json() == {"ok": False, "error": "bad_dependency", "step": "step_nope"}
    assert client.post(f"{P}/{plan_id}/steps", json={"title": ""}).json()["error"] == "title_required"
    assert client.post(f"{P}/plan_nope/steps", json={"title": "x"}).status_code == 404
    monkeypatch.setattr("daedalus.harness.planner_actions.STEPS_MAX", 2)
    assert client.post(f"{P}/{plan_id}/steps", json={"title": "third"}).json()["error"] == "too_many_steps"


def test_dependencies_must_belong_to_the_same_plan(client):
    _, (other_step,) = _plan(client, steps=1)
    plan_id, _ = _plan(client, steps=0)
    res = client.post(f"{P}/{plan_id}/steps", json={"title": "x", "depends_on": [other_step]})
    assert res.status_code == 400 and res.json()["error"] == "bad_dependency"


# ------------------------------------------------------------------------------------------------ mark and override
def test_mark_only_allows_the_states_an_owner_may_set_and_asks_before_claiming_done(client):
    plan_id, (step,) = _plan(client, status="active")
    url = f"{P}/{plan_id}/steps/{step}/mark"
    assert client.post(url, json={"status": "running"}).json()["error"] == "status_not_allowed"        # the loop's state
    assert client.post(url, json={"status": "banana"}).status_code == 400
    assert client.post(url, json={"status": "failed"}).json()["ok"] is True                               # no claim, no question
    assert _status(client, plan_id)["steps"][0]["status"] == "failed"
    assert client.post(url, json={"status": "passed"}).status_code == 400                                 # claiming done needs a confirm
    assert client.post(url, json={"status": "pending"}).status_code == 400                                # re-queueing on an active plan too
    assert _status(client, plan_id)["steps"][0]["status"] == "failed"
    assert client.post(url, json={"status": "passed", "confirm": True}).json()["status"] == "passed"
    assert _status(client, plan_id)["steps"][0]["status"] == "passed"


def test_requeueing_a_step_of_a_plan_that_is_not_active_needs_no_question(client):
    plan_id, (step,) = _plan(client, status="paused")
    assert client.post(f"{P}/{plan_id}/steps/{step}/mark", json={"status": "pending"}).json()["ok"] is True


def test_a_step_of_another_plan_is_never_touched(client):
    plan_a, _ = _plan(client)
    _, (step_b,) = _plan(client)
    assert client.post(f"{P}/{plan_a}/steps/{step_b}/mark", json={"status": "failed"}).status_code == 404
    assert client.post(f"{P}/{plan_a}/steps/{step_b}/override", json={"confirm": True}).status_code == 404
    assert client.post(f"{P}/{plan_a}/steps/{step_b}/run", json={"confirm": True}).status_code == 404


def test_manual_override_needs_a_confirm_and_is_recorded_as_an_override(client):
    plan_id, (step,) = _plan(client, status="paused")
    url = f"{P}/{plan_id}/steps/{step}/override"
    assert client.post(url, json={}).status_code == 400 and _status(client, plan_id)["steps"][0]["status"] == "pending"
    out = client.post(url, json={"confirm": True}).json()
    assert out == {"ok": True, "step_id": step, "status": "passed", "manual_override": True}
    s = _status(client, plan_id)["steps"][0]
    assert s["status"] == "passed" and s["manual_override"] is True and "owner" in (s["diagnostics"] or "")


# ------------------------------------------------------------------------------------------------ pause and resume
def test_pause_and_resume_follow_the_plan_state_and_resume_asks(client):
    plan_id, _ = _plan(client, status="active")
    assert client.post(f"{P}/{plan_id}/pause").json() == {"ok": True, "status": "paused"}
    assert client.post(f"{P}/{plan_id}/pause").status_code == 409                                       # already paused
    assert client.post(f"{P}/{plan_id}/resume", json={}).status_code == 400 and _status(client, plan_id)["plan_status"] == "paused"
    assert client.post(f"{P}/{plan_id}/resume", json={"confirm": True}).json() == {"ok": True, "status": "active"}
    assert client.post(f"{P}/{plan_id}/resume", json={"confirm": True}).status_code == 409              # not paused any more


def test_a_draft_is_not_resumed_it_is_approved(client):
    plan_id, _ = _plan(client, status="draft")
    res = client.post(f"{P}/{plan_id}/resume", json={"confirm": True})
    assert res.status_code == 409 and res.json()["error"] == "use_approve"
    assert _status(client, plan_id)["plan_status"] == "draft"
    assert client.post(f"{P}/{plan_id}/pause").status_code == 409                                       # a draft is not running either
    assert client.post(f"{P}/plan_nope/pause").status_code == 404


# ------------------------------------------------------------------------------------------------ progress
def test_progress_has_the_same_shape_on_both_routes(client):
    plan_id, (step,) = _plan(client)
    new = _status(client, plan_id)
    old = client.get(f"/admin/daedalus/planner/plans/{plan_id}/progress").json()
    assert new["ok"] is True and new["steps"][0]["id"] == step and new["steps"][0]["bg_running"] is False
    assert {k: v for k, v in new.items() if k != "ok"} == old                                            # the old page reads the same data
    assert set(new["steps"][0]["checkpoints"]) == {"passed", "passed_count", "total", "all"}
    assert new["loop_running"] is False                                       # nothing in this app starts the planner loop
    assert client.get(f"{P}/plan_nope/progress").status_code == 404
    assert client.get("/admin/daedalus/planner/plans/plan_nope/progress").status_code == 404


# ------------------------------------------------------------------------------------------------ run in the background
class FakeAgent:
    def __init__(self, gate=None):
        self.calls = []
        self.gate = gate
        self.memory = types.SimpleNamespace()

    async def chat(self, message, session_id=""):
        self.calls.append((message, session_id))
        if self.gate is not None:
            await self.gate.wait()
        return {"reply": "done it", "context": {"tool_calls": [{"tool": "read_file"}]}}


def _active(perm):
    from daedalus import outbox

    return any(p.get("perm_class") == perm and p.get("active") for p in outbox.list_permissions())


def test_run_in_background_needs_a_confirm_and_runs_the_step_text_not_a_request_text(client, monkeypatch):
    agent = FakeAgent()
    monkeypatch.setattr("daedalus.agent.get_daedalus", lambda: agent)
    plan_id, (step,) = _plan(client, status="active")
    url = f"{P}/{plan_id}/steps/{step}/run"
    assert client.post(url, json={"grant_recommended": True}).status_code == 400
    assert agent.calls == [] and not _active("web_search")                                              # nothing ran, nothing granted
    assert client.post(f"{P}/plan_nope/steps/{step}/run", json={"confirm": True}).status_code == 404
    out = client.post(url, json={"confirm": True, "task": "rm -rf everything", "permissions": ["execute_raw_sql"]}).json()
    assert out["ok"] is True and out["granted"] == [] and out["session_id"].startswith("bg-")
    assert not _active("execute_raw_sql") and not _active("web_search")                                  # no grant unless asked


def test_only_the_servers_recommended_set_can_be_granted_and_only_when_asked(a0_tmp, monkeypatch):  # noqa: F811
    from daedalus.harness import planner_actions

    agent = FakeAgent()
    monkeypatch.setattr("daedalus.agent.get_daedalus", lambda: agent)
    planner_actions._bg.clear()

    async def scenario():
        from daedalus import planner

        created = await planner.create_plan("p", "d", status="active")
        step = (await planner.add_step(created["plan_id"], "the step", "its text"))["step_id"]
        res = await planner_actions.run_background(created["plan_id"], step, True, True)
        await asyncio.gather(*list(planner_actions._bg.values()))
        return res

    res = asyncio.run(scenario())
    assert res["ok"] is True and res["granted"] == planner_actions.recommended_permissions() and res["granted"]
    assert all(_active(p) for p in res["granted"]) and not _active("execute_raw_sql")
    message, session = agent.calls[0]
    assert "the step" in message and "its text" in message and "Step ID: step_" in message and session == res["session_id"]


def test_a_step_runs_once_at_a_time_and_its_result_reaches_the_outbox(a0_tmp, monkeypatch):  # noqa: F811
    from daedalus import outbox
    from daedalus.harness import planner_actions

    planner_actions._bg.clear()

    async def scenario():
        from daedalus import planner

        gate = asyncio.Event()
        monkeypatch.setattr("daedalus.agent.get_daedalus", lambda: FakeAgent(gate))
        created = await planner.create_plan("p", "d", status="active")
        step = (await planner.add_step(created["plan_id"], "slow step", ""))["step_id"]
        first = await planner_actions.run_background(created["plan_id"], step, True, False)
        second = await planner_actions.run_background(created["plan_id"], step, True, False)
        shown = (await planner_actions.progress(created["plan_id"]))["steps"][0]["bg_running"]
        gate.set()
        await asyncio.gather(*list(planner_actions._bg.values()))
        return first, second, shown, (await planner_actions.progress(created["plan_id"]))["steps"][0]["bg_running"]

    first, second, shown, after = asyncio.run(scenario())
    assert first["ok"] is True and second == {"ok": False, "error": "already_running"}
    assert shown is True and after is False
    assert any("slow step" in (i.get("title") or "") or "slow step" in str(i.get("payload")) for i in outbox.list_outbox(limit=20))


def test_a_failing_background_step_is_reported_to_the_outbox_not_lost(a0_tmp, monkeypatch):  # noqa: F811
    from daedalus import outbox
    from daedalus.harness import planner_actions

    class Boom(FakeAgent):
        async def chat(self, message, session_id=""):
            raise RuntimeError("model down")

    async def scenario():
        await planner_actions.execute_task(Boom(), "the task", "bg-x")

    asyncio.run(scenario())
    items = outbox.list_outbox(limit=20)
    assert any(i.get("category") == "bg_error" for i in items)


def test_the_old_pages_run_background_grants_only_the_recommended_set(client, monkeypatch):
    from daedalus.harness import planner_actions

    monkeypatch.setattr("daedalus.agent.get_daedalus", lambda: FakeAgent())
    recommended = planner_actions.recommended_permissions()
    asked = [recommended[0], "execute_raw_sql", "delete_everything"]
    out = client.post("/admin/daedalus/run-background", json={"task": "do the thing", "grant_perms": asked}).json()
    assert out["ok"] is True and out["granted_perms"] == [recommended[0]] and out["ignored_perms"] == ["execute_raw_sql", "delete_everything"]
    assert _active(recommended[0]) and not _active("execute_raw_sql")


# ------------------------------------------------------------------------------------------------ discoveries
class FakeMemory:
    def __init__(self):
        self.items = {"d1": ("a finding", {"kind": "x"})}
        self.discoveries_col = types.SimpleNamespace(get=self._get)

    def _get(self, ids):
        hit = [i for i in ids if i in self.items]
        return {"ids": hit, "documents": [self.items[i][0] for i in hit], "metadatas": [self.items[i][1] for i in hit]}

    def delete_discovery(self, discovery_id):
        return self.items.pop(discovery_id, None) is not None


@pytest.fixture
def agent_with_memory(monkeypatch):
    agent = FakeAgent()
    agent.memory = FakeMemory()
    monkeypatch.setattr("daedalus.agent.get_daedalus", lambda: agent)
    return agent


def test_deleting_a_discovery_needs_a_confirm_and_says_when_it_is_gone(client, agent_with_memory):
    url = "/api/v1/harness/review/discoveries/d1/delete"
    assert client.post(url, json={}).status_code == 400 and "d1" in agent_with_memory.memory.items
    assert client.post(url, json={"confirm": True}).json() == {"ok": True, "discovery_id": "d1"}
    assert client.post(url, json={"confirm": True}).status_code == 404                                    # already gone: not "ok"


def test_regenerating_a_plan_is_confirmed_single_flight_and_reports_how_it_ended(a0_tmp, monkeypatch, agent_with_memory):  # noqa: F811
    from daedalus.harness import planner_actions

    planner_actions._regen.clear()
    planner_actions._regen_last.clear()

    async def scenario():
        gate = asyncio.Event()

        async def generate_plan(discovery, registry, agent, save_on_error=True):
            assert discovery == {"id": "d1", "content": "a finding", "metadata": {"kind": "x"}}
            await gate.wait()
            return {"id": "plan_new"}

        monkeypatch.setattr("daedalus.plans.generate_plan", generate_plan)
        refused = await planner_actions.regenerate_start("d1", False)
        missing = await planner_actions.regenerate_start("d_nope", True)
        first = await planner_actions.regenerate_start("d1", True)
        second = await planner_actions.regenerate_start("d1", True)
        running = planner_actions.regenerate_status()
        gate.set()
        await asyncio.gather(*list(planner_actions._regen.values()))
        return refused, missing, first, second, running, planner_actions.regenerate_status()

    refused, missing, first, second, running, done = asyncio.run(scenario())
    assert refused == {"ok": False, "error": "confirm_required"} and missing == {"ok": False, "error": "not_found"}
    assert first["ok"] is True and second == {"ok": False, "error": "already_running"}
    assert running["running"] == ["d1"] and running["last"] == {}
    assert done["running"] == [] and done["last"]["d1"]["ok"] is True and done["last"]["d1"]["plan_id"] == "plan_new"


def test_a_failed_regeneration_is_recorded_as_failed(a0_tmp, monkeypatch, agent_with_memory):  # noqa: F811
    from daedalus.harness import planner_actions

    planner_actions._regen.clear()
    planner_actions._regen_last.clear()

    async def boom(discovery, registry, agent, save_on_error=True):
        raise RuntimeError("no model")

    monkeypatch.setattr("daedalus.plans.generate_plan", boom)

    async def scenario():
        await planner_actions.regenerate_start("d1", True)
        await asyncio.gather(*list(planner_actions._regen.values()))
        return planner_actions.regenerate_status()

    last = asyncio.run(scenario())["last"]["d1"]
    assert last["ok"] is False and last["error"] == "RuntimeError" and "plan_id" not in last


def test_the_status_route_and_the_start_route_answer_with_codes(client, agent_with_memory):
    assert client.get("/api/v1/harness/review/discoveries/regenerate-status").json()["ok"] is True
    assert client.post("/api/v1/harness/review/discoveries/d1/regenerate", json={}).status_code == 400
    assert client.post("/api/v1/harness/review/discoveries/d_nope/regenerate", json={"confirm": True}).status_code == 404


def test_loop_running_reports_the_truth_about_the_process(a0_tmp):  # noqa: F811
    from daedalus import planner
    from daedalus.harness import planner_actions

    async def impostor():                                # a task that merely sleeps is not the planner loop
        await asyncio.sleep(30)

    async def scenario():
        other = asyncio.get_running_loop().create_task(impostor())
        before = planner_actions.loop_running()
        real = asyncio.get_running_loop().create_task(planner.run_planner_loop())       # it waits 90 s before its first look
        await asyncio.sleep(0)
        during = planner_actions.loop_running()
        for task in (real, other):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        return before, during, planner_actions.loop_running()

    assert asyncio.run(scenario()) == (False, True, False)
    assert planner_actions.loop_running() is False      # no running loop at all: false, never an error


# ------------------------------------------------------------------------------------------------ UI (real clicks)
import json  # noqa: E402
import re  # noqa: E402

from test_a61c_maintenance import _dialogs  # noqa: E402
from test_a62a_review import _open_review, _record, _until  # noqa: E402
from test_studio_phase2_workspace import _fulfill, _hit, studio_server  # noqa: E402,F401  (fixture and helpers)

PLANS = {"plans": [
    {"id": "plan_act", "title": "Active plan", "description": "d", "status": "active"},
    {"id": "plan_pau", "title": "Paused plan", "description": "d", "status": "paused"},
    {"id": "plan_dra", "title": "Draft plan", "description": "d", "status": "draft"}]}


def _step(i, status="pending", **kw):
    return {"id": f"step_{i}", "step_order": i, "title": f"Step number {i}", "status": status, "proposal_id": None,
            "progress_note": kw.get("note"), "diagnostics": None, "repair_attempts": 0, "manual_override": kw.get("override", False),
            "checkpoints": {"passed": ["C1"] * kw.get("cp", 0), "passed_count": kw.get("cp", 0), "total": 6, "all": []},
            "error_msg": kw.get("err"), "bg_running": kw.get("bg", False)}


def _progress(plan_id, status, steps, loop=False):
    return {"ok": True, "plan_id": plan_id, "plan_status": status, "title": plan_id, "steps": steps, "loop_running": loop}


def _plans_page(page, plans=None):
    page.route(re.compile(r".*/admin/daedalus/planner/plans(\?.*)?$"),
               lambda r: _fulfill(r, 200, plans or PLANS) if r.request.method == "GET" else r.fallback())


def _open_plans(page, server, lang="en", plans=None):
    _plans_page(page, plans)
    _open_review(page, server, "proposals", lang=lang, wait="#plan-list .item")


def _row(plan_id):
    return f"#plan-list .item[data-id='{plan_id}']"


def _result_has(page, manage, text):
    """The steps panel redraws after every action and writes its outcome into its note: wait for THAT outcome."""
    page.wait_for_function("([s, t]) => { const e = document.querySelector(s); return !!e && !e.hidden && e.textContent.includes(t); }",
                           arg=[manage + " .note.result", text])


def _uncovered(page, selector):
    """The element is on screen and is what a click at its centre would reach (it is not covered); nothing is clicked."""
    info = page.locator(selector).first.evaluate("""(el) => {
      const r = el.getBoundingClientRect();
      const x = r.left + r.width / 2, y = r.top + r.height / 2;
      const top = document.elementFromPoint(x, y);
      return {w: r.width, h: r.height, inside: x >= 0 && y >= 0 && x <= window.innerWidth && y <= window.innerHeight,
              hit: !!top && (top === el || el.contains(top))};
    }""")
    assert info["w"] > 8 and info["h"] > 8 and info["inside"] and info["hit"], (selector, info)


def _status_has(page, element, text):
    page.wait_for_function("([e, t]) => (document.getElementById(e).textContent || '').includes(t)", arg=[element, text])


def test_a_manual_plan_is_created_as_a_draft_through_the_real_server(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _open_review(page, studio_server, "proposals", wait="#plan-manual-create")
        page.locator("#plan-manual-create").click()                                   # no title yet
        _status_has(page, "plan-status", "A title is needed")
        assert page.locator("#plan-status").get_attribute("data-state") == "error"
        page.locator("#plan-manual-title").fill("Hand made plan")
        page.locator("#plan-manual-desc").fill("for the demo")
        page.locator("#plan-manual-create").click()
        _status_has(page, "plan-status", "Draft plan plan_")
        page.wait_for_selector("#plan-list .item[data-status='draft']:has-text('Hand made plan')")
        assert page.locator("#plan-manual-title").input_value() == ""                 # the form is ready for the next one
        browser.close()


def test_plan_rows_offer_pause_or_resume_by_status_and_resume_asks(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        pauses = _record(page, "**/api/v1/harness/review/planner/plans/plan_act/pause", [(200, {"ok": True, "status": "paused"})])
        resumes = _record(page, "**/api/v1/harness/review/planner/plans/plan_pau/resume", [(200, {"ok": True, "status": "active"})])
        _open_plans(page, studio_server)
        assert page.locator(_row("plan_act") + " button[data-role='pause']").count() == 1
        assert page.locator(_row("plan_act") + " button[data-role='resume']").count() == 0
        assert page.locator(_row("plan_pau") + " button[data-role='resume']").count() == 1
        assert page.locator(_row("plan_dra") + " button[data-role='pause'], " + _row("plan_dra") + " button[data-role='resume']").count() == 0
        assert all(page.locator(_row(p) + " button[data-role='steps']").count() == 1 for p in ("plan_act", "plan_pau", "plan_dra"))
        page.locator(_row("plan_act") + " button[data-role='pause']").click()
        _status_has(page, "plan-status", "Plan paused")
        assert pauses == [{}]                                                         # pausing needs no question
        asked = _dialogs(page, [False, True])
        page.locator(_row("plan_pau") + " button[data-role='resume']").click()
        assert _until(page, lambda: asked)
        page.wait_for_timeout(200)
        assert resumes == [] and "pending steps go back into the planner" in asked[0]
        page.locator(_row("plan_pau") + " button[data-role='resume']").click()
        _status_has(page, "plan-status", "Plan resumed")
        assert resumes == [{"confirm": True}]
        browser.close()


def _open_manager(page, plan_id, data, studio_server=None):
    page.route(f"**/api/v1/harness/review/planner/plans/{plan_id}/progress", lambda r: _fulfill(r, 200, data))
    return _row(plan_id) + " .manage"


def test_the_steps_panel_shows_each_step_honestly_and_says_whether_the_loop_runs(studio_server):
    from playwright.sync_api import sync_playwright

    steps = [_step(1, "passed", cp=6, override=True), _step(2, "running", bg=True, note="working on it"), _step(3, "failed", err="boom", cp=2)]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        manage = _open_manager(page, "plan_act", _progress("plan_act", "active", steps, loop=False))
        _open_plans(page, studio_server)
        page.locator(_row("plan_act") + " button[data-role='steps']").click()
        page.wait_for_selector(manage + " .step")
        text = page.inner_text(manage)
        assert "planner loop is not running in this app" in text                       # the truth: nothing runs it here
        assert "1. Step number 1" in text and "checkpoints 6/6" in text and "manual override" in text
        assert "running in background" in text and "working on it" in text and "boom" in text and "checkpoints 2/6" in text
        assert page.locator(manage + " .step[data-step='step_1'] button[data-role='override']").is_disabled()   # already passed
        assert page.locator(manage + " .step[data-step='step_2'] button[data-role='run']").is_disabled()        # already running
        assert page.locator(_row("plan_act") + " button[data-role='steps']").inner_text() == "Hide steps"
        page.locator(_row("plan_act") + " button[data-role='steps']").click()
        assert page.locator(manage).count() == 0
        browser.close()


def test_the_steps_panel_says_when_the_loop_is_running_and_only_for_active_plans(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        on = _open_manager(page, "plan_act", _progress("plan_act", "active", [_step(1)], loop=True))
        off = _open_manager(page, "plan_dra", _progress("plan_dra", "draft", [], loop=False))
        _open_plans(page, studio_server)
        page.locator(_row("plan_act") + " button[data-role='steps']").click()
        page.wait_for_selector(on + " .step")
        assert "planner loop is running" in page.inner_text(on)
        page.locator(_row("plan_dra") + " button[data-role='steps']").click()
        page.wait_for_selector(off + " .add-step")
        assert "planner loop" not in page.inner_text(off) and "No steps yet" in page.inner_text(off)     # a draft has no loop line
        browser.close()


def test_setting_a_status_asks_only_when_it_claims_or_queues_work(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        act = _open_manager(page, "plan_act", _progress("plan_act", "active", [_step(1, "failed")]))
        pau = _open_manager(page, "plan_pau", _progress("plan_pau", "paused", [_step(1, "failed")]))
        posts = _record(page, "**/api/v1/harness/review/planner/plans/*/steps/step_1/mark", [(200, {"ok": True, "step_id": "step_1", "status": "x"})])
        _open_plans(page, studio_server)
        page.locator(_row("plan_act") + " button[data-role='steps']").click()
        page.wait_for_selector(act + " .step")
        asked = _dialogs(page, [False, True, True, True])
        step = act + " .step[data-step='step_1']"
        page.locator(step + " select").select_option("blocked")
        page.locator(step + " button[data-role='set']").click()                         # blocked claims nothing: no question
        _result_has(page, act, "Status set to blocked")
        assert asked == [] and posts == [{"status": "blocked", "confirm": False}]
        page.locator(step + " select").select_option("passed")
        page.locator(step + " button[data-role='set']").click()                         # passed: asks; declined sends nothing
        assert _until(page, lambda: asked)
        page.wait_for_timeout(200)
        assert len(posts) == 1 and "done by hand" in asked[0] and "Nothing is checked" in asked[0]
        page.locator(step + " button[data-role='set']").click()                         # passed, accepted
        assert _until(page, lambda: len(posts) == 2)
        assert posts[1] == {"status": "passed", "confirm": True}
        _result_has(page, act, "Status set to passed")
        page.locator(act + " .step[data-step='step_1'] select").select_option("pending")
        page.locator(act + " .step[data-step='step_1'] button[data-role='set']").click()   # pending on an ACTIVE plan: asks
        assert _until(page, lambda: len(posts) == 3)
        assert posts[2] == {"status": "pending", "confirm": True} and "back in the queue" in asked[-1]
        page.locator(_row("plan_pau") + " button[data-role='steps']").click()
        page.wait_for_selector(pau + " .step")
        before = len(asked)
        page.locator(pau + " .step[data-step='step_1'] select").select_option("pending")
        page.locator(pau + " .step[data-step='step_1'] button[data-role='set']").click()    # pending on a PAUSED plan: no question
        assert _until(page, lambda: len(posts) == 4)
        assert posts[3] == {"status": "pending", "confirm": False} and len(asked) == before
        browser.close()


def test_override_asks_first_and_names_what_it_does(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        manage = _open_manager(page, "plan_pau", _progress("plan_pau", "paused", [_step(1, "failed")]))
        posts = _record(page, "**/api/v1/harness/review/planner/plans/plan_pau/steps/step_1/override", [(200, {"ok": True, "status": "passed"})])
        _open_plans(page, studio_server)
        page.locator(_row("plan_pau") + " button[data-role='steps']").click()
        page.wait_for_selector(manage + " .step")
        asked = _dialogs(page, [False, True])
        button = manage + " button[data-role='override']"
        page.locator(button).click()
        assert _until(page, lambda: asked)
        page.wait_for_timeout(200)
        assert posts == [] and "without checking it" in asked[0] and "manual override" in asked[0]
        page.locator(button).click()
        assert _until(page, lambda: posts)
        assert posts == [{"confirm": True}]
        browser.close()


def test_run_now_asks_twice_and_grants_nothing_unless_the_second_answer_is_yes(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        manage = _open_manager(page, "plan_pau", _progress("plan_pau", "paused", [_step(1)]))
        page.route("**/api/v1/harness/review/blockers", lambda r: _fulfill(r, 200, {"ok": True, "open": 0, "recommended": ["modify_files", "web_search"]}))
        posts = _record(page, "**/api/v1/harness/review/planner/plans/plan_pau/steps/step_1/run",
                        [(200, {"ok": True, "session_id": "bg-1", "granted": []}), (200, {"ok": True, "session_id": "bg-2", "granted": ["modify_files", "web_search"]}),
                         (409, {"ok": False, "error": "already_running"})])
        _open_plans(page, studio_server)
        page.locator(_row("plan_pau") + " button[data-role='steps']").click()
        page.wait_for_selector(manage + " .step")
        asked = _dialogs(page, [False, True, False, True, True, True, False])      # three clicks: 1, 2 + 2, 2 questions
        run = manage + " button[data-role='run']"
        page.locator(run).click()                                                       # 1st question declined: nothing happens at all
        assert _until(page, lambda: asked)
        page.wait_for_timeout(200)
        assert posts == [] and "full agent turn" in asked[0] and "run budget" in asked[0] and len(asked) == 1
        page.locator(run).click()                                                       # yes, then no to the permissions
        assert _until(page, lambda: posts)
        assert posts == [{"confirm": True, "grant_recommended": False}]
        assert "modify_files, web_search" in asked[2] and "Cancel runs the step without granting" in asked[2]
        page.wait_for_selector(manage + " .note.result:not([hidden])")
        assert "Started. The result goes to the Outbox." in page.inner_text(manage)
        page.locator(run).click()                                                       # yes, yes
        assert _until(page, lambda: len(posts) == 2)
        assert posts[1] == {"confirm": True, "grant_recommended": True}
        page.locator(run).click()                                                       # the server says it is already running
        _status_has(page, "plan-status", "already running")
        browser.close()


def test_adding_a_step_sends_the_chosen_dependencies_and_asks_only_for_an_active_plan(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        act = _open_manager(page, "plan_act", _progress("plan_act", "active", [_step(1), _step(2)]))
        dra = _open_manager(page, "plan_dra", _progress("plan_dra", "draft", []))
        posts_a = _record(page, "**/api/v1/harness/review/planner/plans/plan_act/steps", [(200, {"ok": True, "step_id": "step_3"})])
        posts_d = _record(page, "**/api/v1/harness/review/planner/plans/plan_dra/steps", [(400, {"ok": False, "error": "title_required"}), (200, {"ok": True, "step_id": "step_9"})])
        _open_plans(page, studio_server)
        page.locator(_row("plan_act") + " button[data-role='steps']").click()
        page.wait_for_selector(act + " .add-step")
        asked = _dialogs(page, [False, True])
        form = act + " .add-step"
        page.locator(form + " input").first.fill("A new step")
        page.locator(form + " input").nth(1).fill("do the thing")
        page.locator(form + " select").select_option(["step_1", "step_2"])
        page.locator(form + " button").click()
        assert _until(page, lambda: asked)
        page.wait_for_timeout(200)
        assert posts_a == [] and "goes straight into its queue" in asked[0]
        page.locator(form + " button").click()
        assert _until(page, lambda: posts_a)
        assert posts_a == [{"title": "A new step", "description": "do the thing", "depends_on": ["step_1", "step_2"], "confirm": True}]
        page.locator(_row("plan_dra") + " button[data-role='steps']").click()
        page.wait_for_selector(dra + " .add-step")
        before = len(asked)
        page.locator(dra + " .add-step button").click()                                  # draft plan, empty title: no question, server refuses
        _status_has(page, "plan-status", "A title is needed")
        assert posts_d[0]["confirm"] is False and len(asked) == before
        browser.close()


def test_an_open_steps_panel_survives_a_reload_of_the_list(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        manage = _open_manager(page, "plan_act", _progress("plan_act", "active", [_step(1)]))
        _open_plans(page, studio_server)
        page.locator(_row("plan_act") + " button[data-role='steps']").click()
        page.wait_for_selector(manage + " .step")
        page.locator("#plan-refresh").click()
        page.wait_for_function("() => !document.getElementById('plan-refresh').disabled")
        page.wait_for_selector(manage + " .step")                                        # still open after the list was redrawn
        assert page.locator(_row("plan_act") + " button[data-role='steps']").inner_text() == "Hide steps"
        browser.close()


DISCS = {"discoveries": [{"id": "disc_a", "content": "a find", "metadata": {"title": "Find A", "status": "approved", "kind": "doc"}},
                         {"id": "disc_b", "content": "b find", "metadata": {"title": "Find B", "status": "pending", "kind": "doc"}}], "count": 2}


def _discs_page(page, data=None):
    page.route(re.compile(r".*/admin/daedalus/discoveries\?.*"), lambda r: _fulfill(r, 200, data or DISCS) if r.request.method == "GET" else r.fallback())


def test_regenerating_a_plan_asks_runs_in_the_background_and_reports_how_it_ended(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _discs_page(page)
        posts = _record(page, "**/api/v1/harness/review/discoveries/disc_a/regenerate", [(200, {"ok": True, "started": True, "discovery_id": "disc_a"})])
        polls = {"n": 0}

        def status(route):
            polls["n"] += 1
            running = ["disc_a"] if polls["n"] < 2 else []
            _fulfill(route, 200, {"ok": True, "running": running, "last": {} if running else {"disc_a": {"ok": True, "plan_id": "plan_new"}}})

        page.route("**/api/v1/harness/review/discoveries/regenerate-status", status)
        _open_plans(page, studio_server)
        page.wait_for_selector("#discovery-list .item[data-id='disc_a']")
        asked = _dialogs(page, [False, True])
        button = "#discovery-list .item[data-id='disc_a'] button[data-role='regenerate']"
        page.locator(button).click()
        assert _until(page, lambda: asked)
        page.wait_for_timeout(200)
        assert posts == [] and "full agent turn" in asked[0]
        page.locator(button).click()
        _status_has(page, "discovery-status", "Plan generation started")
        assert posts == [{"confirm": True}]
        _status_has(page, "discovery-status", "New plan created: plan_new")             # the result is reported when it ends
        assert page.locator("#discovery-status").get_attribute("data-state") == "ok"
        browser.close()


def test_a_failed_regeneration_and_a_second_start_are_reported_not_hidden(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _discs_page(page)
        _record(page, "**/api/v1/harness/review/discoveries/disc_a/regenerate",
                [(200, {"ok": True, "started": True}), (409, {"ok": False, "error": "already_running"})])
        page.route("**/api/v1/harness/review/discoveries/regenerate-status", lambda r: _fulfill(r, 200, {"ok": True, "running": [], "last": {"disc_a": {"ok": False, "error": "RuntimeError"}}}))
        _open_plans(page, studio_server)
        page.wait_for_selector("#discovery-list .item[data-id='disc_a']")
        _dialogs(page, [True, True])
        button = "#discovery-list .item[data-id='disc_a'] button[data-role='regenerate']"
        page.locator(button).click()
        _status_has(page, "discovery-status", "Plan generation failed (RuntimeError)")
        assert page.locator("#discovery-status").get_attribute("data-state") == "error"
        page.locator(button).click()
        _status_has(page, "discovery-status", "already running")
        browser.close()


def test_deleting_a_discovery_asks_first_and_the_list_follows(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        data = {"v": DISCS}
        page.route(re.compile(r".*/admin/daedalus/discoveries\?.*"), lambda r: _fulfill(r, 200, data["v"]) if r.request.method == "GET" else r.fallback())

        def delete(route):
            posts.append(json.loads(route.request.post_data or "{}"))
            data["v"] = {"discoveries": [DISCS["discoveries"][1]], "count": 1}
            _fulfill(route, 200, {"ok": True, "discovery_id": "disc_a"})

        posts: list[dict] = []
        page.route("**/api/v1/harness/review/discoveries/disc_a/delete", delete)
        _open_plans(page, studio_server)
        page.wait_for_selector("#discovery-list .item[data-id='disc_a']")
        asked = _dialogs(page, [False, True])
        button = "#discovery-list .item[data-id='disc_a'] button[data-role='delete']"
        page.locator(button).click()
        assert _until(page, lambda: asked)
        page.wait_for_timeout(200)
        assert posts == [] and "Delete this discovery for good" in asked[0]
        page.locator(button).click()
        _status_has(page, "discovery-status", "Discovery deleted")
        page.wait_for_function("() => !document.querySelector(\"#discovery-list .item[data-id='disc_a']\")")
        assert posts == [{"confirm": True}]
        browser.close()


def test_one_extra_action_runs_at_a_time_and_a_late_result_after_leaving_is_dropped(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        held = {}
        page.route("**/api/v1/harness/review/planner/plans/plan_act/pause", lambda r: held.update(route=r))
        _open_plans(page, studio_server)
        page.locator(_row("plan_act") + " button[data-role='pause']").click()
        assert _until(page, lambda: "route" in held)
        for selector in (_row("plan_pau") + " button[data-role='resume']", _row("plan_act") + " button[data-role='steps']", "#plan-manual-create"):
            assert page.locator(selector).is_disabled(), selector                          # one extra action at a time
        page.locator("#workspace-nav button[data-panel=chat]").click()                      # the owner leaves Review...
        _fulfill(held["route"], 200, {"ok": True, "status": "paused"})
        page.wait_for_timeout(500)
        assert "Plan paused" not in page.locator("#plan-status").text_content()             # ...so the late answer writes nothing
        page.locator("#workspace-nav button[data-panel=review]").click()
        page.wait_for_selector("#plan-list .item")
        page.wait_for_function("() => !document.getElementById('plan-manual-create').disabled", timeout=10000)
        browser.close()


def test_bulgarian_shows_the_planner_and_discovery_actions_without_raw_keys(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        manage = _open_manager(page, "plan_act", _progress("plan_act", "active", [_step(1)]))
        _discs_page(page)
        _open_plans(page, studio_server, lang="bg")
        page.locator(_row("plan_act") + " button[data-role='steps']").click()
        page.wait_for_selector(manage + " .step")
        text = page.inner_text("#panel-review")
        low = text.lower()
        for expected in ("създай ръчен план", "паузирай", "стъпки", "цикълът на планировчика не работи", "пусни сега", "добави стъпка",
                         "прегенерирай плана", "изтрий"):
            assert expected in low, expected
        assert not re.search(r"\b(studio|settings)\.[A-Za-z]", text), re.findall(r"\b(?:studio|settings)\.[A-Za-z.]+", text)
        assert "create manual plan" not in low and "run now" not in low
        browser.close()


@pytest.mark.parametrize("size", [(1280, 800), (1366, 768), (910, 640)])
def test_planner_and_discovery_controls_fit_and_are_really_clickable(studio_server, size):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": size[0], "height": size[1]})
        manage = _open_manager(page, "plan_act", _progress("plan_act", "active", [_step(1), _step(2, "failed")]))
        _discs_page(page)
        _open_plans(page, studio_server)
        page.locator(_row("plan_act") + " button[data-role='steps']").click()
        page.wait_for_selector(manage + " .step")
        page.wait_for_selector("#discovery-list .item[data-id='disc_a']")
        over = page.evaluate("""() => { const p = document.getElementById('panel-review');
          return {sw: p.scrollWidth, cw: p.clientWidth, docw: document.documentElement.scrollWidth, vw: window.innerWidth}; }""")
        assert over["sw"] <= over["cw"] + 1 and over["docw"] <= over["vw"] + 1, over
        for selector in ("#plan-manual-create", _row("plan_act") + " button[data-role='pause']", _row("plan_act") + " button[data-role='steps']",
                         manage + " .step[data-step='step_1'] button[data-role='set']", manage + " .step[data-step='step_1'] button[data-role='run']",
                         manage + " .add-step button", "#discovery-list .item[data-id='disc_a'] button[data-role='regenerate']",
                         "#discovery-list .item[data-id='disc_a'] button[data-role='delete']"):
            page.locator(selector).first.evaluate("el => el.scrollIntoView({block: 'center'})")
            _uncovered(page, selector)                                                       # looks, never clicks
        browser.close()


# ------------------------------------------------------------------------------------------------ review fixes
def test_a_failed_model_call_saves_no_junk_plan_and_is_recorded_as_failed(a0_tmp, monkeypatch, agent_with_memory):  # noqa: F811
    from daedalus import plans
    from daedalus.harness import planner_actions

    planner_actions._regen.clear()
    planner_actions._regen_last.clear()
    saved = []
    monkeypatch.setattr(plans, "save_plan", lambda plan: saved.append(plan))

    async def llm_down(**kw):
        raise RuntimeError("llm down")

    monkeypatch.setattr("daedalus.agent._call_claude", llm_down)

    async def scenario():
        await planner_actions.regenerate_start("d1", True)
        await asyncio.gather(*list(planner_actions._regen.values()))
        return planner_actions.regenerate_status()["last"]["d1"]

    last = asyncio.run(scenario())
    assert last["ok"] is False and last["error"] == "RuntimeError" and saved == []


def test_a_running_row_in_the_database_does_not_block_a_run_and_dependencies_and_plan_state_are_checked(client, monkeypatch):
    monkeypatch.setattr("daedalus.agent.get_daedalus", lambda: FakeAgent())
    from daedalus import planner
    from daedalus.harness import planner_actions

    planner_actions._bg.clear()
    plan_id, (a, b) = _plan(client, status="active", steps=2)
    asyncio.run(planner.mark_step(a, "running"))                                   # a crash left this behind
    ok = client.post(f"{P}/{plan_id}/steps/{a}/run", json={"confirm": True}).json()
    assert ok["ok"] is True
    dep_plan, (x,) = _plan(client, status="active")
    y = client.post(f"{P}/{dep_plan}/steps", json={"title": "second", "depends_on": [x], "confirm": True}).json()["step_id"]
    blocked = client.post(f"{P}/{dep_plan}/steps/{y}/run", json={"confirm": True})
    assert blocked.status_code == 409 and blocked.json()["error"] == "dependency_open"
    draft, (d1,) = _plan(client, status="draft")
    refused = client.post(f"{P}/{draft}/steps/{d1}/run", json={"confirm": True})
    assert refused.status_code == 409 and refused.json()["error"] == "plan_not_active"


def test_a_background_run_leaves_honest_notes_on_the_step_and_does_not_pass_it(a0_tmp, monkeypatch):  # noqa: F811
    from daedalus.harness import planner_actions

    planner_actions._bg.clear()

    async def scenario():
        from daedalus import planner

        monkeypatch.setattr("daedalus.agent.get_daedalus", lambda: FakeAgent())
        created = await planner.create_plan("p", "d", status="active")
        step = (await planner.add_step(created["plan_id"], "s", ""))["step_id"]
        await planner_actions.run_background(created["plan_id"], step, True, False)
        await asyncio.gather(*list(planner_actions._bg.values()))
        return (await planner_actions.progress(created["plan_id"]))["steps"][0]

    st = asyncio.run(scenario())
    assert st["status"] == "pending" and "background run ended" in (st["progress_note"] or "")


def test_deleting_a_missing_discovery_is_not_found_and_cancels_a_running_regeneration(a0_tmp, monkeypatch, agent_with_memory):  # noqa: F811
    from daedalus.harness import planner_actions

    planner_actions._regen.clear()
    planner_actions._regen_last.clear()

    async def scenario():
        gate = asyncio.Event()

        async def slow(discovery, registry, agent, save_on_error=True):
            await gate.wait()
            return {"id": "plan_late"}

        monkeypatch.setattr("daedalus.plans.generate_plan", slow)
        missing = planner_actions.delete_discovery("d_nope", True)
        await planner_actions.regenerate_start("d1", True)
        gone = planner_actions.delete_discovery("d1", True)
        await asyncio.sleep(0)
        return missing, gone, planner_actions.regenerate_status()

    missing, gone, status = asyncio.run(scenario())
    assert missing == {"ok": False, "error": "not_found"} and gone["ok"] is True
    assert status["running"] == [] and "d1" not in status["last"]
