"""Asset generate, auto-pack, and pipeline in the Files rail.

Posts are intercepted. The temporary server has no provider keys. Scratch
data only. A confirm cancel sends nothing. A timeout is not retried.
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
from dx_confirm_support import answer_confirms

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "app" / "static" / "studio.html").read_text(encoding="utf-8")
WORK = (ROOT / "app" / "static" / "studio-workspace.js").read_text(encoding="utf-8")
I18N = (ROOT / "app" / "static" / "i18n.js").read_text(encoding="utf-8")

KEYS = (
    "studio.assetGenerate",
    "studio.assetAutopack",
    "studio.assetGenerateConfirm",
    "studio.assetAutopackConfirm",
    "studio.assetMeshConfirm",
    "studio.assetWriteConfirm",
    "studio.assetReadConfirm",
    "studio.assetMusicConfirm",
    "studio.assetBackendNote",
    "studio.assetUnknown",
    "studio.assetPackNone",
    "studio.assetGreybox",
    "studio.assetNeedKey",
    "studio.assetRead",
    "studio.assetReadFile",
    "studio.assetMeshPrompt",
    "studio.assetNeedMeshPrompt",
    "studio.assetStatusPollConfirm",
    "studio.assetStatusLocalConfirm",
    "studio.assetStatusLocal",
    "studio.assetStatusChecked",
    "studio.assetJobUpdated",
    "studio.assetKitNote",
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
    data = tmp_path_factory.mktemp("asset-rail-data")
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
    app = _api(server, "POST", "/api/v1/playground/projects", {"name": "Old App", "kind": "fastapi"})
    server["game"] = game["project"]["slug"]
    server["app"] = app["project"]["slug"]
    yield server
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_asset_labels_exist_and_mesh_is_not_the_default():
    for key in KEYS:
        assert I18N.count(f'"{key}":') == 2, key
    assert 'id="asset-controls"' in HTML
    assert 'id="asset-action"' in HTML
    assert '<option value="status" selected>status</option>' in HTML
    assert '<option value="mesh">mesh</option>' in HTML
    assert 'id="asset-mesh-prompt"' in HTML
    assert 'id="asset-mesh-name" value="hero"' in HTML
    assert '<option value="fal">fal</option>' in HTML
    assert '<option value="tripo">tripo</option>' in HTML
    assert '<option value="meshy">meshy</option>' in HTML
    assert 'id="asset-job"' in HTML
    assert "custom source images" in HTML
    assert "sources:" not in WORK
    assert "ASSET_GENERATE_TIMEOUT_MS = 120000" in WORK
    assert "ASSET_AUTOPACK_TIMEOUT_MS = 660000" in WORK
    assert "ASSET_PIPELINE_TIMEOUT_MS = 60000" in WORK
    assert '{ path, prompt, size }' in WORK
    assert '{ description }' in WORK
    assert "const body = { action };" in WORK
    assert "body.prompt" in WORK
    assert "body.job_id = jobId" in WORK
    assert "studio.assetUnknown" in WORK
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
        posts.append({
            "url": route.request.url,
            "body": json.loads(route.request.post_data or "{}"),
        })
        reply(route, posts)

    page.route("**/assets/generate", handle)
    page.route("**/assets/auto-pack", handle)
    page.route("**/assets/pipeline", handle)
    return posts


def _open(page, server, game):
    page.goto(server["url"] + "/?slug=" + game, wait_until="domcontentloaded")
    page.wait_for_function(
        """(slug) => {
          const btn = document.getElementById('asset-generate');
          const row = document.querySelector('#plist [data-slug="' + slug + '"]');
          return btn && !btn.disabled && row && row.classList.contains('active');
        }""",
        arg=game,
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


def test_generate_cancel_and_empty_prompt_send_nothing(studio_server):
    from playwright.sync_api import sync_playwright
    game = studio_server["game"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        leaked = _guard(page)
        posts = _arm(page, lambda route, _posts: _fulfill(route, 200, {"ok": True, "path": "assets/custom.png", "bytes": 12}))
        _open(page, studio_server, game)
        assert page.locator("#asset-action").input_value() == "status"
        page.locator("#asset-prompt").fill("")
        page.locator("#asset-generate").click()
        assert posts == []
        assert "Enter a prompt." in page.locator("#asset-status").inner_text()
        page.locator("#asset-prompt").fill("a small tree")
        answer_confirms(page, accept=False)   # click #dx-confirm-cancel
        page.locator("#asset-generate").click()
        assert posts == []
        assert leaked == []
        browser.close()


def test_generate_posts_the_old_body_and_names_the_file(studio_server):
    from playwright.sync_api import sync_playwright
    game = studio_server["game"]
    seen = []

    def reply(route, _posts):
        if route.request.url.endswith("/assets"):
            _fulfill(route, 200, {"ok": True, "assets": [{"path": "assets/tree.png", "bytes": 40}]})
            return
        _fulfill(route, 200, {"ok": True, "path": "assets/tree.png", "bytes": 40})

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        leaked = _guard(page)
        page.route("**/assets", lambda route: reply(route, []))
        posts = _arm(page, lambda route, _posts: _fulfill(
            route, 200, {"ok": True, "path": "assets/tree.png", "bytes": 40},
        ))

        seen = answer_confirms(page)   # click #dx-confirm-ok, list the questions
        _open(page, studio_server, game)
        page.locator("#asset-path").fill("assets/tree.png")
        page.locator("#asset-prompt").fill("a small tree")
        page.locator("#asset-size").select_option("1024x768")
        page.locator("#asset-generate").click()
        page.wait_for_function(
            """() => document.getElementById('asset-status').textContent.includes('assets/tree.png')
              && document.getElementById('assetlist').textContent.includes('assets/tree.png')"""
        )
        assert len(posts) == 1
        assert posts[0]["body"] == {"path": "assets/tree.png", "prompt": "a small tree", "size": "1024x768"}
        assert f"/projects/{game}/assets/generate" in posts[0]["url"]
        assert "model" not in posts[0]["body"]
        assert "image service" in seen[0] and "PNG" in seen[0] and game in seen[0]   # no stale model name
        assert "not safe to retry" in seen[0]
        text = page.locator("#asset-status").inner_text()
        assert "Wrote assets/tree.png (40 bytes)." == text
        assert "published" not in text.lower()
        assert leaked == []
        browser.close()


def test_generate_empty_200_and_500_are_not_a_file(studio_server):
    from playwright.sync_api import sync_playwright
    game = studio_server["game"]
    replies = [(200, None), (500, {"ok": False, "error": "image down"})]

    def reply(route, _posts):
        status, payload = replies.pop(0)
        _fulfill(route, status, payload)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        leaked = _guard(page)
        posts = _arm(page, reply)
        answer_confirms(page)   # every question: click #dx-confirm-ok
        _open(page, studio_server, game)
        page.locator("#asset-prompt").fill("a small tree")
        for needle in ("did not succeed", "image down"):
            page.locator("#asset-generate").click()
            page.wait_for_function(
                """(needle) => {
                  const status = document.getElementById('asset-status');
                  return status.dataset.state === 'error'
                    && status.textContent.includes(needle)
                    && !status.textContent.includes('Wrote')
                    && !status.textContent.toLowerCase().includes('published')
                    && !document.getElementById('asset-generate').disabled;
                }""",
                arg=needle,
            )
        assert len(posts) == 2
        assert "assets/tree.png" not in page.locator("#assetlist").inner_text()
        assert leaked == []
        browser.close()


def test_autopack_shows_partial_and_empty_built(studio_server):
    from playwright.sync_api import sync_playwright
    game = studio_server["game"]
    replies = [
        (200, {"ok": True, "planned": 4, "built": ["assets/a.png"], "errors": [{"path": "assets/b.png", "error": "boom"}]}),
        (200, {"ok": True, "planned": 4, "built": [], "errors": [{"path": "assets/b.png", "error": "boom"}]}),
    ]
    seen = []

    def reply(route, _posts):
        status, payload = replies.pop(0)
        _fulfill(route, status, payload)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        leaked = _guard(page)
        posts = _arm(page, reply)
        seen = answer_confirms(page)   # click #dx-confirm-ok, list the questions
        _open(page, studio_server, game)
        page.locator("#asset-autopack").click()
        assert "Enter a description for auto-pack." in page.locator("#asset-status").inner_text()
        assert posts == []
        page.locator("#asset-desc").fill("a short platform game")
        page.locator("#asset-autopack").click()
        page.wait_for_function(
            """() => {
              const text = document.getElementById('asset-status').textContent;
              return text.includes('Planned 4') && text.includes('Built 1')
                && text.includes('assets/a.png') && text.includes('assets/b.png: boom');
            }"""
        )
        partial = page.locator("#asset-status").inner_text()
        assert "No image was generated." not in partial
        page.locator("#asset-autopack").click()
        page.wait_for_function(
            """() => {
              const text = document.getElementById('asset-status').textContent;
              return text.includes('Built 0') && text.includes('No image was generated.')
                && text.includes('Errors 1');
            }"""
        )
        assert len(posts) == 2
        assert posts[0]["body"] == {"description": "a short platform game"}
        assert "model" not in posts[0]["body"]
        assert f"/projects/{game}/assets/auto-pack" in posts[0]["url"]
        assert "3–6" in seen[0] and "SDXL" in seen[0] and "alltoken:deepseek-v3.2" in seen[0]
        assert leaked == []
        browser.close()


def test_pipeline_renders_each_contract_without_extra_fields(studio_server):
    from playwright.sync_api import sync_playwright
    game = studio_server["game"]
    cases = [
        ("atlas", {"ok": True, "png": "assets/atlas.png", "json": "assets/atlas.json", "count": 8}, "Wrote assets/atlas.png, assets/atlas.json."),
        ("greybox", {"ok": True, "greybox": True, "hero": False, "glb": "assets/greybox_proxy.glb", "note": "greybox cube — not a hero mesh"}, "Greybox cube assets/greybox_proxy.glb. Not a hero mesh."),
        ("mesh", {"ok": True, "id": "job-9", "status": "running", "provider": "fal"}, "Mesh job job-9 is running. No mesh file yet."),
        ("mesh", {"ok": False, "need_key": True, "error": "Need FAL_KEY"}, "Mesh was not submitted. Need FAL_KEY"),
        ("status", {"ok": True, "jobs": [{"id": "job-9", "status": "done", "output": "assets/hero.glb"}]}, "Status reports mesh file assets/hero.glb."),
        ("music", {"ok": False, "handoff": True, "supported": False, "drop": "assets/music.mp3"}, "No track was generated. Drop a file at assets/music.mp3."),
    ]

    def reply(route, posts):
        _action, payload, _expect = cases[len(posts) - 1]
        _fulfill(route, 200, payload)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        leaked = _guard(page)
        posts = _arm(page, reply)
        answer_confirms(page)   # every question: click #dx-confirm-ok
        _open(page, studio_server, game)
        for action, _payload, expect in cases:
            page.locator("#asset-action").select_option(action)
            if action == "mesh":
                page.locator("#asset-mesh-prompt").fill("a stone lantern")
            if action == "status":
                page.locator("#asset-job").select_option("")
            if action == "atlas":
                note = page.locator("#asset-kit-note")
                assert note.is_visible()
                assert "does not send" in note.inner_text()
            page.locator("#asset-pipeline").click()
            page.wait_for_function(
                """(expect) => document.getElementById('asset-status').textContent.includes(expect)
                  && !document.getElementById('asset-pipeline').disabled""",
                arg=expect,
            )
            text = page.locator("#asset-status").inner_text()
            assert expect in text
            assert "published" not in text.lower()
            if action == "status":
                assert "Nothing was written." in text
                assert "was not polled" in text
                assert "Wrote " not in text
            if action == "mesh" and _payload.get("need_key"):
                assert "assets/" not in text
        expect_bodies = []
        for action, _payload, _expect in cases:
            if action == "mesh":
                expect_bodies.append({
                    "action": "mesh",
                    "prompt": "a stone lantern",
                    "name": "hero",
                    "provider": "auto",
                })
            else:
                expect_bodies.append({"action": action})
        assert [row["body"] for row in posts] == expect_bodies
        assert all(f"/projects/{game}/assets/pipeline" in row["url"] for row in posts)
        assert leaked == []
        browser.close()


def test_mesh_prompt_job_check_and_slug_clears_the_binding(studio_server):
    from playwright.sync_api import sync_playwright
    game = studio_server["game"]
    other = studio_server["app"]
    replies = [
        {"ok": True, "id": "job-queued", "status": "queued", "provider": "fal"},
        {"ok": True, "id": "job-queued", "status": "running"},
        {"ok": True, "id": "job-queued", "status": "done"},
        {"ok": True, "id": "job-queued", "status": "done", "output": "assets/lantern.glb"},
        {"ok": True, "jobs": []},
    ]
    dialogs = []

    def reply(route, _posts):
        _fulfill(route, 200, replies.pop(0))

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        leaked = _guard(page)
        posts = _arm(page, reply)
        dialogs = answer_confirms(page)   # click #dx-confirm-ok, list the questions
        _open(page, studio_server, game)
        page.locator("#asset-action").select_option("mesh")
        page.locator("#asset-mesh-prompt").fill("   ")
        page.locator("#asset-pipeline").click()
        assert posts == []
        assert "Enter a mesh prompt." in page.locator("#asset-status").inner_text()
        page.locator("#asset-mesh-prompt").fill("a stone lantern")
        assert page.locator("#asset-mesh-name").input_value() == "hero"
        assert page.locator("#asset-mesh-provider").input_value() == "auto"
        page.locator("#asset-pipeline").click()
        page.wait_for_function(
            """() => document.getElementById('asset-job').value === 'job-queued'
              && !document.getElementById('asset-pipeline').disabled"""
        )
        assert posts[0]["body"] == {
            "action": "mesh",
            "prompt": "a stone lantern",
            "name": "hero",
            "provider": "auto",
        }
        assert "not safe to retry" in dialogs[0]
        assert "fal, Tripo, or Meshy" in dialogs[0]
        assert "mesh prompt is sent" in dialogs[0]
        page.locator("#asset-action").select_option("status")
        page.locator("#asset-pipeline").click()
        page.wait_for_function(
            """() => document.getElementById('asset-status').textContent.includes('No mesh file yet')
              && document.getElementById('asset-status').textContent.includes('job record was updated')
              && !document.getElementById('asset-pipeline').disabled"""
        )
        running = page.locator("#asset-status").inner_text()
        assert "Mesh file" not in running
        assert "Nothing was written." not in running
        assert posts[1]["body"] == {"action": "status", "job_id": "job-queued"}
        assert "job-queued" in dialogs[1]
        assert "does not submit a new mesh" in dialogs[1]
        assert "not safe to retry" in dialogs[1]
        page.locator("#asset-pipeline").click()
        page.wait_for_function(
            """() => document.getElementById('asset-status').textContent.includes('Provider reports done')
              && !document.getElementById('asset-pipeline').disabled"""
        )
        assert "Mesh file" not in page.locator("#asset-status").inner_text()
        page.locator("#asset-pipeline").click()
        page.wait_for_function(
            """() => document.getElementById('asset-status').textContent.includes('Mesh file assets/lantern.glb.')
              && !document.getElementById('asset-pipeline').disabled"""
        )
        done = page.locator("#asset-status").inner_text()
        assert "The job record was updated." in done
        assert "Nothing was written." not in done
        assert [row["body"]["action"] for row in posts] == ["mesh", "status", "status", "status"]
        page.locator(f"#plist [data-slug='{other}']").click()
        page.wait_for_function(
            """(slug) => document.querySelector('#plist [data-slug="' + slug + '"]').classList.contains('active')
              && document.getElementById('asset-job').value === ''""",
            arg=other,
        )
        options = page.locator("#asset-job option").evaluate_all("els => els.map(el => el.value)")
        assert "job-queued" not in options
        page.locator("#asset-action").select_option("status")
        page.locator("#asset-pipeline").click()
        page.wait_for_function(
            """() => document.getElementById('asset-status').textContent.includes('was not polled')
              && !document.getElementById('asset-pipeline').disabled"""
        )
        assert posts[-1]["body"] == {"action": "status"}
        assert "job_id" not in posts[-1]["body"]
        assert "does not poll the provider" in dialogs[-1]
        assert sum(1 for row in posts if row["body"].get("action") == "mesh") == 1
        assert leaked == []
        browser.close()


def test_slug_change_discards_the_reply_and_a_second_click_does_not_post(studio_server):
    from playwright.sync_api import sync_playwright
    game = studio_server["game"]
    other = studio_server["app"]
    held = []

    def reply(route, _posts):
        held.append(route)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        leaked = _guard(page)
        posts = _arm(page, reply)
        answer_confirms(page)   # every question: click #dx-confirm-ok
        _open(page, studio_server, game)
        page.locator("#asset-prompt").fill("a small tree")
        page.locator("#asset-generate").click()
        page.wait_for_function("() => document.getElementById('asset-generate').disabled")
        try:
            page.locator("#asset-generate").click(timeout=400)
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
        _fulfill(held[0], 200, {"ok": True, "path": "assets/from-old.png", "bytes": 9})
        page.wait_for_function("() => !document.getElementById('asset-generate').disabled")
        assert "assets/from-old.png" not in page.locator("#asset-status").inner_text()
        assert "assets/from-old.png" not in page.locator("#assetlist").inner_text()
        assert len(posts) == 1
        assert leaked == []
        browser.close()


def test_languages_and_settings_viewports_stay_put(studio_server):
    from playwright.sync_api import sync_playwright
    game = studio_server["game"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        leaked = _guard(page)
        _arm(page, lambda route, _posts: _fulfill(route, 500, {"ok": False, "error": "provider-raw-error"}))
        answer_confirms(page)   # every question: click #dx-confirm-ok
        _open(page, studio_server, game)
        page.locator(".dx-lang button[data-lang=bg]").click()
        page.wait_for_function(
            """() => document.querySelector('#asset-generate').textContent === 'Генерирай'
              && document.getElementById('asset-backend-note').textContent.includes('не спира сървъра')"""
        )
        assert page.locator("#asset-path").input_value() == "assets/custom.png"
        assert "Old Game" in page.locator(f"#plist [data-slug='{game}']").inner_text()
        page.locator("#asset-prompt").fill("a small tree")
        page.locator("#asset-generate").click()
        page.wait_for_function(
            """() => document.getElementById('asset-status').textContent.includes('provider-raw-error')"""
        )
        assert "provider-raw-error" in page.locator("#asset-status").inner_text()
        page.locator(".dx-lang button[data-lang=en]").click()
        page.wait_for_function("() => document.querySelector('#asset-generate').textContent === 'Generate'")
        for width, height in ((1280, 800), (910, 640)):
            page.set_viewport_size({"width": width, "height": height})
            _hit(page, "#settings-link")
            page.wait_for_function("() => new URL(location.href).searchParams.get('panel') === 'settings'")
            assert page.url.startswith(studio_server["url"] + "/")
            assert "/admin/daedalus/connectors" not in page.url
            assert page.locator("#panel-settings").is_visible()
            assert page.locator("#asset-action").input_value() == "status"
            assert page.evaluate("() => window.scrollY") == 0
        assert leaked == []
        browser.close()
