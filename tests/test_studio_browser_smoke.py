"""Studio browser audit: isolated uvicorn (127.0.0.1), Playwright Chromium, no LLM/chat spend.

Blocks non-loopback HTTP(S) in the browser so accidental cloud calls fail closed.
(Outbound inference is not triggered — no chat/send.)

Screenshots: /opt/cursor/artifacts/studio-audit-4549/ when writable.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _server_env(tmp_projects: Path, tmp_data: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["DAEDALUS_STANDALONE"] = "1"
    env["DAEDALUS_DATA"] = str(tmp_data)
    env["DAEDALUS_PLAYGROUND"] = str(tmp_projects)
    env["DAEDALUS_BIND_HOST"] = "127.0.0.1"
    env["DAEDALUS_TOKEN"] = "pytest-studio-browser-smoke"
    env.pop("DAEDALUS_MENTOR", None)
    env.pop("DAEDALUS_WRITER_ASSIST", None)
    env.pop("DAEDALUS_RUN_BUDGET", None)
    env.pop("DAEDALUS_OFFLINE", None)
    return env


def _wait_health(base: str, timeout_s: float = 45.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = httpx.get(f"{base}/health", timeout=1.0)
            if r.status_code == 200 and r.json().get("ok") is True:
                return
        except Exception:
            time.sleep(0.12)
    raise AssertionError("server did not become healthy")


def _start_uvicorn(port: int, env: dict[str, str]) -> subprocess.Popen:
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    return subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


@pytest.fixture
def studio_server(tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    monkeypatch.delenv("DAEDALUS_USE_ADN_DB", raising=False)
    projects = tmp_path / "projects"
    data = tmp_path / "data"
    projects.mkdir(parents=True)
    data.mkdir(parents=True)

    from daedalus.paths import data_root, playground_projects_dir
    from daedalus.store import reset_sqlite_pool

    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    reset_sqlite_pool()

    port = _free_port()
    env = _server_env(projects, data)
    proc = _start_uvicorn(port, env)
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_health(base)
        yield {
            "base": base,
            "token": env["DAEDALUS_TOKEN"],
            "proc": proc,
            "env": env,
            "port": port,
            "projects": projects,
            "data": data,
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        reset_sqlite_pool()


def _api(base: str, token: str, method: str, path: str, **kwargs):
    h = {"Authorization": f"Bearer {token}"}
    url = f"{base}{path}"
    m = (method or "GET").upper()
    if m == "GET":
        return httpx.get(url, headers=h, timeout=30.0, **kwargs)
    if m == "POST":
        return httpx.post(url, headers=h, timeout=30.0, **kwargs)
    raise ValueError(method)


def _allow_localhost(url: str, server_base: str) -> bool:
    try:
        u = urlparse(url)
        host = (u.hostname or "").lower()
        if host in {"127.0.0.1", "localhost", "::1"}:
            return True
        sp = urlparse(server_base)
        return u.netloc == sp.netloc or host == ""
    except Exception:
        return False


def _install_local_route(page, base: str) -> None:
    page.route(
        "**/*",
        lambda route: (
            route.abort()
            if route.request.resource_type == "websocket"
            else route.continue_()
            if _allow_localhost(route.request.url, base)
            else route.abort()
        ),
    )


def _hit_test_header_controls(page) -> list[dict]:
    """Return per-selector {selector, ok, topTag, topId}; ok iff elementFromPoint is control or inside it."""
    return page.evaluate(
        """() => {
      const sels = [
        '[data-lang="bg"]',
        '.mentor button[data-mentor="off"]',
        '.assist button[data-assist="off"]',
        '.budget button[data-budget="5"]',
        '#mentor-custom',
      ];
      return sels.map((sel) => {
        const el = document.querySelector(sel);
        if (!el) return { sel, ok: false, reason: 'missing' };
        const r = el.getBoundingClientRect();
        const x = r.left + Math.min(r.width / 2, 40);
        const y = r.top + Math.min(r.height / 2, 20);
        const top = document.elementFromPoint(x, y);
        const ok = top && (el === top || el.contains(top));
        return {
          sel,
          ok: !!ok,
          topTag: top ? top.tagName : null,
          topId: top && top.id ? top.id : null,
          cx: x,
          cy: y,
        };
      });
    }""",
    )


def test_studio_header_hit_targets_not_obscured_by_main(studio_server):
    """Regression: center of header chips must hit the control, not <main> (grid row clip)."""
    from playwright.sync_api import sync_playwright

    base = studio_server["base"]
    for viewport, label in (
        ({"width": 1400, "height": 900}, "wide"),
        ({"width": 900, "height": 700}, "narrow"),
    ):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = browser.new_context(viewport=viewport)
            page = context.new_page()
            _install_local_route(page, base)
            page.goto(f"{base}/", wait_until="load", timeout=45000)
            page.wait_for_selector("#prompt", timeout=20000)
            hits = _hit_test_header_controls(page)
            bad = [h for h in hits if not h.get("ok")]
            assert not bad, f"{label} viewport hit-test failed: {bad}"
            browser.close()


def test_studio_browser_controls_and_persistence(studio_server):
    """Smoke: effort note, localStorage reload, prefs via UI, fresh context, Flight tab."""
    from playwright.sync_api import sync_playwright

    art = Path("/opt/cursor/artifacts/studio-audit-4549")
    art.mkdir(parents=True, exist_ok=True)

    base = studio_server["base"]
    token = studio_server["token"]
    h = {"Authorization": f"Bearer {token}"}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()
        _install_local_route(page, base)

        page.goto(f"{base}/", wait_until="load", timeout=30000)
        page.wait_for_selector("#prompt", timeout=15000)
        page.screenshot(path=str(art / "studio-loaded.png"), full_page=True)

        page.locator('[data-effort="epic"]').click()
        page.fill("#prompt", "build a small phaser snake")
        page.wait_for_timeout(900)
        note = page.locator("#effortnote")
        note_text = note.inner_text(timeout=15000)
        assert "Epic" in note_text or "Епос" in note_text, note_text

        assert page.evaluate("() => localStorage.getItem('dx.effort')") == "epic"
        page.reload(wait_until="load")
        page.wait_for_selector("#prompt", timeout=15000)
        assert page.evaluate("() => localStorage.getItem('dx.effort')") == "epic"
        page.screenshot(path=str(art / "studio-after-reload.png"), full_page=True)

        mark = f"ui-pref-{time.time_ns()}"
        page.fill("#preftext", mark)
        page.locator("#prefadd").click()
        page.wait_for_function("() => !document.getElementById('prefadd').disabled", timeout=15000)
        page.reload(wait_until="load")
        page.wait_for_selector("#preflist", timeout=15000)
        assert mark in page.content()

        list_got = httpx.get(f"{base}/api/v1/daedalus/preferences", headers=h, timeout=15.0)
        texts = " ".join((i.get("text") or "") for i in (list_got.json().get("items") or []))
        assert mark in texts

        browser.close()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context2 = browser.new_context(viewport={"width": 1400, "height": 900})
        page2 = context2.new_page()
        _install_local_route(page2, base)
        page2.goto(f"{base}/", wait_until="load", timeout=30000)
        page2.wait_for_selector("#prompt", timeout=15000)
        ev_fresh = page2.evaluate("() => localStorage.getItem('dx.effort')")
        assert ev_fresh is None or ev_fresh == "auto"
        page2.screenshot(path=str(art / "studio-fresh-context.png"), full_page=True)
        browser.close()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        pg = ctx.new_page()
        _install_local_route(pg, base)
        pg.goto(f"{base}/", wait_until="load")
        pg.wait_for_selector(".tabs", timeout=15000)
        pg.locator('.tabs button[data-pane="flight"]').click()
        pg.wait_for_selector("#pane-flight.on", timeout=5000)
        pg.screenshot(path=str(art / "studio-tab-flight.png"))
        browser.close()


def test_studio_browser_audit_safe_controls(studio_server):
    """Language, mentor (rules, no LLM), assist, budget, prefs delete, project, context, nav, offline, world refresh, restart."""
    from playwright.sync_api import sync_playwright

    art = Path("/opt/cursor/artifacts/studio-audit-4549")
    art.mkdir(parents=True, exist_ok=True)

    base = studio_server["base"]
    token = studio_server["token"]
    proc = studio_server["proc"]
    env = dict(studio_server["env"])
    port = studio_server["port"]

    def _get(path: str):
        return _api(base, token, "get", path)

    off0 = _get("/api/v1/daedalus/offline").json()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()
        errs: list[str] = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        _install_local_route(page, base)

        page.goto(f"{base}/", wait_until="load", timeout=45000)
        page.wait_for_selector("#prompt", timeout=20000)
        assert not errs, errs

        assert not [h for h in _hit_test_header_controls(page) if not h.get("ok")]

        page.locator('[data-lang="bg"]').click()
        page.wait_for_function("() => document.documentElement.lang === 'bg'", timeout=8000)
        page.screenshot(path=str(art / "studio-lang-bg.png"), full_page=True)
        page.locator('[data-lang="en"]').click()
        page.wait_for_function("() => document.documentElement.lang === 'en'", timeout=8000)

        page.locator('.mentor button[data-mentor="off"]').click()
        page.wait_for_timeout(500)
        m1 = _get("/api/v1/daedalus/mentor").json()
        assert m1.get("enabled") is False
        page.locator('.mentor button[data-mentor="on"]').click()
        page.wait_for_timeout(500)
        m2 = _get("/api/v1/daedalus/mentor").json()
        assert m2.get("enabled") is True

        page.wait_for_selector("#mentor-choice", state="visible", timeout=15000)
        page.select_option("#mentor-choice", "rules")
        page.wait_for_timeout(500)
        m3 = _get("/api/v1/daedalus/mentor").json()
        assert (m3.get("llm") or {}).get("choice", {}).get("id") == "rules"

        page.locator("#mentor-custom").click()
        page.wait_for_selector("#mentor-custom-dlg[open]", timeout=5000)
        page.screenshot(path=str(art / "studio-mentor-custom-open.png"))
        page.locator("#mentor-custom-cancel").click()
        page.wait_for_timeout(200)

        page.locator('.assist button[data-assist="off"]').click()
        page.wait_for_timeout(500)
        wa1 = _get("/api/v1/daedalus/writer-assist").json()
        assert wa1.get("mode") == "off"
        page.locator('.assist button[data-assist="auto"]').click()
        page.wait_for_timeout(500)
        wa2 = _get("/api/v1/daedalus/writer-assist").json()
        assert wa2.get("mode") == "auto"

        page.locator('.budget button[data-budget="5"]').click()
        page.wait_for_timeout(500)
        rb = _get("/api/v1/daedalus/run-budget").json()
        assert rb.get("usd") == 5

        t1 = f"audit-del-a-{time.time_ns()}"
        t2 = f"audit-del-b-{time.time_ns()}"
        page.fill("#preftext", t1)
        page.locator("#prefadd").click()
        page.wait_for_function("() => !document.getElementById('prefadd').disabled", timeout=15000)
        page.fill("#preftext", t2)
        page.locator("#prefadd").click()
        page.wait_for_function("() => !document.getElementById('prefadd').disabled", timeout=15000)
        page.locator("#preflist .item").filter(has_text=t2).click()
        page.wait_for_function(
            """(txt) => {
            const box = document.getElementById('preflist');
            return box && !(box.innerText || '').includes(txt);
        }""",
            arg=t2,
            timeout=15000,
        )

        page.fill("#pname", "Audit Iso")
        page.fill("#pdesc", "browser audit fixture")
        page.select_option("#pkind", "static")
        page.locator("#pcreate").click()
        page.wait_for_timeout(800)
        page.locator("#plist .item").filter(has_text="Audit Iso").click()
        page.wait_for_timeout(400)
        slug = page.evaluate("() => localStorage.getItem('dx.slug')")
        assert slug
        pj = httpx.get(f"{base}/api/v1/playground/projects", headers={"Authorization": f"Bearer {token}"}).json()
        assert any((x.get("slug") == slug) for x in (pj.get("projects") or []))

        page.locator("#ctxplus").click()
        page.wait_for_function("() => { const s = document.getElementById('ctxsheet'); return s && !s.hidden; }")
        page.screenshot(path=str(art / "studio-ctx-open.png"), full_page=True)
        page.locator("#ctxclose").click()
        page.wait_for_timeout(200)

        for href, name in (
            ("/admin/daedalus/proposals", "proposals"),
            ("/admin/daedalus/hub", "hub"),
        ):
            page.goto(f"{base}/", wait_until="load")
            page.wait_for_selector("#prompt", timeout=15000)
            with page.expect_navigation(wait_until="load", timeout=15000):
                page.locator(f'a[href="{href}"]').click()
            assert href in page.url or "/admin/daedalus/" in page.url
            page.screenshot(path=str(art / f"studio-nav-{name}.png"), full_page=True)

        page.goto(f"{base}/", wait_until="load")
        page.wait_for_selector("#prompt", timeout=15000)
        page.locator('.tabs button[data-pane="llm"]').click()
        page.wait_for_selector("#pane-llm.on", timeout=15000)
        off_before = _get("/api/v1/daedalus/offline").json().get("offline")
        page.locator("#offon").click()
        page.wait_for_timeout(600)
        off_mid = _get("/api/v1/daedalus/offline").json()
        assert off_mid.get("offline") is not off_before
        page.locator("#offon").click()
        page.wait_for_timeout(600)
        off_after = _get("/api/v1/daedalus/offline").json()
        assert off_after.get("offline") == off_before

        page.locator('.tabs button[data-pane="llm"]').click()
        page.wait_for_selector("#wardrobe", timeout=10000)
        page.screenshot(path=str(art / "studio-brain-tab.png"), full_page=True)

        page.locator('.tabs button[data-pane="world"]').click()
        page.wait_for_selector("#pane-world.on", timeout=10000)
        page.locator("#cxrefresh").click()
        page.wait_for_timeout(800)
        wb = page.locator("#worldbox").inner_text(timeout=15000)
        assert "REACH" in wb or "connectors" in wb.lower()

        page.screenshot(path=str(art / "studio-audit-final.png"), full_page=True)
        browser.close()

    proc.terminate()
    try:
        proc.wait(timeout=12)
    except subprocess.TimeoutExpired:
        proc.kill()
    time.sleep(0.3)

    proc2 = _start_uvicorn(port, env)
    try:
        _wait_health(base)
        r_m = _get("/api/v1/daedalus/mentor").json()
        assert r_m.get("enabled") is True
        assert (r_m.get("llm") or {}).get("choice", {}).get("id") == "rules"
        r_w = _get("/api/v1/daedalus/writer-assist").json()
        assert r_w.get("mode") == "auto"
        r_b = _get("/api/v1/daedalus/run-budget").json()
        assert r_b.get("usd") == 5
        r_o = _get("/api/v1/daedalus/offline").json()
        assert r_o.get("offline") == off0.get("offline")
    finally:
        proc2.terminate()
        try:
            proc2.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc2.kill()
