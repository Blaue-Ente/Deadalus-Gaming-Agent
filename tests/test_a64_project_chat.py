"""A6.4: project chat in the Studio (the classic page's project chat) with a reviewable, revertible record of what the
turn changed. Documents and the blocking chat are already covered by Studio attachments and chat jobs."""
from __future__ import annotations

import asyncio

import pytest
from a0_support import a0_tmp  # noqa: F401
from fastapi.testclient import TestClient


class Agent:
    """Does what a real turn does to a project: writes, edits and deletes files through the playground."""

    def __init__(self, slug="", edits=None, gate=None):
        self.slug, self.edits, self.gate, self.calls = slug, edits or (lambda: None), gate, []

    async def chat(self, message, session_id="", **kw):
        self.calls.append((message, session_id))
        if self.gate:
            await self.gate.wait()
        self.edits()
        return {"reply": "done"}


@pytest.fixture
def project(a0_tmp):  # noqa: F811
    from daedalus import playground as pg

    slug = asyncio.run(pg.create_project("Chat Me", kind="static"))["slug"]
    pg.write_file(slug, "index.html", "<h1>old</h1>\n")
    pg.write_file(slug, "game.js", "let score = 0;\n")
    return slug


def _run(slug, agent, monkeypatch, **kw):
    from daedalus import chat_jobs

    monkeypatch.setattr("daedalus.agent.get_daedalus", lambda: agent)

    async def go():
        started = await chat_jobs.start_chat(message="make it red", session_id="studio-x", project=slug, **kw)
        await chat_jobs._jobs[started["job_id"]]["task"]
        return started, chat_jobs.get_job(started["job_id"])

    return asyncio.run(go())


def test_project_chat_sees_the_project_and_keeps_the_project_history(project, monkeypatch):
    agent = Agent()
    started, job = _run(project, agent, monkeypatch)
    message, session = agent.calls[0]
    assert session == f"pg-{project}" and started["session_id"] == f"pg-{project}"
    assert message.startswith(f"[PLAYGROUND {project} · static · standalone]") and "index.html" in message
    assert message.endswith("make it red") and job["project"] == project and job["changes"] == []


def test_a_plain_chat_is_unchanged(project, monkeypatch):
    from daedalus import chat_jobs

    agent = Agent()
    monkeypatch.setattr("daedalus.agent.get_daedalus", lambda: agent)

    async def go():
        started = await chat_jobs.start_chat(message="hello", session_id="studio-x")
        await chat_jobs._jobs[started["job_id"]]["task"]
        return chat_jobs.get_job(started["job_id"])

    job = asyncio.run(go())
    assert agent.calls == [("hello", "studio-x")] and job["project"] is None and job["changes"] is None


def test_the_turns_changes_are_listed_with_diffs_and_secrets_are_never_read(project, monkeypatch):
    from daedalus import playground as pg

    def edits():
        pg.write_file(project, "index.html", "<h1>new</h1>\n")
        pg.write_file(project, "level.json", '{"n": 1}\n')
        (pg.project_dir(project) / "game.js").unlink()
        pg.write_file(project, ".env", "TOKEN=secret-value\n")

    _, job = _run(project, Agent(edits=edits), monkeypatch)
    by = {c["path"]: c for c in job["changes"]}
    assert by["index.html"]["status"] == "modified" and "-<h1>old</h1>" in by["index.html"]["diff"] and "+<h1>new</h1>" in by["index.html"]["diff"]
    assert by["level.json"]["status"] == "created" and by["game.js"]["status"] == "deleted"
    assert by[".env"] == {"path": ".env", "status": "created", "secret": True, "diff": "", "revertible": False}
    assert "secret-value" not in str(job) and all("before_text" not in c and "after_sha" not in c for c in job["changes"])


def _client():
    from app.main import app

    return TestClient(app)


def test_reverting_restores_removes_and_never_overwrites_a_later_edit(project, monkeypatch):
    from daedalus import playground as pg

    def edits():
        pg.write_file(project, "index.html", "<h1>new</h1>\n")
        pg.write_file(project, "level.json", "{}\n")
        (pg.project_dir(project) / "game.js").unlink()

    started, _ = _run(project, Agent(edits=edits), monkeypatch)
    url = f"/api/v1/daedalus/chat/jobs/{started['job_id']}/revert"
    c = _client()
    assert c.post(url, json={"path": "index.html"}).status_code == 400                      # asks first
    assert c.post(url, json={"path": "index.html", "confirm": True}).json() == {"ok": True, "path": "index.html", "reverted": "restored"}
    assert pg.read_file(project, "index.html") == "<h1>old</h1>\n"
    assert c.post(url, json={"path": "index.html", "confirm": True}).json()["error"] == "already_reverted"
    assert c.post(url, json={"path": "game.js", "confirm": True}).json()["reverted"] == "restored"
    assert pg.read_file(project, "game.js") == "let score = 0;\n"
    pg.write_file(project, "level.json", '{"edited": "later"}\n')                          # the owner edited it after the turn
    res = c.post(url, json={"path": "level.json", "confirm": True})
    assert res.status_code == 409 and res.json()["error"] == "changed_since"
    assert pg.read_file(project, "level.json") == '{"edited": "later"}\n'
    assert c.post(url, json={"path": "nope.txt", "confirm": True}).status_code == 404
    assert c.post("/api/v1/daedalus/chat/jobs/nojob/revert", json={"path": "x", "confirm": True}).status_code == 404


def test_a_created_file_is_removed_on_revert(project, monkeypatch):
    from daedalus import playground as pg

    started, _ = _run(project, Agent(edits=lambda: pg.write_file(project, "new.js", "x\n")), monkeypatch)
    res = _client().post(f"/api/v1/daedalus/chat/jobs/{started['job_id']}/revert", json={"path": "new.js", "confirm": True})
    assert res.json()["reverted"] == "removed" and not (pg.project_dir(project) / "new.js").exists()


def test_a_running_turn_cannot_be_reverted_and_an_unknown_project_is_refused(project, monkeypatch):
    from daedalus import chat_jobs

    monkeypatch.setattr("daedalus.agent.get_daedalus", lambda: Agent())
    res = _client().post("/api/v1/daedalus/chat/jobs", json={"message": "hi", "project": "no-such-project"})
    assert res.status_code == 404 and res.json()["detail"] == "project_not_found"

    async def go():
        gate = asyncio.Event()
        monkeypatch.setattr("daedalus.agent.get_daedalus", lambda: Agent(gate=gate))
        started = await chat_jobs.start_chat(message="m", session_id="s", project=project)
        mid = chat_jobs.revert_change(started["job_id"], "index.html")
        gate.set()
        await chat_jobs._jobs[started["job_id"]]["task"]
        return mid

    assert asyncio.run(go()) == {"ok": False, "error": "still_running"}


# ------------------------------------------------------------------------------------------------ UI (real clicks)
import json  # noqa: E402
import re  # noqa: E402

from test_a61c_maintenance import _dialogs  # noqa: E402
from test_a62a_review import _record, _until  # noqa: E402
from test_a63a_build_controls import G, _browser, _game  # noqa: E402
from test_studio_phase2_workspace import _fulfill, studio_server  # noqa: E402,F401  (fixture and helpers)

CHANGES = [{"path": "index.html", "status": "modified", "diff": "--- a/index.html\n+++ b/index.html\n-<h1>old</h1>\n+<h1>new</h1>\n",
            "revertible": True, "binary": False},
           {"path": "level.json", "status": "created", "diff": "+{}\n", "revertible": True, "binary": False},
           {"path": ".env", "status": "created", "secret": True, "diff": "", "revertible": False}]


def _chat(page, job_row):
    posts = []

    def start(route):
        if route.request.method != "POST":
            return route.fallback()
        posts.append(json.loads(route.request.post_data or "{}"))
        _fulfill(route, 200, {"ok": True, "job_id": "j1", "session_id": "pg-" + G})

    page.route(re.compile(r".*/api/v1/daedalus/chat/jobs$"), start)
    body = "event: done\ndata: " + json.dumps(job_row) + "\n\n"
    page.route(re.compile(r".*/api/v1/daedalus/chat/jobs/j1/stream.*"),
               lambda r: r.fulfill(status=200, headers={"Content-Type": "text/event-stream"}, body=body))
    page.route(re.compile(r".*/api/v1/daedalus/chat/jobs/j1$"), lambda r: _fulfill(r, 200, {"ok": True, **job_row}))
    return posts


def _open_chat(page, server, lang="en"):
    assert _game(server) == G
    page.add_init_script(f"try {{ localStorage.setItem('dx.lang', '{lang}'); localStorage.setItem('dx.slug', '{G}'); }} catch (e) {{}}")
    page.goto(server["url"] + f"/?slug={G}", wait_until="domcontentloaded")
    page.wait_for_function(f"() => window.dxStudio && window.dxStudio.getSlug() === '{G}'")
    page.wait_for_selector("#projchatwrap:not([hidden])")


def _send(page, text):
    page.locator("#prompt").fill(text)
    page.locator("#send").click()


def _job(changes, project=G):
    return {"id": "j1", "status": "done", "session_id": "pg-" + G, "result": {"reply": "Made it red."}, "error": None,
            "cancelled": False, "project": project, "changes": changes}


def test_project_chat_is_off_by_default_and_sends_the_project_only_when_switched_on(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        posts = _chat(page, _job([], project=None))
        _open_chat(page, studio_server)
        assert page.locator("#projchat").is_checked() is False
        _send(page, "hello")
        assert _until(page, lambda: posts)
        assert posts[0]["project"] == ""                                            # a plain chat stays a plain chat
        page.wait_for_function("() => !document.getElementById('send').disabled")
        page.locator("#projchat").check()
        _send(page, "make it red")
        assert _until(page, lambda: len(posts) == 2)
        assert posts[1]["project"] == G
        browser.close()


def test_the_changes_of_a_project_turn_are_listed_with_diffs_and_a_revert_that_asks(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        _chat(page, _job(CHANGES))
        reverts = _record(page, "**/api/v1/daedalus/chat/jobs/j1/revert",
                          [(200, {"ok": True, "path": "index.html", "reverted": "restored"}),
                           (409, {"ok": False, "error": "changed_since"})])
        _open_chat(page, studio_server)
        page.locator("#projchat").check()
        _send(page, "make it red")
        page.wait_for_selector(".msg.turn-changes .change[data-path='level.json']")
        card = ".msg.turn-changes"
        assert f"This turn changed 3 files in {G}:" in page.inner_text(card)
        assert "secret file: not shown" in page.inner_text(card + " .change[data-path='.env']")
        assert page.locator(card + " .change[data-path='.env'] button").count() == 0
        page.locator(card + " .change[data-path='index.html'] button[data-role='diff']").click()
        assert "+<h1>new</h1>" in page.inner_text(card + " .change[data-path='index.html'] pre")
        asked = _dialogs(page, [False, True, True])
        page.locator(card + " .change[data-path='index.html'] button[data-role='revert']").click()
        assert _until(page, lambda: asked)
        page.wait_for_timeout(150)
        assert reverts == [] and "before this turn" in asked[0]
        page.locator(card + " .change[data-path='index.html'] button[data-role='revert']").click()
        page.wait_for_function("() => document.querySelector(\".change[data-path='index.html'] small\").textContent.includes('Restored')")
        assert reverts == [{"path": "index.html", "confirm": True}]
        assert page.locator(card + " .change[data-path='index.html'] button[data-role='revert']").count() == 0
        page.locator(card + " .change[data-path='level.json'] button[data-role='revert']").click()
        assert _until(page, lambda: len(asked) == 3) and "which this turn created" in asked[2]
        page.wait_for_function("() => document.querySelector(\".change[data-path='level.json'] small\").textContent.includes('changed again')")
        browser.close()


def test_a_turn_that_changed_nothing_says_so(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        _chat(page, _job([]))
        _open_chat(page, studio_server)
        page.locator("#projchat").check()
        _send(page, "just look")
        page.wait_for_selector(".msg.turn-changes")
        assert f"This turn changed no files in {G}." in page.inner_text(".msg.turn-changes")
        browser.close()


def test_bulgarian_project_chat_without_raw_keys(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw, (910, 640))
        _chat(page, _job(CHANGES))
        _open_chat(page, studio_server, lang="bg")
        assert " ".join(page.inner_text("#projchatwrap").split()) == f"Чат за проекта · {G}"   # it names the project
        page.locator("#projchat").check()
        _send(page, "направи го червено")
        page.wait_for_selector(".msg.turn-changes .change[data-path='index.html']")
        text = page.inner_text(".msg.turn-changes")
        assert "Този ход промени 3 файла" in text and "Върни" in text and "Разлики" in text
        assert not re.search(r"\bstudio\.[A-Za-z]", page.inner_text("body"))
        browser.close()


@pytest.mark.parametrize("size", [(1280, 800), (1366, 768), (910, 640)])
def test_the_project_chat_switch_fits_and_is_really_clickable(studio_server, size):
    from playwright.sync_api import sync_playwright

    from test_a62b_planner import _uncovered

    with sync_playwright() as pw:
        browser, page = _browser(pw, size)
        _open_chat(page, studio_server)
        for selector in ("#projchat", "#send"):
            page.locator(selector).evaluate("el => el.scrollIntoView({block: 'center'})")
            _uncovered(page, selector)
        over = page.evaluate("() => ({docw: document.documentElement.scrollWidth, vw: window.innerWidth})")
        assert over["docw"] <= over["vw"] + 1, over
        browser.close()


# ------------------------------------------------------------------------------------------------ review fixes
def test_a_modified_secret_is_reported_but_never_shown_or_revertible(project, monkeypatch):
    from daedalus import playground as pg

    pg.write_file(project, ".env", "TOKEN=old-secret\n")
    _, job = _run(project, Agent(edits=lambda: pg.write_file(project, ".env", "TOKEN=new-secret\n")), monkeypatch)
    assert job["changes"] == [{"path": ".env", "status": "modified", "secret": True, "diff": "", "revertible": False}]
    assert "secret" not in str(job).replace('"secret": True', "").replace("'secret': True", "")       # no secret value anywhere


@pytest.mark.parametrize("how", ["error", "cancel"])
def test_a_failed_or_stopped_turn_still_lists_what_it_wrote(project, monkeypatch, how):
    from daedalus import chat_jobs
    from daedalus import playground as pg

    class Broken(Agent):
        async def chat(self, message, session_id="", **kw):
            pg.write_file(project, "half.js", "partial\n")
            if how == "error":
                raise RuntimeError("model down")
            await asyncio.sleep(10)

    monkeypatch.setattr("daedalus.agent.get_daedalus", lambda: Broken())

    async def go():
        started = await chat_jobs.start_chat(message="make it red", session_id="s", project=project)
        task = chat_jobs._jobs[started["job_id"]]["task"]
        if how == "cancel":
            await asyncio.sleep(0.05)
            chat_jobs.cancel(started["job_id"])
        try:
            await task
        except asyncio.CancelledError:
            pass
        return chat_jobs.get_job(started["job_id"])

    job = asyncio.run(go())
    assert job["status"] == ("error" if how == "error" else "cancelled")
    assert [c["path"] for c in job["changes"]] == ["half.js"] and job["changes"][0]["status"] == "created"


def test_the_classic_project_chat_sends_the_same_prompt(project, monkeypatch):
    agent = Agent()
    monkeypatch.setattr("daedalus.agent.get_daedalus", lambda: agent)
    res = _client().post(f"/admin/daedalus/playground/api/projects/{project}/chat", json={"message": "make it red"})
    assert res.json()["ok"] is True
    message, session = agent.calls[0]
    assert session == f"pg-{project}" and message.startswith(f"[PLAYGROUND {project} · static · standalone]") and message.endswith("make it red")


def test_switching_project_turns_project_chat_off(studio_server):
    from playwright.sync_api import sync_playwright

    from test_studio_phase2_workspace import _api

    other = _api(studio_server, "POST", "/api/v1/playground/projects", {"name": "Other Chat", "kind": "static"})["project"]["slug"]
    with sync_playwright() as pw:
        browser, page = _browser(pw)
        _open_chat(page, studio_server)
        page.locator("#projchat").check()
        page.evaluate(f"() => window.dxStudio.selectSlug('{other}')")
        page.wait_for_function(f"() => document.getElementById('projchatslug').textContent.includes('{other}')")
        assert page.locator("#projchat").is_checked() is False
        browser.close()
