"""A6.5c: the last Playground-only capabilities move into the Studio (ideas, hints, AI description, coding mode,
workspace backup, published apps, Expo tunnel with QR, learning stats), and their old routes stop presenting a
fallback or a swallowed error as a real answer."""
from __future__ import annotations

import asyncio
import json
import re

import pytest
from a0_support import a0_tmp  # noqa: F401
from fastapi.testclient import TestClient

_PG = "/admin/daedalus/playground/api"
ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]


@pytest.fixture
def client(a0_tmp):  # noqa: F811
    from app.main import app

    return TestClient(app)


@pytest.fixture
def slug(a0_tmp):  # noqa: F811
    from daedalus import playground as pg

    return asyncio.run(pg.create_project("Port Me", kind="static"))["slug"]


# ---- server: a fallback or a swallowed error is said, never dressed as the real thing ------------------------------

def _llm(monkeypatch, reply=None, boom=False):
    seen = []

    async def complete(**kw):
        seen.append(kw)
        if boom:
            raise RuntimeError("provider down")
        return {"reply": reply}

    monkeypatch.setattr("daedalus.llm_client.complete", complete)
    return seen


def test_ideas_from_the_model_are_plain_and_generic_ones_are_marked_as_fallback(client, slug, monkeypatch):
    _llm(monkeypatch, reply='["Add a boss", "Add a shop"]')
    real = client.post(f"{_PG}/projects/{slug}/suggest", json={"lang": "en"}).json()
    assert real == {"ok": True, "suggestions": ["Add a boss", "Add a shop"], "fallback": False}
    _llm(monkeypatch, boom=True)
    generic = client.post(f"{_PG}/projects/{slug}/suggest", json={"lang": "en"}).json()
    assert generic["ok"] is True and generic["fallback"] is True and len(generic["suggestions"]) == 5
    assert all(re.search(r"[a-z]", s) and not re.search(r"[а-яА-Я]", s) for s in generic["suggestions"])   # English asked, English given
    bg = client.post(f"{_PG}/projects/{slug}/suggest", json={}).json()                                   # the classic page sends no lang
    assert bg["fallback"] is True and any(re.search(r"[а-яА-Я]", s) for s in bg["suggestions"])


def test_ideas_are_asked_for_in_the_language_of_the_screen(client, slug, monkeypatch):
    seen = _llm(monkeypatch, reply='["x"]')
    client.post(f"{_PG}/projects/{slug}/suggest", json={"lang": "en"})
    client.post(f"{_PG}/projects/{slug}/suggest", json={"lang": "bg"})
    assert "English" in seen[0]["user"] and "български" in seen[1]["user"]


def test_a_description_says_whether_the_model_wrote_it(client, monkeypatch):
    _llm(monkeypatch, reply="A tight maze crawler with three floors.")
    wrote = client.post(f"{_PG}/ai-gen-desc", json={"name": "Maze", "kind": "static", "criteria": "three floors", "lang": "en"}).json()
    assert wrote["ok"] is True and wrote["generated"] is True and wrote["description"] == "A tight maze crawler with three floors."
    seen = _llm(monkeypatch, boom=True)
    template = client.post(f"{_PG}/ai-gen-desc", json={"name": "Maze", "kind": "static", "lang": "en"}).json()
    assert template["ok"] is True and template["generated"] is False and "Build Maze" in template["description"]
    assert seen and "English" in seen[0]["system"]
    assert client.post(f"{_PG}/ai-gen-desc", json={"name": ""}).status_code == 400


def test_learning_and_published_reads_report_a_failure_instead_of_an_empty_list(client, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("store down")

    monkeypatch.setattr("daedalus.learning.list_recent", boom)
    recent = client.get(f"{_PG}/learning/recent").json()
    assert recent["records"] == [] and recent["ok"] is False and recent["error"] == "RuntimeError"
    monkeypatch.setattr("daedalus.learning.get_stats", boom)
    stats = client.get(f"{_PG}/learning/stats").json()
    assert stats["ok"] is False and stats["error"] == "RuntimeError"
    monkeypatch.setattr("daedalus.playground_publish.list_published", boom)
    pub = client.get(f"{_PG}/published").json()
    assert pub["apps"] == [] and pub["ok"] is False and pub["error"] == "RuntimeError"


def test_working_learning_and_published_reads_are_ok(client):
    assert client.get(f"{_PG}/learning/recent").json() == {"ok": True, "records": []}
    stats = client.get(f"{_PG}/learning/stats").json()
    assert stats["ok"] is True and stats["total"] == 0
    assert client.get(f"{_PG}/published").json() == {"ok": True, "apps": []}


def test_coding_mode_is_saved_on_the_project_and_a_wrong_one_is_refused(client, slug):
    assert client.post(f"{_PG}/projects/{slug}/set-mode", json={"mode": "pro"}).json() == {"ok": True, "slug": slug, "coding_mode": "pro"}
    listed = {p["slug"]: p for p in client.get("/api/v1/playground/projects").json()["projects"]}
    assert listed[slug]["coding_mode"] == "pro"
    assert client.post(f"{_PG}/projects/{slug}/set-mode", json={"mode": "turbo"}).status_code == 400


def test_the_ported_rows_are_home_the_error_stub_is_retired_and_the_playground_page_redirects(client):
    from daedalus.harness.parity import PARITY_ROWS
    from daedalus.harness.parity_gate import blocking_rows, redirect_target

    by = {r.id: r.disposition for r in PARITY_ROWS}
    for row in ("pg-suggestions", "pg-backup-workspace", "pg-expo-tunnel", "pg-game-hints", "pg-ai-description",
                "pg-learning", "pg-coding-mode", "pg-published-list"):
        assert by[row] == "in_studio", row
    # R7 (owner sign-off 2026-11-10): the error history was a stub that always answered []. It is gone, not ported.
    assert "pg-preview-errors" not in by and by["retire-preview-errors"] == "retired:R7"
    for method in ("get", "post", "delete"):
        assert getattr(client, method)(f"{_PG}/projects/any/errors").status_code == 404
    page = (ROOT / "ui" / "templates" / "daedalus_playground.html").read_text(encoding="utf-8")
    assert "/errors" not in page and "ErrHistory" not in page and "pv-err-hist" not in page
    assert 'id="pv-error-bar"' in page                         # the live error bar of the running preview stays
    assert blocking_rows("playground") == [] and redirect_target("/admin/daedalus/playground") == "/?panel=build"
    res = client.get("/admin/daedalus/playground", follow_redirects=False)
    assert res.status_code == 302 and res.headers["location"] == "/?panel=build"


# ---- Studio: real clicks -------------------------------------------------------------------------------------------

from test_a61c_maintenance import _dialogs  # noqa: E402
from test_a62a_review import _until  # noqa: E402
from test_a62c_memory import _mem_page, _open_memory, _sec  # noqa: E402
from test_a63a_build_controls import G, _browser, _build_page, _open_build  # noqa: E402
from test_studio_phase2_workspace import _api, _fulfill, studio_server  # noqa: E402,F401  (fixture and helpers)

X = "#build-extras"


def _post(page, pattern, answers):
    """Stub a POST: record each body, answer from a list (the last answer repeats)."""
    seen = []

    def handler(route):
        if route.request.method != "POST":
            return route.fallback()
        seen.append(json.loads(route.request.post_data or "{}"))
        status, body = answers[min(len(seen) - 1, len(answers) - 1)]
        _fulfill(route, status, body)

    page.route(pattern, handler)
    return seen


def test_ideas_ask_first_show_what_came_back_and_one_goes_to_the_chat(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        try:
            _build_page(page)
            posts = _post(page, re.compile(rf".*/projects/{G}/suggest$"),
                          [(200, {"ok": True, "suggestions": ["Add a boss fight", "Add a shop"], "fallback": False}),
                           (200, {"ok": True, "suggestions": ["Add a high-score table"], "fallback": True})])
            asked = _dialogs(page, [False, True, True])
            _open_build(page, studio_server, sub="design")
            page.locator("#build-ideas").click()                              # declined: nothing is sent
            assert _until(page, lambda: len(asked) == 1) and "one model call" in asked[0] and posts == []
            page.locator("#build-ideas").click()
            page.wait_for_selector(f"{X} .idea")
            assert posts == [{"lang": "en"}]
            assert [t.strip() for t in page.locator(f"{X} .idea span").all_text_contents()] == ["Add a boss fight", "Add a shop"]
            assert "generic" not in page.locator("#build-extras-status").text_content()
            page.locator("#build-ideas").click()                              # the model did not answer: said, not hidden
            page.wait_for_function("() => document.getElementById('build-extras-status').textContent.includes('generic')")
            assert page.locator("#build-extras-status").get_attribute("data-state") == "error"
            page.locator(f"{X} .idea button").first.click()
            page.wait_for_selector("#panel-chat:not([hidden])")
            assert page.locator("#prompt").input_value() == "Add a high-score table"
        finally:
            browser.close()


def test_hints_and_a_description_use_the_idea_and_say_when_the_model_did_not_write_it(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        hints = []
        try:
            _build_page(page)

            def hint(route):
                hints.append(route.request.url)
                _fulfill(route, 200, {"ok": True, "effective_kind": "static", "is_game": True, "ui_short": "Canvas game, one file per system",
                                      "architecture": "Central game loop in rAF", "libraries": ["Phaser"], "staging": ["core loop first"]})

            page.route(re.compile(r".*/playground/api/game-hints.*"), hint)
            desc = _post(page, re.compile(r".*/playground/api/ai-gen-desc$"),
                         [(200, {"ok": True, "description": "A neon maze with three floors.", "generated": True}),
                          (200, {"ok": True, "description": "Build Orbit UI as a static project.", "generated": False})])
            asked = _dialogs(page, [True, True, False])
            _open_build(page, studio_server, sub="design")
            page.locator("#build-idea").fill("a maze with three floors")
            page.locator("#build-hints").click()
            page.wait_for_function("() => document.getElementById('build-extras-out').textContent.includes('Central game loop')")
            assert "description=a+maze+with+three+floors" in hints[0] or "description=a%20maze%20with%20three%20floors" in hints[0]
            assert "kind=static" in hints[0] and "Phaser" in page.locator("#build-extras-out").text_content()
            assert asked == []                                                # a read: no question
            page.locator("#build-describe").click()
            page.wait_for_function("() => document.getElementById('build-extras-out').textContent.includes('neon maze')")
            assert desc[0] == {"name": "Orbit UI", "kind": "static", "criteria": "a maze with three floors", "lang": "en"}
            assert "one model call" in asked[0]
            page.locator("#build-describe").click()                           # the model failed: a template, and it says so
            page.wait_for_function("() => document.getElementById('build-extras-status').textContent.includes('template')")
            assert page.locator("#build-extras-status").get_attribute("data-state") == "error"
            assert page.locator("#build-idea").input_value() == "a maze with three floors"      # never replaced silently
            page.locator("#build-describe-use").click()                       # asked before replacing; declined
            assert _until(page, lambda: len(asked) == 3)
            assert page.locator("#build-idea").input_value() == "a maze with three floors"
        finally:
            browser.close()


def test_the_coding_mode_shows_the_saved_one_and_a_change_is_saved_on_the_real_server(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        try:
            _build_page(page)
            _open_build(page, studio_server, sub="design")
            page.wait_for_function("() => !document.getElementById('build-coding-mode').disabled")
            assert page.locator("#build-coding-mode").input_value() == "lite"
            page.locator("#build-coding-mode").select_option("premium")
            page.locator("#build-coding-save").click()
            page.wait_for_function("() => document.getElementById('build-extras-status').dataset.state === 'ok' && !document.getElementById('build-extras-status').hidden")
            listed = {p["slug"]: p for p in _api(studio_server, "GET", "/api/v1/playground/projects")["projects"]}
            assert listed[G]["coding_mode"] == "premium"
            page.reload(wait_until="domcontentloaded")
            page.wait_for_function("() => !document.getElementById('build-coding-mode').disabled")
            assert page.locator("#build-coding-mode").input_value() == "premium"
        finally:
            _api(studio_server, "POST", f"{_PG}/projects/{G}/set-mode", {"mode": "lite"})
            browser.close()


def _open_preview(page, server, slug):
    page.add_init_script("try { localStorage.setItem('dx.lang', 'en'); } catch (e) {}")
    page.goto(server["url"] + f"/?panel=preview&subtab=publish&slug={slug}", wait_until="domcontentloaded")
    page.locator(f"#plist [data-slug='{slug}']").wait_for()
    page.wait_for_function("() => !document.getElementById('preview-backup').disabled")


def test_backup_asks_first_and_tells_done_skipped_and_failed_apart(studio_server):
    from playwright.sync_api import sync_playwright

    game = studio_server["game"]
    with sync_playwright() as pw:
        browser, page = _browser(pw)
        try:
            posts = _post(page, re.compile(rf".*/projects/{game}/backup-workspace$"),
                          [(200, {"ok": True, "size_bytes": 20480}),
                           (200, {"ok": False, "skipped": True, "reason": "no_object_storage"}),
                           (200, {"ok": False, "error": "disk full"})])
            asked = _dialogs(page, [False, True, True, True])
            _open_preview(page, studio_server, game)
            page.locator("#preview-backup").click()
            assert _until(page, lambda: len(asked) == 1) and posts == []
            status = page.locator("#preview-extras-status")
            page.locator("#preview-backup").click()
            page.wait_for_function("() => document.getElementById('preview-extras-status').textContent.includes('20 KB')")
            assert status.get_attribute("data-state") == "ok"
            page.locator("#preview-backup").click()
            page.wait_for_function("() => document.getElementById('preview-extras-status').textContent.includes('no_object_storage')")
            assert status.get_attribute("data-state") == "error" and "Nothing was backed up" in status.text_content()
            page.locator("#preview-backup").click()
            page.wait_for_function("() => document.getElementById('preview-extras-status').textContent.includes('disk full')")
            assert status.get_attribute("data-state") == "error" and len(posts) == 3
        finally:
            browser.close()


def test_published_apps_are_listed_with_links_and_a_failed_read_is_not_an_empty_list(studio_server):
    from playwright.sync_api import sync_playwright

    game = studio_server["game"]
    with sync_playwright() as pw:
        browser, page = _browser(pw)
        answers = [{"ok": True, "apps": [{"slug": "old-game", "name": "Old <b>Game</b>", "icon": "🎮", "enabled": True},
                                         {"slug": "hidden", "name": "Hidden", "enabled": False}]},
                   {"ok": True, "apps": []}, {"ok": False, "apps": [], "error": "RuntimeError"}]
        calls = {"n": 0}

        def serve(route):
            body = answers[min(calls["n"], len(answers) - 1)]
            calls["n"] += 1
            _fulfill(route, 200, body)

        try:
            page.route(re.compile(r".*/playground/api/published$"), serve)
            _open_preview(page, studio_server, game)
            page.locator("#preview-published").click()
            page.wait_for_selector("#preview-extras-out .item")
            rows = page.locator("#preview-extras-out .item")
            assert rows.count() == 2 and "Old <b>Game</b>" in rows.nth(0).text_content()        # text, never markup
            assert page.locator("#preview-extras-out b").count() == 0
            assert rows.nth(0).locator("a").get_attribute("href") == "/apps/old-game/"
            assert rows.nth(1).locator("a").count() == 0 and "not served" in rows.nth(1).text_content()
            page.locator("#preview-published").click()
            page.wait_for_function("() => document.getElementById('preview-extras-status').textContent.includes('No published apps')")
            page.locator("#preview-published").click()
            page.wait_for_function("() => document.getElementById('preview-extras-status').dataset.state === 'error'")
            assert "RuntimeError" in page.locator("#preview-extras-status").text_content()
        finally:
            browser.close()


def test_the_tunnel_is_offered_only_for_expo_asks_first_and_the_qr_loads_only_after_a_second_yes(studio_server):
    from playwright.sync_api import sync_playwright

    game = studio_server["game"]
    expo = _api(studio_server, "POST", "/api/v1/playground/projects", {"name": "Tunnel Me", "kind": "expo"})["project"]["slug"]
    qr = "https://api.qrserver.com/v1/create-qr-code/?size=220x220&data=exp%3A%2F%2Fu.test"
    with sync_playwright() as pw:
        browser, page = _browser(pw)
        outside = []
        try:
            page.route(re.compile(r"https://api\.qrserver\.com/.*"),
                       lambda r: (outside.append(r.request.url), r.fulfill(status=200, content_type="image/png", body=b"\x89PNG\r\n")))
            runs = _post(page, re.compile(rf".*/projects/{expo}/run$"), [(200, {"ok": True, "run": {"status": "running", "expo_url": "", "qr_url": ""}})])
            logs = {"n": 0}

            def serve_logs(route):
                logs["n"] += 1
                ready = logs["n"] >= 2
                _fulfill(route, 200, {"slug": expo, "logs": [], "status": {"status": "running", "expo_url": "exp://u.test" if ready else "",
                                                                         "qr_url": qr if ready else ""}})

            page.route(re.compile(rf".*/projects/{expo}/logs$"), serve_logs)
            asked = _dialogs(page, [False, True, False, True])
            _open_preview(page, studio_server, game)
            assert page.locator("#preview-tunnel").is_hidden()                 # a static project has no tunnel
            page.goto(studio_server["url"] + f"/?panel=preview&slug={expo}", wait_until="domcontentloaded")
            page.wait_for_selector("#preview-tunnel:not([hidden])")
            page.wait_for_function("() => !document.getElementById('preview-tunnel').disabled")
            page.locator("#preview-tunnel").click()
            assert _until(page, lambda: len(asked) == 1) and runs == []
            page.locator("#preview-tunnel").click()
            page.wait_for_function("() => document.getElementById('preview-extras-out').textContent.includes('exp://u.test')", timeout=20000)
            assert runs == [{"install_deps": True, "tunnel": True}]
            assert outside == [] and page.locator("#preview-qr").count() == 0   # the address has not left this machine
            page.locator("#preview-qr-show").click()                           # declined
            assert _until(page, lambda: len(asked) == 3) and "api.qrserver.com" in asked[2] and outside == []
            page.locator("#preview-qr-show").click()
            page.wait_for_selector("#preview-qr")
            assert _until(page, lambda: outside == [qr])
        finally:
            _api(studio_server, "DELETE", f"/api/v1/playground/projects/{expo}")
            browser.close()


def test_learning_is_read_on_request_and_a_failed_read_is_said(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        stats = [{"ok": True, "total": 4, "success_rate": 75.0, "avg_attempts": 1.5, "top_models": [{"model": "m-one", "total": 3, "success_rate": 100.0}],
                  "common_errors": [{"error": "missing asset", "count": 2}]},
                 {"ok": False, "error": "RuntimeError"}]
        calls = {"n": 0}

        def serve_stats(route):
            body = stats[min(calls["n"], len(stats) - 1)]
            calls["n"] += 1
            _fulfill(route, 200, body)

        try:
            _mem_page(page)
            page.route(re.compile(r".*/playground/api/learning/stats.*"), serve_stats)
            page.route(re.compile(r".*/playground/api/learning/recent.*"),
                       lambda r: _fulfill(r, 200, {"ok": True, "records": [{"id": 1, "slug": "old-game", "kind": "static", "success": True,
                                                                         "build_model": "m-one", "duration_sec": 42}]}))
            _open_memory(page, studio_server)
            box = _sec("learning")
            page.wait_for_selector(box)
            assert calls["n"] == 0                                             # nothing is read before the click
            page.locator(f"{box} button[data-mem='learning']").click()
            page.wait_for_function("(s) => document.querySelector(s).textContent.includes('75')", arg=box)
            text = page.locator(box).text_content()
            assert "4" in text and "m-one" in text and "missing asset" in text and "old-game" in text
            page.locator(f"{box} button[data-mem='learning']").click()
            page.wait_for_function("() => document.getElementById('review-status').dataset.state === 'error'")
            assert "RuntimeError" in page.locator("#review-status").text_content()
        finally:
            browser.close()


def test_bulgarian_shows_the_new_controls_without_raw_keys(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        try:
            _build_page(page)
            _open_build(page, studio_server, lang="bg", sub="design")
            page.wait_for_selector(f"{X} #build-ideas")
            for sel in (X, "#preview-extras"):
                text = page.locator(sel).evaluate("el => el.textContent")
                assert "studio." not in text, sel
                assert re.search(r"[а-яА-Я]", text), sel
            assert page.locator("#build-ideas").text_content().strip() != "Ideas"
        finally:
            browser.close()


@pytest.mark.parametrize("size", [(1280, 800), (1366, 768), (910, 640)])
def test_the_new_controls_fit_and_are_really_clickable(studio_server, size):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw, size)
        try:
            _build_page(page)
            _open_build(page, studio_server, sub="design")
            page.wait_for_function("() => !document.getElementById('build-coding-mode').disabled")
            for sel in ("#build-ideas", "#build-hints", "#build-describe", "#build-coding-save"):
                loc = page.locator(sel)
                loc.scroll_into_view_if_needed()
                box = loc.bounding_box()
                assert box and box["width"] > 8 and box["height"] > 8 and box["x"] >= 0 and box["x"] + box["width"] <= size[0] + 1, sel
                loc.click(trial=True)                                          # really hittable: nothing covers it
            assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")
        finally:
            browser.close()
