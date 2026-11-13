"""PR10: the sidebar holds projects and files; Build and Preview have sub-tabs.

A temporary server with scratch data. Browser checks follow what a person sees: the sidebar does not need
page scroll with five projects, the New project dialog creates and selects a project, one group of each
sub-tab bar shows at a time, a deep link opens Publish & Git, and an action with nothing to do says so
next to its field.
"""
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
HTML = (ROOT / "app" / "static" / "studio.html").read_text(encoding="utf-8")
I18N = (ROOT / "app" / "static" / "i18n.js").read_text(encoding="utf-8")

NEW_KEYS = (
    "studio.newProject", "studio.sub.design", "studio.sub.run", "studio.sub.assets", "studio.sub.export",
    "studio.sub.play", "studio.sub.publish", "studio.sub.data", "studio.needName", "studio.needPref",
    "studio.needCommand", "studio.needSource",
)


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _api(server, method, path, body=None):
    token = (Path(server["data"]) / "owner.token").read_text(encoding="utf-8").strip()
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(server["url"] + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as res:
        raw = res.read().decode()
        return json.loads(raw) if raw else {}


@pytest.fixture(scope="module")
def studio_server(tmp_path_factory):
    pytest.importorskip("playwright")
    data = tmp_path_factory.mktemp("sidebar-tabs-data")
    port = _free_port()
    env = dict(os.environ)
    env.update({
        "DAEDALUS_STANDALONE": "1",
        "DAEDALUS_DATA": str(data),
        "DAEDALUS_PLAYGROUND": str(data / "playground"),
        "DAEDALUS_OFFLINE": "1",
    })
    for key in ("DAEDALUS_MENTOR", "DAEDALUS_RUN_BUDGET", "OPENAI_API_KEY", "ALLTOKEN_API_KEY", "OPENROUTER_API_KEY"):
        env.pop(key, None)
    server_log = tempfile.TemporaryFile()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT), env=env, stdout=server_log, stderr=subprocess.STDOUT,
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
    slugs = []
    for i in range(5):
        res = _api(server, "POST", "/api/v1/playground/projects", {"name": f"Sidebar Game {i}", "kind": "static"})
        slugs.append(res["project"]["slug"])
    server["slugs"] = slugs
    yield server
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_markup_and_i18n_contract():
    for key in NEW_KEYS:
        assert I18N.count(f'"{key}":') == 2, key
    aside = HTML[HTML.index("<aside>"):HTML.index("</aside>")]
    for kept in ("new-project", "kind-filter", "plist", "filelist"):
        assert f'id="{kept}"' in aside
    for moved in ("pname", "pcreate", "assetlist", "asset-pipeline", "git-menu", "preftext", "lessonlist",
                  "skillpin", "pcapsule", "ppub", "plive"):
        assert f'id="{moved}"' not in aside, moved
    for removed in ("pcapsule", "ppub", "plive", "skillpin", "skillpick"):
        assert f'id="{removed}"' not in HTML, removed
    assert '<dialog id="new-project-dlg"' in HTML
    assert 'id="build-tabs"' in HTML and 'id="preview-tabs"' in HTML
    for tab in ("design", "run", "assets", "export"):
        assert f'id="build-sub-{tab}"' in HTML
    for tab in ("play", "publish", "data"):
        assert f'id="preview-sub-{tab}"' in HTML


def _open(page, server, query=""):
    page.goto(server["url"] + "/" + query, wait_until="domcontentloaded")
    page.wait_for_function("() => document.querySelectorAll('#plist .item').length >= 5")


def _visible_groups(page, panel):
    return page.evaluate(
        """(panel) => [...document.querySelectorAll('#panel-' + panel + ' > .subpanel')]
          .filter((el) => !el.hidden).map((el) => el.dataset.sub)""",
        panel,
    )


def test_sidebar_fits_without_scroll_with_five_projects(studio_server):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1366, "height": 768})
        _open(page, studio_server)
        sizes = page.evaluate("() => { const a = document.querySelector('aside'); return [a.scrollHeight, a.clientHeight]; }")
        assert sizes[0] <= sizes[1], sizes
        browser.close()


def test_new_project_dialog_creates_and_selects(studio_server):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1366, "height": 768})
        _open(page, studio_server)
        assert page.locator("#new-project-dlg").evaluate("el => el.open") is False
        page.locator("#new-project").click()
        assert page.locator("#new-project-dlg").evaluate("el => el.open") is True
        # M18: an empty name says so next to the field and keeps the dialog open
        page.locator("#pcreate").click()
        assert page.locator("#pname-msg").is_visible()
        assert page.locator("#new-project-dlg").evaluate("el => el.open") is True
        page.locator("#pname").fill("Dialog Made Game")
        assert page.locator("#pname-msg").is_hidden()
        page.locator("#pcreate").click()
        page.wait_for_function("() => !document.getElementById('new-project-dlg').open")
        page.wait_for_function(
            "() => { const a = document.querySelector('#plist .item.active'); return a && /Dialog Made Game/.test(a.textContent); }"
        )
        browser.close()


def test_build_subtabs_switch_and_show_one_group(studio_server):
    from playwright.sync_api import sync_playwright
    slug = studio_server["slugs"][0]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1366, "height": 768})
        _open(page, studio_server, f"?panel=build&slug={slug}")
        assert _visible_groups(page, "build") == ["design"]
        assert page.locator("#build-brainstorm").is_visible()
        assert page.locator("#build-guided").is_hidden()
        for tab, probe in (("run", "#build-guided"), ("assets", "#asset-pipeline"), ("export", "#ship-unity-init"),
                           ("design", "#build-brainstorm")):
            page.locator(f'#build-tabs button[data-tab="{tab}"]').click()
            assert _visible_groups(page, "build") == [tab]
            assert page.locator(probe).is_visible(), probe
            expect_q = "" if tab == "design" else f"subtab={tab}"
            assert (expect_q in page.url) if expect_q else ("subtab=" not in page.url)
        browser.close()


def test_preview_subtabs_and_deep_link(studio_server):
    from playwright.sync_api import sync_playwright
    slug = studio_server["slugs"][0]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1366, "height": 768})
        _open(page, studio_server, f"?panel=preview&subtab=publish&slug={slug}")
        assert _visible_groups(page, "preview") == ["publish"]
        assert page.locator('#preview-tabs button[aria-selected="true"]').get_attribute("data-tab") == "publish"
        assert page.locator("#preview-local").is_visible() and page.locator("#git-menu").is_visible()
        assert page.locator("#preview-run").is_hidden()
        page.locator('#preview-tabs button[data-tab="data"]').click()
        assert _visible_groups(page, "preview") == ["data"]
        assert page.locator("#app-data-load").is_visible() and page.locator("#git-menu").is_hidden()
        page.locator('#preview-tabs button[data-tab="play"]').click()
        assert _visible_groups(page, "preview") == ["play"]
        assert page.locator("#preview-run").is_visible()
        assert "subtab=" not in page.url
        # a bad value falls back to the first group
        _open(page, studio_server, f"?panel=preview&subtab=nope&slug={slug}")
        assert _visible_groups(page, "preview") == ["play"]
        browser.close()


def test_preferences_and_lessons_live_under_review_memory(studio_server):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1366, "height": 768})
        _open(page, studio_server, "?panel=review&tab=memory")
        page.wait_for_selector("#preftext", state="visible")
        assert page.locator("#lessonlist").is_visible()
        # M18: an empty preference says so next to the field
        page.locator("#prefadd").click()
        assert page.locator("#preftext-msg").is_visible()
        page.locator("#workspace-nav button[data-panel=review]").click()
        page.locator('#review-tabs button[data-tab="proposals"]').click()
        assert page.locator("#preftext").is_hidden()
        browser.close()


def test_empty_shell_and_dissection_say_so(studio_server):
    from playwright.sync_api import sync_playwright
    slug = studio_server["slugs"][0]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1366, "height": 768})
        _open(page, studio_server, f"?slug={slug}")
        page.locator('.tabs button[data-pane="shell"]').click()
        page.locator("#shrun").click()
        assert page.locator("#shcmd-msg").is_visible()
        page.locator("#shcmd").fill("echo hi")
        assert page.locator("#shcmd-msg").is_hidden()
        page.locator('.tabs button[data-pane="world"]').click()
        page.wait_for_function("() => !document.getElementById('dissectbtn').disabled")
        page.locator("#dissectbtn").click()
        assert page.locator("#dissectsrc-msg").is_visible()
        browser.close()


def test_project_delete_reports_in_the_header_from_any_panel(studio_server):
    from playwright.sync_api import sync_playwright
    res = _api(studio_server, "POST", "/api/v1/playground/projects", {"name": "Delete Me Game", "kind": "static"})
    slug = res["project"]["slug"]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1366, "height": 768})
        _open(page, studio_server, f"?panel=review&tab=memory&slug={slug}")
        page.locator("#project-menu summary").click()
        page.locator("#project-delete").click()
        page.locator("#dx-confirm-ok").click()
        page.wait_for_function("() => { const h = document.getElementById('header-status'); return h && !h.hidden && h.textContent.trim() !== ''; }")
        assert page.locator("#header-status").get_attribute("data-state") == "ok"
        assert page.locator("#workspace-status").inner_text().strip() == ""
        browser.close()
