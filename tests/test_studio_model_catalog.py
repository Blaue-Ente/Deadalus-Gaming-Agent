"""Studio Settings catalog: draft save, refresh, and mentor datalist."""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import pytest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "app" / "static" / "studio.html").read_text(encoding="utf-8")
WORK = (ROOT / "app" / "static" / "studio-workspace.js").read_text(encoding="utf-8")
JS = (ROOT / "app" / "static" / "studio.js").read_text(encoding="utf-8")
I18N = (ROOT / "app" / "static" / "i18n.js").read_text(encoding="utf-8")
SECRET = "SECRET-KEY-SHOULD-NOT-LEAK"

KEYS = (
    "studio.catalogProvider",
    "studio.catalogModel",
    "studio.catalogBase",
    "studio.catalogUseCache",
    "studio.catalogSearch",
    "studio.catalogSearchPh",
    "studio.catalogList",
    "studio.catalogRefresh",
    "studio.catalogSave",
    "studio.catalogDraftDiff",
    "studio.catalogLastRefresh",
    "studio.catalogStale",
    "studio.catalogOutdated",
    "studio.catalogFailed",
    "studio.catalogManualOnly",
    "studio.catalogMissing",
    "studio.catalogPin",
    "studio.catalogPinConfirmed",
    "studio.catalogCached",
    "studio.catalogRefine",
    "studio.catalogListing",
    "studio.catalogEmpty",
    "studio.catalogMentorUnconfirmed",
    "studio.catalogEffective",
)


def _slice(src: str, start: str, end: str) -> str:
    at = src.index(start)
    return src[at:src.index(end, at + len(start))]


def test_catalog_strings_and_markup():
    for key in KEYS:
        assert I18N.count(f'"{key}":') == 2, key
    assert 'id="settings-catalog"' in HTML
    assert 'id="catalog-search"' in HTML
    assert 'id="catalog-list"' in HTML
    assert 'role="listbox"' in HTML
    assert 'id="catalog-refresh"' in HTML
    assert 'id="catalog-save"' in HTML
    block = _slice(WORK, "const catalogState", "function paintBrain")
    refresh = _slice(WORK, "async function refreshCatalogList", "async function saveCatalogDraft")
    save = _slice(WORK, "async function saveCatalogDraft", "function paintBrain")
    assert "innerHTML" not in block
    assert "/api/v1/llm/catalog/refresh" in refresh
    assert "llm-config" not in refresh
    assert "/admin/daedalus/llm-config" in save
    assert 'api_key: ""' in save
    assert "catalog/refresh" not in save
    assert "enrichMentorFromCatalog" in JS
    assert "setMentorCustom" in JS


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="module")
def studio_server(tmp_path_factory):
    pytest.importorskip("playwright")
    data = tmp_path_factory.mktemp("catalog-data")
    port = _free_port()
    env = {k: v for k, v in __import__("os").environ.items()}
    env.update({
        "DAEDALUS_STANDALONE": "1",
        "DAEDALUS_DATA": str(data),
        "DAEDALUS_PLAYGROUND": str(data / "playground"),
        "DAEDALUS_OFFLINE": "1",
        "DAEDALUS_CATALOG_NETWORK": "0",
    })
    for key in (
        "OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "NOVITA_API_KEY",
        "ALLTOKEN_API_KEY", "NVIDIA_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY",
        "POOLSIDE_API", "DAEDALUS_API_KEY", "GITHUB_TOKEN", "DAEDALUS_MENTOR_MODEL",
    ):
        env.pop(key, None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with __import__("urllib.request").request.urlopen(url + "/health", timeout=1) as res:
                if res.status == 200:
                    break
        except Exception:
            time.sleep(0.2)
    else:
        out = proc.stdout.read().decode("utf-8", "replace") if proc.stdout else ""
        proc.kill()
        pytest.fail("studio server did not start\n" + out[-2000:])
    yield {"url": url, "proc": proc}
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _models(ids):
    return [{"id": item, "name": item} for item in ids]


def _catalog(provider, model, ids, **flags):
    success = None
    if ids is not None:
        success = {
            "fetched_at": flags.get("fetched_at", "2026-11-07T00:00:00+00:00"),
            "models": _models(ids),
            "count": len(ids),
        }
    warnings = flags.get("warnings")
    if warnings is None and ids is not None and model not in ids:
        warnings = [{"code": "missing_from_cache", "target": "brain", "model": model}]
    return {
        "ok": True,
        "mode": flags.get("mode", "catalog"),
        "reason": flags.get("reason", ""),
        "scope": {
            "provider": provider,
            "base_url": "https://openrouter.ai/v1",
            "key": provider + ":https://openrouter.ai/v1",
        },
        "last_success": success,
        "last_attempt": {
            "fetched_at": "2026-11-07T00:00:00+00:00",
            "ok": flags.get("attempt_ok", True),
            "error": "",
            "reason": flags.get("reason", ""),
        },
        "stale_hint": flags.get("stale_hint", False),
        "catalog_outdated": flags.get("catalog_outdated", False),
        "pins": flags.get("pins", [{
            "id": "repo/pin",
            "label": "Repo pin",
            "provider": provider,
            "confirmed": False,
        }]),
        "effective_selection": {
            "provider": provider,
            "model": model,
            "base_url": "https://openrouter.ai/v1",
            "source": "db",
        },
        "mentor_selection": {"id": "custom", "provider": provider, "model": "mentor/missing"},
        "warnings": warnings or [],
        "providers": [
            {"id": "openrouter", "manual_only": False, "show_base": True, "show_cache": False},
            {"id": "ollama", "manual_only": False, "show_base": False, "show_cache": False},
            {"id": "nvidia", "manual_only": True, "show_base": False, "show_cache": False},
            {"id": "anthropic", "manual_only": True, "show_base": False, "show_cache": True},
        ],
    }


def _status(provider, model):
    return {"ok": True, "provider": provider, "model": model, "has_key": False, "source": "db"}


def _fulfill(route, payload):
    route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))


def _open(page, server, state):
    def handle(route):
        req = route.request
        host = urlparse(req.url).hostname
        if host not in {"127.0.0.1", "localhost"}:
            state["external"].append(req.url)
            route.abort()
            return
        path = req.url.split("?", 1)[0]
        if path.endswith("/api/v1/llm/catalog/refresh") and req.method == "POST":
            state["refresh"].append(req.post_data or "")
            if state.get("hold_refresh"):
                state["held"].append(route)
                return
            body = state["refresh_body"] if state.get("refresh_body") is not None else state["catalog"]
            _fulfill(route, body)
            return
        if path.endswith("/api/v1/llm/catalog") and req.method == "GET":
            payload = dict(state["catalog"])
            if state.get("leak"):
                payload["api_key"] = SECRET
            _fulfill(route, payload)
            return
        if path.endswith("/api/v1/llm/status") and req.method == "GET":
            _fulfill(route, state["status"])
            return
        if path.endswith("/admin/daedalus/llm-config") and req.method == "POST":
            state["saves"].append(req.post_data or "")
            _fulfill(route, {"ok": True, "provider": "openrouter", "model": "saved"})
            return
        if path.endswith("/api/v1/daedalus/mentor") and req.method == "POST":
            state["mentor_posts"].append(req.post_data or "")
            _fulfill(route, {"ok": True})
            return
        if path.endswith("/api/v1/daedalus/mentor") and req.method == "GET" and state.get("mentor"):
            _fulfill(route, state["mentor"])
            return
        route.continue_()

    page.route("**/*", handle)
    page.goto(server["url"] + "/", wait_until="domcontentloaded")
    page.locator("#settings-link").click()
    page.wait_for_selector("#panel-settings:not([hidden])")
    page.wait_for_function(
        "() => (document.getElementById('catalog-effective').textContent || '').length > 0"
    )


def _click(page, selector):
    loc = page.locator(selector)
    loc.scroll_into_view_if_needed()
    loc.click()


def test_provider_switch_keeps_effective_cache(studio_server):
    from playwright.sync_api import sync_playwright
    state = {
        "catalog": _catalog("openrouter", "saved/missing", ["beta/two"]),
        "status": _status("openrouter", "saved/missing"),
        "refresh": [], "saves": [], "mentor_posts": [], "external": [], "held": [],
        "refresh_body": None, "hold_refresh": False,
    }
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        try:
            _open(page, studio_server, state)
            page.locator("#catalog-search").fill("beta")
            page.wait_for_function("() => document.getElementById('catalog-list').innerText.includes('beta/two')")
            page.locator("#catalog-provider").select_option("ollama")
            assert "not saved" in page.locator("#catalog-draft-note").inner_text().lower() or "не е запазен" in page.locator("#catalog-draft-note").inner_text()
            assert "beta/two" in page.locator("#catalog-list").inner_text()
            _click(page, "#catalog-refresh")
            page.wait_for_function("() => document.getElementById('catalog-list').dataset.done === '1'")
            page.evaluate("() => 1")
            assert state["refresh"] == ["{}"]
            assert state["saves"] == []
            assert state["external"] == []
        finally:
            browser.close()


def test_out_of_order_refresh_keeps_the_newer_list(studio_server):
    from playwright.sync_api import sync_playwright
    state = {
        "catalog": _catalog("openrouter", "saved/missing", ["m-seed"]),
        "status": _status("openrouter", "saved/missing"),
        "refresh": [], "saves": [], "mentor_posts": [], "external": [], "held": [],
        "refresh_body": None, "hold_refresh": True,
    }
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        try:
            _open(page, studio_server, state)
            page.locator("#catalog-search").fill("m")
            _click(page, "#catalog-refresh")
            deadline = time.time() + 5
            while time.time() < deadline and len(state["held"]) < 1:
                page.evaluate("() => 1")
            assert len(state["held"]) == 1
            _click(page, "#catalog-refresh")
            deadline = time.time() + 5
            while time.time() < deadline and len(state["held"]) < 2:
                page.evaluate("() => 1")
            assert len(state["held"]) == 2
            _fulfill(state["held"][1], _catalog("openrouter", "saved/missing", ["m-new"]))
            page.wait_for_function("() => document.getElementById('catalog-list').innerText.includes('m-new')")
            _fulfill(state["held"][0], _catalog("openrouter", "saved/missing", ["m-old"]))
            page.wait_for_function("() => document.getElementById('catalog-list').dataset.done === '2'")
            page.evaluate("() => 1")
            text = page.locator("#catalog-list").inner_text()
            assert "m-new" in text
            assert "m-old" not in text
            assert page.locator("#catalog-list").get_attribute("data-paint") == "m-new"
            assert state["saves"] == []
            assert state["external"] == []
        finally:
            for route in state["held"]:
                try:
                    route.abort()
                except Exception:
                    pass
            browser.close()


def test_missing_model_warns_without_replacing_the_input(studio_server):
    from playwright.sync_api import sync_playwright
    state = {
        "catalog": _catalog("openrouter", "saved/missing", ["other/one"]),
        "status": _status("openrouter", "saved/missing"),
        "refresh": [], "saves": [], "mentor_posts": [], "external": [], "held": [],
        "refresh_body": None, "hold_refresh": False,
    }
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        try:
            _open(page, studio_server, state)
            page.wait_for_function("() => !document.getElementById('catalog-warning').hidden")
            assert page.locator("#catalog-model").input_value() == "saved/missing"
            assert "saved/missing" in page.locator("#catalog-warning").inner_text()
            assert page.locator("#catalog-model").input_value() != "other/one"
            assert state["saves"] == []
            assert state["refresh"] == []
        finally:
            browser.close()


def test_failed_refresh_keeps_the_visible_cache(studio_server):
    from playwright.sync_api import sync_playwright
    state = {
        "catalog": _catalog("openrouter", "kept", ["kept"], warnings=[]),
        "status": _status("openrouter", "kept"),
        "refresh": [], "saves": [], "mentor_posts": [], "external": [], "held": [],
        "refresh_body": _catalog(
            "openrouter", "kept", ["kept"],
            attempt_ok=False, catalog_outdated=True, reason="http_error", warnings=[],
        ),
        "hold_refresh": False,
    }
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        try:
            _open(page, studio_server, state)
            page.locator("#catalog-search").fill("kept")
            page.wait_for_function("() => document.getElementById('catalog-list').innerText.includes('kept')")
            _click(page, "#catalog-refresh")
            page.wait_for_function("() => document.getElementById('catalog-meta').innerText.includes('outdated')")
            assert "kept" in page.locator("#catalog-list").inner_text()
            assert state["saves"] == []
            assert state["external"] == []
        finally:
            browser.close()


def test_manual_id_save_does_not_refresh(studio_server):
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright
    state = {
        "catalog": _catalog("openrouter", "saved/missing", ["other/one"]),
        "status": _status("openrouter", "saved/missing"),
        "refresh": [], "saves": [], "mentor_posts": [], "external": [], "held": [],
        "refresh_body": None, "hold_refresh": False,
    }
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        try:
            _open(page, studio_server, state)
            page.locator("#catalog-model").fill("owner/typed-id")
            with page.expect_request(lambda r: r.method == "POST" and "/admin/daedalus/llm-config" in r.url.split("?", 1)[0]) as caught:
                _click(page, "#catalog-save")
            page.evaluate("() => 1")
            body = json.loads(caught.value.post_data)
            assert body["model"] == "owner/typed-id"
            assert body["provider"] == "openrouter"
            assert body["api_key"] == ""
            assert state["refresh"] == []
            try:
                with page.expect_request(lambda r: r.method == "POST" and "/catalog/refresh" in r.url, timeout=1000):
                    page.evaluate("() => 1")
                raise AssertionError("refresh posted")
            except PlaywrightTimeout:
                pass
            assert state["mentor_posts"] == []
            assert state["external"] == []
        finally:
            browser.close()


def test_manual_only_copy_still_allows_a_typed_save(studio_server):
    from playwright.sync_api import sync_playwright
    state = {
        "catalog": _catalog(
            "nvidia", "saved/missing", None,
            mode="manual_only", reason="unsupported_provider", warnings=[],
        ),
        "status": _status("nvidia", "saved/missing"),
        "refresh": [], "saves": [], "mentor_posts": [], "external": [], "held": [],
        "refresh_body": _catalog(
            "nvidia", "saved/missing", None,
            mode="manual_only", reason="unsupported_provider", warnings=[],
        ),
        "hold_refresh": False,
    }
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        try:
            _open(page, studio_server, state)
            page.wait_for_function("() => document.getElementById('catalog-manual').textContent.includes('Manual model id only')")
            _click(page, "#catalog-refresh")
            page.wait_for_function("() => document.getElementById('catalog-list').dataset.done === '1'")
            assert "Manual model id only" in page.locator("#catalog-manual").inner_text()
            page.locator("#catalog-model").fill("owner/nvidia-id")
            with page.expect_request(lambda r: r.method == "POST" and "/admin/daedalus/llm-config" in r.url.split("?", 1)[0]) as caught:
                _click(page, "#catalog-save")
            page.evaluate("() => 1")
            assert state["refresh"] == ["{}"]
            saved = json.loads(caught.value.post_data)
            assert saved["model"] == "owner/nvidia-id"
            assert saved["provider"] == "nvidia"
            assert saved["api_key"] == ""
            assert state["external"] == []
        finally:
            browser.close()


def test_secret_field_is_not_rendered_and_search_caps_rows(studio_server):
    from playwright.sync_api import sync_playwright
    ids = [f"m-{i}" for i in range(101)]
    state = {
        "catalog": _catalog("openrouter", "m-0", ids, warnings=[]),
        "status": _status("openrouter", "m-0"),
        "refresh": [], "saves": [], "mentor_posts": [], "external": [], "held": [],
        "refresh_body": None, "hold_refresh": False, "leak": True,
    }
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        try:
            _open(page, studio_server, state)
            assert SECRET not in page.locator("#panel-settings").inner_text()
            page.locator("#catalog-search").fill("")
            assert page.locator("#catalog-list [role=option]").count() == 0
            assert not page.locator("#catalog-refine").is_hidden()
            page.locator("#catalog-search").fill("m-")
            page.wait_for_function("() => document.querySelectorAll('#catalog-list [role=option]').length === 100")
            assert not page.locator("#catalog-refine").is_hidden()
            page.locator("#catalog-search").fill("m-100")
            page.wait_for_function("() => document.querySelectorAll('#catalog-list [role=option]').length === 1")
            assert page.locator("#catalog-refine").is_hidden()
            assert SECRET not in page.locator("#panel-settings").inner_text()
            assert state["external"] == []
        finally:
            browser.close()


def test_bulgarian_refresh_label(studio_server):
    from playwright.sync_api import sync_playwright
    state = {
        "catalog": _catalog("openrouter", "saved/missing", ["beta/two"]),
        "status": _status("openrouter", "saved/missing"),
        "refresh": [], "saves": [], "mentor_posts": [], "external": [], "held": [],
        "refresh_body": None, "hold_refresh": False,
    }
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        try:
            _open(page, studio_server, state)
            _click(page, ".dx-lang button[data-lang=bg]")
            page.wait_for_function("() => document.getElementById('catalog-refresh').textContent.includes('Обнови списъка')")
            assert "Модел в списъка" in page.locator("#panel-settings").inner_text()
        finally:
            browser.close()


def test_mentor_datalist_uses_catalog_without_a_mentor_post(studio_server):
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright
    state = {
        "catalog": _catalog("openrouter", "pinned-model", ["cached-live-id"], warnings=[]),
        "status": _status("openrouter", "pinned-model"),
        "mentor": {
            "ok": True,
            "enabled": False,
            "llm": {
                "catalog": [{
                    "id": "pin-1",
                    "label": "Pinned",
                    "group": "recommended",
                    "provider": "openrouter",
                    "model": "pinned-model",
                    "rank": 1,
                }],
                "presets": [],
                "choice": {"id": "pin-1", "provider": "openrouter", "model": "pinned-model"},
                "spend": {},
            },
        },
        "refresh": [], "saves": [], "mentor_posts": [], "external": [], "held": [],
        "refresh_body": None, "hold_refresh": False,
    }
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        try:
            _open(page, studio_server, state)
            page.wait_for_function(
                "() => Array.from(document.getElementById('mentor-model-list').options).some((opt) => opt.value === 'cached-live-id')"
            )
            page.wait_for_function(
                "() => Array.from(document.querySelectorAll('#mentor-choice option')).some((opt) => opt.dataset.model === 'pinned-model' && opt.dataset.catalogState === 'unconfirmed')"
            )
            page.evaluate("() => 1")
            assert state["mentor_posts"] == []
            try:
                with page.expect_request(lambda r: r.method == "POST" and "/api/v1/daedalus/mentor" in r.url, timeout=1000):
                    page.evaluate("() => 1")
                raise AssertionError("mentor posted")
            except PlaywrightTimeout:
                pass
            assert state["external"] == []
        finally:
            browser.close()
