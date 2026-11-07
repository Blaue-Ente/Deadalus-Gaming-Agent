"""A6.1b: workers, provider test, connection tests, MCP, durability and the mentor-vision test (API level)."""
from __future__ import annotations

import asyncio
import struct
import sys
import types
import zlib

import pytest
from a0_support import a0_tmp  # noqa: F401
from fastapi.testclient import TestClient

BRAIN = {"provider": "openrouter", "model": "owner/pick", "api_key": "SECRET-MASTER-KEY", "base_url": "http://x",
         "sdk": "openai", "source": "db"}


@pytest.fixture
def client(a0_tmp, monkeypatch):  # noqa: F811
    from app.main import app

    monkeypatch.setattr("daedalus.llm_config.get_config", lambda: dict(BRAIN))
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.delenv("DAEDALUS_RUN_BUDGET", raising=False)
    return TestClient(app)


def _install_openai(monkeypatch, text="OK", exc=None):
    record: dict = {"calls": 0}

    class Client:
        def __init__(self, **kw):
            record["init"] = kw
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self.create))

        async def create(self, **kw):
            record["calls"] += 1
            record["body"] = kw
            if exc:
                raise exc
            msg = types.SimpleNamespace(content=text)
            return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)], usage=None)

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=Client))
    return record


# ------------------------------------------------------------------------------------------------ workers
def test_workers_list_never_contains_a_key(client, monkeypatch):
    monkeypatch.setattr("daedalus.workers.get_worker_config", lambda: {
        "fast": {"provider": "novita", "model": "fast/m", "api_key": "SECRET-WORKER-KEY", "enabled": True, "source": "db"},
        "deep": {"provider": "", "model": "", "api_key": "", "enabled": True}})
    res = client.get("/api/v1/harness/workers")
    assert res.status_code == 200
    rows = {r["slot"]: r for r in res.json()["slots"]}
    assert rows["master"]["model"] == "owner/pick" and rows["master"]["key_set"] is True
    assert rows["fast"]["key_set"] is True and "deep" not in rows            # a slot with no model is not listed
    assert "SECRET" not in res.text


# ------------------------------------------------------------------------------------------------ provider test
def test_provider_test_goes_through_the_gateway_with_the_saved_configuration(client, monkeypatch):
    rec = _install_openai(monkeypatch, text="OK")
    out = client.post("/api/v1/harness/test-provider", json={"slot": "master"}).json()
    assert out["ok"] is True and out["reply"] == "OK" and out["model"] == "owner/pick"
    assert rec["init"]["api_key"] == "SECRET-MASTER-KEY" and "SECRET" not in str(out)
    monkeypatch.setattr("daedalus.workers.get_worker_config", lambda: {
        "fast": {"provider": "novita", "model": "fast/m", "api_key": "sk-w", "base_url": "http://n", "sdk": "openai", "enabled": True}})
    out = client.post("/api/v1/harness/test-provider", json={"slot": "fast"}).json()
    assert out["ok"] is True and out["model"] == "fast/m" and rec["init"]["api_key"] == "sk-w"


def test_a_key_in_the_request_body_is_ignored(client, monkeypatch):
    rec = _install_openai(monkeypatch)
    res = client.post("/api/v1/harness/test-provider", json={"slot": "master", "api_key": "EVIL", "base_url": "http://evil"})
    assert res.status_code == 200 and rec["init"]["api_key"] == "SECRET-MASTER-KEY" and rec["init"]["base_url"] == "http://x"


@pytest.mark.parametrize("exc,etype", [(RuntimeError("Error code: 401 - invalid_api_key"), "auth"),
                                       (RuntimeError("429 Too Many Requests"), "quota"),
                                       (RuntimeError("request timed out"), "timeout")])
def test_provider_failures_are_typed_not_fake_success(client, monkeypatch, exc, etype):
    _install_openai(monkeypatch, exc=exc)
    out = client.post("/api/v1/harness/test-provider", json={"slot": "master"}).json()
    assert out["ok"] is False and out["error"]["type"] == etype and "reply" not in out


def test_provider_test_respects_the_budget_and_never_reaches_the_sdk(client, monkeypatch):
    from daedalus import run_budget

    rec = _install_openai(monkeypatch)
    monkeypatch.setenv("DAEDALUS_RUN_BUDGET", "5")
    run_budget.note_usage({"cost_usd": 6, "cost_known": True}, model="m", role="writer")
    assert run_budget.exhausted()
    out = client.post("/api/v1/harness/test-provider", json={"slot": "master"}).json()
    assert out["ok"] is False and out["budget_stopped"] is True and rec["calls"] == 0


def test_unconfigured_or_disabled_slots_are_refused_before_any_call(client, monkeypatch):
    rec = _install_openai(monkeypatch)
    monkeypatch.setattr("daedalus.workers.get_worker_config", lambda: {
        "fast": {"provider": "novita", "model": "m", "api_key": "k", "enabled": False},
        "deep": {"provider": "novita", "model": "m", "api_key": "", "enabled": True}})
    for slot, etype in (("nope", "not_configured"), ("fast", "disabled"), ("deep", "not_configured")):
        out = client.post("/api/v1/harness/test-provider", json={"slot": slot}).json()
        assert out["ok"] is False and out["error"]["type"] == etype, slot
    assert rec["calls"] == 0


# ------------------------------------------------------------------------------------------------ git tests
@pytest.mark.parametrize("raw,code", [
    ({"ok": True, "code": "ok", "configured": True, "login": "me", "can_push": True, "source": "env"}, "ok"),
    ({"ok": False, "code": "not_configured", "configured": False, "error": "GITHUB_TOKEN не е зададен"}, "not_configured"),
    ({"ok": False, "code": "invalid_token", "configured": True, "error": "Токенът е невалиден или изтекъл (401)"}, "invalid_token"),
    ({"ok": False, "code": "forbidden", "configured": True, "error": "Достъпът е отказан (403) — липсва scope repo"}, "forbidden"),
    ({"ok": False, "code": "api_error", "configured": True, "error": "GitHub API 502"}, "api_error"),
    ({"ok": False, "code": "network", "configured": True, "error": "ConnectError: timed out"}, "network"),
    # the prose is never parsed: a network error that happens to mention 401 stays a network error
    ({"ok": False, "code": "network", "configured": True, "error": "connect to 10.0.0.401:403 failed"}, "network"),
    ({"ok": False, "configured": True, "error": "Токенът е невалиден или изтекъл (401)"}, "network"),     # no code: not guessed
])
def test_git_test_answers_a_code_not_a_bulgarian_sentence(client, monkeypatch, raw, code):
    async def fake():
        return dict(raw)

    monkeypatch.setattr("daedalus.connectors_config.test_github_connection", fake)
    out = client.post("/api/v1/harness/connections/test", json={"target": "github"}).json()
    assert out["code"] == code and out["ok"] is raw["ok"]
    assert "error" not in out and "hint" not in out                          # prose stays on the server


def test_the_real_connector_tests_report_structured_codes(a0_tmp, monkeypatch):  # noqa: F811
    from daedalus import connectors_config as cc

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    assert asyncio.run(cc.test_github_connection())["code"] == "not_configured"
    assert asyncio.run(cc.test_gitlab_connection())["code"] == "not_configured"
    monkeypatch.setenv("GITHUB_TOKEN", "t")

    class R:
        def __init__(self, status):
            self.status_code, self.headers = status, {}

        def json(self):
            return {"login": "u"}

    for status, code in ((401, "invalid_token"), (403, "forbidden"), (500, "api_error")):
        monkeypatch.setattr("httpx.get", lambda *a, _s=status, **k: R(_s))
        assert asyncio.run(cc.test_github_connection())["code"] == code, status
    monkeypatch.setattr("httpx.get", lambda *a, **k: (_ for _ in ()).throw(OSError("unreachable")))
    assert asyncio.run(cc.test_github_connection())["code"] == "network"


def test_the_git_test_does_not_block_the_event_loop(client, monkeypatch):
    import threading
    import time as _t

    main = threading.get_ident()
    seen = {}

    async def slow():
        seen["thread"] = threading.get_ident()
        _t.sleep(0.05)                                                          # what blocking httpx would do
        return {"ok": True, "code": "ok", "configured": True, "login": "x"}

    monkeypatch.setattr("daedalus.connectors_config.test_github_connection", slow)
    assert client.post("/api/v1/harness/connections/test", json={"target": "github"}).json()["ok"] is True
    assert seen["thread"] != main


def test_gitlab_test_and_unknown_target(client, monkeypatch):
    async def fake():
        return {"ok": True, "configured": True, "username": "u", "host": "gitlab.com"}

    monkeypatch.setattr("daedalus.connectors_config.test_gitlab_connection", fake)
    out = client.post("/api/v1/harness/connections/test", json={"target": "gitlab"}).json()
    assert out == {"ok": True, "target": "gitlab", "code": "ok", "configured": True, "username": "u", "host": "gitlab.com"}
    assert client.post("/api/v1/harness/connections/test", json={"target": "bitbucket"}).status_code == 404


# ------------------------------------------------------------------------------------------------ MCP
def test_mcp_toggle_only_for_catalog_servers_and_never_takes_a_command(client):
    view = client.get("/api/v1/harness/connections").json()["mcp"]
    ids = [r["id"] for r in view["servers"]]
    assert "git" in ids and all(r["enabled"] is False for r in view["servers"])
    on = client.post("/api/v1/harness/mcp", json={"id": "git", "enabled": True, "command": "calc.exe", "args": ["x"]})
    assert on.status_code == 200 and {r["id"]: r["enabled"] for r in on.json()["servers"]}["git"] is True
    again = client.get("/api/v1/harness/connections").json()["mcp"]
    assert {r["id"]: r["enabled"] for r in again["servers"]}["git"] is True
    from daedalus import mcp_client
    assert "calc.exe" not in str(mcp_client.load_servers())                  # the extra field never reached the config
    off = client.post("/api/v1/harness/mcp", json={"id": "git", "enabled": False}).json()
    assert {r["id"]: r["enabled"] for r in off["servers"]}["git"] is False
    assert client.post("/api/v1/harness/mcp", json={"id": "evil-server", "enabled": True}).status_code == 404


# ------------------------------------------------------------------------------------------------ durability
def test_durability_counts_are_real(client, monkeypatch):
    async def projects():
        return [{"slug": "a"}, {"slug": "b"}, {"slug": "c"}]

    async def published(enabled_only=True):
        return [{"slug": "a"}]

    monkeypatch.setattr("daedalus.playground.list_projects", projects)
    monkeypatch.setattr("daedalus.playground_workspace_backup.workspace_file_count", lambda slug: 0 if slug == "b" else 4)
    monkeypatch.setattr("daedalus.playground_publish.list_published", published)
    d = client.get("/api/v1/harness/connections").json()["durability"]
    assert (d["projects"], d["empty_workspaces"], d["published"]) == (3, 1, 1)
    assert isinstance(d["storage_configured"], bool)


def test_one_broken_section_does_not_hide_the_other(client, monkeypatch):
    async def boom():
        raise RuntimeError("storage down")

    monkeypatch.setattr("daedalus.harness.connections.durability_view", boom)
    body = client.get("/api/v1/harness/connections").json()
    assert body["durability"] == {"ok": False, "error": "RuntimeError"} and body["mcp"]["ok"] is True


# ------------------------------------------------------------------------------------------------ vision test
def _decode(png: bytes):
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pos, idat, w, h = 8, b"", 0, 0
    while pos < len(png):
        n, kind = struct.unpack(">I4s", png[pos:pos + 8])
        data = png[pos + 8:pos + 8 + n]
        assert struct.unpack(">I", png[pos + 8 + n:pos + 12 + n])[0] == zlib.crc32(kind + data) & 0xFFFFFFFF
        if kind == b"IHDR":
            w, h = struct.unpack(">II", data[:8])
        if kind == b"IDAT":
            idat += data
        pos += 12 + n
    raw = zlib.decompress(idat)
    return w, h, [raw[i * (w + 1) + 1:(i + 1) * (w + 1)] for i in range(h)]


def test_label_image_is_a_valid_png_with_one_glyph_block_per_character():
    from daedalus.harness import label_image

    label = "AC37HY"
    w, h, rows = _decode(label_image.render_png(label))
    ink_cols = [any(r[x] == 0 for r in rows) for x in range(w)]
    blocks, prev = 0, False
    for on in ink_cols:
        blocks += on and not prev
        prev = on
    assert blocks == len(label) and any(0 in r for r in rows) and any(255 in r for r in rows)
    assert label_image.render_png("AC37HY") != label_image.render_png("ACT7HY")
    assert len(label_image.new_label()) == 6 and set(label_image.new_label(8)) <= set(label_image.ALPHABET)
    with pytest.raises(ValueError):
        label_image.render_png("AB!")


def test_vision_test_sends_a_rendered_label_and_never_returns_it(client, monkeypatch):
    seen = {}

    async def fake_probe(path, label):
        from pathlib import Path
        seen["exists"] = Path(path).is_file()
        seen["size"] = len(Path(path).read_bytes())
        seen["path"] = str(path)
        seen["label"] = label
        return {"ok": True, "perceived": True, "provider": "openrouter", "model": "v/m", "recorded": True,
                "answer": '{"label": "' + label + '"}'}

    monkeypatch.setattr("daedalus.mentor_helm.probe_vision", fake_probe)
    out = client.post("/api/v1/harness/vision-probe").json()
    assert out["ok"] is True and out["perceived"] is True and out["model"] == "v/m" and out["recorded"] is True
    assert seen["exists"] and seen["size"] > 200 and not __import__("pathlib").Path(seen["path"]).exists()   # temp file removed
    assert "label" not in out                                                  # the random label is not echoed as a field


def test_vision_test_without_a_mentor_model_is_an_honest_failure(client, monkeypatch):
    async def none(*a, **k):
        return {"ok": False, "perceived": False, "error": "no_mentor_model"}

    monkeypatch.setattr("daedalus.mentor_helm.probe_vision", none)
    out = client.post("/api/v1/harness/vision-probe").json()
    assert out["ok"] is False and out["perceived"] is False and out["error"] == "no_mentor_model"


# ------------------------------------------------------------------------------------------------ UI (real clicks)
import json  # noqa: E402
import re  # noqa: E402

from test_a61_settings import _open  # noqa: E402
from test_studio_phase2_workspace import _fulfill, _hit, studio_server  # noqa: E402,F401  (fixture and helpers)

WORKERS = {"ok": True, "slots": [
    {"slot": "master", "label": "master", "provider": "openrouter", "model": "owner/pick", "enabled": True, "source": "db", "key_set": True},
    {"slot": "fast", "label": "fast", "provider": "novita", "model": "fast/m", "enabled": True, "source": "db", "key_set": False},
    {"slot": "deep", "label": "deep", "provider": "novita", "model": "deep/m", "enabled": False, "source": "db", "key_set": True}]}


def _stub_remote(page, *, github=None, probe=None, provider=None, engines_after=None):
    """Stub only what would call a paid or external service; everything else is the real server."""
    seen = {"provider": [], "git": [], "probe": 0}

    def on_provider(route):
        slot = json.loads(route.request.post_data or "{}").get("slot")
        seen["provider"].append(slot)
        out = (provider or {}).get(slot) or {"ok": True, "slot": slot, "provider": "openrouter", "model": "owner/pick",
                                             "latency_ms": 12, "reply": "OK"}
        _fulfill(route, 200, out)

    def on_git(route):
        target = json.loads(route.request.post_data or "{}").get("target")
        seen["git"].append(target)
        _fulfill(route, 200, (github or {}).get(target) or {"ok": True, "target": target, "code": "ok", "configured": True,
                                                            "login": "octo", "can_push": True})

    def on_probe(route):
        seen["probe"] += 1
        _fulfill(route, 200, probe or {"ok": True, "perceived": True, "provider": "openrouter", "model": "v/m", "recorded": True})

    page.route("**/api/v1/harness/workers", lambda r: _fulfill(r, 200, WORKERS))
    page.route("**/api/v1/harness/test-provider", on_provider)
    page.route("**/api/v1/harness/connections/test", on_git)
    page.route("**/api/v1/harness/vision-probe", on_probe)
    if engines_after is not None:
        page.route("**/api/v1/harness/engines", lambda r: engines_after(r, seen))
    return seen


def _open_links(page, server, lang="en"):
    _open(page, server, lang)
    page.wait_for_selector(".cfg-row[data-id='worker-master']")
    page.wait_for_selector(".cfg-row[data-id='link-github']")
    page.wait_for_selector(".cfg-row[data-id^='mcp-']")


def test_provider_and_git_tests_report_in_plain_words_with_real_clicks(studio_server):
    from playwright.sync_api import sync_playwright

    github = {"github": {"ok": False, "target": "github", "code": "invalid_token", "configured": True}}
    provider = {"fast": {"ok": False, "slot": "fast", "error": {"type": "auth", "message": "401"}},
                "deep": {"ok": False, "slot": "deep", "error": {"type": "budget_blocked", "message": "x"}, "budget_stopped": True}}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        seen = _stub_remote(page, github=github, provider=provider)
        _open_links(page, studio_server)
        # the key state is shown without ever showing a key
        assert "key set" in page.inner_text(".cfg-row[data-id='worker-master']")
        assert "no key" in page.inner_text(".cfg-row[data-id='worker-fast']")
        assert "disabled" in page.inner_text(".cfg-row[data-id='worker-deep']")
        page.locator(".cfg-row[data-id='worker-master'] button").click()
        page.wait_for_function("() => document.querySelector(\".cfg-row[data-id='worker-master'] .cfg-note:not([hidden])\") !== null")
        note = page.inner_text(".cfg-row[data-id='worker-master'] .cfg-note")
        assert "Works: owner/pick" in note and "12 ms" in note and "OK" in note
        assert page.locator(".cfg-row[data-id='worker-master']").get_attribute("data-state") == "ok"
        page.locator(".cfg-row[data-id='worker-fast'] button").click()
        page.wait_for_function("() => document.querySelector(\".cfg-row[data-id='worker-fast'] .cfg-note:not([hidden])\") !== null")
        assert "the key was refused" in page.inner_text(".cfg-row[data-id='worker-fast'] .cfg-note")
        assert page.locator(".cfg-row[data-id='worker-fast']").get_attribute("data-state") == "error"
        page.locator(".cfg-row[data-id='worker-deep'] button").click()
        page.wait_for_function("() => document.querySelector(\".cfg-row[data-id='worker-deep'] .cfg-note:not([hidden])\") !== null")
        assert "run budget is spent" in page.inner_text(".cfg-row[data-id='worker-deep'] .cfg-note")
        page.locator(".cfg-row[data-id='link-github'] button").click()
        page.wait_for_function("() => document.querySelector(\".cfg-row[data-id='link-github'] .cfg-note:not([hidden])\") !== null")
        assert page.inner_text(".cfg-row[data-id='link-github'] .cfg-note") == "The token is invalid or expired"
        page.locator(".cfg-row[data-id='link-gitlab'] button").click()
        page.wait_for_function("() => document.querySelector(\".cfg-row[data-id='link-gitlab'] .cfg-note:not([hidden])\") !== null")
        assert "Connected as octo" in page.inner_text(".cfg-row[data-id='link-gitlab'] .cfg-note")
        assert seen["provider"] == ["master", "fast", "deep"] and seen["git"] == ["github", "gitlab"]
        browser.close()


def test_bulgarian_shows_the_new_sections_in_bulgarian_without_raw_keys(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _stub_remote(page, provider={"master": {"ok": False, "slot": "master", "error": {"type": "quota", "message": "x"}}})
        _open_links(page, studio_server, lang="bg")
        page.locator(".cfg-row[data-id='worker-master'] button").click()
        page.wait_for_function("() => document.querySelector(\".cfg-row[data-id='worker-master'] .cfg-note:not([hidden])\") !== null")
        assert "квота" in page.inner_text(".cfg-row[data-id='worker-master'] .cfg-note")
        text = page.inner_text("#panel-settings")
        low = text.lower()
        for heading in ("работници и доставчици", "връзки", "mcp сървъри", "устойчивост"):
            assert heading in low, heading
        assert not re.search(r"\b(studio|settings)\.[A-Za-z]", text), re.findall(r"\b(?:studio|settings)\.[A-Za-z.]+", text)
        assert "workers and providers" not in low and "durability" not in low
        browser.close()


def test_mcp_is_enabled_only_after_a_confirm_and_a_declined_confirm_changes_nothing(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _stub_remote(page)
        posts: list[str] = []
        page.on("request", lambda r: posts.append(r.url) if r.method == "POST" and r.url.endswith("/api/v1/harness/mcp") else None)
        _open_links(page, studio_server)
        row = page.locator(".cfg-row[data-id='mcp-git']")
        assert "off" in row.inner_text() and row.locator("button").inner_text() == "Enable"
        asked: list[str] = []

        def decline(dialog):
            asked.append(dialog.message)
            dialog.dismiss()

        page.once("dialog", decline)
        row.locator("button").click()
        page.wait_for_timeout(300)
        assert asked and "git" in asked[0] and "local program" in asked[0]
        assert posts == [] and page.locator(".cfg-row[data-id='mcp-git'] button").inner_text() == "Enable"   # nothing was sent
        page.once("dialog", lambda d: d.accept())
        page.locator(".cfg-row[data-id='mcp-git'] button").click()
        page.wait_for_function("() => document.querySelector(\".cfg-row[data-id='mcp-git'] button\").textContent === 'Disable'")
        assert "on" in page.inner_text(".cfg-row[data-id='mcp-git']") and len(posts) == 1
        # it stays on after a reload (the real server saved it), and turning it off needs no confirm
        page.reload(wait_until="domcontentloaded")
        _open_links(page, studio_server)
        assert page.locator(".cfg-row[data-id='mcp-git'] button").inner_text() == "Disable"
        page.locator(".cfg-row[data-id='mcp-git'] button").click()
        page.wait_for_function("() => document.querySelector(\".cfg-row[data-id='mcp-git'] button\").textContent === 'Enable'")
        browser.close()


def test_durability_shows_real_counts(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _stub_remote(page)
        page.route("**/api/v1/harness/connections", lambda r: _fulfill(r, 200, {
            "ok": True, "mcp": {"ok": True, "servers": []},
            "durability": {"ok": True, "storage_configured": False, "projects": 7, "empty_workspaces": 2, "published": 3}}))
        _open_links_no_mcp = lambda: (_open(page, studio_server), page.wait_for_selector("#settings-durability p"))  # noqa: E731
        _open_links_no_mcp()
        text = page.inner_text("#settings-durability")
        assert "not configured" in text and "Projects: 7" in text and "published: 3" in text and "empty workspace: 2" in text
        browser.close()


def test_vision_test_updates_the_state_line_and_tells_the_truth_when_it_fails(studio_server):
    from playwright.sync_api import sync_playwright

    state = {"verified": None}

    def engines(route, seen):
        real = route.fetch().json()      # the real answer; only the vision block is replaced by what the probe stored
        real["vision"] = {"provider": "openrouter", "model": "v/m", "verified": state["verified"]}
        _fulfill(route, 200, real)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        seen = _stub_remote(page, engines_after=engines)
        _open_links(page, studio_server)
        assert "not verified" in page.inner_text("#settings-engines [data-role='vision-state']").lower()
        state["verified"] = True
        page.locator(".cfg-row[data-id='vision-test'] button").click()
        page.wait_for_function("() => document.querySelector(\".cfg-row[data-id='vision-test'] .cfg-note:not([hidden])\") !== null")
        assert "Vision is verified" in page.inner_text(".cfg-row[data-id='vision-test'] .cfg-note")
        page.wait_for_function("() => /verified/i.test(document.querySelector(\"#settings-engines [data-role='vision-state']\").textContent) && !/not verified/i.test(document.querySelector(\"#settings-engines [data-role='vision-state']\").textContent)")
        assert seen["probe"] == 1
        browser.close()


def test_vision_test_failures_are_not_reported_as_success(studio_server):
    from playwright.sync_api import sync_playwright

    cases = [({"ok": True, "perceived": False, "provider": "p", "model": "m"}, "NOT verified"),
             ({"ok": False, "perceived": False, "error": "no_mentor_model"}, "No mentor model is set"),
             ({"ok": False, "perceived": False, "error": "timeout"}, "could not run: timeout")]
    for payload, expect in cases:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            _stub_remote(page, probe=payload)
            _open_links(page, studio_server)
            page.locator(".cfg-row[data-id='vision-test'] button").click()
            page.wait_for_function("() => document.querySelector(\".cfg-row[data-id='vision-test'] .cfg-note:not([hidden])\") !== null")
            assert expect in page.inner_text(".cfg-row[data-id='vision-test'] .cfg-note")
            assert page.locator(".cfg-row[data-id='vision-test']").get_attribute("data-state") == "error"
            browser.close()


@pytest.mark.parametrize("size", [(1280, 800), (1366, 768), (910, 640)])
def test_new_sections_fit_and_their_buttons_are_really_clickable(studio_server, size):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": size[0], "height": size[1]})
        _stub_remote(page)
        _open_links(page, studio_server)
        over = page.evaluate("""() => { const p = document.getElementById('panel-settings');
          return {sw: p.scrollWidth, cw: p.clientWidth, docw: document.documentElement.scrollWidth, vw: window.innerWidth}; }""")
        assert over["sw"] <= over["cw"] + 1 and over["docw"] <= over["vw"] + 1, over
        for selector in (".cfg-row[data-id='worker-master'] button", ".cfg-row[data-id='link-github'] button",
                         ".cfg-row[data-id='mcp-git'] button", ".cfg-row[data-id='vision-test'] button"):
            page.locator(selector).scroll_into_view_if_needed()
            _hit(page, selector)
        browser.close()


# ------------------------------------------------------------------------------------------------ review fixes
def test_a_second_billed_test_of_the_same_kind_is_refused_while_one_runs(client, monkeypatch):
    from daedalus.harness import connections

    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()

        class Client:
            def __init__(self, **kw):
                self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self.create))

            async def create(self, **kw):
                started.set()
                await release.wait()
                msg = types.SimpleNamespace(content="OK")
                return types.SimpleNamespace(choices=[types.SimpleNamespace(message=msg)], usage=None)

        monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(AsyncOpenAI=Client))
        first = asyncio.create_task(connections.test_provider("master"))
        await started.wait()
        second = await connections.test_provider("master")
        release.set()
        return await first, second

    first, second = asyncio.run(scenario())
    assert first["ok"] is True
    assert second["ok"] is False and second["error"]["type"] == "busy"
    assert connections._inflight == set()                                       # released, even after the refusal


def test_vision_test_is_single_flight_and_releases_on_failure(client, monkeypatch):
    from daedalus.harness import connections

    async def boom(path, label):
        raise RuntimeError("provider down")

    monkeypatch.setattr("daedalus.mentor_helm.probe_vision", boom)
    connections._inflight.add("vision")
    busy = asyncio.run(connections.vision_test())
    assert busy["ok"] is False and busy["error"] == "busy"
    connections._inflight.discard("vision")
    with pytest.raises(RuntimeError):
        asyncio.run(connections.vision_test())
    assert "vision" not in connections._inflight                                # a failure never leaves it stuck


def test_a_local_provider_needs_no_key_and_is_not_called_unconfigured(client, monkeypatch):
    monkeypatch.setattr("daedalus.llm_config.get_config", lambda: {
        "provider": "ollama", "model": "llama3", "api_key": "", "base_url": "http://127.0.0.1:11434/v1", "sdk": "openai", "source": "env"})
    rec = _install_openai(monkeypatch)
    rows = {r["slot"]: r for r in client.get("/api/v1/harness/workers").json()["slots"]}
    assert rows["master"]["key_set"] is False and rows["master"]["keyless_ok"] is True
    out = client.post("/api/v1/harness/test-provider", json={"slot": "master"}).json()
    assert out["ok"] is True and rec["calls"] == 1


def test_a_cloud_provider_without_a_key_is_still_unconfigured(client, monkeypatch):
    monkeypatch.setattr("daedalus.llm_config.get_config", lambda: {
        "provider": "openrouter", "model": "m", "api_key": "", "base_url": "http://x", "sdk": "openai", "source": "env"})
    rec = _install_openai(monkeypatch)
    rows = {r["slot"]: r for r in client.get("/api/v1/harness/workers").json()["slots"]}
    assert rows["master"]["key_set"] is False and rows["master"]["keyless_ok"] is False
    out = client.post("/api/v1/harness/test-provider", json={"slot": "master"}).json()
    assert out["ok"] is False and out["error"]["type"] == "not_configured" and rec["calls"] == 0


def test_the_server_refuses_to_enable_an_mcp_server_that_offline_mode_blocks(client, monkeypatch):
    monkeypatch.setenv("DAEDALUS_OFFLINE", "1")
    view = {r["id"]: r for r in client.get("/api/v1/harness/connections").json()["mcp"]["servers"]}
    blocked = [i for i, r in view.items() if r["blocked_offline"]]
    assert blocked, "the catalog has at least one server that needs the network"
    res = client.post("/api/v1/harness/mcp", json={"id": blocked[0], "enabled": True})
    assert res.status_code == 409 and res.json()["error"] == "blocked_offline"
    from daedalus import mcp_client
    assert blocked[0] not in mcp_client.load_servers()                          # nothing was written


def test_a_refused_mcp_toggle_keeps_the_list_and_does_not_say_saved(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _stub_remote(page)
        page.route("**/api/v1/harness/mcp", lambda r: _fulfill(r, 200, {"ok": False, "id": "git", "error": "mcp.json unreadable"}))
        _open_links(page, studio_server)
        before = page.locator(".cfg-row[data-id^='mcp-']").count()
        assert before >= 2
        page.once("dialog", lambda d: d.accept())
        page.locator(".cfg-row[data-id='mcp-git'] button").click()
        page.wait_for_function("() => /mcp.json unreadable/.test(document.getElementById('harness-status').textContent)")
        assert page.locator(".cfg-row[data-id^='mcp-']").count() == before            # the list is still there
        assert page.locator(".cfg-row[data-id='mcp-git'] button").inner_text() == "Enable"
        assert "Saved" not in page.inner_text("#harness-status")
        browser.close()


def test_a_verdict_that_could_not_be_saved_is_not_shown_as_verified(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _stub_remote(page, probe={"ok": True, "perceived": True, "provider": "p", "model": "m", "recorded": False})
        _open_links(page, studio_server)
        page.locator(".cfg-row[data-id='vision-test'] button").click()
        page.wait_for_function("() => document.querySelector(\".cfg-row[data-id='vision-test'] .cfg-note:not([hidden])\") !== null")
        note = page.inner_text(".cfg-row[data-id='vision-test'] .cfg-note")
        assert "could not be saved" in note and "Vision is verified" not in note
        assert page.locator(".cfg-row[data-id='vision-test']").get_attribute("data-state") == "error"
        browser.close()


def test_a_local_slot_says_no_key_is_needed(studio_server):
    from playwright.sync_api import sync_playwright

    workers = {"ok": True, "slots": [{"slot": "master", "label": "master", "provider": "ollama", "model": "llama3", "enabled": True,
                                      "source": "env", "key_set": False, "keyless_ok": True}]}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _stub_remote(page)
        page.route("**/api/v1/harness/workers", lambda r: _fulfill(r, 200, workers))
        _open_links(page, studio_server)
        text = page.inner_text(".cfg-row[data-id='worker-master']")
        assert "no key needed" in text and "no key\n" not in text + "\n"
        browser.close()


def test_a_test_that_finishes_after_settings_was_reopened_still_shows_its_result(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _stub_remote(page)
        held = {}

        def slow_provider(route):
            held["route"] = route                                              # answer it later, after the repaint

        page.route("**/api/v1/harness/test-provider", slow_provider)
        _open_links(page, studio_server)
        page.locator(".cfg-row[data-id='worker-master'] button").click()
        page.wait_for_function("() => true")
        page.wait_for_timeout(200)
        page.locator("#workspace-nav button[data-panel=chat]").click()
        page.locator("#workspace-nav button[data-panel=settings]").click()       # the rows are rebuilt
        page.wait_for_selector(".cfg-row[data-id='worker-master']")
        page.wait_for_timeout(400)
        _fulfill(held["route"], 200, {"ok": True, "slot": "master", "provider": "openrouter", "model": "owner/pick",
                                      "latency_ms": 7, "reply": "OK"})
        page.wait_for_function("() => document.querySelector(\".cfg-row[data-id='worker-master'] .cfg-note:not([hidden])\") !== null")
        assert "Works: owner/pick" in page.inner_text(".cfg-row[data-id='worker-master'] .cfg-note")
        browser.close()
