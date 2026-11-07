"""Checkpoint continue in the shell. Streams and continue calls are intercepted."""
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

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "app" / "static" / "studio.html").read_text(encoding="utf-8")
WORK = (ROOT / "app" / "static" / "studio-workspace.js").read_text(encoding="utf-8")
I18N = (ROOT / "app" / "static" / "i18n.js").read_text(encoding="utf-8")

OPTIONS = [
    {"id": "approve", "label": "Approve and continue", "hint": "keep going"},
    {"id": "skip", "label": "Skip this stage", "hint": "leave it"},
]


def _checkpoint_sse(options=None, review="stage needs a look"):
    payload = {"review": review, "options": OPTIONS if options is None else options}
    return (
        "event: log\n"
        'data: {"line":"compile-slice-ready"}\n\n'
        "event: checkpoint\n"
        "data: " + json.dumps(payload) + "\n\n"
    )


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _token(server) -> str:
    return (Path(server["data"]) / "owner.token").read_text(encoding="utf-8").strip()


def _api(server, method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(server["url"] + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + _token(server))
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
    data = tmp_path_factory.mktemp("checkpoint-data")
    port = _free_port()
    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    env.update({
        "DAEDALUS_STANDALONE": "1",
        "DAEDALUS_DATA": str(data),
        "DAEDALUS_PLAYGROUND": str(data / "playground"),
    })
    env.pop("DAEDALUS_MENTOR", None)
    env.pop("DAEDALUS_RUN_BUDGET", None)
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
        pytest.fail("studio server did not start\n" + out[-2000:])
    game = _api(server, "POST", "/api/v1/playground/projects", {"name": "Old Game", "kind": "static"})
    app = _api(server, "POST", "/api/v1/playground/projects", {"name": "Old App", "kind": "fastapi"})
    server["game"] = game["project"]["slug"]
    server["app"] = app["project"]["slug"]
    yield server
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_checkpoint_labels_exist_in_both_languages():
    assert I18N.count('"studio.checkpointNotes":') == 2
    assert 'id="build-checkpoint"' in HTML
    assert 'id="flight-checkpoint"' in HTML
    assert "guided/continue" in WORK
    assert "milestone_complete" in WORK
    assert "Одобри" not in WORK


def _arm(page, game, bodies, continue_for):
    streams = []
    posts = []
    advances = []

    def workspace(route):
        path = route.request.url.split("?", 1)[0].rstrip("/")
        if route.request.method != "GET" or not path.endswith("/game-studio/api/projects/" + game):
            route.fallback()
            return
        _fulfill(route, 200, {
            "ok": True,
            "can_guided": True,
            "can_autopilot": False,
            "can_advance": True,
            "milestone_summary": {"milestones": []},
        })

    def stream(route):
        streams.append(route.request.url)
        body = bodies.pop(0) if bodies else "event: log\ndata: {\"line\":\"extra\"}\n\n"
        route.fulfill(status=200, headers={"Content-Type": "text/event-stream"}, body=body)

    def cont(route):
        if route.request.method != "POST":
            route.fallback()
            return
        posts.append({
            "url": route.request.url,
            "body": json.loads(route.request.post_data or "{}"),
        })
        continue_for(route, posts)

    def advance(route):
        advances.append(route.request.url)
        _fulfill(route, 200, {"ok": True})

    page.route(f"**/game-studio/api/projects/{game}", workspace)
    page.route(f"**/game-studio/api/projects/{game}/build/guided", lambda route: _fulfill(
        route, 200, {"ok": True, "job_id": "job-7"},
    ))
    page.route(f"**/playground/api/projects/{game}/guided/active", lambda route: _fulfill(
        route, 200, {"active": False},
    ))
    page.route(f"**/playground/api/projects/{game}/guided/stream**", stream)
    page.route(f"**/playground/api/projects/{game}/guided/continue", cont)
    page.route(f"**/game-studio/api/projects/{game}/milestones/advance", advance)
    return streams, posts, advances


def _open(page, server, game):
    trace = diag.attach(page)
    page.goto(server["url"] + f"/?panel=build&slug={game}", wait_until="domcontentloaded")
    diag.wait(
        page,
        trace,
        "() => !document.getElementById('build-guided').disabled",
        "checkpoint-guided-enabled",
    )
    page.locator("#build-guided").click()
    diag.wait(
        page,
        trace,
        """() => !document.getElementById('build-checkpoint').hidden
          && !document.getElementById('flight-checkpoint').hidden
          && document.getElementById('pane-flight').classList.contains('on')""",
        "checkpoint-open",
    )


def test_checkpoint_options_resume_only_after_explicit_ok(studio_server):
    from playwright.sync_api import sync_playwright
    game = studio_server["game"]
    bodies = [
        _checkpoint_sse(),
        'event: log\ndata: {"line":"resumed-after-skip"}\n\n',
    ]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        streams, posts, advances = _arm(page, game, bodies, lambda route, _posts: _fulfill(
            route, 200, {"ok": True, "action": "skip", "stage_index": 2},
        ))
        _open(page, studio_server, game)
        assert "stage needs a look" in page.locator("#build-cp-review").inner_text()
        assert "stage needs a look" in page.locator("#flight-cp-review").inner_text()
        assert "stage needs a look" in page.locator("#log").inner_text()
        assert page.locator("#build-status").inner_text() != "done"
        assert page.locator("#build-cp-options button").count() == 2
        assert page.locator("#flight-cp-options [data-action=skip]").inner_text() == "Skip this stage"
        page.locator(".dx-lang button[data-lang=bg]").click()
        page.wait_for_function(
            """() => document.querySelector('#build-checkpoint [data-i18n="studio.checkpointNotes"]').textContent === 'Бележки'
              && document.querySelector('#flight-checkpoint [data-i18n="studio.checkpointNotes"]').textContent === 'Бележки'"""
        )
        assert page.locator("#build-cp-options [data-action=skip]").inner_text() == "Skip this stage"
        assert "Old Game" in page.locator(f"#plist [data-slug='{game}']").inner_text()
        page.locator(".dx-lang button[data-lang=en]").click()
        page.locator("#build-cp-notes").fill("hold the gate")
        page.wait_for_function("() => document.getElementById('flight-cp-notes').value === 'hold the gate'")
        page.locator("#build-cp-options [data-action=skip]").click()
        page.wait_for_function("() => document.getElementById('log').textContent.includes('resumed-after-skip')")
        assert len(posts) == 1
        assert posts[0]["body"] == {"job_id": "job-7", "action": "skip", "notes": "hold the gate"}
        assert f"/projects/{game}/guided/continue" in posts[0]["url"]
        assert len(streams) == 2
        assert "job-7" in streams[1]
        assert advances == []
        assert page.locator("#build-checkpoint").is_hidden()
        page.locator("#settings-link").click()
        page.wait_for_function("() => new URL(location.href).searchParams.get('panel') === 'settings'")
        assert page.url.startswith(studio_server["url"])
        page.locator("#workspace-nav [data-panel=build]").click()
        page.wait_for_function(
            """(slug) => {
              const q = new URL(location.href).searchParams;
              return q.get('panel') === 'build' && q.get('slug') === slug;
            }""",
            arg=game,
        )
        browser.close()


def test_checkpoint_rejects_empty_false_and_missing_job(studio_server):
    from playwright.sync_api import sync_playwright
    game = studio_server["game"]
    bodies = [_checkpoint_sse()]
    replies = [
        (200, {}),
        (200, {"ok": False, "error": "job not at checkpoint"}),
        (404, {"ok": False, "error": "job not found"}),
    ]

    def cont(route, _posts):
        status, payload = replies.pop(0)
        _fulfill(route, status, payload)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        streams, posts, _advances = _arm(page, game, bodies, cont)
        _open(page, studio_server, game)
        for expected in ("", "job not at checkpoint", "job not found"):
            page.locator("#flight-cp-options [data-action=approve]").click()
            page.wait_for_function(
                """(needle) => {
                  const status = document.getElementById('build-status');
                  const zone = document.getElementById('build-checkpoint');
                  return status.dataset.state === 'error'
                    && !zone.hidden
                    && status.textContent.includes(needle)
                    && !status.textContent.includes('Saved')
                    && !document.getElementById('build-cp-options').querySelector('button').disabled;
                }""",
                arg=expected,
            )
        assert len(posts) == 3
        assert all(row["body"]["action"] == "approve" and row["body"]["job_id"] == "job-7" for row in posts)
        assert len(streams) == 1
        assert "resumed-after-skip" not in page.locator("#log").inner_text()
        browser.close()


def test_checkpoint_without_options_invents_nothing(studio_server):
    from playwright.sync_api import sync_playwright
    game = studio_server["game"]
    bodies = [_checkpoint_sse(options=[], review="no actions yet")]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _arm(page, game, bodies, lambda route, _posts: _fulfill(route, 200, {"ok": True}))
        _open(page, studio_server, game)
        assert page.locator("#build-cp-review").inner_text() == "no actions yet"
        assert page.locator("#flight-cp-review").inner_text() == "no actions yet"
        assert page.locator("#build-cp-options button").count() == 0
        assert page.locator("#flight-cp-options button").count() == 0
        assert page.locator("#build-status").inner_text() != "done"
        browser.close()


def test_checkpoint_slug_switch_drops_the_actions(studio_server):
    from playwright.sync_api import sync_playwright
    game = studio_server["game"]
    other = studio_server["app"]
    bodies = [_checkpoint_sse()]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _streams, posts, _advances = _arm(page, game, bodies, lambda route, _posts: _fulfill(
            route, 200, {"ok": True, "action": "skip"},
        ))
        _open(page, studio_server, game)
        page.locator(f"#plist [data-slug='{other}']").click()
        page.wait_for_function(
            """(slug) => document.getElementById('build-slug').textContent.includes(slug)
              && document.getElementById('build-checkpoint').hidden""",
            arg=other,
        )
        assert posts == []
        assert page.locator("#flight-checkpoint").is_hidden()
        browser.close()


def test_duplicate_click_and_inflight_slug_change_do_not_resume(studio_server):
    from playwright.sync_api import sync_playwright
    game = studio_server["game"]
    other = studio_server["app"]
    bodies = [_checkpoint_sse()]
    held = []

    def cont(route, _posts):
        held.append(route)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        streams, posts, _advances = _arm(page, game, bodies, cont)
        _open(page, studio_server, game)
        page.locator("#build-cp-options [data-action=skip]").click()
        page.wait_for_function(
            """() => document.querySelector('#build-cp-options [data-action=approve]').disabled
              && document.querySelector('#flight-cp-options [data-action=skip]').disabled"""
        )
        assert len(posts) == 1
        assert posts[0]["body"]["action"] == "skip"
        page.locator(f"#plist [data-slug='{other}']").click()
        page.wait_for_function(
            """(slug) => document.getElementById('build-slug').textContent.includes(slug)""",
            arg=other,
        )
        assert len(held) == 1
        held[0].fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"ok": True, "action": "skip", "stage_index": 2}),
        )
        page.wait_for_timeout(400)
        assert len(streams) == 1
        assert "resumed-after-skip" not in page.locator("#log").inner_text()
        assert "resumed-after-skip" not in page.locator("#build-out").inner_text()
        browser.close()


def test_terminal_error_and_milestone_complete_clear_the_checkpoint(studio_server):
    from playwright.sync_api import sync_playwright
    game = studio_server["game"]
    bodies = [
        _checkpoint_sse(),
        'event: error\ndata: {"message":"build broke"}\n\n',
        _checkpoint_sse(),
    ]
    replies = [
        {"ok": True, "action": "skip", "stage_index": 2},
        {"ok": True, "action": "approve", "milestone_complete": True},
    ]

    def cont(route, _posts):
        _fulfill(route, 200, replies.pop(0))

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        streams, posts, advances = _arm(page, game, bodies, cont)
        _open(page, studio_server, game)
        page.locator("#build-cp-options [data-action=skip]").click()
        page.wait_for_function(
            """() => {
              const status = document.getElementById('build-status');
              return status.dataset.state === 'error'
                && status.textContent.includes('build broke')
                && document.getElementById('build-checkpoint').hidden
                && document.getElementById('flight-checkpoint').hidden
                && document.getElementById('build-stop').hidden;
            }"""
        )
        assert len(streams) == 2
        page.locator("#build-guided").click()
        page.wait_for_function("() => !document.getElementById('build-checkpoint').hidden")
        page.locator("#build-cp-options [data-action=approve]").click()
        page.wait_for_function(
            """() => document.getElementById('build-status').textContent === 'done'
              && document.getElementById('build-checkpoint').hidden
              && document.getElementById('build-stop').hidden"""
        )
        page.wait_for_timeout(400)
        assert len(streams) == 3
        assert len(posts) == 2
        assert advances == []
        assert "Started." not in page.locator("#build-status").inner_text()
        browser.close()
