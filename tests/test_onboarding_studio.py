"""First steps in Studio, with real clicks: it opens by itself once, the SubQ switch saves, Done closes it for good,
Setup brings it back, and Bulgarian is one click away (owner 2026-11-13)."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    pytest.importorskip("playwright")
    data = tmp_path_factory.mktemp("onb-data")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    env = dict(os.environ)
    env.update({"DAEDALUS_STANDALONE": "1", "DAEDALUS_DATA": str(data), "DAEDALUS_PLAYGROUND": str(data / "pg"),
                "DAEDALUS_ONBOARDING_AUTO": "1"})
    env.pop("DAEDALUS_SUBQ_SHADOW", None)
    log = tempfile.TemporaryFile()
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
                             "--port", str(port)], cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "/health", timeout=1):
                break
        except Exception:
            time.sleep(0.2)
    else:
        log.seek(0)
        proc.kill()
        pytest.fail("server did not start\n" + log.read().decode("utf-8", "replace")[-2000:])
    yield {"url": url, "data": data}
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.mark.parametrize("size", [(1280, 800), (910, 640)])
def test_first_steps_open_once_save_the_switch_and_come_back_from_setup(server, size):
    from playwright.sync_api import sync_playwright

    settings = server["data"] / "settings.json"
    if settings.exists():
        settings.unlink()                                       # each viewport is a first run
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": size[0], "height": size[1]})
        posts = []
        page.on("request", lambda r: posts.append((r.url, r.post_data)) if r.method == "POST" else None)
        try:
            page.add_init_script("try { localStorage.setItem('dx.lang', 'en'); } catch (e) {}")
            page.goto(server["url"] + "/", wait_until="domcontentloaded")
            dlg = page.locator("#onb-dlg")
            dlg.wait_for(state="visible", timeout=15000)
            page.wait_for_selector("#onb-body .onb-item", timeout=15000)
            body = page.locator("#onb-body").inner_text()
            assert "Python" in body and "How to start" in body and "daedalus serve" in body
            assert "What Daedalus can do" in body and "SubQ judge" in body

            switch = page.locator("#onb-subq")
            was = switch.is_checked()
            switch.click()
            page.wait_for_function("() => document.getElementById('onb-subq') && !document.getElementById('onb-subq').disabled")
            saved = [json.loads(d) for u, d in posts if u.endswith("/api/v1/harness/config")]
            assert saved and saved[-1] == {"key": "subq.shadow", "value": (not was)}

            page.locator("#onb-dlg [data-lang='bg']").click()
            page.wait_for_function("() => document.getElementById('onb-body').innerText.includes('Какво може Дедал')")
            page.locator("#onb-dlg [data-lang='en']").click()

            with page.expect_response(lambda r: r.url.endswith("/api/v1/onboarding/seen")) as seen:
                page.locator("#onb-close").click()
            assert seen.value.ok
            dlg.wait_for(state="hidden")
            assert json.loads(settings.read_text())["onboarding_seen"] >= 1
            assert json.loads(settings.read_text())["subq_shadow"] is (not was)

            page.reload(wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            assert not dlg.is_visible()                          # seen: it does not open by itself again
            page.locator("#onb-link").click()
            dlg.wait_for(state="visible")
            page.wait_for_selector("#onb-body .onb-item", timeout=15000)
            box = page.locator("#onb-close").bounding_box()
            assert box and box["x"] + box["width"] <= size[0]
        finally:
            browser.close()
