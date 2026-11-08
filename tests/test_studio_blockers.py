"""Blockers list and confirmed Resolve at the bottom of Review.

The list GET and the resolve POST are intercepted in the browser. A separate
test calls the real resolve route against a temporary data directory. Resolve
marks the row resolved. It does not grant a permission.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "app" / "static" / "studio.html").read_text(encoding="utf-8")
WORK = (ROOT / "app" / "static" / "studio-workspace.js").read_text(encoding="utf-8")
I18N = (ROOT / "app" / "static" / "i18n.js").read_text(encoding="utf-8")
ADMIN = (ROOT / "app" / "admin_compat.py").read_text(encoding="utf-8")
BLOCKERS = (ROOT / "daedalus" / "blockers.py").read_text(encoding="utf-8")

KEYS = (
    "studio.blockerTitle",
    "studio.blockerNote",
    "studio.blockerRefresh",
    "studio.blockerResolve",
    "studio.blockerNone",
    "studio.blockerHint",
    "studio.blockerConfirm",
    "studio.blockerResolved",
    "studio.blockerNotOk",
    "studio.blockerUnknown",
)

REASON = "compile failed <img src=x onerror=alert(1)>"
ITEM = {
    "id": "blk_demo",
    "status": "open",
    "reason": REASON,
    "tried": "once",
    "next_steps": "look at the log",
    "file_path": "src/main.js",
    "analysis": {
        "category": "permission",
        "suggested_permissions": ["modify_files", "web_search"],
        "auto_fixable": True,
    },
}
SUMMARY = {
    "grant_all_recommended": [
        "modify_files",
        "create_files",
        "auto_approve",
        "deep_worker",
        "web_search",
        "mesh_call",
        "planner_auto_run",
    ],
}


def _slice(src: str, start: str, end: str) -> str:
    at = src.index(start)
    return src[at:src.index(end, at + len(start))]


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
    body = "" if payload is None else json.dumps(payload)
    route.fulfill(status=status, content_type="application/json", body=body)


@pytest.fixture(scope="module")
def studio_server(tmp_path_factory):
    pytest.importorskip("playwright")
    data = tmp_path_factory.mktemp("blockers-data")
    port = _free_port()
    env = dict(os.environ)
    env.update({
        "DAEDALUS_STANDALONE": "1",
        "DAEDALUS_DATA": str(data),
        "DAEDALUS_PLAYGROUND": str(data / "playground"),
        "DAEDALUS_OFFLINE": "1",
    })
    for key in (
        "DAEDALUS_MENTOR",
        "DAEDALUS_RUN_BUDGET",
        "FAL_KEY",
        "FAL_API_KEY",
        "TRIPO_API_KEY",
        "MESHY_API_KEY",
        "SUNO_API_KEY",
        "OPENAI_API_KEY",
        "ALLTOKEN_API_KEY",
        "OPENROUTER_API_KEY",
        "GITHUB_TOKEN",
        "FIREBASE_TOKEN",
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
        pytest.fail("studio server did not start\n" + out[-2000:])
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


def test_blocker_contract_is_local_and_does_not_grant():
    for key in KEYS:
        assert I18N.count(f'"{key}":') == 2, key
    review = HTML[HTML.find('id="panel-review"'):HTML.find('id="panel-settings"')]
    assert 'id="blocker-controls"' in review
    assert 'id="blocker-list"' in review
    assert 'id="blocker-refresh"' in review
    assert 'id="blocker-note"' in review
    for banned in ("auto-fix", "grant-recommended", "remediate", "panel-blockers"):
        assert banned not in review
    block = _slice(WORK, "function blockerStill", "async function loadReview")
    assert block.count("dx.api(") == 2
    assert "/admin/daedalus/blockers" in block
    assert "encodeURIComponent(id)" in block
    assert "JSON.stringify(body)" in block
    assert "note ? { note } : {}" in block
    assert "data.ok !== true" in block
    assert "studio.blockerUnknown" in block
    assert "innerHTML" not in block
    for banned in ("setInterval", "setTimeout", "auto-fix", "grant-recommended", "remediate", "gist"):
        assert banned not in block
    route = _slice(ADMIN, "async def blocker_resolve", "async def blocker_auto_fix")
    assert "resolve_blocker" in route
    for banned in ("grant_permission", "auto_fix", "remediate", "playground"):
        assert banned not in route
    fn = _slice(BLOCKERS, "def resolve_blocker", "def count_open_blockers")
    assert 'b["status"] = "resolved"' in fn
    for banned in ("grant_permission", "auto_fix", "remediate"):
        assert banned not in fn


def test_resolve_on_temp_data_marks_resolved_and_does_not_grant(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.setenv("DAEDALUS_OFFLINE", "1")
    monkeypatch.delenv("DAEDALUS_USE_ADN_DB", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    from daedalus.paths import data_root, playground_projects_dir
    from daedalus.store import reset_sqlite_pool

    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    reset_sqlite_pool()
    try:
        from fastapi.testclient import TestClient

        from app.main import app
        from daedalus.blockers import list_blockers, push_blocker
        from daedalus.outbox import list_permissions

        assert list_permissions() == []
        bid = push_blocker("compile failed", tried="once", next_steps="needs web_search")
        with TestClient(app) as client:
            listed = client.get("/admin/daedalus/blockers")
            assert listed.status_code == 200
            body = listed.json()
            row = next(item for item in body["blockers"] if item["id"] == bid)
            assert "web_search" in row["analysis"]["suggested_permissions"]
            assert "modify_files" in body["summary"]["grant_all_recommended"]
            empty = client.post(f"/admin/daedalus/blockers/{bid}/resolve", json={})
            assert empty.status_code == 200
            assert empty.json() == {"ok": True, "blocker_id": bid}
            assert "granted" not in empty.json()
            saved = next(item for item in list_blockers() if item["id"] == bid)
            assert saved["status"] == "resolved"
            assert saved["resolution_note"] == ""
            noted_id = push_blocker("second check")
            noted = client.post(
                f"/admin/daedalus/blockers/{noted_id}/resolve",
                json={"note": "checked by hand"},
            )
            assert noted.status_code == 200
            assert noted.json()["ok"] is True
            saved_note = next(item for item in list_blockers() if item["id"] == noted_id)
            assert saved_note["status"] == "resolved"
            assert saved_note["resolution_note"] == "checked by hand"
            missing = client.post("/admin/daedalus/blockers/blk_missing/resolve", json={})
            assert missing.status_code == 200
            assert missing.json() == {"ok": False, "error": "blocker not found"}
        assert list_permissions() == []
        assert list(playground_projects_dir().iterdir()) == []
    finally:
        reset_sqlite_pool()
        data_root.cache_clear()
        playground_projects_dir.cache_clear()


def _guard(page):
    leaked = []

    def guard(route):
        req = route.request
        url = req.url
        if not url.startswith("http://127.0.0.1"):
            leaked.append(url)
            route.abort()
            return
        path = url.split("?", 1)[0]
        if req.method not in ("GET", "HEAD") and not path.endswith("/api/v1/daedalus/effort"):
            leaked.append(req.method + " " + url)
            route.abort()
            return
        route.fallback()

    page.route("**/*", guard)
    return leaked


def _arm(page, state, held, posts):
    def handle(route):
        req = route.request
        path = req.url.split("?", 1)[0]
        if req.method == "GET" and path.endswith("/admin/daedalus/blockers"):
            if state["hold_get"]:
                held["get"].append(route)
                return
            _fulfill(route, state["get_status"], state["list"])
            return
        if req.method == "POST" and path.endswith("/resolve"):
            raw = req.post_data
            posts.append(json.loads(raw) if raw else None)
            if state["hold_post"]:
                held["post"].append(route)
                return
            _fulfill(route, state["post_status"], state["post"])
            return
        route.fallback()

    page.route("**/admin/daedalus/blockers**", handle)


def _open(page, server):
    page.goto(server["url"] + "/?panel=review", wait_until="domcontentloaded")
    page.wait_for_function(
        """(reason) => document.getElementById('blocker-list').textContent.includes(reason)""",
        arg=REASON,
    )


def _hit(page, selector):
    loc = page.locator(selector)
    loc.scroll_into_view_if_needed()
    box = loc.bounding_box()
    assert box is not None and box["width"] > 8 and box["height"] > 8
    point = loc.evaluate(
        """(el) => {
          const r = el.getBoundingClientRect();
          const x = r.left + r.width / 2;
          const y = r.top + r.height / 2;
          const top = document.elementFromPoint(x, y);
          return {ok: top === el || (top && el.contains(top)), tag: top && top.tagName};
        }"""
    )
    assert point["ok"], selector + " covered by " + str(point)
    viewport = page.viewport_size
    assert box["y"] + box["height"] <= viewport["height"]
    assert box["x"] + box["width"] <= viewport["width"]
    loc.click()


def _wait_len(page, items, count):
    for _ in range(50):
        if len(items) >= count:
            return
        page.wait_for_timeout(20)
    raise AssertionError(len(items))


def _status_has(page, text):
    page.wait_for_function(
        """(text) => (document.getElementById('blocker-status').textContent || '').includes(text)""",
        arg=text,
    )


def _settled(page):
    page.wait_for_function(
        """() => {
          const refresh = document.getElementById('blocker-refresh');
          const button = document.querySelector('#blocker-list button');
          return refresh && !refresh.disabled && button && !button.disabled;
        }"""
    )


def test_review_lists_blockers_and_resolve_posts_the_note(studio_server):
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    state = {
        "list": {"blockers": [ITEM], "summary": SUMMARY},
        "get_status": 200,
        "post": {"ok": True, "blocker_id": "blk_demo"},
        "post_status": 200,
        "hold_get": False,
        "hold_post": False,
    }
    held = {"get": [], "post": []}
    posts = []
    dialogs = []
    accept = {"on": False}

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        leaked = _guard(page)
        _arm(page, state, held, posts)
        page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.accept() if accept["on"] else dialog.dismiss()))
        _open(page, studio_server)
        panel = page.locator("#blocker-controls")
        assert panel.locator("img").count() == 0
        text = panel.inner_text()
        assert REASON in text
        assert "modify_files" not in text
        assert "web_search" not in text
        assert "auto_approve" not in text
        assert "does not grant a permission" in text
        _hit(page, "#blocker-list button[data-gen]")
        assert posts == []
        assert "blk_demo" in dialogs[-1]
        assert "only marks it resolved" in dialogs[-1]
        assert "does not grant a permission" in dialogs[-1]
        assert "change a project kind" in dialogs[-1]
        assert "Cancel sends nothing" in dialogs[-1]
        accept["on"] = True
        page.locator("#blocker-note").fill("   ")
        _hit(page, "#blocker-list button[data-gen]")
        _status_has(page, "Blocker blk_demo is resolved.")
        assert posts == [{}]
        _settled(page)
        page.locator("#blocker-note").fill("  checked by hand  ")
        _hit(page, "#blocker-list button[data-gen]")
        _status_has(page, "Blocker blk_demo is resolved.")
        assert posts[-1] == {"note": "checked by hand"}
        _settled(page)
        page.locator("#blocker-note").fill("")
        state["post"] = None
        _hit(page, "#blocker-list button[data-gen]")
        _status_has(page, "The response did not succeed.")
        assert "is resolved" not in page.locator("#blocker-status").inner_text()
        assert posts[-1] == {}
        _settled(page)
        state["post"] = {"ok": False, "error": "blocker not found"}
        _hit(page, "#blocker-list button[data-gen]")
        _status_has(page, "blocker not found")
        assert "is resolved" not in page.locator("#blocker-status").inner_text()
        assert "The response did not succeed." in page.locator("#blocker-status").inner_text()
        _settled(page)
        state["post"] = {"ok": True, "blocker_id": "blk_demo"}
        state["hold_post"] = True
        before = len(posts)
        _hit(page, "#blocker-list button[data-gen]")
        assert len(posts) == before + 1
        with pytest.raises(PlaywrightTimeout):
            page.locator("#blocker-list button[data-gen]").click(timeout=1000)
        assert len(posts) == before + 1
        state["hold_post"] = False
        _fulfill(held["post"].pop(), 200, state["post"])
        _status_has(page, "Blocker blk_demo is resolved.")
        _settled(page)
        state["list"] = {"blockers": [], "summary": SUMMARY}
        _hit(page, "#blocker-refresh")
        page.wait_for_function(
            """() => document.getElementById('blocker-list').textContent.includes('No blockers.')
              && document.getElementById('blocker-status').hidden
              && document.getElementById('blocker-status').dataset.state !== 'error'"""
        )
        assert "modify_files" not in page.locator("#blocker-controls").inner_text()
        state["list"] = {}
        _hit(page, "#blocker-refresh")
        _status_has(page, "The response did not succeed.")
        assert "No blockers." not in page.locator("#blocker-list").inner_text()
        state["list"] = {"error": "blocker-down"}
        state["get_status"] = 500
        _hit(page, "#blocker-refresh")
        _status_has(page, "blocker-down")
        assert "No blockers." not in page.locator("#blocker-list").inner_text()
        state["get_status"] = 200
        state["list"] = {"blockers": [ITEM], "summary": SUMMARY}
        _hit(page, "#blocker-refresh")
        _settled(page)
        page.locator(f"#plist [data-slug='{studio_server['other']}']").click()
        page.wait_for_function(
            """(slug) => {
              const row = document.querySelector('#plist [data-slug="' + slug + '"]');
              return row && row.classList.contains('active');
            }""",
            arg=studio_server["other"],
        )
        assert REASON in page.locator("#blocker-list").evaluate("el => el.textContent")
        assert posts[-1] == {}
        assert len(posts) == before + 1
        page.locator(".dx-lang button[data-lang=bg]").click()
        page.wait_for_function("() => document.getElementById('blocker-refresh').textContent === 'Обнови'")
        _hit(page, "#blocker-refresh")
        page.wait_for_function(
            """() => document.querySelector('#blocker-list button').textContent === 'Разреши'"""
        )
        assert REASON in page.locator("#blocker-list").inner_text()
        assert page.locator("#blocker-controls img").count() == 0
        accept["on"] = False
        posted = len(posts)
        _hit(page, "#blocker-list button[data-gen]")
        assert len(posts) == posted
        assert "blk_demo" in dialogs[-1]
        assert "Не дава разрешение" in dialogs[-1]
        page.locator(".dx-lang button[data-lang=en]").click()
        page.wait_for_function("() => document.getElementById('blocker-refresh').textContent === 'Refresh'")
        assert leaked == []
        browser.close()


def test_leaving_review_drops_late_replies_and_a_stale_button(studio_server):
    from playwright.sync_api import sync_playwright

    state = {
        "list": {"blockers": [ITEM], "summary": SUMMARY},
        "get_status": 200,
        "post": {"ok": True, "blocker_id": "blk_demo"},
        "post_status": 200,
        "hold_get": True,
        "hold_post": False,
    }
    held = {"get": [], "post": []}
    posts = []
    dialogs = []
    accept = {"on": True}

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        leaked = _guard(page)
        _arm(page, state, held, posts)
        page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.accept() if accept["on"] else dialog.dismiss()))
        page.goto(studio_server["url"] + "/?panel=review", wait_until="domcontentloaded")
        _wait_len(page, held["get"], 1)
        _hit(page, "#workspace-nav button[data-panel=build]")
        state["hold_get"] = False
        state["list"] = {"blockers": [{"id": "blk_late", "status": "open", "reason": "late-payload"}]}
        _fulfill(held["get"].pop(), 200, state["list"])
        page.evaluate("() => new Promise((resolve) => setTimeout(resolve, 50))")
        assert "late-payload" not in page.locator("#blocker-list").evaluate("el => el.textContent")
        state["list"] = {"blockers": [ITEM], "summary": SUMMARY}
        _hit(page, "#workspace-nav button[data-panel=review]")
        _open_ready = page.wait_for_function(
            """(reason) => document.getElementById('blocker-list').textContent.includes(reason)
              && !document.getElementById('blocker-list').textContent.includes('late-payload')""",
            arg=REASON,
        )
        assert _open_ready
        state["hold_post"] = True
        _hit(page, "#blocker-list button[data-gen]")
        _wait_len(page, posts, 1)
        assert posts[0] == {}
        _hit(page, "#workspace-nav button[data-panel=build]")
        state["hold_post"] = False
        _fulfill(held["post"].pop(), 200, {"ok": True, "blocker_id": "blk_demo"})
        page.evaluate("() => new Promise((resolve) => setTimeout(resolve, 50))")
        assert "is resolved" not in page.locator("#blocker-status").evaluate("el => el.textContent")
        _hit(page, "#workspace-nav button[data-panel=review]")
        page.wait_for_function(
            """(reason) => {
              const list = document.getElementById('blocker-list').textContent;
              const status = document.getElementById('blocker-status').textContent;
              return list.includes(reason) && !list.includes('is resolved') && !status.includes('is resolved');
            }""",
            arg=REASON,
        )
        assert len(posts) == 1
        _hit(page, "#workspace-nav button[data-panel=build]")
        page.evaluate("() => { document.getElementById('panel-review').hidden = false; }")
        posted = len(posts)
        dialogs.clear()
        _hit(page, "#blocker-list button[data-gen]")
        assert dialogs == []
        assert len(posts) == posted
        _hit(page, "#workspace-nav button[data-panel=review]")
        page.wait_for_function(
            """(reason) => document.getElementById('blocker-list').textContent.includes(reason)""",
            arg=REASON,
        )
        for width, height in ((1280, 800), (910, 640)):
            page.set_viewport_size({"width": width, "height": height})
            _hit(page, "#settings-link")
            page.wait_for_function("() => new URL(location.href).searchParams.get('panel') === 'settings'")
            assert page.url.startswith(studio_server["url"] + "/")
            assert page.locator("#panel-settings").is_visible()
            assert page.locator("#blocker-note").input_value() == ""
            assert page.evaluate("() => window.scrollY") == 0
        assert leaked == []
        browser.close()
