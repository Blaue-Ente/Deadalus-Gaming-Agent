"""PR5 model manager: GET /api/v1/harness/models, POST /api/v1/harness/brain (API) and the Models page (browser)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

import pytest
from a0_support import a0_tmp  # noqa: F401
from fastapi.testclient import TestClient
from test_studio_phase1_header import ROOT, _free_port
from test_studio_phase2_workspace import studio_server  # noqa: F401  (fixture)

SECRET = "sk-test-SECRET-VALUE-123456"
KEY_ENVS = ("OPENROUTER_API_KEY", "ALLTOKEN_API_KEY", "NOVITA_API_KEY", "NVIDIA_API_KEY", "DAEDALUS_API_KEY",
            "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY", "POOLSIDE_API",
            "OMNIROUTE_API_KEY", "DAEDALUS_MODEL", "DAEDALUS_PROVIDER", "DAEDALUS_LOCAL_LLM")


@pytest.fixture
def fresh(a0_tmp, monkeypatch):  # noqa: F811
    from daedalus import llm_wardrobe

    for name in KEY_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(llm_wardrobe, "_path", lambda: a0_tmp / "wardrobe.json")
    from app.main import app

    return TestClient(app)


def test_models_is_200_and_not_configured_when_nothing_is_set(fresh):
    res = fresh.get("/api/v1/harness/models")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["configured"] is False and body["brain"]["provider"] == "" and body["brain"]["locked"] is False
    ids = {p["id"]: p for p in body["providers"]}
    assert ids["openrouter"]["kind"] == "cloud" and ids["openrouter"]["has_key"] is False
    assert ids["ollama"]["kind"] == "local" and ids["ollama"]["has_key"] is True
    assert body["catalog"]["ollama"] if "ollama" in body["catalog"] else True
    assert any(m["id"] == "deepseek/deepseek-v3.2" for m in body["catalog"]["openrouter"])
    assert {t["intent"] for t in body["tasks"]} >= {"writer", "critic", "planner", "mentor"}


def test_choosing_a_cloud_model_is_saved_and_beats_a_leftover_local_wardrobe(fresh, monkeypatch):
    from daedalus import llm_config, llm_wardrobe

    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)
    llm_wardrobe.save({"backend": "ollama", "active": "qwen2.5-coder:7b", "offline": False, "roles": {}})
    res = fresh.post("/api/v1/harness/brain", json={"provider": "openrouter", "model": "x/y"})
    assert res.status_code == 200, res.text
    brain = fresh.get("/api/v1/harness/models").json()
    assert brain["configured"] is True
    assert brain["brain"]["provider"] == "openrouter" and brain["brain"]["model"] == "x/y"
    assert brain["brain"]["has_key"] is True and brain["brain"]["source_kind"] == "saved"
    cfg = llm_config.get_brain_config()                       # M8: the wardrobe no longer overrides the pick
    assert cfg["provider"] == "openrouter" and cfg["model"] == "x/y"
    assert llm_wardrobe.load()["backend"] == "cloud"
    assert SECRET not in res.text and SECRET not in json.dumps(brain)


def test_choosing_a_local_model_sets_the_wardrobe(fresh):
    from daedalus import llm_wardrobe

    res = fresh.post("/api/v1/harness/brain", json={"provider": "ollama", "model": "qwen2.5-coder:7b"})
    assert res.status_code == 200, res.text
    data = llm_wardrobe.load()
    assert data["backend"] == "ollama" and data["active"] == "qwen2.5-coder:7b"
    assert res.json()["brain"]["provider"] == "ollama" and res.json()["brain"]["source_kind"] == "local"


def test_choosing_is_refused_while_the_server_pins_the_model(fresh, monkeypatch):
    monkeypatch.setenv("DAEDALUS_MODEL", "pinned/model")
    res = fresh.post("/api/v1/harness/brain", json={"provider": "openrouter", "model": "x/y"})
    assert res.status_code == 409
    assert res.json() == {"ok": False, "error": "env_locked", "field": "brain", "env": "DAEDALUS_MODEL"}
    view = fresh.get("/api/v1/harness/models").json()["brain"]
    assert view["locked"] is True and view["lock_env"] == "DAEDALUS_MODEL"


def test_a_pinned_model_is_reported_as_set_by_the_server_not_picked_automatically(fresh, monkeypatch):
    # provider + key from the env take the "openrouter default" path, but DAEDALUS_MODEL is what pins the model
    monkeypatch.setenv("DAEDALUS_MODEL", "pinned/model")
    monkeypatch.setenv("DAEDALUS_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)
    view = fresh.get("/api/v1/harness/models").json()["brain"]
    assert view["locked"] is True and view["source_kind"] == "server", view
    assert SECRET not in json.dumps(view)


def _saved_row():
    from daedalus.llm_config import _read_db

    return _read_db()


def test_changing_only_the_model_keeps_the_saved_key_of_the_same_provider(fresh):
    from daedalus.llm_config import set_config
    from daedalus.llm_router_config import router_write_context

    with router_write_context():
        set_config(model="claude-a", api_key="k1", provider="anthropic")
    res = fresh.post("/api/v1/harness/brain", json={"provider": "anthropic", "model": "other"})
    assert res.status_code == 200, res.text
    row = _saved_row()
    assert row["model"] == "other" and row["api_key"] == "k1"
    assert "k1" not in res.text
    res = fresh.post("/api/v1/harness/brain", json={"provider": "openai", "model": "gpt-x"})
    assert res.status_code == 200, res.text
    row = _saved_row()
    assert row["provider"] == "openai" and not row.get("api_key")        # a key never travels to another provider


def test_only_daedalus_model_locks_the_main_model(fresh, monkeypatch):
    monkeypatch.setenv("DAEDALUS_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    view = fresh.get("/api/v1/harness/models").json()
    assert view["brain"]["locked"] is False and view["env_locked"] is False
    assert fresh.post("/api/v1/harness/brain", json={"provider": "openrouter", "model": "x/y"}).status_code == 200
    monkeypatch.setenv("DAEDALUS_MODEL", "pinned/model")
    view = fresh.get("/api/v1/harness/models").json()
    assert view["brain"]["locked"] is True and view["env_locked"] is True
    assert fresh.post("/api/v1/harness/brain", json={"provider": "openrouter", "model": "x/y"}).status_code == 409


def test_a_catalog_mentor_choice_is_reported_as_provider_model(fresh):
    from daedalus import mentor_helm

    mentor_helm.set_choice({"choice_id": "p:openrouter:x/mentor-model"})
    rows = {r["intent"]: r for r in fresh.get("/api/v1/harness/models").json()["tasks"]}
    assert rows["mentor"]["owner_tag"] == "openrouter:x/mentor-model"
    mentor_helm.set_choice({"choice_id": "rules"})
    rows = {r["intent"]: r for r in fresh.get("/api/v1/harness/models").json()["tasks"]}
    assert rows["mentor"]["owner_tag"] == ""


def test_an_unknown_provider_or_bad_input_is_a_400(fresh):
    assert fresh.post("/api/v1/harness/brain", json={"provider": "nope", "model": "x"}).status_code == 400
    assert fresh.post("/api/v1/harness/brain", json={"provider": "openrouter", "model": "  "}).status_code == 400
    bad = fresh.post("/api/v1/harness/brain", json={"provider": "openrouter", "model": "x", "base_url": "ftp://h"})
    assert bad.status_code == 400


def test_a_key_in_the_environment_is_never_returned(fresh, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET + "2")
    fresh.post("/api/v1/harness/brain", json={"provider": "anthropic", "model": "claude-x", "api_key": SECRET})
    text = fresh.get("/api/v1/harness/models").text
    assert SECRET not in text and "api_key" not in text.replace("has_key", "")


def test_chat_and_writer_share_one_task_model(fresh):
    res = fresh.post("/api/v1/harness/intents", json={"intent": "persona", "model": "openrouter:test/shared"})
    assert res.status_code == 200, res.text
    rows = {r["intent"]: r for r in fresh.get("/api/v1/harness/models").json()["tasks"]}
    assert rows["writer"]["owner_choice"] == rows["persona"]["owner_choice"] == "openrouter:test/shared"
    fresh.post("/api/v1/harness/intents", json={"intent": "writer", "model": ""})
    rows = {r["intent"]: r for r in fresh.get("/api/v1/harness/models").json()["tasks"]}
    assert not rows["persona"]["owner_choice"]


# ------------------------------------------------------------------------------------------------ browser
def _open(page, server, **query):
    page.goto(server["url"] + "/?panel=settings&page=models", wait_until="domcontentloaded")
    page.wait_for_selector("#settings-brain .dx-model-picker", state="attached")
    page.wait_for_selector(".cfg-row[data-intent='critic'] .dx-model-picker", state="attached")


def test_main_model_is_chosen_saved_and_survives_a_reload(studio_server):  # noqa: F811
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1366, "height": 768})
        _open(page, studio_server)
        box = page.locator("#settings-brain")
        box.locator("select.dx-mp-provider").select_option("openrouter")
        box.locator("input.dx-mp-model").fill("x/pr5-main")
        box.locator("button.dx-mp-save").click()
        page.wait_for_function("() => document.querySelector('#settings-brain .dx-mp-note').textContent.includes('Saved')")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_selector("#settings-brain .dx-model-picker", state="attached")
        page.wait_for_function("() => document.querySelector('#settings-brain input.dx-mp-model').value === 'x/pr5-main'")
        assert page.locator("#settings-brain select.dx-mp-provider").input_value() == "openrouter"
        assert "saved here" in page.inner_text("#settings-brain").lower()
        # a task row: set a model, then "Same as main" clears it
        row = page.locator(".cfg-row[data-intent='critic']")
        row.locator("select.dx-mp-provider").select_option("openrouter")
        row.locator("input.dx-mp-model").fill("x/pr5-critic")
        row.locator("button.dx-mp-save").click()
        page.wait_for_function("() => document.querySelector(\".cfg-row[data-intent='critic'] .dx-mp-note\").textContent.includes('Saved')")
        assert "x/pr5-critic" in page.inner_text(".cfg-row[data-intent='critic'] .cfg-name")
        row.locator("select.dx-mp-provider").select_option("")           # first option: Same as main
        row.locator("button.dx-mp-save").click()
        page.wait_for_function("() => !document.querySelector(\".cfg-row[data-intent='critic'] .cfg-name\").textContent.includes('pr5-critic')")
        assert page.locator(".cfg-row[data-intent='critic'] select.dx-mp-provider").input_value() == ""
        browser.close()


def test_a_task_row_has_words_not_raw_sources(studio_server):  # noqa: F811
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1366, "height": 768})
        _open(page, studio_server)
        text = page.inner_text("#settings-intents")
        for raw in ("unconfigured", "mentor-choice", "env-lock", "owner-override", "registry-default"):
            assert raw not in text, raw
        assert page.locator("#settings-intents .cfg-row[data-intent]").count() == 7
        assert page.locator("#settings-intents .cfg-row[data-intent='persona']").count() == 0   # merged into writer
        assert "Chat & writing" in page.inner_text(".cfg-row[data-intent='writer'] .cfg-name")
        browser.close()


def test_a_server_pinned_model_shows_disabled_with_a_badge():
    from playwright.sync_api import sync_playwright

    port = _free_port()
    data = tempfile.mkdtemp(prefix="dx-pr5-lock-")
    env = {**os.environ, "DAEDALUS_STANDALONE": "1", "DAEDALUS_DATA": data, "DAEDALUS_MODEL": "pinned/model",
           "DAEDALUS_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "k", "DAEDALUS_ONBOARDING_AUTO": "0",
           "DAEDALUS_BIND_HOST": "127.0.0.1"}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port), "--host", "127.0.0.1"],
                            cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                urllib.request.urlopen(url + "/health", timeout=1)
                break
            except Exception:
                time.sleep(0.3)
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1366, "height": 768})
            page.goto(url + "/?panel=settings&page=models", wait_until="domcontentloaded")
            page.wait_for_selector("#settings-brain .dx-model-picker[data-locked='1']", state="attached")
            box = page.locator("#settings-brain")
            assert box.locator("select.dx-mp-provider").is_disabled() and box.locator("input.dx-mp-model").is_disabled()
            assert box.locator("button.dx-mp-save").count() == 0 or box.locator("button.dx-mp-save").is_disabled()
            assert box.locator(".badge").count() == 1
            assert "DAEDALUS_MODEL" in box.inner_text() and "Change it where the server is started" in box.inner_text()
            # the task table says it once, not once per row, so a locked Models page stays short
            page.wait_for_selector("#settings-intents .cfg-row[data-intent='writer'][data-locked='1']", state="attached")
            intents = page.locator("#settings-intents")
            assert intents.locator(".dx-mp-lockall").count() == 1
            assert intents.inner_text().count("Change it where the server is started") == 1
            height = page.evaluate("() => document.querySelector('#panel-settings .settings-shell').scrollHeight")
            assert height <= 1400, height
            browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_saving_the_main_model_keeps_unsaved_task_row_edits_and_refuses_an_empty_provider(studio_server):  # noqa: F811
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1366, "height": 768})
        posts = []
        page.on("request", lambda r: posts.append(r.url) if r.method == "POST" and "/harness/brain" in r.url else None)
        _open(page, studio_server)
        brain = page.locator("#settings-brain")
        # an empty provider cannot be saved: an inline message, no request
        brain.locator("select.dx-mp-provider").select_option("")
        brain.locator("button.dx-mp-save").click()
        page.wait_for_function("() => !document.querySelector('#settings-brain .dx-mp-note').hidden")
        assert brain.locator(".dx-mp-note").inner_text().strip() != "" and posts == []
        # an unsaved edit in a task row survives a main-model save
        row = page.locator(".cfg-row[data-intent='critic']")
        row.locator("select.dx-mp-provider").select_option("openrouter")
        row.locator("input.dx-mp-model").fill("x/unsaved-typed")
        brain.locator("select.dx-mp-provider").select_option("openrouter")
        brain.locator("input.dx-mp-model").fill("x/fix2-main")
        brain.locator("button.dx-mp-save").click()
        page.wait_for_function("() => document.querySelector('#settings-brain .dx-mp-note').textContent.includes('Saved')")
        assert len(posts) == 1
        assert page.locator(".cfg-row[data-intent='critic'] input.dx-mp-model").input_value() == "x/unsaved-typed"
        browser.close()
