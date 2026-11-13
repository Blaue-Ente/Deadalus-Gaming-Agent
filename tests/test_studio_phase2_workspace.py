"""Phase 2 Game Studio shell: real panels, same slug, honest failures."""
from __future__ import annotations

from dx_confirm_support import answer_confirms
import importlib.util
import json
import shutil
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
    "studio.liveReady",
    "studio.liveLocal",
    "studio.capsuleMeta",
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


def _git_output(args: list[str]) -> str | None:
    """Read git output when the binary exists. CI's slim image has no git."""
    if shutil.which("git") is None:
        return None
    try:
        return subprocess.check_output(
            args, cwd=str(ROOT), text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None


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
    server_log = tempfile.TemporaryFile()  # a file, never an unread pipe: a full pipe blocks the server
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT),
        env=env,
        stdout=server_log,
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
        server_log.seek(0)
        out = server_log.read().decode("utf-8", "replace")
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
    server_log = tempfile.TemporaryFile()  # a file, never an unread pipe: a full pipe blocks the server
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(server["port"])],
        cwd=str(ROOT),
        env=server["env"],
        stdout=server_log,
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
        with urllib.request.urlopen(studio_server["url"] + "/admin/daedalus/connectors") as res:   # A6.5b: 302 -> Settings
            assert res.status == 200 and res.url.endswith("/?panel=settings")
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
        page.goto(studio_server["url"] + f"/?panel=build&subtab=run&slug={game}", wait_until="domcontentloaded")
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
        answer_confirms(page)                      # Local copy now asks first
        page.goto(studio_server["url"] + f"/?panel=preview&slug={game}", wait_until="domcontentloaded")
        page.locator(f"#plist [data-slug='{game}']").wait_for()
        page.locator("#preview-run").click()
        page.wait_for_function("() => document.getElementById('preview-status').textContent.includes('preview down')")
        assert page.locator("#preview-run").is_enabled()
        assert not page.locator("#preview-frame").get_attribute("src")
        page.locator('#preview-tabs button[data-tab="publish"]').click()
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
        page.goto(studio_server["url"] + f"/?panel=build&subtab=run&slug={game}", wait_until="domcontentloaded")
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


def test_game_index_failure_keeps_all_and_reports_the_error(studio_server):
    from playwright.sync_api import sync_playwright
    game = studio_server["game"]
    app = studio_server["app"]
    expo = studio_server.get("expo")
    if not expo:
        made = _api(studio_server, "POST", "/api/v1/playground/projects", {"name": "Expo Marker", "kind": "expo"})
        expo = made["project"]["slug"]
        _api(studio_server, "POST", f"/admin/daedalus/playground/api/projects/{expo}/file", {
            "path": "design/game-studio.json",
            "content": json.dumps({"build_mode": "guided", "studio_preset": "custom"}),
        })
        studio_server["expo"] = expo
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
              && document.querySelector('#kind-filter [data-filter=games]').disabled
              && document.querySelector('#kind-filter [data-filter=apps]').disabled
              && !document.querySelector('#kind-filter [data-filter=all]').disabled"""
        )
        page.wait_for_selector(f"#plist [data-slug='{game}']")
        page.wait_for_selector(f"#plist [data-slug='{app}']")
        page.wait_for_selector(f"#plist [data-slug='{expo}']")
        buckets = page.evaluate(
            """(slugs) => slugs.map((slug) => {
              const row = document.querySelector("#plist [data-slug='" + slug + "']");
              return row && row.dataset.bucket;
            })""",
            [game, app, expo],
        )
        assert buckets == ["pending", "pending", "pending"]
        assert page.locator("#kind-filter [data-filter=all]").get_attribute("aria-pressed") == "true"
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
        trace = diag.attach(page)
        page.goto(studio_server["url"] + f"/?panel=build&subtab=run&slug={game}", wait_until="domcontentloaded")
        page.wait_for_selector("#build-milestones li[data-current]")
        assert "1/4" in page.locator("#build-milestones").inner_text()
        assert "Boot" in page.locator("#build-milestones").inner_text()
        page.locator("#build-guided").click()
        diag.wait(
            page,
            trace,
            """() => document.getElementById('build-out').textContent.includes('compile-slice-ready')
              && document.getElementById('log').textContent.includes('compile-slice-ready')
              && document.getElementById('pane-flight').classList.contains('on')""",
            "build-stream-progress",
        )
        page.screenshot(path="/opt/cursor/artifacts/phase2-build-progress.png")
        browser.close()


def test_build_stream_failure_and_cancel_are_visible(studio_server):
    from playwright.sync_api import sync_playwright
    game = studio_server["game"]
    error_sse = 'event: error\ndata: {"message":"build broke"}\n\n'
    running_sse = 'event: log\ndata: {"line":"still-running"}\n\n'

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
            "milestone_summary": {"milestones": []},
        })

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        calls = {"stream": 0}

        def stream(route):
            calls["stream"] += 1
            body = error_sse if calls["stream"] == 1 else running_sse
            route.fulfill(status=200, headers={"Content-Type": "text/event-stream"}, body=body)

        page.route(f"**/game-studio/api/projects/{game}/build/guided", lambda route: _fulfill(
            route, 200, {"ok": True, "job_id": "job-err"},
        ))
        page.route(f"**/playground/api/projects/{game}/guided/stream**", stream)
        page.route(f"**/playground/api/projects/{game}/guided/active", lambda route: _fulfill(
            route, 200, {"active": False},
        ))
        page.route(f"**/playground/api/projects/{game}/guided/stop", lambda route: _fulfill(
            route, 200, {"ok": True, "cancelled": True},
        ))
        page.route(f"**/game-studio/api/projects/{game}", workspace)
        trace = diag.attach(page)
        page.goto(studio_server["url"] + f"/?panel=build&subtab=run&slug={game}", wait_until="domcontentloaded")
        page.locator("#build-guided").click()
        diag.wait(
            page,
            trace,
            """() => {
              const status = document.getElementById('build-status');
              return status.dataset.state === 'error'
                && status.textContent.includes('build broke')
                && document.getElementById('pane-flight').classList.contains('on')
                && document.getElementById('log').textContent.includes('build broke');
            }""",
            "build-stream-failure",
        )
        assert "Started." not in page.locator("#build-status").inner_text()
        page.locator("#build-guided").click()
        page.wait_for_function("() => document.getElementById('build-out').textContent.includes('still-running')")
        page.locator("#build-stop").click()
        page.wait_for_function(
            """() => document.getElementById('build-status').textContent.includes('Stopped.')
              && document.getElementById('build-out').textContent.includes('still-running')"""
        )
        browser.close()


def test_preview_rejects_false_ok_and_distinguishes_publish_copy(studio_server):
    from playwright.sync_api import sync_playwright
    game = studio_server["game"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.route(
            "**/playground/api/projects/" + game + "/run",
            lambda route: _fulfill(route, 200, {"ok": False, "error": "runner refused"}),
        )
        page.route(
            "**/api/v1/playground/projects/" + game + "/publish-local",
            lambda route: _fulfill(route, 200, {"ok": True, "url": f"/apps/{game}/"}),
        )
        page.route(
            "**/api/v1/playground/projects/" + game + "/live",
            lambda route: _fulfill(route, 200, {
                "ok": True,
                "backend": "local",
                "url": None,
                "local_url": f"/apps/{game}/",
            }),
        )
        page.route(
            "**/api/v1/playground/projects/" + game + "/capsule.json",
            lambda route: _fulfill(route, 200, {"ok": True, "slug": game, "has_index": True}),
        )
        answer_confirms(page)                      # Local copy now asks first
        page.goto(studio_server["url"] + f"/?panel=preview&slug={game}", wait_until="domcontentloaded")
        page.locator(f"#plist [data-slug='{game}']").wait_for()
        page.locator("#preview-run").click()
        page.wait_for_function("() => document.getElementById('preview-status').textContent.includes('runner refused')")
        assert "Started." not in page.locator("#preview-status").inner_text()
        page.unroute("**/playground/api/projects/" + game + "/run")
        page.locator('#preview-tabs button[data-tab="publish"]').click()
        page.route(
            "**/api/v1/playground/projects/" + game + "/live",
            lambda route: _fulfill(route, 200, {
                "ok": True,
                "backend": "netlify",
                "url": "https://example.netlify.app",
            }),
        )
        page.locator("#preview-local").click()
        page.wait_for_function("() => document.getElementById('preview-status').textContent === 'Local copy is ready.'")
        page.locator("#preview-live").click()
        page.wait_for_function("() => document.getElementById('preview-status').textContent === 'Live link is ready.'")
        page.unroute("**/api/v1/playground/projects/" + game + "/live")
        page.route(
            "**/api/v1/playground/projects/" + game + "/live",
            lambda route: _fulfill(route, 200, {
                "ok": True,
                "backend": "local",
                "url": None,
                "local_url": f"/apps/{game}/",
            }),
        )
        page.locator("#preview-live").click()
        page.wait_for_function(
            "() => document.getElementById('preview-status').textContent.includes('No public link')"
        )
        assert "Local copy is ready." not in page.locator("#preview-status").inner_text()
        page.locator("#preview-capsule").click()
        page.wait_for_function(
            "() => document.getElementById('preview-status').textContent.includes('Nothing was published')"
        )
        browser.close()


def test_review_requires_explicit_ok(studio_server):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})

        def proposals(route):
            if route.request.method == "GET":
                _fulfill(route, 200, {"proposals": [{"id": "prop-empty", "status": "pending", "reason": "Empty body"}]})
            else:
                route.fallback()

        page.route("**/admin/daedalus/proposals/api", proposals)
        page.route(
            "**/admin/daedalus/proposals/prop-empty/approve",
            lambda route: _fulfill(route, 200, {}),
        )
        page.goto(studio_server["url"] + "/?panel=review&tab=proposals", wait_until="domcontentloaded")
        page.get_by_text("Empty body").wait_for()
        page.locator("#review-list button[data-act=approve]").click()
        page.wait_for_function(
            """() => {
              const status = document.getElementById('review-status');
              return status.dataset.state === 'error' && !status.textContent.includes('Saved');
            }"""
        )
        browser.close()


def test_proposal_approve_and_outbox_dismiss_roundtrip(studio_server):
    """Real handlers: approve a scratch proposal, then dismiss an outbox note. No send."""
    data = Path(studio_server["data"])
    proposal_id = "prop_phase2_review"
    scratch = ROOT / "tests" / "phase2_review_scratch.py"
    proposal_path = data / "proposals" / f"{proposal_id}.json"
    kv = data / "kv"
    kv.mkdir(parents=True, exist_ok=True)
    item_id = "out-phase2"
    (kv / "adn_outbox_index.json").write_text(json.dumps([item_id]), encoding="utf-8")
    (kv / "adn_outbox_out-phase2.json").write_text(json.dumps({
        "id": item_id,
        "type": "message",
        "title": "Phase2 note",
        "body": "do not send",
        "category": "",
        "payload": None,
        "status": "pending",
        "session_id": "",
        "created_at": 1,
        "updated_at": 1,
        "apply_result": None,
        "dedup_key": "",
    }), encoding="utf-8")
    skills_path = data / "skills.json"
    skills = json.loads(skills_path.read_text(encoding="utf-8")) if skills_path.exists() else {}
    skills["write_tests"] = True
    skills_path.write_text(json.dumps(skills), encoding="utf-8")
    head_before = _git_output(["git", "rev-parse", "HEAD"])
    proposal_path.parent.mkdir(parents=True, exist_ok=True)
    proposal_path.write_text(json.dumps({
        "id": proposal_id,
        "created_at": 1,
        "status": "pending",
        "file_path": "tests/phase2_review_scratch.py",
        "file_existed": False,
        "skill": "write_tests",
        "reason": "phase2 review fixture",
        "proposed_by": "test",
        "risk_level": "low",
        "risk_rationale": "scratch",
        "old_content": "",
        "new_content": "# phase2 review scratch\n",
        "diff": "",
        "stats": {},
    }), encoding="utf-8")
    try:
        approved = _api(studio_server, "POST", f"/admin/daedalus/proposals/{proposal_id}/approve", {})
        assert approved["ok"] is True
        assert scratch.read_text(encoding="utf-8") == "# phase2 review scratch\n"
        listed = _api(studio_server, "GET", "/admin/daedalus/outbox/api")
        assert item_id in [row["id"] for row in listed["items"]]
        dismissed = _api(studio_server, "POST", f"/admin/daedalus/outbox/{item_id}/dismiss", {})
        assert dismissed["ok"] is True
        assert dismissed["item"]["status"] == "dismissed"
        again = _api(studio_server, "GET", "/admin/daedalus/outbox/api")
        match = [row for row in again["items"] if row["id"] == item_id][0]
        assert match["status"] == "dismissed"
        rolled = _api(studio_server, "POST", f"/admin/daedalus/proposals/{proposal_id}/rollback", {})
        assert rolled["ok"] is True
        assert not scratch.exists()
    finally:
        scratch.unlink(missing_ok=True)
        proposal_path.unlink(missing_ok=True)
        head_now = _git_output(["git", "rev-parse", "HEAD"]) if head_before else None
        if head_before and head_now and head_now != head_before:
            subjects = _git_output(["git", "log", "--format=%s", f"{head_before}..HEAD"])
            lines = [line for line in (subjects or "").splitlines() if line]
            if subjects is not None and lines and all(
                line.startswith("daedalus-snapshot:") for line in lines
            ):
                subprocess.check_call(["git", "reset", "--mixed", head_before], cwd=str(ROOT))
