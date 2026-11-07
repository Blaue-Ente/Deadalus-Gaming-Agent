"""Unity, Capacitor, Firebase, and hybrid init/export in the Build panel.

Posts are intercepted. The temporary server has no provider keys. Scratch
data only. A confirm cancel sends nothing. A timeout is not retried.
"""
from __future__ import annotations

import json
import os
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
I18N = (ROOT / "app" / "static" / "i18n.js").read_text(encoding="utf-8")

KEYS = (
    "studio.shipUnityInit",
    "studio.shipUnityBuild",
    "studio.shipFirebase",
    "studio.shipHybridInit",
    "studio.shipHybridExport",
    "studio.shipCapInit",
    "studio.shipCapExport",
    "studio.shipUnityConfirm",
    "studio.shipHybridConfirm",
    "studio.shipUnknown",
    "studio.shipStopped",
    "studio.shipCloneFailed",
    "studio.shipSkipped",
    "studio.shipNoIpa",
    "studio.shipNoStore",
    "studio.shipPlaceholder",
    "studio.shipOverwrite",
)

PATHS = (
    "/unity/init",
    "/unity/generate-build-scripts",
    "/firebase/configure",
    "/hybrid-mobile/init",
    "/hybrid-mobile/export",
    "/capacitor/init",
    "/capacitor/export",
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
    body = "" if payload is None else json.dumps(payload)
    route.fulfill(status=status, content_type="application/json", body=body)


@pytest.fixture(scope="module")
def studio_server(tmp_path_factory):
    pytest.importorskip("playwright")
    data = tmp_path_factory.mktemp("ship-init-data")
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
        pytest.fail("studio server did not start\n" + out[-2000:])
    game = _api(server, "POST", "/api/v1/playground/projects", {"name": "Old Game", "kind": "static"})
    vite = _api(server, "POST", "/api/v1/playground/projects", {"name": "Vite Game", "kind": "vite"})
    unity = _api(server, "POST", "/api/v1/playground/projects", {"name": "Unity Game", "kind": "unity"})
    app = _api(server, "POST", "/api/v1/playground/projects", {"name": "Old App", "kind": "fastapi"})
    server["game"] = game["project"]["slug"]
    server["vite"] = vite["project"]["slug"]
    server["unity"] = unity["project"]["slug"]
    server["app"] = app["project"]["slug"]
    yield server
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_ship_contract_is_present_and_leaves_dead_controls_out():
    for key in KEYS:
        assert I18N.count(f'"{key}":') == 2, key
    for button in (
        "ship-unity-init",
        "ship-unity-build",
        "ship-firebase",
        "ship-hybrid-init",
        "ship-hybrid-export",
        "ship-cap-init",
        "ship-cap-export",
    ):
        assert f'id="{button}"' in HTML
    assert '<option value="daedalus_minimal" selected>daedalus_minimal</option>' in HTML
    assert '<option value="custom" selected>custom</option>' in HTML
    assert "unity_game_framework" in HTML
    assert "daedalus_ugf" in HTML
    assert "hybrid_mobile" in HTML
    assert "attach-template" not in HTML
    assert "unity-export" not in HTML
    assert "with_ionic" not in WORK
    assert "unity-export" not in WORK
    assert "attach-template" not in WORK
    assert "preview-frame" not in WORK[WORK.find("function runShip"):WORK.find("function onSlug")]
    for path in PATHS:
        assert path in WORK
    assert "SHIP_TIMEOUT_MS = 60000" in WORK
    assert "SHIP_UNITY_TIMEOUT_MS = 240000" in WORK
    run = WORK[WORK.find("function runShip"):WORK.find("function onSlug")]
    assert run.count("dx.api(") == 1
    assert "studio.shipUnknown" in run
    assert "setTimeout" not in run
    assert "not safe to retry" in I18N
    assert "Не е безопасно да се праща пак." in I18N


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


def _arm(page, reply):
    posts = []

    def handle(route):
        if route.request.method != "POST":
            route.fallback()
            return
        raw = route.request.post_data
        posts.append({
            "url": route.request.url,
            "body": json.loads(raw) if raw else None,
        })
        reply(route, posts)

    for path in PATHS:
        page.route("**" + path, handle)
    return posts


def _open(page, server, slug):
    page.goto(server["url"] + "/?slug=" + slug + "&panel=build", wait_until="domcontentloaded")
    page.wait_for_function(
        """(slug) => {
          const btn = document.getElementById('ship-unity-init');
          const row = document.querySelector('#plist [data-slug="' + slug + '"]');
          return btn && !btn.disabled && row && row.classList.contains('active');
        }""",
        arg=slug,
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


def test_unity_chain_names_overwrite_clone_and_stops(studio_server):
    from playwright.sync_api import sync_playwright
    game = studio_server["game"]
    paths = [f"studio/file-{i}.txt" for i in range(9)]
    mode = {"firebase": 500, "unity": "clone"}
    dialogs = []
    accept = {"on": False}

    def reply(route, posts):
        url = route.request.url
        if url.endswith("/unity/init"):
            if mode["unity"] == "clone":
                _fulfill(route, 200, {
                    "ok": True,
                    "written": paths,
                    "cloned": "FAILED: offline",
                    "preview": "studio/preview/index.html",
                })
                return
            if mode["unity"] == "full":
                _fulfill(route, 200, {
                    "ok": True,
                    "written": paths,
                    "cloned": "",
                    "preview": "studio/preview/index.html",
                })
                return
            _fulfill(route, 200, {
                "ok": True,
                "written": ["studio/second.md", "studio/unity/build-local.sh"],
                "cloned": "",
                "preview": "studio/preview/index.html",
            })
            return
        if url.endswith("/firebase/configure"):
            if mode["firebase"] == 500:
                _fulfill(route, 500, {"ok": False, "error": "firebase-down"})
                return
            if mode["firebase"] == "empty":
                _fulfill(route, 200, {})
                return
            _fulfill(route, 200, {"ok": True, "written": ["studio/integrations/firebase.config.json"]})
            return
        if url.endswith("/unity/generate-build-scripts"):
            _fulfill(route, 200, {
                "ok": True,
                "local_only": True,
                "written": ["studio/unity/build-local.sh", "Assets/_Project/Editor/DaedalusBuild.cs"],
            })
            return
        _fulfill(route, 500, {"ok": False, "error": "unexpected"})

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        leaked = _guard(page)
        posts = _arm(page, reply)

        def on_dialog(dialog):
            dialogs.append(dialog.message)
            if accept["on"]:
                dialog.accept()
            else:
                dialog.dismiss()

        page.on("dialog", on_dialog)
        _open(page, studio_server, game)
        assert page.locator("#ship-template").input_value() == "daedalus_minimal"
        assert page.locator("#ship-preset").input_value() == "custom"
        assert page.locator("#ship-hybrid-init").is_hidden()
        assert page.locator("#ship-cap-init").is_visible()
        page.locator("#ship-unity-init").click()
        assert posts == []
        assert game in dialogs[0]
        assert "Build scripts are overwritten" in dialogs[0]
        assert "overwrites the Firebase files with defaults" in dialogs[0]
        assert "does not clone a repository" in dialogs[0]
        assert "github.com" not in dialogs[0]
        assert "not safe to retry" in dialogs[0]
        page.locator("#ship-preset").select_option("racing")
        page.locator("#ship-unity-init").click()
        assert "does not write Firebase files by itself" in dialogs[1]
        assert "overwrites the Firebase files with defaults" not in dialogs[1]
        page.locator("#ship-template").select_option("unity_game_framework")
        page.locator("#ship-unity-init").click()
        assert "github.com/EllanJiang/UnityGameFramework.git" in dialogs[2]
        assert "external network call" in dialogs[2]
        page.locator("#ship-template").select_option("daedalus_ugf")
        page.locator("#ship-unity-init").click()
        assert "overwrites the Firebase files with defaults" in dialogs[3]
        assert "github.com/EllanJiang/UnityGameFramework.git" in dialogs[3]
        page.locator("#ship-template").select_option("daedalus_minimal")
        page.locator("#ship-preset").select_option("custom")
        page.locator("#ship-firebase-project").fill("demo-fb")
        page.locator("#ship-bundle").fill("com.example.game")
        accept["on"] = True
        page.locator("#ship-unity-init").click()
        page.wait_for_function(
            """() => document.getElementById('ship-status').textContent.includes('Clone failed')
              && document.getElementById('ship-status').textContent.includes('Stopped')
              && !document.getElementById('ship-unity-init').disabled"""
        )
        cloned = page.locator("#ship-status").inner_text()
        for path in paths:
            assert path in cloned
        assert "Clone failed. FAILED: offline" in cloned
        assert "Cloned " not in cloned
        assert "installed" not in cloned.lower()
        assert "Stopped. Later steps were not sent." in cloned
        assert len(posts) == 1
        assert posts[0]["url"].endswith("/unity/init")
        assert posts[0]["body"] == {
            "template_id": "daedalus_minimal",
            "studio_preset": "custom",
            "project_id": "demo-fb",
            "bundle_id": "com.example.game",
        }
        page.locator("#ship-firebase-project").fill("")
        page.locator("#ship-bundle").fill("")
        mode["unity"] = "full"
        page.locator("#ship-unity-init").click()
        page.wait_for_function(
            """() => {
              const text = document.getElementById('ship-status').textContent;
              return text.includes('studio/file-8.txt') && !text.includes('Clone failed')
                && !document.getElementById('ship-unity-init').disabled;
            }"""
        )
        text = page.locator("#ship-status").inner_text()
        for path in paths:
            assert path in text
        assert "installed" not in text.lower()
        assert "Server set kind to unity." in text
        assert "Preview path studio/preview/index.html." in text
        assert "studio/preview" not in (page.locator("#preview-frame").get_attribute("src") or "")
        assert "static" in page.locator(f"#plist [data-slug='{game}']").inner_text()
        assert posts[1]["body"] == {"template_id": "daedalus_minimal", "studio_preset": "custom"}
        page.locator("#ship-firebase-project").fill("demo-fb")
        page.locator("#ship-bundle").fill("com.example.game")
        mode["unity"] = "second"
        page.locator("#ship-unity-init").click()
        page.wait_for_function(
            """() => document.getElementById('ship-status').textContent.includes('firebase-down')
              && !document.getElementById('ship-unity-init').disabled"""
        )
        failed = page.locator("#ship-status").inner_text()
        assert "studio/second.md" in failed
        assert "studio/unity/build-local.sh" in failed
        assert "Request failed: firebase-down" in failed
        assert "Stopped" not in failed
        assert "live key" not in failed
        assert [row["body"] for row in posts[2:]] == [
            {
                "template_id": "daedalus_minimal",
                "studio_preset": "custom",
                "project_id": "demo-fb",
                "bundle_id": "com.example.game",
            },
            {"project_id": "demo-fb", "bundle_id": "com.example.game"},
        ]
        assert posts[3]["url"].endswith("/firebase/configure")
        page.locator("#ship-firebase-project").fill("")
        page.locator("#ship-unity-build").click()
        page.wait_for_function(
            """() => document.getElementById('ship-status').textContent.includes('Not a store listing.')
              && !document.getElementById('ship-unity-build').disabled"""
        )
        assert posts[4]["url"].endswith("/unity/generate-build-scripts")
        assert posts[4]["body"] == {"bundle_id": "com.example.game"}
        assert "DaedalusBuild.cs" in dialogs[-1]
        assert "not safe to retry" in dialogs[-1]
        page.locator("#ship-bundle").fill("")
        mode["firebase"] = "empty"
        page.locator("#ship-firebase").click()
        page.wait_for_function(
            """() => document.getElementById('ship-status').textContent.includes('did not succeed')
              && !document.getElementById('ship-firebase').disabled"""
        )
        assert "Wrote:" not in page.locator("#ship-status").inner_text()
        assert posts[5]["body"] == {}
        assert posts[5]["url"].endswith("/firebase/configure")
        assert leaked == []
        browser.close()


def test_hybrid_order_and_exports_stay_honest(studio_server):
    from playwright.sync_api import sync_playwright
    vite = studio_server["vite"]
    mode = {"firebase": 200}

    def reply(route, _posts):
        url = route.request.url
        if url.endswith("/hybrid-mobile/init"):
            _fulfill(route, 200, {
                "ok": True,
                "written": ["hybrid/index.html", "hybrid/package.json"],
                "preview": "hybrid/index.html",
            })
            return
        if url.endswith("/firebase/configure"):
            if mode["firebase"] == 500:
                _fulfill(route, 500, {"ok": False, "error": "firebase-down"})
                return
            _fulfill(route, 200, {"ok": True, "written": ["studio/integrations/firebase.md"]})
            return
        if url.endswith("/capacitor/init"):
            _fulfill(route, 200, {
                "ok": True,
                "skipped": True,
                "reason": "Export from the editor. Capacitor does not wrap this kind.",
                "written": [],
            })
            return
        if url.endswith("/hybrid-mobile/export"):
            _fulfill(route, 200, {
                "ok": True,
                "written": ["studio/hybrid/export-manifest.json"],
                "manifest": {"dist_ready": False, "next_steps": ["Open PakePlus-iOS on Mac"]},
            })
            return
        if url.endswith("/capacitor/export"):
            _fulfill(route, 200, {
                "ok": True,
                "written": ["studio/capacitor/export-manifest.json"],
                "manifest": {
                    "store_ready": False,
                    "skipped": True,
                    "next_steps": ["This kind is not a WebView."],
                },
            })
            return
        _fulfill(route, 500, {"ok": False, "error": "unexpected"})

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        leaked = _guard(page)
        posts = _arm(page, reply)
        page.on("dialog", lambda dialog: dialog.accept())
        _open(page, studio_server, vite)
        page.wait_for_function("() => !document.getElementById('ship-hybrid-init').hidden")
        assert page.locator("#ship-cap-init").is_visible()
        page.locator("#ship-bundle").fill("com.example.game")
        page.locator("#ship-hybrid-init").click()
        page.wait_for_function(
            """() => document.getElementById('ship-status').textContent.includes('Skipped.')
              && !document.getElementById('ship-hybrid-init').disabled"""
        )
        text = page.locator("#ship-status").inner_text()
        assert "hybrid/index.html" in text
        assert "hybrid/package.json" in text
        assert "Export from the editor. Capacitor does not wrap this kind." in text
        assert "initialized" not in text.lower()
        assert "Preview path hybrid/index.html." in text
        frame_src = page.locator("#preview-frame").get_attribute("src") or ""
        assert "hybrid/index.html" not in frame_src
        assert "studio/preview" not in frame_src
        assert [row["url"].rsplit("/", 2)[-2] + "/" + row["url"].rsplit("/", 1)[-1] for row in posts] == [
            "hybrid-mobile/init",
            "capacitor/init",
        ]
        assert posts[0]["body"] == {"bundle_id": "com.example.game"}
        assert posts[1]["body"] == {"bundle_id": "com.example.game"}
        assert "with_ionic" not in json.dumps(posts)
        page.locator("#ship-firebase-project").fill("demo-fb")
        mode["firebase"] = 500
        page.locator("#ship-hybrid-init").click()
        page.wait_for_function(
            """() => document.getElementById('ship-status').textContent.includes('firebase-down')
              && document.getElementById('ship-status').textContent.includes('Stopped')
              && !document.getElementById('ship-hybrid-init').disabled"""
        )
        assert [row["url"].rsplit("/", 1)[-1] for row in posts[2:]] == ["init", "configure"]
        assert posts[2]["url"].endswith("/hybrid-mobile/init")
        assert posts[3]["url"].endswith("/firebase/configure")
        assert posts[2]["body"] == {"project_id": "demo-fb", "bundle_id": "com.example.game"}
        assert posts[3]["body"] == {"project_id": "demo-fb", "bundle_id": "com.example.game"}
        partial = page.locator("#ship-status").inner_text()
        assert "hybrid/index.html" in partial
        assert "placeholder" not in partial
        page.locator("#ship-hybrid-export").click()
        page.wait_for_function(
            """() => document.getElementById('ship-status').textContent.includes('No IPA was built.')
              && !document.getElementById('ship-hybrid-export').disabled"""
        )
        exported = page.locator("#ship-status").inner_text()
        assert "Open PakePlus-iOS on Mac" in exported
        assert "studio/hybrid/export-manifest.json" in exported
        assert posts[4]["body"] is None
        page.locator("#ship-cap-export").click()
        page.wait_for_function(
            """() => document.getElementById('ship-status').textContent.includes('Not a store listing.')
              && document.getElementById('ship-status').textContent.includes('This kind is not a WebView.')
              && !document.getElementById('ship-cap-export').disabled"""
        )
        assert posts[5]["body"] is None
        assert posts[5]["url"].endswith("/capacitor/export")
        assert leaked == []
        browser.close()


def test_slug_switch_stops_the_chain_and_context_stays(studio_server):
    from playwright.sync_api import sync_playwright
    game = studio_server["game"]
    other = studio_server["app"]
    unity = studio_server["unity"]
    held = []

    def reply(route, _posts):
        if route.request.url.endswith("/unity/init"):
            held.append(route)
            return
        _fulfill(route, 500, {"ok": False, "error": "unexpected"})

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        leaked = _guard(page)
        posts = _arm(page, reply)
        page.on("dialog", lambda dialog: dialog.accept())
        _open(page, studio_server, game)
        page.locator("#ship-firebase-project").fill("demo-fb")
        page.locator("#ship-unity-init").click()
        page.wait_for_function("() => document.getElementById('ship-unity-init').disabled")
        try:
            page.locator("#ship-unity-init").click(timeout=400)
            clicked = True
        except Exception:
            clicked = False
        assert clicked is False
        assert len(posts) == 1
        page.locator(f"#plist [data-slug='{other}']").click()
        page.wait_for_function(
            """(slug) => document.querySelector('#plist [data-slug="' + slug + '"]').classList.contains('active')""",
            arg=other,
        )
        assert len(held) == 1
        _fulfill(held[0], 200, {
            "ok": True,
            "written": ["studio/from-old.md"],
            "cloned": "FAILED: offline",
            "preview": "studio/preview/index.html",
        })
        page.wait_for_function("() => !document.getElementById('ship-unity-init').disabled")
        assert "studio/from-old.md" not in page.locator("#ship-status").inner_text()
        assert "Clone failed" not in page.locator("#ship-status").inner_text()
        assert len(posts) == 1
        assert posts[0]["url"].endswith("/unity/init")
        assert f"/projects/{game}/unity/init" in posts[0]["url"]
        page.locator(f"#plist [data-slug='{unity}']").click()
        page.wait_for_function(
            """(slug) => {
              const row = document.querySelector('#plist [data-slug="' + slug + '"]');
              return row && row.classList.contains('active')
                && document.getElementById('ship-cap-init').hidden
                && document.getElementById('ship-hybrid-init').hidden
                && !document.getElementById('ship-unity-init').hidden;
            }""",
            arg=unity,
        )
        page.locator("#ship-preset").select_option("hybrid_mobile")
        page.wait_for_function(
            """() => document.getElementById('ship-unity-init').hidden
              && !document.getElementById('ship-hybrid-init').hidden
              && !document.getElementById('ship-cap-init').hidden"""
        )
        page.locator(".dx-lang button[data-lang=bg]").click()
        page.wait_for_function(
            """() => document.getElementById('ship-hybrid-init').textContent === 'Старт на hybrid'"""
        )
        assert "Unity Game" in page.locator(f"#plist [data-slug='{unity}']").inner_text()
        page.locator(".dx-lang button[data-lang=en]").click()
        page.wait_for_function("() => document.getElementById('ship-firebase').textContent === 'Firebase'")
        for width, height in ((1280, 800), (910, 640)):
            page.set_viewport_size({"width": width, "height": height})
            _hit(page, "#settings-link")
            page.wait_for_function("() => new URL(location.href).searchParams.get('panel') === 'settings'")
            assert page.url.startswith(studio_server["url"] + "/")
            assert page.locator("#panel-settings").is_visible()
            assert page.locator("#ship-preset").input_value() == "hybrid_mobile"
            assert page.evaluate("() => window.scrollY") == 0
        assert leaked == []
        browser.close()
