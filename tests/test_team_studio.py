"""Gap 3b (owner, 2026-11-12, plan approved): the Team section in Studio, with real clicks in a real browser."""
from __future__ import annotations

import pytest
from test_studio_phase2_workspace import _api, _hit, studio_server  # noqa: F401  (fixture and helpers)


def _page(p, server, *, token: str = "", lang: str = "en"):
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.add_init_script(f"try {{ localStorage.setItem('dx.lang', '{lang}'); }} catch (e) {{}}")
    if token:
        page.add_init_script(f"try {{ localStorage.setItem('dx.ownerToken', '{token}'); }} catch (e) {{}}")
    page.goto(server["url"] + "/?panel=settings&page=team", wait_until="domcontentloaded")
    return browser, page


def test_the_owner_invites_and_the_sign_in_is_shown_once(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page = _page(p, studio_server)
        try:
            page.wait_for_selector("#team-owner:not([hidden])")
            assert page.locator("#team-chip").is_hidden()                      # the owner wears no badge
            page.fill("#team-name", "Dee")
            page.select_option("#team-role", "designer")
            _hit(page, "#team-invite button[type=submit]")
            page.wait_for_selector("#team-token:not([hidden])")
            token = page.locator("#team-token-value").inner_text()
            assert token.startswith("dt_") and "run code on this machine" in page.locator("#team-trust").inner_text()
            page.wait_for_selector(".cfg-row[data-id^='member-']")
            assert "Dee" in page.locator("#team-members").inner_text()
            _hit(page, "#settings-link")
            assert page.locator("#team-token").is_hidden()                      # shown once
        finally:
            browser.close()
    members = _api(studio_server, "GET", "/api/v1/team/members")["members"]
    assert [m["name"] for m in members].count("Dee") >= 1 and "dt_" not in str(members)


def test_a_designer_sees_their_badge_and_not_the_owners_settings(studio_server):
    from playwright.sync_api import sync_playwright

    made = _api(studio_server, "POST", "/api/v1/team/members", {"name": "Bea", "role": "designer"})
    with sync_playwright() as p:
        browser, page = _page(p, studio_server, token=made["token"], lang="bg")
        try:
            page.wait_for_selector("#team-chip:not([hidden])")
            assert page.locator("#team-chip").inner_text() == "Bea · дизайнер"
            page.wait_for_selector("#team-not-owner:not([hidden])")
            assert page.locator("#team-owner").is_hidden()
            assert page.locator("#settings-config").is_hidden() and page.locator("#settings-secret").is_hidden()
            msg = page.evaluate("""async () => {
                try { await window.dxStudio.api('/api/v1/team/members'); return 'no error'; } catch (e) { return e.message; }
            }""")
            assert msg == "Това е за собственик: влязъл си като дизайнер."
        finally:
            browser.close()


def test_a_paused_member_keeps_their_sign_in_and_an_unknown_one_is_asked_again(studio_server):
    """Review 2026-11-12: a team sign-in is shown once; forgetting it on any refusal meant a new invite."""
    from playwright.sync_api import sync_playwright

    made = _api(studio_server, "POST", "/api/v1/team/members", {"name": "Cid", "role": "tester"})
    mid = made["member"]["id"]
    _api(studio_server, "POST", f"/api/v1/team/members/{mid}", {"disabled": True})
    with sync_playwright() as p:
        browser, page = _page(p, studio_server, token=made["token"])
        try:
            status = page.evaluate("async () => (await fetch(new Request('/api/v1/team/me'))).status")
            assert status == 403 and page.evaluate("localStorage.getItem('dx.ownerToken')") == made["token"]
            _api(studio_server, "POST", f"/api/v1/team/members/{mid}", {"disabled": False})
            assert page.evaluate("async () => (await fetch('/api/v1/team/me')).status") == 200   # works again
        finally:
            browser.close()
        browser, page = _page(p, studio_server, token="dt_never-issued")
        try:
            page.evaluate("async () => (await fetch(new Request('/api/v1/team/me'))).status")
            assert page.evaluate("localStorage.getItem('dx.ownerToken')") != "dt_never-issued"
        finally:
            browser.close()


def test_the_sign_in_shows_once_whichever_way_settings_is_reopened(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser, page = _page(p, studio_server)
        try:
            page.wait_for_selector("#team-owner:not([hidden])")
            page.fill("#team-name", "Eve")
            _hit(page, "#team-invite button[type=submit]")
            page.wait_for_selector("#team-token:not([hidden])")
            _hit(page, "button[data-panel=chat]")
            _hit(page, "button[data-panel=settings]")
            page.wait_for_selector("#team-owner:not([hidden])")
            assert page.locator("#team-token").is_hidden() and page.locator("#team-token-value").inner_text() == ""
        finally:
            browser.close()


def test_the_sign_in_prompt_speaks_the_chosen_language(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        asked: list[str] = []
        page.add_init_script("try { localStorage.setItem('dx.lang', 'bg'); } catch (e) {}")
        page.route("**/api/v1/auth/bootstrap", lambda r: r.fulfill(status=403, body="{}"))   # as if remote
        page.on("dialog", lambda d: (asked.append(d.message), d.dismiss()))   # a prompt blocks the page: answer it
        try:
            page.goto(studio_server["url"] + "/", wait_until="domcontentloaded")
            for _ in range(150):                    # wait for the prompt itself (up to 15 s), not a fixed sleep
                if asked:
                    break
                page.wait_for_timeout(100)
        finally:
            browser.close()
    assert asked and asked[0].startswith("Вход:") and "dt_" in asked[0]


def test_a_designer_saves_their_own_key_and_the_owner_shares_one(studio_server):
    """Gap 3c: keys in the Team section, write-only, with real clicks."""
    from playwright.sync_api import sync_playwright

    made = _api(studio_server, "POST", "/api/v1/team/members", {"name": "Kay", "role": "designer"})
    with sync_playwright() as p:
        browser, page = _page(p, studio_server, token=made["token"])
        try:
            page.wait_for_selector("#team-mykeys:not([hidden])")
            page.wait_for_selector("#team-key-name option[value=OPENROUTER_API_KEY]", state="attached")
            page.select_option("#team-key-name", "OPENROUTER_API_KEY")
            page.fill("#team-key-value", "kay-secret-value")
            page.locator("#team-key-form button[type=submit]").scroll_into_view_if_needed()
            _hit(page, "#team-key-form button[type=submit]")
            page.wait_for_function("() => document.querySelector('#team-mykeys-state').textContent"
                                   ".includes('OPENROUTER_API_KEY')")
            assert page.locator("#team-key-value").input_value() == ""
            assert "kay-secret-value" not in page.content()
        finally:
            browser.close()
        browser, page = _page(p, studio_server)
        try:
            page.wait_for_selector("#team-share-list input[data-key=OPENROUTER_API_KEY]")
            page.locator("#team-share-list input[data-key=OPENROUTER_API_KEY]").scroll_into_view_if_needed()
            _hit(page, "#team-share-list input[data-key=OPENROUTER_API_KEY]")
            page.wait_for_function("() => document.querySelector('#team-status').textContent.length > 0")
        finally:
            browser.close()
    assert "OPENROUTER_API_KEY" in _api(studio_server, "GET", "/api/v1/team/sharing")["shared"]
