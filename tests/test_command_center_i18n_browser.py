"""Command Center / admin shell: default EN, BG switch + persistence on representative pages.

Uses same isolated uvicorn + Playwright pattern as test_studio_browser_smoke.
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
    env["DAEDALUS_TOKEN"] = "pytest-cc-i18n-browser"
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


@pytest.fixture
def cc_server(tmp_path, monkeypatch):
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
        yield {"base": base, "proc": proc, "env": env}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        reset_sqlite_pool()


def test_command_center_hub_playground_game_studio_i18n(cc_server):
    """Default EN; ordinary click BG; dx.lang persistence; visible intro switches language."""
    from playwright.sync_api import sync_playwright

    base = cc_server["base"]
    cases = (
        ("/admin/daedalus/hub", "hub.intro", "Command Center"),
        ("/admin/daedalus/playground", "pg.intro", "Build"),
        ("/admin/daedalus/game-studio", "gs.intro", "games"),
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()
        _install_local_route(page, base)

        for path, i18n_key, en_needle in cases:
            page.goto(f"{base}/", wait_until="load", timeout=45000)
            page.evaluate("() => localStorage.removeItem('dx.lang')")
            page.goto(f"{base}{path}", wait_until="load", timeout=45000)
            page.wait_for_selector(f'[data-i18n="{i18n_key}"]', timeout=20000)
            assert page.evaluate("() => localStorage.getItem('dx.lang')") in (None, "en")
            assert page.evaluate("() => document.documentElement.lang") == "en"
            intro_en = page.locator(f'[data-i18n="{i18n_key}"]').inner_text(timeout=10000)
            assert en_needle in intro_en, (path, intro_en)

            page.locator("nav.daedalus-section-nav .dx-lang button[data-lang='bg']").click()
            page.wait_for_function("() => document.documentElement.lang === 'bg'", timeout=8000)
            assert page.evaluate("() => localStorage.getItem('dx.lang')") == "bg"
            intro_bg = page.locator(f'[data-i18n="{i18n_key}"]').inner_text(timeout=10000)
            assert en_needle not in intro_bg, (path, intro_bg)
            has_cyrillic = page.evaluate(
                """(key) => /[\\u0400-\\u04FF]/.test(
                    document.querySelector(`[data-i18n="${key}"]`)?.textContent || ''
                )""",
                i18n_key,
            )
            assert has_cyrillic, path

            page.reload(wait_until="load")
            page.wait_for_selector(f'[data-i18n="{i18n_key}"]', timeout=20000)
            assert page.evaluate("() => document.documentElement.lang") == "bg"
            assert page.evaluate("() => localStorage.getItem('dx.lang')") == "bg"

            page.locator("nav.daedalus-section-nav .dx-lang button[data-lang='en']").click()
            page.wait_for_function("() => document.documentElement.lang === 'en'", timeout=8000)
            assert en_needle in page.locator(f'[data-i18n="{i18n_key}"]').inner_text(timeout=10000)

        browser.close()
