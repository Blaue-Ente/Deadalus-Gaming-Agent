"""While Daedalus works, the Studio chat shows what it is doing: a timer and one line per step (tools, model calls, the
Mentor's plan, review and what it saw). Owner, 2026-11-12: "it must be visible what Daedalus is doing, so I know it
works, not that there is no reaction at all"."""
from __future__ import annotations

import json

import pytest
from a0_support import a0_tmp  # noqa: F401

from daedalus import activity
from daedalus.native_tools import activity_target, activity_tool


@pytest.fixture
def runs(a0_tmp):  # noqa: F811
    from daedalus.harness import runs

    runs.reset()
    yield runs
    runs.reset()


def test_targets_name_what_was_touched_and_never_carry_contents():
    assert activity_target("write_file", {"slug": "g", "path": "game.js", "content": "secret=abc"}) == "game.js"
    assert activity_target("run_command", {"command": "npm install --token sk-live-123"}) == "npm install"
    assert activity_target("web_fetch", {"url": "https://example.com/a?key=sk-1"}) == "example.com"
    assert activity_target("think", "raw") == ""


def test_a_tool_call_inside_a_run_becomes_an_activity_row(runs):
    rid = runs.open_run("chat", slug="g", summary="t")
    tok = runs.bind(rid)
    try:
        activity_tool("write_file", {"slug": "g", "path": "index.html"}, ok=True, blocked=False, ms=12, result="── ok")
        activity_tool("browser", {"slug": "g"}, ok=False, blocked=False, ms=900, result="── browser FAILED: blank\nmore")
    finally:
        runs.unbind(tok)
    rows, seq = activity.since(rid, 0)
    tools = [r for r in rows if r["type"] == "tool"]
    assert [(r["tool"], r["target"], r["ok"]) for r in tools] == [("write_file", "index.html", True), ("browser", "g", False)]
    assert tools[1]["note"] == "── browser FAILED: blank" and seq == runs.events(rid)[-1]["seq"]
    assert activity.since(rid, seq) == ([], seq)


def test_mentor_rows_are_readable_and_capped():
    plan = activity.compact({"seq": 3, "ts": 1.0, "type": "mentor_plan",
                             "payload": {"brief": "b" * 900, "checklist": ["jump", "score", ""], "model": "m"}})
    assert len(plan["brief"]) == 400 and plan["checklist"] == ["jump", "score"]
    rev = activity.compact({"seq": 4, "ts": 2.0, "type": "mentor_review",
                            "payload": {"accept": False, "missing": ["no restart"], "saw_frames": 2}})
    assert rev["accept"] is False and rev["missing"] == ["no restart"] and rev["saw_frames"] == 2
    assert activity.compact({"type": "mentor_plan", "payload": {"skipped": "budget"}})["skipped"] == "budget"
    assert activity.compact({"type": "secret_internal", "payload": {}}) is None


def test_the_chat_stream_sends_activity_before_done(runs, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    from daedalus import chat_jobs

    rid = runs.open_run("chat", slug="g", summary="t")
    tok = runs.bind(rid)
    activity_tool("write_file", {"path": "index.html"}, ok=True, blocked=False, ms=5)
    runs.unbind(tok)
    chat_jobs._jobs["jx"] = {"id": "jx", "status": "done", "session_id": "s", "created": __import__("time").time(), "run_id": rid,
                             "result": {"reply": "ok"}}
    monkeypatch.setenv("DAEDALUS_TOKEN", "t")
    try:
        body = TestClient(app, headers={"x-daedalus-token": "t"}).get("/api/v1/daedalus/chat/jobs/jx/stream").text
    finally:
        chat_jobs._jobs.pop("jx", None)
    assert "event: activity" in body and body.index("event: activity") < body.index("event: done")
    data = json.loads(body.split("event: activity\ndata: ", 1)[1].split("\n", 1)[0])
    assert any(r["type"] == "tool" and r["target"] == "index.html" for r in data["rows"])


# ── Studio ─────────────────────────────────────────────────────────────────────────────────────────────────────────

import re  # noqa: E402

from test_a63a_build_controls import _browser  # noqa: E402
from test_studio_phase2_workspace import _fulfill, studio_server  # noqa: E402,F401  (fixture and helpers)

ROWS = [{"seq": 2, "ts": 0, "type": "mentor_plan", "brief": "A tiny runner", "checklist": ["jump", "score"], "model": "m"},
        {"seq": 3, "ts": 0, "type": "tool", "tool": "write_file", "target": "game.js", "ok": True, "ms": 40},
        {"seq": 4, "ts": 0, "type": "tool", "tool": "browser", "target": "g", "ok": False, "ms": 1500,
         "note": "blank screen"},
        {"seq": 5, "ts": 0, "type": "mentor_review", "accept": False, "missing": ["no restart"], "saw_frames": 2}]


@pytest.mark.parametrize("lang, done_word, plan_word", [("en", "What Daedalus did", "Mentor plan"),
                                                         ("bg", "Какво направи Дедал", "План на Ментора")])
def test_the_studio_chat_shows_each_step_and_a_summary(studio_server, lang, done_word, plan_word):
    from playwright.sync_api import sync_playwright

    job = {"id": "j1", "status": "done", "session_id": "s", "result": {"reply": "Built it."}, "error": None}
    body = ("event: activity\ndata: " + json.dumps({"rows": ROWS}) + "\n\n"
            + "event: done\ndata: " + json.dumps(job) + "\n\n")
    with sync_playwright() as pw:
        browser, page = _browser(pw)
        page.route(re.compile(r".*/api/v1/daedalus/chat/jobs$"),
                   lambda r: _fulfill(r, 200, {"ok": True, "job_id": "j1", "session_id": "s"})
                   if r.request.method == "POST" else r.fallback())
        page.route(re.compile(r".*/api/v1/daedalus/chat/jobs/j1/stream.*"),
                   lambda r: r.fulfill(status=200, headers={"Content-Type": "text/event-stream"}, body=body))
        page.route(re.compile(r".*/api/v1/daedalus/chat/jobs/j1$"), lambda r: _fulfill(r, 200, {"ok": True, **job}))
        page.add_init_script(f"try {{ localStorage.setItem('dx.lang', '{lang}'); }} catch (e) {{}}")
        page.goto(studio_server["url"] + "/", wait_until="domcontentloaded")
        page.locator("#prompt").fill("make a runner")
        page.locator("#send").click()
        page.wait_for_selector(".activity.idle")
        summary = page.locator(".activity summary").inner_text()
        assert summary.startswith(done_word) and ("4 steps" in summary or "4 стъпки" in summary)
        page.locator(".activity summary").click()
        items = page.locator(".activity > details > ol > li")
        assert items.count() == 4
        assert plan_word in items.nth(0).inner_text() and "jump" in items.nth(0).inner_text()
        assert "game.js" in items.nth(1).inner_text() and "blank screen" in items.nth(2).inner_text()
        assert "no restart" in items.nth(3).inner_text()
        assert page.locator(".msg.bot").last.inner_text().startswith("Built it.")
        browser.close()
