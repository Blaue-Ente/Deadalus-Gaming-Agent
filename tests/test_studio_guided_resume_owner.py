"""Explicit guided build owns the stream. Resume, stop, slug, error, and timeout do not.

Each guided job id has its own response body. This file does not use the
one-shot pop queue from the diagnostics test. That queue's parent result is
recorded there: job-early took the checkpoint and job-7 was left with the log.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VISIBLE = """() => !document.getElementById('build-checkpoint').hidden
  && !document.getElementById('flight-checkpoint').hidden
  && document.getElementById('pane-flight').classList.contains('on')"""


def _sse(review, line):
    payload = {"review": review, "options": [{"id": "approve", "label": "Approve and continue"}]}
    return (
        "event: log\n"
        "data: " + json.dumps({"line": line}) + "\n\n"
        "event: checkpoint\n"
        "data: " + json.dumps(payload) + "\n\n"
    )


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _api(server, method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(server["url"] + path, data=data, method=method)
    token = (Path(server["data"]) / "owner.token").read_text(encoding="utf-8").strip()
    req.add_header("Authorization", "Bearer " + token)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as res:
        raw = res.read().decode()
        return json.loads(raw) if raw else {}


def _fulfill(route, status, payload):
    route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))


@pytest.fixture(scope="module")
def studio_server(tmp_path_factory):
    pytest.importorskip("playwright")
    data = tmp_path_factory.mktemp("resume-owner-data")
    port = _free_port()
    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    env.update({
        "DAEDALUS_STANDALONE": "1",
        "DAEDALUS_DATA": str(data),
        "DAEDALUS_PLAYGROUND": str(data / "playground"),
        "DAEDALUS_OFFLINE": "1",
    })
    for key in (
        "DAEDALUS_MENTOR",
        "DAEDALUS_RUN_BUDGET",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "NOVITA_API_KEY",
        "ALLTOKEN_API_KEY",
        "GITHUB_TOKEN",
        "DAEDALUS_API_KEY",
    ):
        env.pop(key, None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    server = {"url": f"http://127.0.0.1:{port}", "data": data, "proc": proc}
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(server["url"] + "/health", timeout=1) as res:
                if res.status == 200:
                    break
        except Exception:
            time.sleep(0.2)
    else:
        out = proc.stdout.read().decode("utf-8", "replace") if proc.stdout else ""
        proc.kill()
        pytest.fail("studio server did not start\n" + out[-500:])
    game = _api(server, "POST", "/api/v1/playground/projects", {"name": "Old Game", "kind": "static"})
    other = _api(server, "POST", "/api/v1/playground/projects", {"name": "Other Game", "kind": "static"})
    server["game"] = game["project"]["slug"]
    server["other"] = other["project"]["slug"]
    yield server
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


class _Owned:
    """Stream bodies are chosen by job id. Nothing is popped from a shared list."""

    def __init__(self, page):
        self.page = page
        self.actives = []
        self.posts = []
        self.streams = []
        self.autopilots = []
        self.stops = []
        self.held_posts = []
        self.post_mode = "hold"
        self.autopilot_job = None

    def install(self):
        page = self.page

        def guard(route):
            if not route.request.url.startswith("http://127.0.0.1"):
                route.abort()
                return
            route.fallback()

        page.route("**/*", guard)
        page.route("**/guided/active", self._active)
        page.route("**/guided/stream**", self._stream)
        page.route("**/build/guided", self._guided)
        page.route("**/guided/stop", self._stop)
        page.route("**/autopilot/stream**", self._auto_stream)
        page.route("**/autopilot", self._auto)
        page.route("**/game-studio/api/projects/*", self._workspace)

    def _workspace(self, route):
        path = route.request.url.split("?", 1)[0].rstrip("/")
        if route.request.method != "GET" or "/build/" in path or path.endswith("/gates") or path.endswith("/verify"):
            route.fallback()
            return
        if not path.split("/projects/")[-1]:
            route.fallback()
            return
        _fulfill(route, 200, {
            "ok": True,
            "can_guided": True,
            "can_autopilot": False,
            "can_advance": False,
            "milestone_summary": {"milestones": []},
        })

    def _guided(self, route):
        if route.request.method != "POST":
            route.fallback()
            return
        self.posts.append(route.request.url)
        if self.post_mode == "hold":
            self.held_posts.append(route)
            return
        if self.post_mode == "error":
            _fulfill(route, 400, {"detail": "no saved plan"})
            return
        if self.post_mode == "abort":
            route.abort("timedout")
            return
        _fulfill(route, 200, {"ok": True, "job_id": "job-7"})

    def _active(self, route):
        self.actives.append(route)

    def _stream(self, route):
        url = route.request.url
        self.streams.append(url)
        if "job-early" in url or "job-live" in url:
            review, line = "stale job checkpoint", "stale-line"
        elif "job-7" in url:
            review, line = "stage needs a look", "explicit-line"
        else:
            review, line = "other job checkpoint", "other-line"
        route.fulfill(status=200, headers={"Content-Type": "text/event-stream"}, body=_sse(review, line))

    def _stop(self, route):
        self.stops.append(route.request.url)
        _fulfill(route, 200, {"ok": True, "cancelled": True})

    def _auto(self, route):
        path = route.request.url.split("?", 1)[0].rstrip("/")
        if path.endswith("/autopilot/stream") or "/stream" in path:
            route.fallback()
            return
        if not path.endswith("/autopilot"):
            route.fallback()
            return
        self.autopilots.append(route.request.url)
        job = {"id": "auto-9", "status": "running"} if self.autopilot_job else None
        _fulfill(route, 200, {"ok": True, "job": job})

    def _auto_stream(self, route):
        self.streams.append(route.request.url)
        route.fulfill(
            status=200,
            headers={"Content-Type": "text/event-stream"},
            body=_sse("autopilot checkpoint", "auto-line"),
        )

    def release_actives(self, payload):
        pending = list(self.actives)
        self.actives.clear()
        assert pending, "guided/active was not held"
        for route in pending:
            _fulfill(route, 200, payload)
        return len(pending)

    def release_posts(self, payload):
        pending = list(self.held_posts)
        self.held_posts = []
        assert pending, "guided POST was not held"
        for route in pending:
            _fulfill(route, 200, payload)

    def settle(self):
        for route in list(self.actives) + list(self.held_posts):
            try:
                route.abort()
            except Exception:
                pass


def _open(page, server, game):
    page.goto(server["url"] + f"/?panel=build&slug={game}", wait_until="domcontentloaded")
    page.wait_for_function("() => !document.getElementById('build-guided').disabled")
    page.evaluate("() => 1")


def _pump(page):
    page.evaluate("() => 1")


def _no_resume_request(page, predicate, action):
    """The resume fetch is the positive event on the parent. Its absence is the fix.

    One second is the bound for that fetch to show up. It is not a product delay.
    """
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    try:
        with page.expect_request(predicate, timeout=1000):
            action()
            page.evaluate("() => 1")
    except PlaywrightTimeout:
        return
    raise AssertionError("resume sent a request while the explicit build still owns the stream")


def test_explicit_job_keeps_its_own_body_when_active_arrives_first(studio_server):
    from playwright.sync_api import sync_playwright

    game = studio_server["game"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        owned = _Owned(page)
        try:
            owned.install()
            _open(page, studio_server, game)
            page.locator("#build-guided").click()
            page.wait_for_function("() => document.getElementById('build-guided').disabled")
            _pump(page)
            assert owned.posts
            assert owned.streams == []
            owned.release_actives({"active": True, "job_id": "job-early"})
            _pump(page)
            assert owned.streams == []
            assert owned.autopilots == []
            owned.release_posts({"ok": True, "job_id": "job-7"})
            page.wait_for_function(VISIBLE)
            assert len(owned.streams) == 1
            assert "job-7" in owned.streams[0]
            assert "job-early" not in owned.streams[0]
            assert "stage needs a look" in page.locator("#build-cp-review").inner_text()
            assert "stage needs a look" in page.locator("#flight-cp-review").inner_text()
            assert "explicit-line" in page.locator("#log").inner_text()
            assert "stale job checkpoint" not in page.locator("#log").inner_text()
            assert "stale-line" not in page.locator("#log").inner_text()
        finally:
            owned.settle()
            browser.close()


def test_false_active_while_post_is_pending_does_not_open_autopilot(studio_server):
    from playwright.sync_api import sync_playwright

    game = studio_server["game"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        owned = _Owned(page)
        owned.autopilot_job = "auto-9"
        try:
            owned.install()
            _open(page, studio_server, game)
            page.locator("#build-guided").click()
            page.wait_for_function("() => document.getElementById('build-guided').disabled")
            _no_resume_request(
                page,
                lambda req: req.url.rstrip("/").endswith("/autopilot") or "/guided/stream" in req.url,
                lambda: owned.release_actives({"active": False}),
            )
            assert owned.streams == []
            assert owned.autopilots == []
            owned.release_posts({"ok": True, "job_id": "job-7"})
            page.wait_for_function(VISIBLE)
            assert len(owned.streams) == 1
            assert "job-7" in owned.streams[0]
            assert "/autopilot/stream" not in owned.streams[0]
            assert "stage needs a look" in page.locator("#log").inner_text()
            assert "auto-line" not in page.locator("#log").inner_text()
        finally:
            owned.settle()
            browser.close()


def test_genuine_active_resume_follows_one_job_without_a_guided_post(studio_server):
    from playwright.sync_api import sync_playwright

    game = studio_server["game"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        owned = _Owned(page)
        try:
            owned.install()
            _open(page, studio_server, game)
            assert owned.posts == []
            owned.release_actives({"active": True, "job_id": "job-live"})
            page.wait_for_function(VISIBLE)
            assert owned.posts == []
            assert len(owned.streams) == 1
            assert "job-live" in owned.streams[0]
            assert "stale job checkpoint" in page.locator("#build-cp-review").inner_text()
            assert "stale-line" in page.locator("#log").inner_text()
            assert "on" in (page.locator("#pane-flight").get_attribute("class") or "").split()
        finally:
            owned.settle()
            browser.close()


def test_stop_does_not_resurrect_the_pending_guided_job(studio_server):
    from playwright.sync_api import sync_playwright

    game = studio_server["game"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        owned = _Owned(page)
        try:
            owned.install()
            _open(page, studio_server, game)
            owned.release_actives({"active": True, "job_id": "job-live"})
            page.wait_for_function("() => !document.getElementById('build-stop').hidden")
            assert any("job-live" in url for url in owned.streams)
            page.locator("#build-guided").click()
            page.wait_for_function("() => document.getElementById('build-guided').disabled")
            page.locator("#build-stop").click()
            page.wait_for_function("() => document.getElementById('build-status').textContent.includes('Stopped.')")
            before = list(owned.streams)
            owned.release_posts({"ok": True, "job_id": "job-7"})
            _pump(page)
            assert owned.streams == before
            assert not any("job-7" in url for url in owned.streams)
            assert "Stopped." in page.locator("#build-status").inner_text()
            assert page.locator("#build-checkpoint").is_hidden()
        finally:
            owned.settle()
            browser.close()


def test_slug_change_does_not_resurrect_the_pending_guided_job(studio_server):
    from playwright.sync_api import sync_playwright

    game = studio_server["game"]
    other = studio_server["other"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        owned = _Owned(page)
        try:
            owned.install()
            _open(page, studio_server, game)
            page.locator("#build-guided").click()
            page.wait_for_function("() => document.getElementById('build-guided').disabled")
            started = list(owned.actives)
            page.locator(f"#plist [data-slug='{other}']").click()
            page.wait_for_function(
                """(slug) => {
                  const row = document.querySelector("#plist [data-slug='" + slug + "']");
                  return row && row.className.indexOf("active") >= 0;
                }""",
                arg=other,
            )
            _pump(page)
            stale = [route for route in owned.actives if route in started]
            fresh = [route for route in owned.actives if route not in started]
            owned.actives.clear()
            assert stale, "the first project's guided/active replies were not still held"
            for route in stale:
                _fulfill(route, 200, {"active": True, "job_id": "job-early"})
            for route in fresh:
                _fulfill(route, 200, {"active": False})
            owned.release_posts({"ok": True, "job_id": "job-7"})
            _pump(page)
            assert not any("job-7" in url or "job-early" in url for url in owned.streams)
            assert page.locator("#build-checkpoint").is_hidden()
        finally:
            owned.settle()
            browser.close()


def test_guided_error_and_timeout_do_not_resurrect_a_stale_job(studio_server):
    from playwright.sync_api import sync_playwright

    game = studio_server["game"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        owned = _Owned(page)
        try:
            owned.post_mode = "error"
            owned.install()
            _open(page, studio_server, game)
            page.locator("#build-guided").click()
            page.wait_for_function(
                """() => document.getElementById('build-status').dataset.state === 'error'
                  && document.getElementById('build-status').textContent.includes('no saved plan')"""
            )
            _no_resume_request(
                page,
                lambda req: "/guided/stream" in req.url,
                lambda: owned.release_actives({"active": True, "job_id": "job-early"}),
            )
            assert owned.streams == []
            page.goto(studio_server["url"] + f"/?panel=build&slug={game}", wait_until="domcontentloaded")
            page.wait_for_function("() => !document.getElementById('build-guided').disabled")
            _pump(page)
            owned.streams.clear()
            owned.post_mode = "abort"
            page.locator("#build-guided").click()
            page.wait_for_function(
                """() => {
                  const status = document.getElementById('build-status');
                  return status.dataset.state === 'error' && status.textContent.length > 0;
                }"""
            )
            _no_resume_request(
                page,
                lambda req: "/guided/stream" in req.url,
                lambda: owned.release_actives({"active": True, "job_id": "job-early"}),
            )
            assert owned.streams == []
            assert len(owned.posts) == 2
        finally:
            owned.settle()
            browser.close()
