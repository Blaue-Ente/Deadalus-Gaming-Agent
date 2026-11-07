"""Phase 2 Game Studio shell: real panels, same slug, honest failures."""
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
HTML = (ROOT / "app" / "static" / "studio.html").read_text(encoding="utf-8")
WORK = (ROOT / "app" / "static" / "studio-workspace.js").read_text(encoding="utf-8")
KERNEL = (ROOT / "app" / "static" / "studio.js").read_text(encoding="utf-8")
I18N = (ROOT / "app" / "static" / "i18n.js").read_text(encoding="utf-8")
VIEWPORTS = ((1280, 800), (1366, 768), (910, 640))
SHOTS = {
    (1280, 800): "/opt/cursor/artifacts/phase2-build-1280.png",
    (1366, 768): "/opt/cursor/artifacts/phase2-build-1366.png",
    (910, 640): "/opt/cursor/artifacts/phase2-build-910.png",
}
NEW_KEYS = (
    "studio.headerName",
    "studio.filter.games",
    "studio.filter.apps",
    "studio.harnessCheck",
    "studio.settingsSecretHint",
    "studio.classicRemain",
    "studio.missingSlug",
    "studio.noMatch",
    "studio.saved",
    "studio.indexFallback",
    "studio.checkpoint",
)


def test_phase2_strings_and_shell_contract():
    header = HTML.split("<header", 1)[1].split("</header>", 1)[0]
    assert "<h1" in header and "studio.headerName" in header
    assert 'id="panel-build"' in HTML
    assert 'id="panel-preview"' in HTML
    assert 'id="panel-review"' in HTML
    assert 'id="panel-settings"' in HTML
    assert 'data-filter="apps"' in HTML
    assert "window.dxStudio" in KERNEL
    assert "setGameSlugs" in KERNEL
    assert "failGameIndex" in KERNEL
    assert "fastapi" in KERNEL and "streamlit" in KERNEL
    assert ".mask" not in WORK
    assert "api_key" not in WORK
    assert "harness_check" in WORK
    for key in NEW_KEYS:
        assert I18N.count(f'"{key}":') == 2, key
    assert 'href="/admin/daedalus/connectors"' not in header


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _token(server) -> str:
    return (Path(server["data"]) / "owner.token").read_text(encoding="utf-8").strip()


def _api(server, method: str, path: str, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(server["url"] + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + _token(server))
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as res:
        raw = res.read().decode()
        return json.loads(raw) if raw else {}


@pytest.fixture(scope="module")
def studio_server(tmp_path_factory):
    pytest.importorskip("playwright")
    data = tmp_path_factory.mktemp("phase2-data")
    port = _free_port()
    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    env.update({
        "DAEDALUS_STANDALONE": "1",
        "DAEDALUS_DATA": str(data),
        "DAEDALUS_PLAYGROUND": str(data / "playground"),
    })
    env.pop("DAEDALUS_MENTOR", None)
    env.pop("DAEDALUS_RUN_BUDGET", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    server = {"url": f"http://127.0.0.1:{port}", "data": data, "proc": proc, "env": env, "port": port}
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
        pytest.fail("studio server did not start\n" + out[-2000:])
    game = _api(server, "POST", "/api/v1/playground/projects", {"name": "Old Game", "kind": "static"})
    app = _api(server, "POST", "/api/v1/playground/projects", {"name": "Old App", "kind": "fastapi"})
    server["game"] = game["project"]["slug"]
    server["app"] = app["project"]["slug"]
    _api(server, "POST", f"/admin/daedalus/playground/api/projects/{server['game']}/file", {
        "path": "notes.txt",
        "content": "keep-this-sentence",
    })
    yield server
    proc = server["proc"]
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _hit(page, selector: str) -> None:
    loc = page.locator(selector).first
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


def _restart(server):
    server["proc"].terminate()
    try:
        server["proc"].wait(timeout=10)
    except subprocess.TimeoutExpired:
        server["proc"].kill()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(server["port"])],
        cwd=str(ROOT),
        env=server["env"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    server["proc"] = proc
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(server["url"] + "/health", timeout=1) as res:
                if res.status == 200:
                    return
        except Exception:
            time.sleep(0.2)
    pytest.fail("restart failed")


def _fulfill(route, status, payload):
    route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))


def test_old_projects_filter_files_and_deep_link(studio_server):
    from playwright.sync_api import sync_playwright
    game = studio_server["game"]
    app = studio_server["app"]
    _restart(studio_server)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(studio_server["url"] + "/", wait_until="domcontentloaded")
        page.wait_for_selector(f"#plist [data-slug='{game}']")
        page.wait_for_selector(f"#plist [data-slug='{app}']")
        page.wait_for_function("() => !document.querySelector('#kind-filter [data-filter=games]').disabled")
        page.locator("#kind-filter [data-filter=apps]").click()
        page.wait_for_function(
            """(slugs) => {
              const app = document.querySelector("#plist [data-slug='" + slugs.app + "']");
              const game = document.querySelector("#plist [data-slug='" + slugs.game + "']");
              return !!app && !game;
            }""",
            arg={"app": app, "game": game},
        )
        page.locator("#kind-filter [data-filter=games]").click()
        page.wait_for_selector(f"#plist [data-slug='{game}']")
        assert page.locator(f"#plist [data-slug='{app}']").count() == 0
        page.locator("#kind-filter [data-filter=all]").click()
        page.locator(f"#plist [data-slug='{game}']").click()
        page.locator("#filelist button", has_text="notes.txt").click()
        page.wait_for_function("() => document.getElementById('fileview').textContent.includes('keep-this-sentence')")
        page.goto(studio_server["url"] + f"/?panel=review&tab=skills&slug={game}", wait_until="domcontentloaded")
        page.wait_for_selector("#panel-review:not([hidden])")
        assert page.locator("#review-tabs [data-tab=skills]").get_attribute("aria-selected") == "true"
        page.wait_for_selector(f"#plist .item.active[data-slug='{game}']")
        page.goto(studio_server["url"] + "/?panel=nope&tab=nope", wait_until="domcontentloaded")
        page.wait_for_selector("#panel-chat:not([hidden])")
        page.goto(studio_server["url"] + "/?panel=review&tab=nope&slug=missing-project", wait_until="domcontentloaded")
        page.wait_for_selector("#panel-review:not([hidden])")
        assert page.locator("#review-tabs [data-tab=proposals]").get_attribute("aria-selected") == "true"
        page.wait_for_function("() => document.getElementById('workspace-status').textContent.includes('missing-project')")
        listed = _api(studio_server, "GET", "/api/v1/playground/projects")
        assert "missing-project" not in [p["slug"] for p in listed["projects"]]
        with urllib.request.urlopen(studio_server["url"] + "/admin/daedalus/connectors") as res:
            assert res.status == 200
        browser.close()


def test_build_stays_on_slug_and_gdd_failure_is_honest(studio_server):
    from playwright.sync_api import sync_playwright
    game = studio_server["game"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})

        def gdd(route):
            if route.request.method == "POST":
                _fulfill(route, 500, {"detail": "gdd down"})
            else:
                route.fallback()

        page.route("**/admin/daedalus/game-studio/api/gdd", gdd)
        page.goto(studio_server["url"] + f"/?panel=build&slug={game}", wait_until="domcontentloaded")
        page.wait_for_function("() => document.querySelectorAll('#build-genre option').length > 1")
        page.locator("#build-idea").fill("a small puzzle")
        page.locator("#build-gdd").click()
        page.wait_for_function("() => document.getElementById('build-status').textContent.includes('gdd down')")
        assert page.locator("#build-gdd").is_enabled()
        page.unroute("**/admin/daedalus/game-studio/api/gdd")
        page.locator("#build-start").click()
        page.locator("#filelist button", has_text="design/gdd-lite.md").wait_for(timeout=30000)
        page.locator("#filelist button", has_text="design/game-studio.json").wait_for()
        assert page.locator(f"#plist [data-slug='{game}']").count() == 1
        assert page.locator("#projchip").inner_text() == "Old Game"
        browser.close()


def test_preview_git_and_publish_failures(studio_server):
    from playwright.sync_api import sync_playwright
    game = studio_server["game"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.route("**/admin/daedalus/playground/api/projects/" + game + "/run", lambda route: _fulfill(route, 500, {"detail": "preview down"}))
        page.route("**/api/v1/playground/projects/" + game + "/publish-local", lambda route: _fulfill(route, 200, {"ok": True, "harness_check": True}))
        page.route("**/admin/daedalus/playground/api/clone", lambda route: _fulfill(route, 500, {"detail": "clone down"}))
        page.goto(studio_server["url"] + f"/?panel=preview&slug={game}", wait_until="domcontentloaded")
        page.locator(f"#plist [data-slug='{game}']").wait_for()
        page.locator("#preview-run").click()
        page.wait_for_function("() => document.getElementById('preview-status').textContent.includes('preview down')")
        assert page.locator("#preview-run").is_enabled()
        assert not page.locator("#preview-frame").get_attribute("src")
        page.locator("#preview-local").click()
        page.wait_for_function("() => document.getElementById('preview-status').textContent === 'Harness check'")
        page.locator("#git-menu summary").click()
        page.locator("#git-url").fill("https://example.com/old-game.git")
        before = page.locator("#plist .item").count()
        page.locator("#git-clone").click()
        page.wait_for_function("() => document.getElementById('git-status').textContent.includes('clone down')")
        assert page.locator("#plist .item").count() == before
        browser.close()


def test_review_actions_and_write_only_secret(studio_server):
    from playwright.sync_api import sync_playwright
    secret = "phase2-secret-value"
    planted = "AB••••WXYZ"
    seen = {"body": "", "approve": 0}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})

        def proposals(route):
            if route.request.method == "GET":
                _fulfill(route, 200, {"proposals": [{"id": "prop1", "status": "pending", "reason": "Keep the notes"}]})
            else:
                route.fallback()

        def approve(route):
            seen["approve"] += 1
            if seen["approve"] == 1:
                _fulfill(route, 500, {"detail": "approve down"})
            else:
                _fulfill(route, 200, {"ok": True})

        def overview(route):
            _fulfill(route, 200, {
                "ok": True,
                "groups": [{
                    "id": "brain",
                    "fields": [{
                        "key": "DAEDALUS_PROVIDER_KEY",
                        "label": "Provider key",
                        "secret": True,
                        "set": False,
                        "mask": planted,
                    }],
                }],
                "prefs": {"preferred_git_provider": "auto", "default_github_branch": "main"},
            })

        def secrets(route):
            seen["body"] = route.request.post_data or ""
            _fulfill(route, 200, {"ok": True, "set": ["DAEDALUS_PROVIDER_KEY"]})

        page.route("**/admin/daedalus/proposals/api", proposals)
        page.route("**/admin/daedalus/proposals/prop1/approve", approve)
        page.route("**/admin/daedalus/connectors/overview", overview)
        page.route("**/admin/daedalus/connectors/secrets", secrets)
        page.route("**/api/v1/llm/status", lambda route: _fulfill(route, 500, {"detail": "brain down"}))
        page.goto(studio_server["url"] + "/?panel=review&tab=proposals", wait_until="domcontentloaded")
        page.get_by_text("Keep the notes").wait_for()
        page.locator("#review-list button[data-act=approve]").click()
        page.wait_for_function("() => document.getElementById('review-status').textContent.includes('approve down')")
        assert page.get_by_text("Keep the notes").count() == 1
        page.locator("#review-list button[data-act=approve]").click()
        page.wait_for_function("() => document.getElementById('review-status').textContent.includes('Saved') || document.getElementById('review-status').hidden")
        page.locator("#review-tabs [data-tab=permissions]").click()
        page.locator("#review-list button[data-act=grant]").first.click()
        page.wait_for_selector("#review-list button[data-act=revoke]")
        page.locator("#workspace-nav [data-panel=settings]").click()
        page.wait_for_function("() => document.getElementById('settings-status').textContent.includes('brain down')")
        assert page.locator("#settings-brain").inner_text().strip() == ""
        assert planted not in page.locator("#panel-settings").inner_text()
        page.locator("#secret-value").fill(secret)
        page.locator("#settings-secret button[type=submit]").click()
        page.wait_for_function("() => document.getElementById('secret-value').value === ''")
        assert secret not in page.locator("#panel-settings").inner_text()
        assert planted not in page.locator("#panel-settings").inner_text()
        assert secret in seen["body"]
        browser.close()


def test_language_keeps_user_text_and_screenshots(studio_server):
    from playwright.sync_api import sync_playwright
    game = studio_server["game"]
    Path("/opt/cursor/artifacts").mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(studio_server["url"] + f"/?panel=build&slug={game}", wait_until="domcontentloaded")
        page.wait_for_selector(f"#plist [data-slug='{game}']")
        page.locator(".dx-lang button[data-lang=bg]").click()
        page.wait_for_function("() => document.querySelector('#kind-filter [data-filter=games]').textContent === 'Игри'")
        assert "Old Game" in page.locator(f"#plist [data-slug='{game}']").inner_text()
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("() => localStorage.getItem('dx.lang') === 'bg'")
        assert page.evaluate("() => localStorage.getItem('dx.slug')") == game
        page.locator(".dx-lang button[data-lang=en]").click()
        for width, height in VIEWPORTS:
            page.set_viewport_size({"width": width, "height": height})
            page.locator("#workspace-nav [data-panel=build]").click()
            assert page.evaluate("() => window.scrollY") == 0
            box = page.locator("#settings-link").bounding_box()
            assert box["y"] + box["height"] <= height
            page.screenshot(path=SHOTS[(width, height)])
        page.set_viewport_size({"width": 910, "height": 640})
        _hit(page, "#workspace-nav [data-panel=tools]")
        page.wait_for_selector(".shell.tools-open .rail")
        _hit(page, ".tabs button[data-pane=shell]")
        assert page.locator("#pane-shell").evaluate("el => el.classList.contains('on')")
        browser.close()


def test_game_index_lists_marker_and_workspace_progress(studio_server):
    """Real backend: game-studio.json is the index, and the workspace reports progress."""
    made = _api(studio_server, "POST", "/api/v1/playground/projects", {
        "name": "Expo Marker",
        "kind": "expo",
    })
    slug = made["project"]["slug"]
    _api(studio_server, "POST", f"/admin/daedalus/playground/api/projects/{slug}/file", {
        "path": "design/game-studio.json",
        "content": json.dumps({"build_mode": "guided", "studio_preset": "custom"}),
    })
    listed = _api(studio_server, "GET", "/admin/daedalus/game-studio/api/projects")
    assert slug in [row["slug"] for row in listed["projects"]]
    ctx = _api(studio_server, "GET", f"/admin/daedalus/game-studio/api/projects/{slug}")
    summary = ctx["milestone_summary"]
    assert isinstance(summary["milestones"], list) and summary["milestones"]
    assert "done" in summary["milestones"][0] and "total" in summary["milestones"][0]
    assert isinstance(summary["stages_done"], int) and isinstance(summary["stages_total"], int)
    active = _api(
        studio_server, "GET",
        f"/admin/daedalus/playground/api/projects/{slug}/guided/active",
    )
    assert active["active"] is False
    studio_server["expo"] = slug


def test_game_index_failure_uses_kind_fallback(studio_server):
    from playwright.sync_api import sync_playwright
    game = studio_server["game"]
    app = studio_server["app"]
    expo = studio_server["expo"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        def index_down(route):
            path = route.request.url.split("?", 1)[0].rstrip("/")
            if route.request.method != "GET" or not path.endswith("/game-studio/api/projects"):
                route.fallback()
                return
            _fulfill(route, 500, {"detail": "index down"})

        page.route("**/game-studio/api/projects**", index_down)
        page.goto(studio_server["url"] + "/", wait_until="domcontentloaded")
        page.wait_for_function(
            """() => document.getElementById('workspace-status').textContent.includes('Game index failed')
              && document.getElementById('workspace-status').textContent.includes('index down')
              && window.dxStudio.gameIndexState() === 'failed'
              && !document.querySelector('#kind-filter [data-filter=apps]').disabled"""
        )
        page.locator("#kind-filter [data-filter=apps]").click()
        page.wait_for_selector(f"#plist [data-slug='{app}']")
        page.wait_for_selector(f"#plist [data-slug='{expo}']")
        assert page.locator(f"#plist [data-slug='{game}']").count() == 0
        page.locator("#kind-filter [data-filter=games]").click()
        page.wait_for_selector(f"#plist [data-slug='{game}']")
        assert page.locator(f"#plist [data-slug='{expo}']").count() == 0
        assert page.locator(f"#plist [data-slug='{app}']").count() == 0
        browser.close()


def test_build_progress_and_guided_stream_are_visible(studio_server):
    from playwright.sync_api import sync_playwright
    game = studio_server["game"]
    sse = (
        "event: log\n"
        'data: {"line":"compile-slice-ready"}\n\n'
        "event: done\n"
        "data: {}\n\n"
    )

    def workspace(route):
        path = route.request.url.split("?", 1)[0].rstrip("/")
        if route.request.method != "GET" or not path.endswith("/game-studio/api/projects/" + game):
            route.fallback()
            return
        _fulfill(route, 200, {
            "ok": True,
            "can_guided": True,
            "can_autopilot": False,
            "can_advance": False,
            "current_milestone": "M1",
            "milestone_summary": {
                "stages_done": 1,
                "stages_total": 4,
                "milestones": [{
                    "id": "M1",
                    "title": "Boot",
                    "done": 1,
                    "total": 4,
                    "current": True,
                }],
            },
        })

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.route(f"**/game-studio/api/projects/{game}/build/guided", lambda route: _fulfill(
            route, 200, {"ok": True, "job_id": "job-1"},
        ))
        page.route(f"**/playground/api/projects/{game}/guided/stream**", lambda route: route.fulfill(
            status=200,
            headers={"Content-Type": "text/event-stream"},
            body=sse,
        ))
        page.route(f"**/playground/api/projects/{game}/guided/active", lambda route: _fulfill(
            route, 200, {"active": False},
        ))
        page.route(f"**/game-studio/api/projects/{game}", workspace)
        page.goto(studio_server["url"] + f"/?panel=build&slug={game}", wait_until="domcontentloaded")
        page.wait_for_selector("#build-milestones li[data-current]")
        assert "1/4" in page.locator("#build-milestones").inner_text()
        assert "Boot" in page.locator("#build-milestones").inner_text()
        page.locator("#build-guided").click()
        page.wait_for_function(
            """() => document.getElementById('build-out').textContent.includes('compile-slice-ready')
              && document.getElementById('log').textContent.includes('compile-slice-ready')"""
        )
        page.screenshot(path="/opt/cursor/artifacts/phase2-build-progress.png")
        browser.close()
