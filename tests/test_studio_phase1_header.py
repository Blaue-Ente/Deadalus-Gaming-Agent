"""Phase 1 Studio header: real clicks, visible Settings, honest load errors."""
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
CSS = (ROOT / "app" / "static" / "studio.css").read_text(encoding="utf-8")
HTML = (ROOT / "app" / "static" / "studio.html").read_text(encoding="utf-8")
JS = (ROOT / "app" / "static" / "studio.js").read_text(encoding="utf-8")
I18N = (ROOT / "app" / "static" / "i18n.js").read_text(encoding="utf-8")

VIEWPORTS = ((1280, 800), (1366, 768), (910, 640))
SHOTS = {
    (1280, 800): "/opt/cursor/artifacts/phase1-header-1280.png",
    (1366, 768): "/opt/cursor/artifacts/phase1-header-1366.png",
    (910, 640): "/opt/cursor/artifacts/phase1-header-910.png",
}


def test_header_css_contract():
    assert "grid-template-rows: minmax(var(--header), auto) 1fr;" in CSS
    assert "grid-template-rows: var(--header) 1fr;" not in CSS
    header = CSS.split("header.app {", 1)[1].split("}", 1)[0]
    assert "z-index: 2;" in header
    assert 'id="settings-link"' in HTML
    assert 'href="/admin/daedalus/connectors"' in HTML


def test_settings_string_pair():
    assert '"studio.settings": "Settings"' in I18N
    assert '"studio.settings": "Настройки"' in I18N


def test_load_catch_does_not_invent_status():
    assert "paintMentor({ enabled: true, env_locked: false })" not in JS
    assert "paintAssist({ mode: \"auto\", env_locked: false })" not in JS
    assert "paintBudget({ usd: 0, env_locked: false })" not in JS
    assert "function reportHeaderError" in JS
    assert 'id="header-status"' in HTML


def test_effort_preview_posts():
    assert '"studio.effortPreviewErr": "Effort preview failed"' in I18N
    assert '"studio.effortPreviewErr": "Прегледът на усилието не мина"' in I18N
    start = JS.index("async function previewEffort")
    body = JS[start:start + 700]
    assert 'method: "POST"' in body
    assert "/api/v1/daedalus/effort?" not in JS


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="module")
def studio_server(tmp_path_factory):
    pytest.importorskip("playwright")
    data = tmp_path_factory.mktemp("phase1-data")
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
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    deadline = time.time() + 30
    url = f"http://127.0.0.1:{port}"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "/health", timeout=1) as res:
                if res.status == 200:
                    break
        except Exception:
            time.sleep(0.2)
    else:
        out = proc.stdout.read().decode("utf-8", "replace") if proc.stdout else ""
        proc.kill()
        pytest.fail("studio server did not start\n" + out[-2000:])
    yield {"url": url, "data": data, "proc": proc, "env": env, "port": port}
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
          return {x, y, ok: top === el || (top && el.contains(top)), tag: top && top.tagName};
        }"""
    )
    assert point["ok"], selector + " covered by " + str(point)
    viewport = page.viewport_size
    assert box["y"] + box["height"] <= viewport["height"]
    assert box["x"] + box["width"] <= viewport["width"]
    loc.click()


def test_header_hits_at_laptop_viewports(studio_server):
    from playwright.sync_api import sync_playwright
    url = studio_server["url"] + "/"
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for width, height in VIEWPORTS:
            context = browser.new_context(viewport={"width": width, "height": height})
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_selector("#settings-link")
            assert page.evaluate("() => window.scrollY") == 0
            page.screenshot(path=SHOTS[(width, height)])
            for sel in (
                ".mentor button[data-mentor='on']",
                ".effort button[data-effort='craft']",
                ".budget button[data-budget='5']",
                ".dx-lang button[data-lang='en']",
                ".dx-lang button[data-lang='bg']",
                "#settings-link",
            ):
                if sel == "#settings-link":
                    box = page.locator(sel).bounding_box()
                    assert box is not None
                    assert box["y"] + box["height"] <= height
                    assert page.evaluate("() => window.scrollY") == 0
                    continue
                _hit(page, sel)
            context.close()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(url, wait_until="domcontentloaded")
        page.locator("#settings-link").click()
        page.wait_for_url("**/admin/daedalus/connectors")
        browser.close()


def test_mentor_get_500_shows_error(studio_server):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        def handle(route):
            if route.request.method == "GET":
                route.fulfill(status=500, content_type="application/json", body='{"detail":"mentor down"}')
            else:
                route.fallback()
        page.route("**/api/v1/daedalus/mentor", handle)
        page.goto(studio_server["url"] + "/", wait_until="domcontentloaded")
        status = page.locator("#header-status")
        status.wait_for(state="visible")
        text = status.inner_text()
        assert "500" in text or "mentor down" in text
        assert page.locator(".mentor button[data-mentor='on']").get_attribute("aria-pressed") == "false"
        browser.close()


def test_effort_preview_posts_and_shows_error(studio_server):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        seen = {}
        def handle(route):
            seen["method"] = route.request.method
            seen["body"] = route.request.post_data or ""
            route.fulfill(status=500, content_type="application/json", body='{"detail":"effort down"}')
        page.route("**/api/v1/daedalus/effort", handle)
        page.goto(studio_server["url"] + "/", wait_until="domcontentloaded")
        page.locator(".effort button[data-effort='craft']").click()
        page.wait_for_function("() => document.getElementById('effortnote').textContent.includes('effort down')")
        assert seen.get("method") == "POST"
        assert "craft" in seen.get("body", "")
        assert page.locator(".effort button[data-effort='craft']").get_attribute("aria-pressed") == "true"
        browser.close()


def _restart(server):
    server["proc"].terminate()
    try:
        server["proc"].wait(timeout=10)
    except subprocess.TimeoutExpired:
        server["proc"].kill()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(server["port"])],
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


def test_settings_persist_across_process_and_browser_restart(studio_server):
    from playwright.sync_api import sync_playwright
    url = studio_server["url"] + "/"
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded")
        page.locator(".mentor button[data-mentor='off']").click()
        page.locator(".budget button[data-budget='5']").click()
        page.locator(".effort button[data-effort='craft']").click()
        page.locator(".dx-lang button[data-lang='bg']").click()
        page.wait_for_function("() => document.querySelector('.mentor button[data-mentor=off]').getAttribute('aria-pressed') === 'true'")
        page.wait_for_function("() => document.querySelector('.budget button[data-budget=\"5\"]').getAttribute('aria-pressed') === 'true'")
        _restart(studio_server)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("() => document.querySelector('.mentor button[data-mentor=off]').getAttribute('aria-pressed') === 'true'")
        assert page.locator(".mentor button[data-mentor='off']").get_attribute("aria-pressed") == "true"
        assert page.locator(".budget button[data-budget='5']").get_attribute("aria-pressed") == "true"
        assert page.locator(".effort button[data-effort='craft']").get_attribute("aria-pressed") == "true"
        assert page.evaluate("() => localStorage.getItem('dx.lang')") == "bg"
        fresh = browser.new_context(viewport={"width": 1280, "height": 800})
        page2 = fresh.new_page()
        page2.goto(url, wait_until="domcontentloaded")
        page2.wait_for_function("() => document.querySelector('.mentor button[data-mentor=off]').getAttribute('aria-pressed') === 'true'")
        assert page2.evaluate("() => localStorage.getItem('dx.lang')") in (None, "en")
        assert page2.locator(".dx-lang button[data-lang='en']").get_attribute("aria-pressed") == "true"
        assert page2.locator(".effort button[data-effort='auto']").get_attribute("aria-pressed") == "true"
        assert page2.locator(".mentor button[data-mentor='off']").get_attribute("aria-pressed") == "true"
        assert page2.locator(".budget button[data-budget='5']").get_attribute("aria-pressed") == "true"
        browser.close()


