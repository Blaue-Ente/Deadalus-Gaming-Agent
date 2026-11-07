"""Ordered guided/active barriers for the build stream.

Held routes decide the order. These tests do not change timeouts, and they
do not claim to be the cause of a past CI failure. The third order used to
record the resume reader taking the one-shot checkpoint body. That expectation
is now the product rule: the explicit guided job keeps the checkpoint. The
parent observation stays in that test's docstring.
"""
from __future__ import annotations

import importlib.util
import json
import socket
import subprocess
import tempfile
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _diag():
    name = "studio_stream_diag"
    if name in sys.modules:
        return sys.modules[name]
    path = Path(__file__).resolve().parent / "studio_stream_diag.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


diag = _diag()

VISIBLE = """() => !document.getElementById('build-checkpoint').hidden
  && !document.getElementById('flight-checkpoint').hidden
  && document.getElementById('pane-flight').classList.contains('on')"""


def _checkpoint_sse():
    payload = {
        "review": "stage needs a look",
        "options": [{"id": "approve", "label": "Approve and continue", "hint": "keep going"}],
    }
    return (
        "event: log\n"
        'data: {"line":"compile-slice-ready"}\n\n'
        "event: checkpoint\n"
        "data: " + json.dumps(payload) + "\n\n"
    )


def _log_sse(line):
    return "event: log\ndata: " + json.dumps({"line": line}) + "\n\n"


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
    data = tmp_path_factory.mktemp("stream-diag-data")
    port = _free_port()
    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    env.update({
        "DAEDALUS_STANDALONE": "1",
        "DAEDALUS_DATA": str(data),
        "DAEDALUS_PLAYGROUND": str(data / "playground"),
    })
    for key in (
        "DAEDALUS_MENTOR",
        "DAEDALUS_RUN_BUDGET",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "NOVITA_API_KEY",
        "ALLTOKEN_API_KEY",
    ):
        env.pop(key, None)
    server_log = tempfile.TemporaryFile()  # a file, never an unread pipe: a full pipe blocks the server
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT),
        env=env,
        stdout=server_log,
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
        server_log.seek(0)
        out = server_log.read().decode("utf-8", "replace")
        proc.kill()
        pytest.fail("studio server did not start\n" + out[-500:])
    game = _api(server, "POST", "/api/v1/playground/projects", {"name": "Old Game", "kind": "static"})
    server["game"] = game["project"]["slug"]
    yield server
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_stream_diag_record_drops_auth_owner_paths_and_caps():
    trace = diag.Trace()
    trace.add(
        "request",
        url="http://127.0.0.1:9/guided/stream?job_id=job-7&token=SECRET-VALUE",
        authorization="Bearer owner-secret",
        text="see owner.token under the data dir",
    )
    blob = json.dumps(trace.events)
    assert "SECRET-VALUE" not in blob
    assert "owner-secret" not in blob
    assert "owner.token" not in blob
    assert "job_id=job-7" in blob or "job-7" in blob
    capped = diag.Trace()
    for i in range(diag.MAX_EVENTS + 3):
        capped.add("console", text=f"line-{i}")
    assert len(capped.events) == diag.MAX_EVENTS
    assert capped.dropped == 3


class _Gates:
    def __init__(self, page, game, hold_post):
        self.page = page
        self.game = game
        self.hold_post = hold_post
        self.trace = diag.attach(page)
        self.actives = []
        self.posts = []
        self.streams = []
        self.autopilots = []
        self.bodies = []

    def install(self, bodies):
        self.bodies = list(bodies)
        game = self.game
        page = self.page
        page.route(f"**/game-studio/api/projects/{game}", self._workspace)
        page.route(f"**/*{game}/autopilot**", self._autopilot)
        page.route(f"**/playground/api/projects/{game}/guided/active", self._active)
        page.route(f"**/playground/api/projects/{game}/guided/stream**", self._stream)
        page.route(f"**/game-studio/api/projects/{game}/build/guided", self._guided)

    def _workspace(self, route):
        path = route.request.url.split("?", 1)[0].rstrip("/")
        if route.request.method != "GET" or not path.endswith("/game-studio/api/projects/" + self.game):
            route.fallback()
            return
        self.trace.add("stub-response", method="GET", url=route.request.url, note="workspace")
        _fulfill(route, 200, {
            "ok": True,
            "can_guided": True,
            "can_autopilot": False,
            "can_advance": True,
            "milestone_summary": {"milestones": []},
        })

    def _guided(self, route):
        self.trace.add("stub-post", method="POST", url=route.request.url, held=self.hold_post)
        if self.hold_post:
            self.posts.append(route)
            return
        _fulfill(route, 200, {"ok": True, "job_id": "job-7"})

    def _active(self, route):
        self.actives.append(route)
        self.trace.add("stub-hold", method="GET", url=route.request.url, note="guided/active")

    def _stream(self, route):
        body = self.bodies.pop(0) if self.bodies else _log_sse("extra")
        kind = "checkpoint" if "event: checkpoint" in body else "log"
        self.streams.append({
            "route": route,
            "url": diag.safe_url(route.request.url),
            "kind": kind,
            "body": body,
        })
        self.trace.add("stub-stream", method="GET", url=route.request.url, body=kind)

    def _autopilot(self, route):
        path = route.request.url.split("?", 1)[0].rstrip("/")
        if path.endswith("/autopilot/stream"):
            self.trace.add("stub-autopilot-stream", url=route.request.url)
            route.fulfill(status=200, headers={"Content-Type": "text/event-stream"}, body=_log_sse("autopilot-stream"))
            return
        if not path.endswith("/autopilot"):
            route.fallback()
            return
        self.autopilots.append(diag.safe_url(route.request.url))
        self.trace.add("stub-autopilot", url=route.request.url)
        _fulfill(route, 200, {"ok": True, "job": None})

    def release_actives(self, payload):
        pending = list(self.actives)
        self.actives.clear()
        if not pending:
            summary = diag.dump(self.page, self.trace, "no-held-active")
            raise AssertionError("guided/active was not held\n" + summary)
        for route in pending:
            _fulfill(route, 200, payload)
            self.trace.add("stub-release", method="GET", url=route.request.url, body=json.dumps(payload))
        return len(pending)

    def fulfill_stream(self, index):
        item = self.streams[index]
        item["route"].fulfill(
            status=200,
            headers={"Content-Type": "text/event-stream"},
            body=item["body"],
        )
        item["settled"] = True
        self.trace.add("stub-release", method="GET", url=item["url"], body=item["kind"])

    def settle(self):
        for item in self.streams:
            if item.get("settled"):
                continue
            try:
                item["route"].abort()
            except Exception:
                pass
        for route in self.posts + self.actives:
            try:
                route.abort()
            except Exception:
                pass


def _barrier(page, trace, label, predicate, action):
    try:
        with page.expect_request(predicate):
            action()
        # The route callback runs on the next driver turn. This evaluate is that turn.
        page.evaluate("() => 1")
    except Exception as exc:
        summary = diag.dump(page, trace, label)
        raise AssertionError(label + "\n" + summary) from exc


def _urls(gates):
    return [item["url"] for item in gates.streams]


def test_late_inactive_active_does_not_open_a_second_stream(studio_server):
    """A guided/active reply that arrives after the click's stream starts does not follow again."""
    from playwright.sync_api import sync_playwright

    game = studio_server["game"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        gates = _Gates(page, game, hold_post=False)
        try:
            gates.install([_checkpoint_sse()])
            page.goto(studio_server["url"] + f"/?panel=build&slug={game}", wait_until="domcontentloaded")
            diag.wait(
                page,
                gates.trace,
                "() => !document.getElementById('build-guided').disabled",
                "late-inactive-enabled",
            )
            _barrier(
                page,
                gates.trace,
                "late-inactive-stream",
                lambda req: "/guided/stream" in req.url,
                lambda: page.locator("#build-guided").click(),
            )
            assert len(gates.streams) == 1, _urls(gates)
            assert "job-7" in gates.streams[0]["url"]
            assert gates.streams[0]["kind"] == "checkpoint"
            gates.release_actives({"active": False})
            page.evaluate("() => document.getElementById('pane-flight').classList.contains('on')")
            assert len(gates.streams) == 1, _urls(gates)
            assert gates.autopilots == []
            gates.fulfill_stream(0)
            diag.wait(page, gates.trace, VISIBLE, "late-inactive-visible")
            assert "stage needs a look" in page.locator("#build-cp-review").inner_text()
        finally:
            gates.settle()
            browser.close()


def test_late_active_job_after_stream_start_does_not_consume_checkpoint_body(studio_server):
    """Once followBuild has requested the stream, a late active job is ignored."""
    from playwright.sync_api import sync_playwright

    game = studio_server["game"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        gates = _Gates(page, game, hold_post=False)
        try:
            gates.install([_checkpoint_sse()])
            page.goto(studio_server["url"] + f"/?panel=build&slug={game}", wait_until="domcontentloaded")
            diag.wait(
                page,
                gates.trace,
                "() => !document.getElementById('build-guided').disabled",
                "late-active-enabled",
            )
            _barrier(
                page,
                gates.trace,
                "late-active-stream",
                lambda req: "/guided/stream" in req.url and "job-7" in req.url,
                lambda: page.locator("#build-guided").click(),
            )
            assert len(gates.streams) == 1
            gates.release_actives({"active": True, "job_id": "job-early"})
            page.evaluate("() => document.getElementById('pane-flight').classList.contains('on')")
            assert len(gates.streams) == 1, _urls(gates)
            assert "job-early" not in gates.streams[0]["url"]
            assert gates.autopilots == []
            gates.fulfill_stream(0)
            diag.wait(page, gates.trace, VISIBLE, "late-active-visible")
        finally:
            gates.settle()
            browser.close()


def test_active_job_before_guided_post_keeps_the_explicit_checkpoint(studio_server):
    """A guided/active reply must not follow while the explicit guided POST is pending.

    The body list is still the one-shot fixture: the first stream request pops
    the checkpoint, and the next request receives surviving-log. Those two
    bodies are not production job streams.

    Observed on parent c9c4f62 with this same queue, before the ownership
    change: releasing {active:true, job_id:job-early} opened the first reader
    and popped the checkpoint body. Fulfilling the POST as job-7 opened a
    second reader that received only surviving-log. Both checkpoint zones
    stayed hidden. The log contained surviving-log and did not contain
    "stage needs a look". That run is the parent failure. It is not the cause
    of the earlier CI timeout.

    The fixed rule is one reader. job-early is not requested, so the explicit
    job-7 request keeps the checkpoint body. surviving-log stays unconsumed.
    """
    from playwright.sync_api import sync_playwright

    game = studio_server["game"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        gates = _Gates(page, game, hold_post=True)
        try:
            gates.install([_checkpoint_sse(), _log_sse("surviving-log")])
            page.goto(studio_server["url"] + f"/?panel=build&slug={game}", wait_until="domcontentloaded")
            diag.wait(
                page,
                gates.trace,
                "() => !document.getElementById('build-guided').disabled",
                "active-before-post-enabled",
            )
            _barrier(
                page,
                gates.trace,
                "active-before-post-held",
                lambda req: req.method == "POST" and req.url.endswith("/build/guided"),
                lambda: page.locator("#build-guided").click(),
            )
            assert len(gates.posts) == 1
            assert gates.streams == []
            gates.release_actives({"active": True, "job_id": "job-early"})
            page.evaluate("() => 1")
            assert gates.streams == [], _urls(gates)
            assert gates.autopilots == []
            _barrier(
                page,
                gates.trace,
                "active-before-post-explicit-stream",
                lambda req: "/guided/stream" in req.url and "job-7" in req.url,
                lambda: _fulfill(gates.posts[0], 200, {"ok": True, "job_id": "job-7"}),
            )
            gates.posts.clear()
            assert len(gates.streams) == 1, _urls(gates)
            assert "job-early" not in gates.streams[0]["url"]
            assert gates.streams[0]["kind"] == "checkpoint"
            assert len(gates.bodies) == 1
            assert "surviving-log" in gates.bodies[0]
            gates.fulfill_stream(0)
            diag.wait(page, gates.trace, VISIBLE, "active-before-post-visible")
            assert "stage needs a look" in page.locator("#build-cp-review").inner_text()
            assert "stage needs a look" in page.locator("#flight-cp-review").inner_text()
            assert "stage needs a look" in page.locator("#log").inner_text()
            assert "compile-slice-ready" in page.locator("#log").inner_text()
            assert "surviving-log" not in page.locator("#log").inner_text()
            assert "on" in (page.locator("#pane-flight").get_attribute("class") or "").split()
        finally:
            gates.settle()
            browser.close()
