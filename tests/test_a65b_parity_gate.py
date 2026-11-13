"""A6.5b: the parity gate (real-click evidence for every Studio home and the spec's nine checks) and the redirects of the
old HTML pages that the gate allows. The classic /admin/daedalus page stays (owner, 2026-11-09). The Playground page
stays until its last legacy-only rows (port:A6.5c, found in this audit) move."""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from test_a63a_build_controls import G, _browser, _build_page, _open_build  # noqa: E402
from test_studio_phase2_workspace import _fulfill, studio_server  # noqa: E402,F401  (fixture and helpers)

TESTS = Path(__file__).resolve().parent
REAL_CLICK = re.compile(r"\.click\(|_hit\(|\.check\(|expect_file_chooser|set_input_files")


def _test_source(node: str) -> str:
    file, name = node.split("::", 1)
    src = (TESTS / file).read_text(encoding="utf-8")
    fn = next((n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == name), None)
    assert fn is not None, f"evidence names a test that does not exist: {node}"
    return ast.get_source_segment(src, fn) or ""


# ---- the gate ---------------------------------------------------------------------------------------------------

def test_every_studio_home_has_real_click_evidence_that_exists():
    from daedalus.harness.parity import PARITY_ROWS
    from daedalus.harness.parity_gate import EVIDENCE

    homes = [r.id for r in PARITY_ROWS if r.disposition == "in_studio"]
    assert sorted(set(homes) - set(EVIDENCE)) == []                 # no home without evidence
    assert sorted(set(EVIDENCE) - set(homes)) == []                 # no evidence for a row that is not home
    for row, nodes in EVIDENCE.items():
        assert nodes, row
        for node in nodes:
            body = _test_source(node)
            assert REAL_CLICK.search(body), f"{row}: {node} never clicks"
            assert "force=True" not in body, f"{row}: {node} forces a click"


def test_the_nine_spec_checks_each_name_real_click_evidence():
    from daedalus.harness.parity_gate import SPEC_CHECKS

    assert sorted(SPEC_CHECKS) == list(range(1, 10))
    for check, nodes in SPEC_CHECKS.items():
        assert nodes, check
        for node in nodes:
            assert REAL_CLICK.search(_test_source(node)), f"spec check {check}: {node} never clicks"


def test_only_rows_that_still_wait_keep_a_page_and_nothing_is_guessed():
    from daedalus.harness.parity import PARITY_ROWS
    from daedalus.harness.parity_gate import blocking_rows, redirect_target

    waiting = {r.id: r.disposition for r in PARITY_ROWS if r.disposition.startswith(("port:", "held:"))}
    assert waiting["settings-auto-apply"] == "port:A6.1" and waiting["review-radar-journal"] == "held:MR126"
    # A6.5c moved the Playground-only tools and the owner retired the error-history stub (R7): nothing keeps that page.
    assert sorted(waiting) == ["review-radar-journal", "settings-auto-apply"]
    assert blocking_rows("playground") == [] and redirect_target("/admin/daedalus/playground") == "/?panel=build"
    assert redirect_target("/admin/daedalus") is None                      # the classic page is kept by the owner


REDIRECTS = {
    "/admin/": "/",
    "/admin/daedalus/hub": "/",
    "/admin/daedalus/game-studio": "/?panel=build",
    "/admin/daedalus/playground": "/?panel=build",
    "/admin/daedalus/connectors": "/?panel=settings",
    "/admin/daedalus/proposals": "/?panel=review&tab=proposals",
    "/admin/daedalus/skills": "/?panel=review&tab=skills",
    "/admin/daedalus/bundles": "/?panel=review&tab=bundles",
    "/admin/llm-router": "/?panel=settings",
}


def test_old_pages_redirect_with_302_and_the_kept_pages_stay(a0_tmp):  # noqa: F811
    from app.main import app

    client = TestClient(app)
    for old, new in REDIRECTS.items():
        res = client.get(old, follow_redirects=False)
        assert res.status_code == 302, old
        assert res.headers["location"] == new, old
    kept = client.get("/admin/daedalus", follow_redirects=False)          # the classic page: kept by the owner
    assert kept.status_code == 200 and "<html" in kept.text.lower()
    # an old bookmark that named a project keeps it; the value is escaped and the target stays on this site
    named = client.get("/admin/daedalus/game-studio?project=old-game", follow_redirects=False)
    assert named.headers["location"] == "/?panel=build&slug=old-game"
    odd = client.get("/admin/daedalus/hub?slug=a%20b%26panel%3Dx%2F%2Fevil.test", follow_redirects=False)
    assert odd.headers["location"] == "/?slug=a%20b%26panel%3Dx%2F%2Fevil.test"
    # JSON APIs under the old pages are untouched (phase 3 redirects HTML pages only)
    assert client.get("/admin/daedalus/connectors/overview", follow_redirects=False).status_code == 200
    assert client.get("/admin/daedalus/proposals/api", follow_redirects=False).status_code == 200


def test_every_redirect_lands_on_the_panel_it_names(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        try:
            for old, new in REDIRECTS.items():
                page.goto(studio_server["url"] + old, wait_until="domcontentloaded")
                q = dict(p.split("=", 1) for p in new.split("?", 1)[1].split("&")) if "?" in new else {}
                panel = q.get("panel", "chat")
                page.wait_for_selector(f"#panel-{panel}:not([hidden])")
                assert page.locator(f'#workspace-nav button[data-panel="{panel}"]').get_attribute("aria-selected") == "true", old
                if "tab" in q:
                    assert page.locator(f'#review-tabs [data-tab="{q["tab"]}"]').get_attribute("aria-selected") == "true", old
            game = studio_server["game"]                       # an old game-studio bookmark opens that project in Build
            page.goto(studio_server["url"] + f"/admin/daedalus/game-studio?project={game}", wait_until="domcontentloaded")
            page.wait_for_selector("#panel-build:not([hidden])")
            page.wait_for_function("(slug) => window.dxStudio && window.dxStudio.getSlug() === slug", arg=game)
        finally:
            browser.close()


def test_the_studio_no_longer_links_to_a_redirected_page():
    root = TESTS.parent
    html = (root / "app" / "static" / "studio.html").read_text(encoding="utf-8")
    i18n = (root / "app" / "static" / "i18n.js").read_text(encoding="utf-8")
    for old in REDIRECTS:
        if old in ("/admin/",):
            continue
        assert f'href="{old}' not in html, old
        assert f'href=\\"{old}' not in i18n, old
    assert 'href="/admin/daedalus"' in html                                 # the kept classic page is still linked


# ---- real clicks for the rows that had none yet ------------------------------------------------------------------

def _open_chat(page, server):
    page.add_init_script(f"try {{ localStorage.setItem('dx.lang', 'en'); localStorage.setItem('dx.slug', '{server['game']}'); }} catch (e) {{}}")
    page.goto(server["url"] + "/", wait_until="domcontentloaded")
    page.wait_for_function("() => window.dxStudio && !!window.dxStudio.getSlug()")


def test_a_chat_job_starts_shows_busy_and_stop_calls_the_cancel_route(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        held, starts, cancels = [], [], []
        try:
            def start(route):
                if route.request.method != "POST":
                    return route.fallback()
                starts.append(json.loads(route.request.post_data or "{}"))
                _fulfill(route, 200, {"ok": True, "job_id": "jstop", "session_id": "studio-x"})

            page.route(re.compile(r".*/api/v1/daedalus/chat/jobs$"), start)
            page.route(re.compile(r".*/api/v1/daedalus/chat/jobs/jstop/stream.*"), lambda r: held.append(r))   # the turn keeps running
            page.route(re.compile(r".*/api/v1/daedalus/chat/jobs/jstop/cancel$"),
                       lambda r: (cancels.append(r.request.method), _fulfill(r, 200, {"ok": True, "cancelled": True})))
            _open_chat(page, studio_server)
            page.locator("#prompt").fill("make a maze")
            page.locator("#send").click()
            page.wait_for_function("() => document.getElementById('send').disabled && !document.getElementById('stop').hidden")
            assert starts and starts[0]["message"] == "make a maze"
            page.locator("#stop").click()
            page.wait_for_function("() => !document.getElementById('send').disabled && document.getElementById('stop').hidden")
            assert cancels == ["POST"]
        finally:
            for route in held:
                try:
                    route.abort()
                except Exception:
                    pass
            browser.close()


def test_a_document_is_attached_with_real_clicks_and_goes_with_the_message(studio_server, tmp_path):
    from playwright.sync_api import sync_playwright

    doc = tmp_path / "design-notes.txt"
    doc.write_text("the maze has three floors\n", encoding="utf-8")
    with sync_playwright() as pw:
        browser, page = _browser(pw)
        starts = []
        try:
            def start(route):
                if route.request.method != "POST":
                    return route.fallback()
                starts.append(json.loads(route.request.post_data or "{}"))
                _fulfill(route, 200, {"ok": True, "job_id": "jdoc", "session_id": "studio-x"})

            page.route(re.compile(r".*/api/v1/daedalus/chat/jobs$"), start)
            done = {"id": "jdoc", "status": "done", "result": {"reply": "Read it."}, "error": None, "cancelled": False}
            page.route(re.compile(r".*/api/v1/daedalus/chat/jobs/jdoc/stream.*"),
                       lambda r: r.fulfill(status=200, headers={"Content-Type": "text/event-stream"},
                                           body="event: done\ndata: " + json.dumps(done) + "\n\n"))
            page.route(re.compile(r".*/api/v1/daedalus/chat/jobs/jdoc$"), lambda r: _fulfill(r, 200, {"ok": True, **done}))
            _open_chat(page, studio_server)
            page.locator("#ctxplus").click()
            with page.expect_response(lambda r: r.url.split("?", 1)[0].endswith("/api/v1/daedalus/chat/attachments")) as up:
                with page.expect_file_chooser() as chooser:
                    page.locator('#ctxsheet [data-ctx="files"]').click()
                chooser.value.set_files(str(doc))
            saved = up.value.json()                                   # the real server stored it
            assert up.value.status == 200 and saved.get("id")
            page.wait_for_function("(name) => document.body.textContent.includes(name)", arg="design-notes.txt")
            if not page.locator("#ctxsheet").is_hidden():
                page.locator("#ctxclose").click()
            page.locator("#prompt").fill("use the notes")
            page.locator("#send").click()
            page.wait_for_function("() => document.body.textContent.includes('Read it.')")
            assert starts and starts[0]["attachment_ids"] == [saved["id"]]
        finally:
            browser.close()


def test_dissection_and_lab_are_clicked_and_call_their_routes(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        dissects, labs = [], []
        try:
            page.route(re.compile(r".*/api/v1/daedalus/dissect$"),
                       lambda r: (dissects.append(json.loads(r.request.post_data or "{}")),
                                  _fulfill(r, 200, {"ok": True, "title": "Flappy", "summary": "specimen: flappy, one button"})))

            def lab(route):
                if route.request.method != "POST":
                    return route.fallback()
                labs.append(json.loads(route.request.post_data or "{}"))
                _fulfill(route, 200, {"ok": True, "id": "lab-7"})

            page.route(re.compile(r".*/api/v1/daedalus/labs$"), lab)
            page.route(re.compile(r".*/api/v1/daedalus/labs/status$"), lambda r: _fulfill(r, 200, {"ok": True, "backend": "local"}))
            _open_chat(page, studio_server)
            page.locator('[data-pane="world"]').click()
            page.wait_for_function("() => !document.getElementById('dissectbtn').disabled")
            page.locator("#dissectsrc").fill("Flappy Bird")
            page.locator("#dissectbtn").click()
            page.wait_for_function("() => document.getElementById('worldbox').textContent.includes('specimen: flappy')")
            assert dissects == [{"source": "Flappy Bird", "slug": studio_server["game"], "live": True}]
            page.locator("#labbtn").click()
            page.wait_for_function("() => document.getElementById('worldbox').textContent.includes('lab-7')")
            assert len(labs) == 1 and labs[0].get("title")
        finally:
            browser.close()


def test_gates_and_verify_are_clicked_and_report_what_came_back(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        posts = []
        try:
            _build_page(page)
            for name, body in (("gates", {"ok": True, "slug": G, "current_milestone": "M2-core-loop"}),
                               ("verify", {"ok": True, "slug": G, "current_milestone": "M3-verified"})):
                def answer(name=name, body=body):                       # one route per action, nothing shared
                    return lambda route: (posts.append(name), _fulfill(route, 200, body))

                page.route(re.compile(rf".*/game-studio/api/projects/{G}/{name}$"), answer())
            _open_build(page, studio_server)
            page.wait_for_function("() => !document.getElementById('build-gates').disabled && !document.getElementById('build-verify').disabled")
            page.locator("#build-gates").click()
            page.wait_for_function("() => document.getElementById('build-out').textContent.includes('M2-core-loop')")
            page.wait_for_function("() => !document.getElementById('build-verify').disabled")
            page.locator("#build-verify").click()
            page.wait_for_function("() => document.getElementById('build-out').textContent.includes('M3-verified')")
            assert posts == ["gates", "verify"]
            assert page.locator("#build-status").get_attribute("data-state") == "ok"
        finally:
            browser.close()


def test_revive_and_pull_request_are_clicked_send_their_bodies_and_a_refusal_stays_an_error(studio_server):
    from playwright.sync_api import sync_playwright

    game = studio_server["game"]
    with sync_playwright() as pw:
        browser, page = _browser(pw)
        revives, prs = [], []
        # Revive is a job (2026-11-11): the start returns a running job, the status reports how it ended.
        job = {"id": "j1", "status": "running", "steps": [{"step": "clone", "ok": True, "detail": "old-game"}]}
        failed = dict(job, status="failed", error="not a game repository",
                      steps=job["steps"] + [{"step": "analyze", "ok": False, "detail": "not a game repository"}])
        answers = {"revive": (200, {"ok": True, "job": job}), "status": (200, {"ok": True, "job": failed}),
                   "pr": (200, {"ok": True, "pr": {"url": "https://example.test/pr/7"}})}
        try:
            page.route("**/api/v1/playground/revive",
                       lambda r: (revives.append(json.loads(r.request.post_data or "{}")), _fulfill(r, *answers["revive"])))
            page.route("**/api/v1/playground/revive/j1", lambda r: _fulfill(r, *answers["status"]))
            page.route(f"**/admin/daedalus/playground/api/projects/{game}/create-upstream-pr",
                       lambda r: (prs.append(json.loads(r.request.post_data or "{}")), _fulfill(r, *answers["pr"])))
            page.add_init_script("try { localStorage.setItem('dx.lang', 'en'); } catch (e) {}")
            page.goto(studio_server["url"] + f"/?panel=preview&subtab=publish&slug={game}", wait_until="domcontentloaded")
            page.locator(f"#plist [data-slug='{game}']").wait_for()
            page.locator("#git-menu summary").click()
            page.locator("#git-revive").click()                                    # no URL: asked for, nothing sent
            page.wait_for_function("() => document.getElementById('git-status').dataset.state === 'error'")
            assert revives == []
            page.locator("#git-url").fill("https://example.test/old-game.git")
            page.locator("#git-revive").click()
            page.wait_for_function("() => document.getElementById('git-status').textContent.includes('not a game repository')")
            assert revives == [{"url": "https://example.test/old-game.git", "publish": "local"}]
            assert page.locator("#git-revive-steps li.fail").count() == 1          # the failed step is shown
            assert page.locator("#git-status").get_attribute("data-state") == "error"   # ok:false is never shown as saved
            page.wait_for_function("() => !document.getElementById('git-pr').disabled")
            page.locator("#git-pr").click()
            page.wait_for_function("() => document.getElementById('git-status').dataset.state === 'ok' && !document.getElementById('git-status').hidden")
            assert prs == [{"title": game}]
        finally:
            browser.close()


@pytest.fixture
def a0_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    from daedalus.paths import data_root, playground_projects_dir

    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    yield tmp_path
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
