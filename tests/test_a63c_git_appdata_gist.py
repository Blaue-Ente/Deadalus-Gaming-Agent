"""A6.3c: git browse in the git menu, the per-app data editor in Preview, and a confirm-gated public gist for a skill."""
from __future__ import annotations

import json
import re

import pytest
from a0_support import a0_tmp  # noqa: F401
from fastapi.testclient import TestClient


# ------------------------------------------------------------------------------------------------ server
@pytest.fixture
def client(a0_tmp):  # noqa: F811
    from app.main import app

    return TestClient(app)


def test_a_gist_is_never_created_without_an_explicit_public_confirmation(client, monkeypatch):
    import httpx

    class Refuse:
        def __init__(self, *a, **k):
            raise AssertionError("GitHub must not be called without confirm_public")

    monkeypatch.setattr(httpx, "AsyncClient", Refuse)
    url = "/admin/daedalus/playground/api/skill-creator/github-gist"
    for body in ({"session_id": "s1"}, {"session_id": "s1", "confirm_public": "yes"}, {"session_id": "s1", "confirm_public": 1}):
        res = client.post(url, json=body)
        assert res.status_code == 409 and res.json() == {"ok": False, "error": "confirm_required"}
    assert client.post(url, json={"session_id": "nope", "confirm_public": True}).status_code == 400   # no generated skill


def test_an_unpublished_app_cannot_be_written(client):
    import asyncio

    from daedalus import playground as pg

    slug = asyncio.run(pg.create_project("Not Live", kind="static"))["slug"]
    assert client.post(f"/apps/{slug}/db/score", json={"value": 3}).json() == {"ok": False, "error": "not a published app"}


# ------------------------------------------------------------------------------------------------ UI (real clicks)
from test_a61c_maintenance import _dialogs  # noqa: E402
from test_a62a_review import _record, _until  # noqa: E402
from test_a62b_planner import _status_has, _uncovered  # noqa: E402
from test_a63a_build_controls import G, _browser, _game  # noqa: E402
from test_studio_phase2_workspace import _fulfill, studio_server  # noqa: E402,F401  (fixture and helpers)

PG = "/admin/daedalus/playground/api"
REPOS = {"ok": True, "count": 2, "repos": [
    {"full_name": "nik/space-game", "url": "https://github.com/nik/space-game", "clone_url": "https://github.com/nik/space-game.git",
     "private": False, "language": "JavaScript", "default_branch": "main", "compat": {"level": "high", "score": 88}},
    {"full_name": "nik/secret-tool", "url": "https://github.com/nik/secret-tool", "clone_url": "https://github.com/nik/secret-tool.git",
     "private": True, "language": "", "default_branch": "dev"}]}


def _open(page, server, panel="build", lang="en"):
    assert _game(server) == G
    page.add_init_script(f"try {{ localStorage.setItem('dx.lang', '{lang}'); }} catch (e) {{}}")
    page.goto(server["url"] + f"/?panel={panel}&slug={G}", wait_until="domcontentloaded")
    page.wait_for_function(f"() => window.dxStudio && window.dxStudio.getSlug() === '{G}'")


def _git(page):
    page.locator("#git-menu > summary").click()
    page.wait_for_selector("#git-browse button#git-connections")


def test_connections_say_what_is_configured_and_working(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        page.route(f"**{PG}/git/status", lambda r: _fulfill(r, 200, {"ok": True, "github": {"configured": True, "ok": True, "username": "nik"},
                                                                  "gitlab": {"configured": False}}))
        _open(page, studio_server)
        _git(page)
        page.locator("#git-connections").click()
        page.wait_for_selector("#git-browse-list .item")
        text = page.inner_text("#git-browse-list")
        assert "connected as nik" in text and "not configured (no token)" in text
        browser.close()


def test_github_repos_fill_the_remote_field_and_show_branches_without_cloning(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        posts = []
        page.on("request", lambda r: posts.append(r.url) if r.method == "POST" and "/playground/" in r.url else None)
        page.route(re.compile(r".*/github/repos\?limit=30$"), lambda r: _fulfill(r, 200, REPOS))
        page.route(f"**{PG}/github/repos/nik/space-game/branches",
                   lambda r: _fulfill(r, 200, {"ok": True, "branches": [{"name": "main"}, {"name": "feature/x"}], "tags": [{"name": "v1"}],
                                               "default_branch": "main"}))
        _open(page, studio_server)
        _git(page)
        page.locator("#git-gh-repos").click()
        page.wait_for_selector("#git-browse-list .item[data-repo='nik/secret-tool']")
        assert "compatibility high (88)" in page.inner_text("#git-browse-list .item[data-repo='nik/space-game']")
        assert "private" in page.inner_text("#git-browse-list .item[data-repo='nik/secret-tool']")
        page.locator("#git-browse-list .item[data-repo='nik/space-game'] button[data-gb='use']").click()
        assert page.locator("#git-url").input_value() == "https://github.com/nik/space-game.git"
        _status_has(page, "git-browse-status", "Nothing was cloned yet.")
        page.locator("#git-browse-list .item[data-repo='nik/space-game'] button[data-gb='branches']").click()
        _status_has(page, "git-browse-status", "nik/space-game: 2 branches · tags: v1")
        assert "default branch" in page.inner_text("#git-browse-list")
        assert posts == []                                                          # browsing sends nothing that changes anything
        browser.close()


def test_missing_tokens_and_a_missing_url_are_said_plainly(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        page.route(re.compile(r".*/gitlab/projects\?limit=30$"),
                   lambda r: _fulfill(r, 400, {"ok": False, "error": "GITLAB_TOKEN not configured", "projects": []}))
        compat = _record(page, f"**{PG}/github/compat-check",
                         [(200, {"ok": True, "compat": {"level": "medium", "score": 55, "kind": "vite", "warnings": ["no license"]}})])
        _open(page, studio_server)
        _git(page)
        page.locator("#git-gl-projects").click()
        _status_has(page, "git-browse-status", "GitLab is not configured.")
        page.locator("#git-url").fill("")
        page.locator("#git-compat").click()
        _status_has(page, "git-browse-status", "A remote URL is required.")
        assert compat == []
        page.locator("#git-url").fill("https://github.com/nik/space-game")
        page.locator("#git-compat").click()
        _status_has(page, "git-browse-status", "Compatibility medium (55), looks like vite.")
        assert compat == [{"url": "https://github.com/nik/space-game"}] and "no license" in page.inner_text("#git-browse-list")
        browser.close()


def _db(page, keys, values, writes):
    def handler(route):
        req = route.request
        path = req.url.split("?", 1)[0]
        key = path.split(f"/apps/{G}/db/", 1)[1]
        if req.method == "GET" and not key:
            return _fulfill(route, 200, {"ok": True, "keys": list(keys)})
        if req.method == "GET":
            return _fulfill(route, 200, {"ok": True, "value": values[key]})
        writes.append((req.method, key, json.loads(req.post_data) if req.post_data else None))
        if req.method == "POST":
            keys.append(key) if key not in keys else None
            values[key] = json.loads(req.post_data)["value"]
            return _fulfill(route, 200, {"ok": True})
        if not key:
            n = len(keys)
            keys.clear()
            return _fulfill(route, 200, {"ok": True, "deleted": n})
        keys.remove(key)
        return _fulfill(route, 200, {"ok": True})
    page.route(re.compile(rf".*/apps/{G}/db/.*"), handler)


def test_app_data_lists_views_saves_and_deletes_with_questions_first(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        keys, values, writes = ["best"], {"best": {"score": 42}}, []
        _db(page, keys, values, writes)
        _open(page, studio_server, panel="preview")
        page.locator("#app-data-load").click()
        _status_has(page, "app-data-status", "1 stored keys.")
        page.locator("#app-data-list .item[data-key='best'] button").first.click()
        _status_has(page, "app-data-status", "Showing best.")
        assert json.loads(page.locator("#app-data-value").input_value()) == {"score": 42}
        page.locator("#app-data-key").fill("level")
        page.locator("#app-data-value").fill("not json")
        page.locator("#app-data-save").click()
        _status_has(page, "app-data-status", "not valid JSON")
        assert writes == []
        page.locator("#app-data-value").fill('{"n": 3}')
        page.locator("#app-data-save").click()
        _status_has(page, "app-data-status", "Saved level.")
        assert writes == [("POST", "level", {"value": {"n": 3}})]
        page.wait_for_selector("#app-data-list .item[data-key='level']")
        asked = _dialogs(page, [False, True, False, True])
        page.locator("#app-data-list .item[data-key='best'] button").nth(1).click()
        assert _until(page, lambda: asked)
        page.wait_for_timeout(150)
        assert len(writes) == 1 and "The app loses it" in asked[0]
        page.locator("#app-data-list .item[data-key='best'] button").nth(1).click()
        _status_has(page, "app-data-status", "Deleted best.")
        page.locator("#app-data-clear").click()
        assert _until(page, lambda: len(asked) == 3)
        page.wait_for_timeout(150)
        assert [w[0] for w in writes] == ["POST", "DELETE"]
        page.locator("#app-data-clear").click()
        _status_has(page, "app-data-status", "Cleared 1 keys.")
        assert writes[-1] == ("DELETE", "", None)
        browser.close()


def test_an_unpublished_app_says_so_instead_of_failing_quietly(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        page.route(re.compile(rf".*/apps/{G}/db/x$"), lambda r: _fulfill(r, 404, {"ok": False, "error": "not a published app"}))
        _open(page, studio_server, panel="preview")
        page.locator("#app-data-key").fill("x")
        page.locator("#app-data-value").fill("1")
        page.locator("#app-data-save").click()
        _status_has(page, "app-data-status", "not a published app")
        assert page.locator("#app-data-status").get_attribute("data-state") == "error"
        browser.close()


def _generated_skill(page):
    view = {"ok": True, "session_id": "sess9", "phase": "generated", "platform": "generic", "idea": "maze review", "research_notes": "n",
            "tool_steps": [], "skill_name": "maze-review", "summary": "s", "clarifying_questions": [], "ready_for_generation": True,
            "validation_warnings": [], "files": {"maze-review/SKILL.md": "# Maze"}}
    page.route(f"**{PG}/skill-creator/start", lambda r: _fulfill(r, 200, view))


def test_the_gist_appears_only_for_a_generated_skill_and_asks_before_publishing_publicly(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        _generated_skill(page)
        gists = _record(page, f"**{PG}/skill-creator/github-gist",
                        [(200, {"ok": True, "gist_url": "https://gist.github.com/nik/abc", "gist_id": "abc"})])
        _open(page, studio_server, panel="review")
        page.wait_for_selector("#skill-start")
        assert page.locator("#skill-gist").is_hidden()
        page.locator("#skill-idea").fill("maze review")
        asked = _dialogs(page, [True, False, True])                                     # start, then the gist twice
        page.locator("#skill-start").click()
        page.wait_for_selector("#skill-gist:not([hidden])")
        page.locator("#skill-gist").click()
        assert _until(page, lambda: len(asked) == 2)
        page.wait_for_timeout(150)
        assert gists == [] and "PUBLIC GitHub gist" in asked[1]
        page.locator("#skill-gist").click()
        _status_has(page, "skill-status", "Published as a public gist: https://gist.github.com/nik/abc")
        assert gists == [{"session_id": "sess9", "confirm_public": True}]
        browser.close()


def test_a_gist_without_a_github_token_is_explained(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        _generated_skill(page)
        _record(page, f"**{PG}/skill-creator/github-gist",
                [(503, {"ok": False, "error": "GITHUB_TOKEN not configured — /admin/daedalus/connectors"})])
        _open(page, studio_server, panel="review")
        page.locator("#skill-idea").fill("maze review")
        _dialogs(page, [True, True])
        page.locator("#skill-start").click()
        page.wait_for_selector("#skill-gist:not([hidden])")
        page.locator("#skill-gist").click()
        _status_has(page, "skill-status", "GitHub is not configured.")
        browser.close()


def test_bulgarian_shows_the_new_controls_without_raw_keys(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw, (910, 640))
        _open(page, studio_server, panel="preview", lang="bg")
        page.wait_for_selector("#app-data-load")
        assert page.inner_text("#app-data-load") == "Покажи ключовете"
        text = page.text_content("body") or ""
        for expected in ("Данни на приложението", "Хранилища в GitHub", "Проекти в GitLab", "Публикувай като публичен gist"):
            assert expected in text, expected
        assert not re.search(r"\bstudio\.[A-Za-z]", page.inner_text("body"))
        browser.close()


@pytest.mark.parametrize("size", [(1280, 800), (1366, 768), (910, 640)])
def test_the_new_controls_fit_and_are_really_clickable(studio_server, size):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw, size)
        _open(page, studio_server, panel="preview")
        for selector in ("#app-data-load", "#app-data-clear", "#app-data-save"):
            page.locator(selector).evaluate("el => el.scrollIntoView({block: 'center'})")
            _uncovered(page, selector)
        over = page.evaluate("() => ({docw: document.documentElement.scrollWidth, vw: window.innerWidth})")
        assert over["docw"] <= over["vw"] + 1, over
        _git(page)
        for selector in ("#git-connections", "#git-gh-repos", "#git-gl-projects", "#git-compat"):
            page.locator(selector).evaluate("el => el.scrollIntoView({block: 'center'})")
            _uncovered(page, selector)
        browser.close()
