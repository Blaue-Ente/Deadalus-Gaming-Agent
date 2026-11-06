"""Real browser smoke: Studio against an isolated uvicorn (local loopback, no LLM spend).

Uses Playwright Chromium headless. Writes screenshots under /opt/cursor/artifacts/studio-audit-4549/
when that directory exists (CI may skip images).

Does not send chat, publish, or trigger paid providers.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def studio_server(tmp_path, monkeypatch):
    pytest.importorskip("playwright")
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.setenv("DAEDALUS_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("DAEDALUS_TOKEN", "pytest-studio-browser-smoke")
    monkeypatch.delenv("DAEDALUS_USE_ADN_DB", raising=False)
    from daedalus.paths import data_root, playground_projects_dir

    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    from daedalus.store import reset_sqlite_pool

    reset_sqlite_pool()
    port = _free_port()
    env = os.environ.copy()
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
    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 45
        while time.time() < deadline:
            try:
                r = httpx.get(f"{base}/health", timeout=1.0)
                if r.status_code == 200 and r.json().get("ok") is True:
                    break
            except Exception:
                time.sleep(0.15)
        else:
            err = proc.stderr.read() if proc.stderr else b""
            proc.kill()
            pytest.fail("server did not become healthy:\n" + err.decode("utf-8", errors="replace")[:4000])

        yield base
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
        reset_sqlite_pool()


def test_studio_browser_controls_and_persistence(studio_server):
    from playwright.sync_api import sync_playwright

    art = Path("/opt/cursor/artifacts/studio-audit-4549")
    art.mkdir(parents=True, exist_ok=True)

    base = studio_server
    token = os.environ.get("DAEDALUS_TOKEN", "pytest-studio-browser-smoke")
    h = {"Authorization": f"Bearer {token}"}

    page_errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        page.on("pageerror", lambda exc: page_errors.append(str(exc)))

        page.goto(f"{base}/", wait_until="load", timeout=30000)
        page.wait_for_selector("#prompt", timeout=15000)
        page.screenshot(path=str(art / "studio-loaded.png"), full_page=True)

        assert page_errors == [], f"page JS errors: {page_errors}"

        page.click('[data-effort="epic"]')
        page.fill("#prompt", "build a small phaser snake")
        # debounced preview ~280ms
        page.wait_for_timeout(900)
        note = page.locator("#effortnote")
        note_text = note.inner_text(timeout=15000)
        assert "Epic" in note_text or "Епос" in note_text, note_text
        page.screenshot(path=str(art / "studio-effort-epic-note.png"), full_page=True)

        ev = page.evaluate("() => localStorage.getItem('dx.effort')")
        assert ev == "epic"

        page.reload(wait_until="load")
        page.wait_for_selector("#prompt", timeout=15000)
        ev2 = page.evaluate("() => localStorage.getItem('dx.effort')")
        assert ev2 == "epic"
        page.screenshot(path=str(art / "studio-after-reload.png"), full_page=True)

        # Server-backed preference (survives new browser context)
        mark = f"audit-browser-{time.time_ns()}"
        pr = httpx.post(
            f"{base}/api/v1/daedalus/preferences",
            json={"text": mark},
            headers=h,
            timeout=15.0,
        )
        assert pr.status_code == 200, pr.text
        page.reload(wait_until="load")
        page.wait_for_selector("#preflist", timeout=15000)
        page.wait_for_timeout(300)
        body = page.content()
        assert mark in body, "preference list should show new item after reload"

        browser.close()

    # Fresh browser context (new "session" — local-only dx.* keys reset)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context2 = browser.new_context(viewport={"width": 1400, "height": 900})
        page2 = context2.new_page()
        page2.goto(f"{base}/", wait_until="load", timeout=30000)
        page2.wait_for_selector("#prompt", timeout=15000)
        ev_fresh = page2.evaluate("() => localStorage.getItem('dx.effort')")
        assert ev_fresh is None or ev_fresh == "auto"
        page2.screenshot(path=str(art / "studio-fresh-context.png"), full_page=True)
        browser.close()

    list_got = httpx.get(f"{base}/api/v1/daedalus/preferences", headers=h, timeout=15.0)
    assert list_got.status_code == 200
    texts = " ".join((i.get("text") or "") for i in (list_got.json().get("items") or []))
    assert mark in texts

    # Tab switch (client only): Flight panel visible
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = browser.new_context()
        pg = ctx.new_page()
        pg.goto(f"{base}/", wait_until="load")
        pg.wait_for_selector(".tabs", timeout=15000)
        pg.click('.tabs button[data-pane="flight"]')
        pg.wait_for_selector("#pane-flight.on", timeout=5000)
        art.mkdir(parents=True, exist_ok=True)
        pg.screenshot(path=str(art / "studio-tab-flight.png"))
        browser.close()
