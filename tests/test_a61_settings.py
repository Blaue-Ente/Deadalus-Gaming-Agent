"""A6.1: the harness settings in Studio's Settings panel, driven with real clicks (no force) in a real browser."""
from __future__ import annotations

import json
import re

import pytest
from a0_support import a0_tmp  # noqa: F401
from fastapi.testclient import TestClient
from test_studio_phase2_workspace import _fulfill, _hit, studio_server  # noqa: F401  (fixture and helpers)

KEYS = ["mentor.enabled", "mentor.day_token_cap", "run_budget.usd", "writer_assist.mode", "evolution.enabled",
        "auto_apply.enabled", "play.max_sessions", "subq.shadow", "kv.backend", "brain.model"]
INTENTS = ["persona", "writer", "tools", "mentor", "critic", "planner", "navigator", "summary", "scout", "asset",
           "worker", "test"]


def _open(page, server, lang="en"):
    page.add_init_script(f"try {{ localStorage.setItem('dx.lang', '{lang}'); }} catch (e) {{}}")
    page.goto(server["url"] + "/?panel=settings", wait_until="domcontentloaded")
    page.wait_for_selector(".cfg-row[data-key]")
    page.wait_for_selector(".cfg-row[data-intent]")
    page.wait_for_selector("#settings-proof li")


# ------------------------------------------------------------------------------------------------ API
def test_engines_endpoint_reports_machine_and_proof_levels(a0_tmp, monkeypatch):  # noqa: F811
    from app.main import app

    monkeypatch.setenv("GODOT_BIN", str(a0_tmp / "nowhere"))
    body = TestClient(app).get("/api/v1/harness/engines").json()
    assert body["ok"] is True and body["ready"] is False and body["engine"]["ok"] is False
    assert [p["level"] for p in body["proof_levels"]] == [0, 1, 2, 3, 4, 5]
    by = {p["level"]: p for p in body["proof_levels"]}
    assert by[0]["reachable"] is True and by[3]["reachable"] is False and by[5]["reachable"] is False   # honest
    assert by[1]["reachable"] is False                    # L1 needs the engine, and there is none
    assert isinstance(by[2]["reachable"], bool)           # L2 follows whether a headless browser exists
    assert 1 <= body["max_sessions"] <= body["platform_cap"] <= 3
    assert set(body["vision"]) == {"provider", "model", "verified"}


def test_intents_list_survives_a_fresh_install_with_no_provider(a0_tmp, monkeypatch):  # noqa: F811
    """It used to answer 500 (gateway.resolve raised 'no provider') exactly when the owner opens Settings first."""
    from app.main import app

    for name in ("OPENROUTER_API_KEY", "ALLTOKEN_API_KEY", "NOVITA_API_KEY", "NVIDIA_API_KEY", "DAEDALUS_API_KEY",
                 "DAEDALUS_MODEL", "DAEDALUS_PROVIDER", "DAEDALUS_LOCAL_LLM"):
        monkeypatch.delenv(name, raising=False)
    res = TestClient(app).get("/api/v1/harness/intents")
    assert res.status_code == 200, res.text
    rows = {r["intent"]: r for r in res.json()["intents"]}
    assert sorted(rows) == sorted(INTENTS)
    assert rows["critic"]["resolved"]["source"] == "unconfigured" and rows["critic"]["selectable"] is True


def test_engines_endpoint_survives_a_failing_probe_and_shows_a_model_without_a_key(a0_tmp, monkeypatch):  # noqa: F811
    from app.main import app
    from daedalus import mentor_helm
    from daedalus.harness.play_worker import engines

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    mentor_helm.set_choice({"provider": "openrouter", "model": "x/vision-model"})      # chosen, but no key anywhere
    monkeypatch.setattr(engines, "tools_status", lambda: (_ for _ in ()).throw(RuntimeError("probe hung")))
    res = TestClient(app).get("/api/v1/harness/engines")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["engine"]["ok"] is False and body["ready"] is False and "probe hung" in body["engine"]["message"]
    assert body["vision"]["model"] == "x/vision-model" and body["vision"]["verified"] is None   # not 'no model set'


def test_the_mentor_row_carries_a_real_tag_only_for_a_custom_choice(a0_tmp):  # noqa: F811
    from app.main import app
    from daedalus import mentor_helm

    c = TestClient(app)
    mentor_helm.set_choice({"choice_id": "rules"})
    row = {r["intent"]: r for r in c.get("/api/v1/harness/intents").json()["intents"]}["mentor"]
    assert row["owner_choice"] == "rules" and row["owner_tag"] == ""          # an id is not a provider:model
    mentor_helm.set_choice({"provider": "openrouter", "model": "openai/gpt-4o"})
    row = {r["intent"]: r for r in c.get("/api/v1/harness/intents").json()["intents"]}["mentor"]
    assert row["owner_tag"] == "openrouter:openai/gpt-4o"


# ---------------------------------------------------------------------------------------------- browser
def test_every_setting_task_model_engine_and_proof_level_is_shown(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _open(page, studio_server)
        shown = page.eval_on_selector_all(".cfg-row[data-key]", "els => els.map(e => e.dataset.key)")
        assert sorted(shown) == sorted(KEYS)
        for key in KEYS:
            row = page.locator(f".cfg-row[data-key='{key}']")
            assert row.locator(".badge").count() == 1
        assert sorted(page.eval_on_selector_all(".cfg-row[data-intent]", "els => els.map(e => e.dataset.intent)")) == sorted(INTENTS)
        assert page.locator("#settings-proof li").count() == 6
        text = page.inner_text("#settings-engines")
        assert "Godot" in text and "Play sessions at once" in text
        assert page.locator("#settings-proof li[data-level='0']").get_attribute("data-reachable") == "1"
        assert page.locator("#settings-proof li[data-level='5']").get_attribute("data-reachable") == "0"
        # read-only rows say so and offer no Save
        assert page.locator(".cfg-row[data-key='kv.backend'] .cfg-save").count() == 0
        assert "Read-only" in page.inner_text(".cfg-row[data-key='kv.backend']")
        browser.close()


def test_a_setting_is_saved_with_real_clicks_and_survives_a_reload(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _open(page, studio_server)
        row = page.locator(".cfg-row[data-key='play.max_sessions']")
        assert row.locator(".badge").get_attribute("data-src") == "default"
        field = row.locator("input")
        field.fill("1")
        row.locator(".cfg-save").click()
        page.wait_for_function("() => document.getElementById('harness-status').textContent === 'Saved.'")
        assert page.locator(".cfg-row[data-key='play.max_sessions'] .badge").get_attribute("data-src") == "saved"
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector(".cfg-row[data-key]")
        row = page.locator(".cfg-row[data-key='play.max_sessions']")
        assert row.locator("input").input_value() == "1" and row.locator(".badge").get_attribute("data-src") == "saved"
        # an out-of-range value is refused with the reason, and nothing changes
        row.locator("input").fill("99")
        row.locator(".cfg-save").click()
        page.wait_for_function("() => document.querySelector(\".cfg-row[data-key='play.max_sessions'] .cfg-note:not([hidden])\") !== null")
        assert "between" in page.inner_text(".cfg-row[data-key='play.max_sessions'] .cfg-note")
        browser.close()


def test_a_locked_setting_is_disabled_and_a_409_is_explained(studio_server):
    from playwright.sync_api import sync_playwright

    def base(key, **kw):
        row = {"key": key, "title_key": "settings.runBudget", "group": "spend", "kind": "int", "env": "DAEDALUS_RUN_BUDGET",
               "env_locked": False, "env_value": None, "writable": True, "effective": 5, "persisted": None,
               "default": 0, "source": "default", "min": 0, "max": 100}
        row.update(kw)
        return row

    rows = [base("run_budget.usd", env_locked=True, env_value="20", writable=False, source="env", effective=20),
            base("writer_assist.mode", kind="enum", env="DAEDALUS_WRITER_ASSIST", choices=["off", "auto"], effective="off",
                 title_key="settings.writerAssist")]
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.route("**/api/v1/harness/config", lambda route: (
            _fulfill(route, 200, {"ok": True, "settings": rows}) if route.request.method == "GET"
            else _fulfill(route, 409, {"ok": False, "error": "env_locked", "field": "writer_assist.mode"})))
        _open(page, studio_server)
        locked = page.locator(".cfg-row[data-key='run_budget.usd']")
        assert locked.locator("input").is_disabled() and locked.locator(".cfg-save").count() == 0
        assert locked.locator(".badge").get_attribute("data-src") == "env"
        assert "DAEDALUS_RUN_BUDGET" in locked.inner_text() and locked.get_attribute("data-locked") == "1"
        free = page.locator(".cfg-row[data-key='writer_assist.mode']")
        free.locator("select").select_option("auto")
        free.locator(".cfg-save").click()                       # the server says it is locked after all (race)
        page.wait_for_function("() => document.querySelector(\".cfg-row[data-key='writer_assist.mode'] .cfg-note:not([hidden])\") !== null")
        assert "DAEDALUS_WRITER_ASSIST" in page.inner_text(".cfg-row[data-key='writer_assist.mode'] .cfg-note")
        browser.close()


def test_a_task_model_is_set_and_cleared_with_real_clicks(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _open(page, studio_server)
        row = page.locator(".cfg-row[data-intent='critic']")
        row.locator("input").fill("openrouter:test/critic-model")
        row.locator(".cfg-save").click()
        page.wait_for_function("() => document.getElementById('harness-status').textContent === 'Saved.'")
        assert "openrouter:test/critic-model" in page.inner_text(".cfg-row[data-intent='critic'] .cfg-name")
        page.locator(".cfg-row[data-intent='critic'] .cfg-clear").click()
        page.wait_for_function("() => !document.querySelector(\".cfg-row[data-intent='critic'] .cfg-name\").textContent.includes('critic-model')")
        # a malformed tag is refused with a reason
        row = page.locator(".cfg-row[data-intent='critic']")
        row.locator("input").fill("not-a-model-tag")
        row.locator(".cfg-save").click()
        page.wait_for_function("() => document.querySelector(\".cfg-row[data-intent='critic'] .cfg-note:not([hidden])\") !== null")
        assert "provider:model" in page.inner_text(".cfg-row[data-intent='critic'] .cfg-note")
        # tasks that only follow the brain have no input
        assert page.locator(".cfg-row[data-intent='scout'] input").count() == 0
        browser.close()


def test_bulgarian_has_no_untranslated_keys_and_no_english_headings(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _open(page, studio_server, lang="bg")
        text = page.inner_text("#panel-settings")
        low = text.lower()                                     # headings are upper-cased by the stylesheet
        assert "настройки на harness" in low and "модели по задача" in low and "нива на доказателство" in low
        assert not re.search(r"\b(studio|settings)\.[A-Za-z]", text), re.findall(r"\b(?:studio|settings)\.[A-Za-z.]+", text)
        assert "harness settings" not in low and "models per task" not in low and "proof levels" not in low
        browser.close()


@pytest.mark.parametrize("size", [(1280, 800), (1366, 768), (910, 640)])
def test_the_panel_fits_and_its_buttons_are_really_clickable(studio_server, size):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": size[0], "height": size[1]})
        _open(page, studio_server)
        over = page.evaluate("""() => {
          const p = document.getElementById('panel-settings');
          return {sw: p.scrollWidth, cw: p.clientWidth, docw: document.documentElement.scrollWidth, vw: window.innerWidth};
        }""")
        assert over["sw"] <= over["cw"] + 1 and over["docw"] <= over["vw"] + 1, over
        for selector in (".cfg-row[data-key='play.max_sessions'] .cfg-save", ".cfg-row[data-intent='writer'] .cfg-save"):
            page.locator(selector).scroll_into_view_if_needed()
            box = page.locator(selector).bounding_box()
            assert box and box["x"] >= 0 and box["x"] + box["width"] <= size[0]
            top = page.evaluate("""(sel) => { const el = document.querySelector(sel); const r = el.getBoundingClientRect();
              const t = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
              return t === el || el.contains(t); }""", selector)
            assert top, selector + " is covered by another element"
        browser.close()


def test_opening_settings_twice_does_not_duplicate_rows(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _open(page, studio_server)
        for _ in range(2):
            page.locator("#workspace-nav button[data-panel=chat]").click()
            page.locator("#workspace-nav button[data-panel=settings]").click()
        page.wait_for_selector(".cfg-row[data-key]")
        page.wait_for_timeout(800)
        assert page.locator(".cfg-row[data-key]").count() == len(KEYS)
        assert page.locator(".cfg-row[data-intent]").count() == len(INTENTS)
        browser.close()


def test_the_mentor_field_never_holds_an_internal_choice_id(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _open(page, studio_server)
        value = page.locator(".cfg-row[data-intent='mentor'] input").input_value()
        assert value == "" or ":" in value, value               # 'rules' / 'brain' would be rejected on Save
        browser.close()


def test_pinned_and_shared_tasks_are_labelled_honestly(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _open(page, studio_server)
        assert "Built-in default" in page.inner_text(".cfg-row[data-intent='scout'] .cfg-note")      # not 'follows the brain'
        assert "share one setting" in page.inner_text(".cfg-row[data-intent='persona'] .cfg-note")
        assert "share one setting" in page.inner_text(".cfg-row[data-intent='writer'] .cfg-note")
        page.locator(".cfg-row[data-intent='persona'] input").fill("openrouter:test/shared")
        page.locator(".cfg-row[data-intent='persona'] .cfg-save").click()
        page.wait_for_function("() => document.querySelector(\".cfg-row[data-intent='writer'] input\").value === 'openrouter:test/shared'")
        page.locator(".cfg-row[data-intent='writer'] .cfg-clear").click()
        page.wait_for_function("() => document.querySelector(\".cfg-row[data-intent='persona'] input\").value === ''")
        browser.close()


def test_an_empty_number_is_refused_in_the_page_and_other_edits_survive_a_save(studio_server):
    from playwright.sync_api import sync_playwright

    posts = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.on("request", lambda r: posts.append(r.url) if r.method == "POST" and "harness/config" in r.url else None)
        _open(page, studio_server)
        row = page.locator(".cfg-row[data-key='play.max_sessions']")
        row.locator("input").fill("")
        row.locator(".cfg-save").click()
        page.wait_for_function("() => document.querySelector(\".cfg-row[data-key='play.max_sessions'] .cfg-note:not([hidden])\") !== null")
        assert "whole number" in page.inner_text(".cfg-row[data-key='play.max_sessions'] .cfg-note") and posts == []
        # an unsaved edit in another row is kept when a different row is saved
        page.locator(".cfg-row[data-key='mentor.day_token_cap'] input").fill("12345")
        row.locator("input").fill("1")
        row.locator(".cfg-save").click()
        page.wait_for_function("() => document.getElementById('harness-status').textContent === 'Saved.'")
        assert page.locator(".cfg-row[data-key='mentor.day_token_cap'] input").input_value() == "12345"
        browser.close()


def test_one_broken_section_does_not_hide_the_others(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.route("**/api/v1/harness/intents", lambda route: _fulfill(route, 200, {"ok": True, "intents": [None]}))
        page.route("**/api/v1/harness/engines", lambda route: _fulfill(route, 500, {"detail": "engines down"}))
        page.add_init_script("try { localStorage.setItem('dx.lang', 'en'); } catch (e) {}")
        page.goto(studio_server["url"] + "/?panel=settings", wait_until="domcontentloaded")
        page.wait_for_selector(".cfg-row[data-key]")           # settings still painted
        page.wait_for_function("() => document.getElementById('harness-status').textContent.includes('engines down')")
        assert page.locator(".cfg-row[data-key]").count() == len(KEYS)
        browser.close()
