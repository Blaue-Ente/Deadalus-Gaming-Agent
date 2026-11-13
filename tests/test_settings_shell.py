"""PR4: the Settings shell. Seven short pages behind a sub-nav, one visible at a time, deep-linkable."""
from __future__ import annotations

import pytest
from a0_support import a0_tmp  # noqa: F401
from test_studio_phase2_workspace import studio_server  # noqa: F401  (fixture)

PAGES = ["models", "local", "keys", "projects", "permissions", "team", "advanced"]
MAX_PAGE_HEIGHT = 1400


def _open(page, server, query="panel=settings"):
    page.add_init_script("try { localStorage.setItem('dx.lang', 'en'); } catch (e) {}")
    page.goto(server["url"] + "/?" + query, wait_until="domcontentloaded")
    page.wait_for_selector("#settings-nav", state="attached")
    page.wait_for_selector(".cfg-row[data-key]", state="attached")
    page.wait_for_selector(".cfg-row[data-intent]", state="attached")
    page.wait_for_selector("#settings-proof li", state="attached")


def _visible_pages(page):
    return page.evaluate("() => [...document.querySelectorAll('.settings-page')].filter(e => !e.hidden && e.offsetParent !== null).map(e => e.dataset.page)")


def test_config_view_rows_carry_a_page(a0_tmp):  # noqa: F811
    from daedalus.harness import config_view

    rows = {r["key"]: r["page"] for r in (config_view.view_one(s) for s in config_view.SETTINGS)}
    for key in ("mentor.enabled", "mentor.day_token_cap", "run_budget.usd", "writer_assist.mode", "fallback.mode",
                "fallback.triggers", "brain.model"):
        assert rows[key] == "models", key
    assert rows["play.max_sessions"] == "projects"
    for key in ("evolution.enabled", "auto_apply.enabled", "subq.shadow", "kv.backend"):
        assert rows[key] == "advanced", key
    assert set(rows.values()) <= {"models", "projects", "advanced"}


def test_every_page_is_reachable_alone_and_short_at_1366(studio_server):  # noqa: F811
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1366, "height": 768})
        try:
            _open(page, studio_server)
            assert page.locator("#settings-nav button").count() == 7
            assert _visible_pages(page) == ["models"]
            too_tall = {}
            for name in PAGES:
                page.locator(f"#settings-nav button[data-page={name}]").click()
                assert _visible_pages(page) == [name], name
                assert page.locator(f"#settings-nav button[data-page={name}]").get_attribute("aria-current") == "page"
                assert page.locator("#settings-nav button[aria-current=page]").count() == 1
                assert f"page={name}" in page.url or name == "models"
                height = page.evaluate("() => document.querySelector('#panel-settings .settings-shell').scrollHeight")
                if height > MAX_PAGE_HEIGHT:
                    too_tall[name] = height
            assert not too_tall, f"Settings pages over {MAX_PAGE_HEIGHT}px: {too_tall}"
            # the page that holds a row is the page that shows it
            page.locator("#settings-nav button[data-page=advanced]").click()
            assert page.locator("#settings-config .cfg-row[data-key='evolution.enabled']").is_visible()
            page.locator("#settings-nav button[data-page=models]").click()
            assert page.locator("#settings-config-models .cfg-row[data-key='mentor.enabled']").is_visible()
            page.locator("#settings-nav button[data-page=projects]").click()
            assert page.locator("#settings-config-projects .cfg-row[data-key='play.max_sessions']").is_visible()
        finally:
            browser.close()


def test_deep_link_opens_the_named_page(studio_server):  # noqa: F811
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1366, "height": 768})
        try:
            _open(page, studio_server, "panel=settings&page=team")
            assert _visible_pages(page) == ["team"]
            assert page.locator("#settings-nav button[data-page=team]").get_attribute("aria-current") == "page"
            page.locator("#settings-nav button[data-page=keys]").click()
            assert "page=keys" in page.url and "panel=settings" in page.url
            page.reload(wait_until="domcontentloaded")
            page.wait_for_selector("#settings-nav", state="attached")
            assert _visible_pages(page) == ["keys"]
        finally:
            browser.close()


def test_the_page_switcher_is_usable_on_a_phone_in_landscape(studio_server):  # noqa: F811
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 780, "height": 340})
        try:
            _open(page, studio_server)
            assert page.locator("#settings-nav-select").is_visible()
            assert page.locator("#settings-nav").is_hidden()
            assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth + 1")
            page.locator("#settings-nav-select").select_option("keys")
            assert _visible_pages(page) == ["keys"] and "page=keys" in page.url
            page.locator("#settings-nav-select").select_option("advanced")
            assert _visible_pages(page) == ["advanced"]
            assert page.locator("#settings-nav-select").input_value() == "advanced"
        finally:
            browser.close()
