"""A7: the brain gets the harness: milestones (gates, verify, advance without waivers), preview (run/stop/logs),
play (the native play worker), runs (the run ledger, read-only) and ship requests (publish/push: the brain can only
ask; the owner confirms in the Studio, and the request carries no force or public flag)."""
from __future__ import annotations

import asyncio
import json

import pytest
from a0_support import a0_tmp  # noqa: F401
from fastapi.testclient import TestClient

NEW = ("milestones", "preview", "play", "runs", "ship_request")


@pytest.fixture
def slug(a0_tmp):  # noqa: F811
    from daedalus import playground as pg

    return asyncio.run(pg.create_project("Brain Game", kind="static"))["slug"]


def _call(name, args, session_id="studio-x"):
    from daedalus.native_tools import execute_native_tool

    return asyncio.run(execute_native_tool(name, args, memory=None, session_id=session_id, read_file_fn=lambda *a, **k: {}))


def _json(out: str) -> dict:
    return json.loads(out.split("\n", 1)[1])


# ---- the tools exist where the brain and the registry see them ----------------------------------------------------

def test_the_new_tools_are_native_tools_with_specs_and_permission_entries():
    from daedalus.native_tools import NATIVE_TOOL_SPECS, PERM_MAP

    names = {t["name"] for t in NATIVE_TOOL_SPECS}
    for name in NEW:
        assert name in names, name
        assert name in PERM_MAP, name
        spec = next(t for t in NATIVE_TOOL_SPECS if t["name"] == name)
        assert spec["input_schema"]["type"] == "object" and "action" in spec["input_schema"]["properties"]


def test_the_specs_come_from_one_table_in_the_harness_and_the_registry_exposes_them(a0_tmp):  # noqa: F811
    from daedalus.harness import brain_tools
    from daedalus.harness.registry import build_registry
    from daedalus.native_tools import NATIVE_TOOL_SPECS

    assert [s for s in NATIVE_TOOL_SPECS if s["name"] in NEW] == list(brain_tools.SPECS)
    caps = {c.id: c for c in build_registry()}
    for name in NEW:
        cap = caps[f"tool:{name}"]
        assert cap.brain["exposed"] is True and cap.owner_module == "daedalus.harness.brain_tools", name


def test_a_build_session_offers_the_new_tools(a0_tmp):  # noqa: F811
    from daedalus.native_tools import playground_build_specs

    names = {t["name"] for t in playground_build_specs()}
    assert {"milestones", "preview", "play", "runs", "ship_request"} <= names


def test_game_playbooks_route_the_new_tools_and_native_engines_get_play():
    from daedalus import task_playbooks as tp

    by = {pb.id: set(pb.tools) for pb in tp.PLAYBOOKS}
    for pid, tools in by.items():
        if pid.startswith("playground."):
            assert {"milestones", "preview", "runs", "ship_request"} <= tools, pid
    for pid in ("playground.godot", "playground.unity", "playground.generic"):
        assert "play" in by[pid], pid
    assert not ({"milestones", "preview", "play", "runs", "ship_request"} & by["harness.fix"])


def test_a_route_that_offers_the_tools_also_says_what_they_are_for(a0_tmp):  # noqa: F811
    from daedalus.tool_router import format_system, route

    build = route("make a neon maze game in this project", session_id="pg-neon-maze")
    text = format_system(build, user_message="make a neon maze game")
    assert "HARNESS TOOLS" in text and "ship_request" in text and "cannot publish or push" in text
    # a project chat routed as talk still gets the build tool list; the hint follows what is offered
    from daedalus.native_tools import playground_build_specs
    talk_pg = route("check the preview logs", session_id="pg-neon-maze")
    assert "HARNESS TOOLS" in format_system(talk_pg, user_message="x", offered=playground_build_specs())
    assert "HARNESS TOOLS" not in format_system(talk_pg, user_message="x", offered=[{"name": "read_file"}])
    talk = route("hi, how are you?", session_id="studio-x")
    if not ({"milestones", "preview", "runs", "ship_request"} & set(talk.playbook.tools)):
        assert "HARNESS TOOLS" not in format_system(talk, user_message="hi")


def test_no_spec_offers_a_force_a_public_flag_or_a_waiver():
    from daedalus.harness import brain_tools

    for spec in brain_tools.SPECS:
        props = spec["input_schema"]["properties"]
        for banned in ("force", "confirm", "confirm_public", "public", "unity_waiver", "proof_waiver", "waiver"):
            assert banned not in props, (spec["name"], banned)


# ---- milestones -----------------------------------------------------------------------------------------------------

def test_milestones_state_reads_the_project_and_an_unknown_one_is_said(slug, monkeypatch):
    async def ctx(s):
        if s != slug:
            raise ValueError("not a game studio project")
        return {"current_milestone": "M1", "gates_passed": False, "can_advance": False, "milestones": [{"id": "M1"}],
                "project_complete": False, "plan": {"big": "x" * 10}, "config": {"secret": "no"}}

    monkeypatch.setattr("daedalus.game_studio.get_workspace_context", ctx)
    out = _call("milestones", {"action": "state", "slug": slug})
    assert out.startswith("── milestones state OK")
    body = _json(out)
    assert body["current_milestone"] == "M1" and body["can_advance"] is False and "config" not in body and "plan" not in body
    bad = _call("milestones", {"action": "state", "slug": "nope"})
    assert "FAILED" in bad and "not a game studio project" in bad


def test_advance_never_waives_and_refuses_while_full_auto_owns_the_project(slug, monkeypatch):
    seen = []

    async def advance(s, *, force=False, unity_waiver=False, proof_waiver=False):
        seen.append({"force": force, "unity_waiver": unity_waiver, "proof_waiver": proof_waiver})
        return {"advanced": True, "current_milestone": "M2"}

    monkeypatch.setattr("daedalus.game_studio.advance_project_milestone", advance)
    out = _call("milestones", {"action": "advance", "slug": slug, "force": True, "unity_waiver": True, "proof_waiver": True})
    assert "OK" in out and seen == [{"force": False, "unity_waiver": False, "proof_waiver": False}]
    monkeypatch.setattr("daedalus.harness.full_auto.owns", lambda s: True)
    for action in ("advance", "verify"):
        refused = _call("milestones", {"action": action, "slug": slug})
        assert "FAILED" in refused and "full_auto_running" in refused
    assert len(seen) == 1


def test_gates_and_verify_call_the_studio_checks(slug, monkeypatch):
    calls = []

    async def gates(s, gate_ids=None):
        calls.append(("gates", gate_ids))
        return {"gates": [{"id": "g", "ok": True}], "passed": True}

    async def verify(s, milestone_id=None, on_log=None):
        calls.append(("verify", milestone_id))
        await on_log("checking", "info")
        return {"passed": False, "proof_level": "L1"}

    monkeypatch.setattr("daedalus.game_studio.run_project_gates", gates)
    monkeypatch.setattr("daedalus.game_studio_verify.run_milestone_verify", verify)
    assert "OK" in _call("milestones", {"action": "gates", "slug": slug, "gates": ["g"]})
    out = _call("milestones", {"action": "verify", "slug": slug})
    body = _json(out)
    assert body["passed"] is False and body["logs"] == [{"line": "checking", "level": "info"}]
    assert calls == [("gates", ["g"]), ("verify", None)]


def test_an_unknown_action_is_refused_with_the_list(slug):
    out = _call("milestones", {"action": "delete", "slug": slug})
    assert "FAILED" in out and "state | gates | verify | advance" in out


# ---- preview --------------------------------------------------------------------------------------------------------

def test_preview_runs_stops_and_reads_logs_through_the_runner(slug, monkeypatch):
    seen = []

    async def start(s, *, install_deps=False, tunnel=False):
        seen.append(("start", s, install_deps, tunnel))
        return {"status": "running", "port": 5555, "_proc": object()}

    async def stop(s):
        seen.append(("stop", s))
        return True

    monkeypatch.setattr("daedalus.playground_runner.start", start)
    monkeypatch.setattr("daedalus.playground_runner.stop", stop)
    monkeypatch.setattr("daedalus.playground_runner.logs", lambda s, tail=200: ["line 1", "line 2"][-tail:])
    monkeypatch.setattr("daedalus.playground_runner.status", lambda s: {"status": "running"})
    run = _json(_call("preview", {"action": "run", "slug": slug, "tunnel": True}))
    assert run["ok"] is True and "_proc" not in run["run"] and seen[0] == ("start", slug, False, False)   # no tunnel from the brain
    assert _json(_call("preview", {"action": "logs", "slug": slug, "tail": 1}))["logs"] == ["line 2"]
    assert _json(_call("preview", {"action": "stop", "slug": slug}))["stopped"] is True
    missing = _call("preview", {"action": "run", "slug": "no-such-project"})
    assert "FAILED" in missing and "project not found" in missing


# ---- play -----------------------------------------------------------------------------------------------------------

class _FakeSession:
    def __init__(self):
        self.sent = []

    def frame(self, source="window"):
        return {"ok": True, "sha256": "ab" * 32, "blank": False, "file": "frame_0001_window.png", "level": 3}

    def send(self, *, keys=None, mouse=None, hold_ms=80, via="os"):
        self.sent.append((keys, mouse, via))
        return {"ok": True, "sent": [{"type": "key"}]}

    def wait(self, *, frames=0, ms=0):
        return {"ok": True}

    def game_state(self, path=""):
        return {"ok": True, "state": {"score": 3}}

    def errors(self):
        return {"ok": True, "errors": []}

    def status(self):
        return {"ok": True, "id": "s1", "state": "running", "level": 3}


class _FakeManager:
    def __init__(self):
        self.session = _FakeSession()
        self.started = []

    def start(self, slug, project_dir, *, engine="godot", size=(640, 480)):
        self.started.append((slug, engine))
        return {"ok": True, "id": "s1", "level": 1}

    def get(self, sid):
        from daedalus.harness.play_worker.errors import PlayError

        if sid != "s1":
            raise PlayError("not_found", f"no play session {sid!r}")
        return self.session

    def stop(self, sid):
        return {"ok": True, "id": sid, "state": "stopped"}

    def list(self):
        return [self.session.status()]


def test_play_starts_frames_sends_input_and_stops_with_honest_errors(slug, monkeypatch):
    from daedalus.harness import brain_tools

    mgr = _FakeManager()
    monkeypatch.setattr(brain_tools, "_play_manager", lambda: mgr)
    assert _json(_call("play", {"action": "start", "slug": slug, "engine": "godot"}))["id"] == "s1"
    frame = _json(_call("play", {"action": "frame", "id": "s1"}))
    assert frame["sha256"] == "ab" * 32 and frame["level"] == 3 and "path" not in frame
    _call("play", {"action": "input", "id": "s1", "keys": ["space"], "mouse": {"kind": "click", "x": 3, "y": 4}})
    assert mgr.session.sent == [(["space"], {"kind": "click", "x": 3, "y": 4}, "os")]
    assert _json(_call("play", {"action": "state", "id": "s1"}))["state"] == {"score": 3}
    gone = _call("play", {"action": "frame", "id": "zz"})
    assert "FAILED" in gone and "not_found" in gone
    assert _json(_call("play", {"action": "stop", "id": "s1"}))["state"] == "stopped"
    assert "FAILED" in _call("play", {"action": "start", "slug": "no-such-project"})


def test_play_without_an_engine_is_blocked_not_a_pass(slug, monkeypatch):
    from daedalus.harness import brain_tools
    from daedalus.harness.play_worker.errors import PlayError

    class NoEngine(_FakeManager):
        def start(self, *a, **k):
            raise PlayError("engine_missing", "Godot was not found")

    monkeypatch.setattr(brain_tools, "_play_manager", lambda: NoEngine())
    out = _call("play", {"action": "start", "slug": slug})
    assert "FAILED" in out and "engine_missing" in out and '"ok": false' in out


# ---- runs -----------------------------------------------------------------------------------------------------------

def test_runs_lists_one_run_and_its_events_read_only(slug):
    from daedalus.harness import runs

    rid = runs.open_run("verify", slug=slug, summary="check M1")
    runs.event(rid, "log", {"line": "started"})
    runs.set_state(rid, "running")
    listed = _json(_call("runs", {"action": "list", "slug": slug}))
    assert [r["id"] for r in listed["runs"]] == [rid]
    one = _json(_call("runs", {"action": "get", "id": rid}))
    assert one["run"]["id"] == rid and one["run"]["state"] == "running"
    evs = _json(_call("runs", {"action": "events", "id": rid}))
    assert any(e["type"] == "log" for e in evs["events"])
    assert "FAILED" in _call("runs", {"action": "get", "id": "nope"})
    assert "FAILED" in _call("runs", {"action": "cancel", "id": rid})               # read-only: no other action


# ---- ship requests: the brain asks, the owner decides ---------------------------------------------------------------

def test_a_ship_request_is_only_recorded_and_carries_no_force_or_public_flag(slug, monkeypatch):
    called = []
    for target in ("daedalus.live_publish.publish_live", "daedalus.playground_publish.publish_local"):
        try:
            monkeypatch.setattr(target, lambda *a, **k: called.append(target))
        except AttributeError:
            pass
    out = _call("ship_request", {"action": "request", "slug": slug, "kind": "live", "note": "M3 is verified",
                                 "force": True, "confirm_public": True, "backend": "netlify_anon"})
    body = _json(out)
    req = body["request"]
    assert body["ok"] is True and req["status"] == "pending" and req["kind"] == "live" and req["slug"] == slug
    assert set(req) == {"id", "slug", "kind", "note", "status", "created_at", "session_id"}
    assert called == []
    assert "FAILED" in _call("ship_request", {"action": "request", "slug": slug, "kind": "delete"})
    assert "FAILED" in _call("ship_request", {"action": "request", "slug": "no-such-project", "kind": "local"})
    listed = _json(_call("ship_request", {"action": "list", "slug": slug}))
    assert [r["id"] for r in listed["requests"]] == [req["id"]]


def test_the_owner_resolves_a_request_and_the_brain_cannot(slug, a0_tmp):  # noqa: F811
    from app.main import app

    req = _json(_call("ship_request", {"action": "request", "slug": slug, "kind": "push", "note": "ready"}))["request"]
    assert "FAILED" in _call("ship_request", {"action": "resolve", "id": req["id"], "outcome": "approved"})
    client = TestClient(app)
    listed = client.get("/api/v1/harness/ship-requests").json()
    assert listed["ok"] is True and [r["id"] for r in listed["requests"]] == [req["id"]]
    assert client.post(f"/api/v1/harness/ship-requests/{req['id']}/resolve", json={"outcome": "maybe"}).status_code == 400
    done = client.post(f"/api/v1/harness/ship-requests/{req['id']}/resolve", json={"outcome": "declined"}).json()
    assert done["ok"] is True and done["request"]["status"] == "declined"
    again = client.post(f"/api/v1/harness/ship-requests/{req['id']}/resolve", json={"outcome": "approved"})
    assert again.status_code == 409 and again.json()["error"] == "already_resolved"
    assert client.post("/api/v1/harness/ship-requests/nope/resolve", json={"outcome": "declined"}).status_code == 404
    assert client.get("/api/v1/harness/ship-requests?status=pending").json()["requests"] == []


# ---- review fixes (A7 code review, 2026-11-11) -----------------------------------------------------------------------

def test_tools_that_write_or_run_need_their_permission_and_a_revoke_blocks_them(slug, monkeypatch):
    from daedalus.native_tools import PERM_MAP

    assert PERM_MAP["milestones"] == "write_file" and PERM_MAP["preview"] == "run_command" and PERM_MAP["play"] == "run_command"
    assert PERM_MAP["runs"] is None and PERM_MAP["ship_request"] is None
    started = []

    async def start(*a, **k):
        started.append(a)
        return {"status": "running"}

    monkeypatch.setattr("daedalus.playground_runner.start", start)
    monkeypatch.setattr("daedalus.outbox.check_permission", lambda cls: cls not in ("run_command", "write_file"))
    for name, args in (("preview", {"action": "run", "slug": slug}), ("milestones", {"action": "verify", "slug": slug}),
                       ("play", {"action": "start", "slug": slug})):
        out = _call(name, args)
        assert "OK ──" not in out.split("\n", 1)[0], (name, out[:200])
    assert started == []


def test_offline_mode_refuses_installs_and_full_auto_keeps_its_preview_and_config(slug, monkeypatch):
    started = []

    async def start(*a, **k):
        started.append(k)
        return {"status": "running"}

    monkeypatch.setattr("daedalus.playground_runner.start", start)
    monkeypatch.setattr("daedalus.offline.enabled", lambda: True)
    out = _call("preview", {"action": "run", "slug": slug, "install_deps": True})
    assert "FAILED" in out and "offline" in out and started == []
    monkeypatch.setattr("daedalus.offline.enabled", lambda: False)
    monkeypatch.setattr("daedalus.harness.full_auto.owns", lambda s: True)
    for name, args in (("preview", {"action": "stop", "slug": slug}), ("preview", {"action": "run", "slug": slug}),
                       ("milestones", {"action": "gates", "slug": slug}), ("milestones", {"action": "state", "slug": slug})):
        assert "full_auto_running" in _call(name, args), args
    assert started == []


def test_play_input_is_bounded_and_idle_sessions_are_reaped(slug, monkeypatch):
    from daedalus.harness import brain_tools

    mgr = _FakeManager()
    reaped = []
    mgr.reap_idle = lambda: reaped.append(1) or []
    monkeypatch.setattr(brain_tools, "_play_manager", lambda: mgr)
    monkeypatch.setattr(brain_tools, "_manager", mgr)
    too_many = _call("play", {"action": "input", "id": "s1", "keys": ["a"] * 33})
    assert "FAILED" in too_many and "32 keys" in too_many and mgr.session.sent == []
    sent = []
    mgr.session.send = lambda **kw: sent.append(kw) or {"ok": True, "sent": []}
    _call("play", {"action": "input", "id": "s1", "keys": ["a"], "hold_ms": 600000})
    assert sent[0]["hold_ms"] == 5000
    assert len(reaped) >= 2                                  # every play call sweeps idle sessions
    assert brain_tools.reap_play_sessions() == 0


def test_pending_requests_are_never_evicted_and_asking_again_does_not_stack(slug, monkeypatch):
    from daedalus.harness import brain_tools

    monkeypatch.setattr(brain_tools, "_SHIP_KEEP", 3)
    first = _json(_call("ship_request", {"action": "request", "slug": slug, "kind": "local", "note": "a"}))["request"]
    again = _json(_call("ship_request", {"action": "request", "slug": slug, "kind": "local", "note": "b"}))
    assert again["request"]["id"] == first["id"] and "Already asked" in again["note"]
    _call("ship_request", {"action": "request", "slug": slug, "kind": "live"})
    _call("ship_request", {"action": "request", "slug": slug, "kind": "push"})
    full = _call("ship_request", {"action": "request", "slug": slug, "kind": "local"})   # same as first: returned, not refused
    assert "Already asked" in full
    rows = brain_tools.list_ship_requests(status="pending")
    assert {r["kind"] for r in rows} == {"local", "live", "push"} and first["id"] in {r["id"] for r in rows}
    brain_tools.resolve_ship_request(first["id"], "declined")
    brain_tools._ship_save(brain_tools._ship_load())
    assert len(brain_tools.list_ship_requests()) == 3                            # answered ones go first, pending stay


def test_the_ship_requests_panel_is_a_panel_in_the_registry(a0_tmp):  # noqa: F811
    from daedalus.harness.registry import build_registry

    cap = {c.id: c for c in build_registry()}["panel:preview/ship-requests"]
    assert cap.kind == "panel" and cap.status == "implemented"


# ---- Studio: the owner answers the requests with real clicks ----------------------------------------------------------

from test_a61c_maintenance import _dialogs  # noqa: E402
from test_a62a_review import _until  # noqa: E402
from test_studio_phase2_workspace import _api, _fulfill, studio_server  # noqa: E402,F401  (fixture and helpers)


def _ask(server, slug, kind, note):
    """What the brain does: record a request (through the same code the native tool runs, on the live server's data)."""
    import re as _re
    import subprocess
    import sys

    code = ("import asyncio,json;from daedalus.harness import brain_tools as b;"
            f"print(json.dumps(asyncio.run(b.run('ship_request', {{'action':'request','slug':{slug!r},'kind':{kind!r},'note':{note!r}}}))))")
    out = subprocess.run([sys.executable, "-c", code], env=server["env"], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-800:]
    return json.loads(_re.findall(r"\{.*\}", out.stdout)[-1])["request"]


def _open_preview(page, server, slug):
    page.add_init_script("try { localStorage.setItem('dx.lang', 'en'); } catch (e) {}")
    page.goto(server["url"] + f"/?panel=preview&slug={slug}", wait_until="domcontentloaded")
    page.locator(f"#plist [data-slug='{slug}']").wait_for()


def test_the_owner_runs_a_request_with_the_studios_own_button_and_declines_another(studio_server):
    from playwright.sync_api import sync_playwright

    game = studio_server["game"]
    local = _ask(studio_server, game, "local", "M3 verified")
    push = _ask(studio_server, game, "push", "ready for the repo")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        publishes = []
        try:
            page.route(f"**/api/v1/playground/projects/{game}/publish-local",
                       lambda r: (publishes.append(r.request.method), _fulfill(r, 200, {"ok": True, "harness_check": True})))
            asked = _dialogs(page, [False, True])
            _open_preview(page, studio_server, game)
            page.wait_for_selector(f"#ship-requests-list [data-id='{local['id']}']")
            assert page.locator("#ship-requests-list .item").count() == 2
            assert "M3 verified" in page.locator(f"#ship-requests-list [data-id='{local['id']}']").text_content()
            run = f"#ship-requests-list [data-id='{local['id']}'] button[data-act='run']"
            page.locator(run).click()                                      # declined: nothing happens
            assert _until(page, lambda: len(asked) == 1) and publishes == []
            assert _api(studio_server, "GET", "/api/v1/harness/ship-requests?status=pending")["requests"][-1]["id"] in (local["id"], push["id"])
            page.locator(run).click()
            page.wait_for_function("() => document.getElementById('preview-status').textContent === 'Harness check'")
            assert publishes == ["POST"] and "Local copy" in asked[1]
            page.locator("#git-menu summary").click()                       # a push needs the git menu's button
            page.evaluate("() => { document.getElementById('git-push').disabled = true; }")   # it cannot run now
            page.locator(f"#ship-requests-list [data-id='{push['id']}'] button[data-act='run']").click()
            page.wait_for_function("() => document.getElementById('ship-requests-status').textContent.includes('Nothing was approved')")
            assert _api(studio_server, "GET", "/api/v1/harness/ship-requests?status=pending")["requests"][0]["id"] == push["id"]
            page.locator(f"#ship-requests-list [data-id='{push['id']}'] button[data-act='decline']").click()
            page.wait_for_function("() => document.getElementById('ship-requests-status').textContent.includes('Declined')")
            assert page.locator("#ship-requests-list .item").count() == 0
            rows = {r["id"]: r["status"] for r in _api(studio_server, "GET", "/api/v1/harness/ship-requests")["requests"]}
            assert rows[local["id"]] == "approved" and rows[push["id"]] == "declined"
        finally:
            browser.close()


def test_a_request_answered_elsewhere_is_said_and_bulgarian_has_no_raw_keys(studio_server):
    from playwright.sync_api import sync_playwright

    game = studio_server["game"]
    req = _ask(studio_server, game, "live", "share it")
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        try:
            _dialogs(page, [True])
            _open_preview(page, studio_server, game)
            page.wait_for_selector(f"#ship-requests-list [data-id='{req['id']}']")
            _api(studio_server, "POST", f"/api/v1/harness/ship-requests/{req['id']}/resolve", {"outcome": "declined"})
            page.locator(f"#ship-requests-list [data-id='{req['id']}'] button[data-act='run']").click()
            page.wait_for_function("() => document.getElementById('ship-requests-status').textContent.includes('already answered')")
            assert page.locator("#ship-requests-status").get_attribute("data-state") == "error"
            bg = browser.new_page(viewport={"width": 1280, "height": 800})          # a page whose owner picked BG
            bg.add_init_script("try { localStorage.setItem('dx.lang', 'bg'); } catch (e) {}")
            bg.goto(studio_server["url"] + f"/?panel=preview&slug={game}", wait_until="domcontentloaded")
            bg.wait_for_function("() => document.querySelector('#ship-requests .kicker').textContent.includes('Дедал')")
            text = bg.locator("#ship-requests").evaluate("el => el.textContent")
            assert "studio." not in text and "Заявки от Дедал" in text
        finally:
            browser.close()


def test_a_project_chat_cannot_start_or_write_another_project(slug):
    from daedalus import playground as pg

    sid = f"pg-{slug}"
    made = _call("playground_new", {"name": "Camera Slerp Arcade", "kind": "static"}, session_id=sid)
    assert "REFUSED" in made and f"about project {slug}" in made
    assert not pg.project_dir("camera-slerp-arcade").exists()
    other = _call("write_file", {"slug": "other-game", "path": "index.html", "content": "<h1>x</h1>"}, session_id=sid)
    assert "REFUSED" in other and not pg.project_dir("other-game").exists()
    edit = _call("edit_file", {"slug": "other-game", "path": "index.html", "search": "a", "replace": "b"}, session_id=sid)
    assert "REFUSED" in edit
    own = _call("write_file", {"slug": slug, "path": "notes.txt", "content": "kept"}, session_id=sid)
    assert "REFUSED" not in own and pg.read_file(slug, "notes.txt") == "kept"
    free = _call("playground_new", {"name": "Free Game", "kind": "static"}, session_id="studio-x")   # not a project chat
    assert "OK" in free.split("\n", 1)[0]
    auto = _call("playground_new", {"name": "Auto Game", "kind": "static"}, session_id="pg-auto-123")  # autopilot
    assert "REFUSED" not in auto
