"""Manual radar scan and scratch journal in Review. Not a full parity claim."""
from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "app" / "static" / "studio.html").read_text(encoding="utf-8")
WORK = (ROOT / "app" / "static" / "studio-workspace.js").read_text(encoding="utf-8")
I18N = (ROOT / "app" / "static" / "i18n.js").read_text(encoding="utf-8")
KEYS = (
    "studio.radarTitle",
    "studio.radarHint",
    "studio.radarCatalog",
    "studio.radarState",
    "studio.radarScan",
    "studio.radarScanConfirm",
    "studio.radarCatalogPending",
    "studio.radarStatePending",
    "studio.radarScanPending",
    "studio.radarUnknown",
    "studio.radarCatalogCount",
    "studio.radarPartial",
    "studio.radarScanned",
    "studio.radarNotOk",
    "studio.journalTitle",
    "studio.journalHint",
    "studio.journalLoad",
    "studio.journalLoadConfirm",
    "studio.journalPause",
    "studio.journalClear",
    "studio.journalPauseConfirm",
    "studio.journalClearConfirm",
    "studio.journalListPending",
    "studio.journalPausePending",
    "studio.journalClearPending",
    "studio.journalUnknown",
    "studio.journalEmpty",
    "studio.journalLoaded",
    "studio.journalPauseSet",
    "studio.journalCleared",
    "studio.journalNotOk",
    "studio.journalBudget",
)


def _slice(text, start, end):
    i = text.find(start)
    j = text.find(end, i + len(start))
    if i < 0 or j < 0:
        raise ValueError(start)
    return text[i:j]


def test_radar_and_journal_contract_names_the_real_routes():
    for key in KEYS:
        assert I18N.count(f'"{key}":') == 2, key
    review = HTML[HTML.find('id="panel-review"'):HTML.find('id="panel-settings"')]
    assert review.index("ref-controls") < review.index("radar-controls") < review.index("journal-controls")
    radar_html = review[review.index("radar-controls"):review.index("journal-controls")]
    assert 'type="checkbox"' not in radar_html
    assert "panel-radar" not in HTML
    assert "panel-journal" not in HTML
    scan = _slice(WORK, "function scanRadar", "function journalVisible")
    assert 'method: "POST"' in scan
    assert "JSON.stringify" not in scan
    assert "body:" not in scan
    assert "loadDiscoveries" not in scan
    assert "setInterval" not in scan
    load = _slice(WORK, "function loadJournal", "function mutateJournal")
    assert "/admin/daedalus/journal/list" in load
    assert "studio.journalLoadConfirm" in load
    mutate = _slice(WORK, "function mutateJournal", "function pauseJournal")
    assert "encodeURIComponent(id)" in mutate
    assert 'method: "POST"' in mutate
    assert "JSON.stringify" not in mutate
    entry = _slice(WORK, 'if (panel === "review") {', 'if (panel === "settings")')
    assert "/radar/" not in entry
    assert "/journal/" not in entry
    assert "paintRadar();" in entry
    assert "paintJournal();" in entry
    leave = _slice(WORK, 'if (currentPanel === "review" && panel !== "review")', "currentPanel = panel;")
    assert "radarGen += 1" in leave
    assert "journalGen += 1" in leave
    assert "radarScanOp = null" not in leave
    assert "journalListOp = null" not in leave
    slug = _slice(WORK, "function onSlug", '$("workspace-nav")')
    assert "/radar/" not in slug
    assert "/journal/" not in slug
    confirm = next(line for line in I18N.splitlines() if '"studio.radarScanConfirm":' in line)
    assert "12" in confirm and "DuckDuckGo" in confirm and "radar_state.json" in confirm
    assert "Grok" not in confirm and "Composer" not in confirm
    journal = next(line for line in I18N.splitlines() if '"studio.journalLoadConfirm":' in line)
    assert "24" in journal and "150000" in journal and "80" in journal
    block = _slice(WORK, "function loadRadarCatalog", "async function reviewAct")
    assert "innerHTML" not in block
    assert "setInterval" not in block
    assert 'type="checkbox"' not in block


def _reset(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.setenv("DAEDALUS_OFFLINE", "1")
    for key in (
        "OPENROUTER_API_KEY",
        "NOVITA_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "ALLTOKEN_API_KEY",
        "GITHUB_TOKEN",
        "DAEDALUS_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    from daedalus.agent import Daedalus
    from daedalus.memory import DaedalusMemory
    from daedalus.paths import data_root, playground_projects_dir
    from daedalus.store import reset_sqlite_pool

    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    reset_sqlite_pool()
    Daedalus._instance = None
    DaedalusMemory._instance = None


def test_search_tools_skip_duplicate_urls(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    from daedalus.tools import github_search_tool, web_search_tool

    class Boom(Exception):
        pass

    async def web(query, limit=5, fetch_content=False):
        return {
            "source": "duckduckgo",
            "result_count": 2,
            "results": [
                {"title": "Known", "url": "https://example.test/known", "snippet": "old"},
                {"title": "New", "url": "https://example.test/new", "snippet": "fresh"},
            ],
        }

    async def github(query, language=None, limit=5, min_stars=0):
        return {
            "result_count": 2,
            "results": [
                {"url": "https://github.com/known/repo", "full_name": "known/repo", "total_score": 90},
                {"url": "https://github.com/new/repo", "full_name": "new/repo", "total_score": 40},
            ],
        }

    monkeypatch.setattr("daedalus.tools._web_search_impl", lambda: (web, Boom, Boom))
    monkeypatch.setattr("daedalus.tools._github_finder_impl", lambda: (github, Boom, Boom))
    seen = {"https://example.test/known", "https://github.com/known/repo"}
    captured = []

    class Memory:
        def find_discovery_by_url(self, url):
            return {"url": url} if url in seen else None

        def capture_discovery(self, **kwargs):
            captured.append(kwargs)
            seen.add(kwargs["url"])
            return "disc-" + str(len(captured))

    memory = Memory()
    web_result = asyncio.run(web_search_tool(
        memory, "agents", record_discoveries=True, for_self=False,
    ))
    gh_result = asyncio.run(github_search_tool(
        memory, "agents", record_discoveries=True, for_self=False,
    ))
    assert web_result["source"] == "duckduckgo"
    assert web_result["discoveries_recorded"] == 1
    assert gh_result["discoveries_recorded"] == 1
    assert [row["url"] for row in captured] == [
        "https://example.test/new",
        "https://github.com/new/repo",
    ]


def test_radar_scan_and_journal_handlers_use_temp_stubs(tmp_path, monkeypatch):
    _reset(tmp_path, monkeypatch)
    from daedalus.paths import data_root
    from daedalus.radar import RADAR_SOURCES

    github_calls = []
    web_calls = []
    models = []

    class Memory:
        def find_discovery_by_url(self, url):
            return None

        def capture_discovery(self, **kwargs):
            return "kept"

    class Agent:
        memory = Memory()

    async def fake_github(memory, query, limit=5, min_stars=0, record_discoveries=True, for_self=False):
        github_calls.append({
            "query": query,
            "record_discoveries": record_discoveries,
            "for_self": for_self,
            "has_memory": memory is not None,
        })
        if "awesome MCP" in query:
            raise RuntimeError("source blew up")
        if "local LLM" in query:
            return {"ok": False, "error": "github_search_failed: down", "results": [], "discoveries_recorded": 0}
        return {"ok": True, "result_count": 1, "results": [], "discoveries_recorded": 0}

    async def fake_web(memory, query, limit=5, fetch_content=False, record_discoveries=True, for_self=False):
        web_calls.append({
            "query": query,
            "record_discoveries": record_discoveries,
            "for_self": for_self,
            "fetch_content": fetch_content,
            "has_memory": memory is not None,
        })
        return {
            "ok": True,
            "source": "duckduckgo",
            "result_count": 0,
            "results": [],
            "discoveries_recorded": 0,
        }

    async def fake_complete(**kwargs):
        models.append(kwargs)
        raise AssertionError("radar scan called a model")

    monkeypatch.setattr("daedalus.tools.github_search_tool", fake_github)
    monkeypatch.setattr("daedalus.tools.web_search_tool", fake_web)
    monkeypatch.setattr("daedalus.llm_client.complete", fake_complete)
    monkeypatch.setattr("daedalus.agent.get_daedalus", lambda: Agent())
    from daedalus import kv, scratch_journal as sj
    from fastapi.testclient import TestClient
    from app.main import app

    sj.write_step("fresh-sess", "look", "S" * 240, task_name="maze")
    sj.write_step("stale-sess", "old", "gone", task_name="old task")
    stale = kv.get("adn:journal:sess:stale-sess")
    stale["updated_at"] = int(time.time()) - sj.JOURNAL_TTL_SECONDS - 5
    kv.set("adn:journal:sess:stale-sess", stale)
    kv.set("adn:journal:pause:stale-sess", {"reason": "owner_requested", "ts": 1})
    with TestClient(app) as client:
        catalog = client.get("/admin/daedalus/radar/sources")
        assert catalog.status_code == 200
        sources = catalog.json()["sources"]
        assert [src["id"] for src in sources] == [src["id"] for src in RADAR_SOURCES]
        assert sum(src["type"] == "github" for src in sources) == 8
        assert sum(src["type"] == "web" for src in sources) == 4
        before = client.get("/admin/daedalus/radar/state").json()
        assert before["last_scan"] is None
        assert before["scan_count"] == 0
        assert before["last_summary"] is None
        assert "last_scan_ts" not in before
        selected = client.post(
            "/admin/daedalus/radar/scan",
            json={"source_ids": ["github-mcp"]},
        )
        assert selected.status_code == 200
        payload = selected.json()
        assert payload["ok"] is True
        assert payload["for_self"] is False
        assert payload["sources_scanned"] == 12
        assert len(github_calls) == 8
        assert len(web_calls) == 4
        assert {row["query"] for row in github_calls + web_calls} == {src["query"] for src in RADAR_SOURCES}
        assert all(row["record_discoveries"] is True and row["for_self"] is False for row in github_calls + web_calls)
        assert all(row["has_memory"] is True for row in github_calls + web_calls)
        assert all(row["fetch_content"] is False for row in web_calls)
        failed = [src for src in payload["sources"] if src["ok"] is False]
        assert {src["id"] for src in failed} == {"github-awesome-mcp", "github-local-llm"}
        assert any("source blew up" in str(src["error"]) for src in failed)
        assert any("github_search_failed: down" in str(src["error"]) for src in failed)
        assert models == []
        state_path = data_root() / "radar_state.json"
        saved = json.loads(state_path.read_text(encoding="utf-8"))
        assert "last_scan_ts" not in saved
        assert saved["last_scan"] == payload["ts"]
        assert saved["scan_count"] == 1
        assert saved["last_summary"]["sources_scanned"] == 12
        assert saved["last_summary"]["for_self"] is False
        listed = client.get("/admin/daedalus/journal/list")
        assert listed.status_code == 200
        body = listed.json()
        assert body["ok"] is True
        assert body["budget"] == 150000
        assert body["threshold_pct"] == 80
        rows = body["journals"]
        assert [row["session_id"] for row in rows] == ["fresh-sess"]
        assert rows[0]["task_name"] == "maze"
        assert rows[0]["last_summary"] == "S" * 200
        assert rows[0]["paused"] is False
        assert kv.get("adn:journal:sess:stale-sess") is None
        assert kv.get("adn:journal:pause:stale-sess") is None
        paused = client.post("/admin/daedalus/journal/fresh-sess/pause")
        assert paused.status_code == 200
        assert paused.json() == {"ok": True}
        stored = kv.get("adn:journal:sess:fresh-sess")
        assert stored["status"] != "paused"
        assert kv.get("adn:journal:pause:fresh-sess")["reason"] == "owner_requested"
        again = client.get("/admin/daedalus/journal/list").json()["journals"][0]
        assert again["pause_requested"] is True
        assert again["paused"] is False
        cleared = client.post("/admin/daedalus/journal/fresh-sess/clear")
        assert cleared.json() == {"ok": True}
        assert kv.get("adn:journal:sess:fresh-sess") is None
        assert kv.get("adn:journal:pause:fresh-sess") is None


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _api(server, method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(server["url"] + path, data=data, method=method)
    token = (Path(server["data"]) / "owner.token").read_text(encoding="utf-8").strip()
    req.add_header("Authorization", "Bearer " + token)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as res:
        raw = res.read().decode()
        return json.loads(raw) if raw else {}


def _fulfill(route, status, payload):
    route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))


@pytest.fixture(scope="module")
def studio_server(tmp_path_factory):
    pytest.importorskip("playwright")
    data = tmp_path_factory.mktemp("radar-journal-data")
    port = _free_port()
    env = {k: v for k, v in __import__("os").environ.items()}
    env.update({
        "DAEDALUS_STANDALONE": "1",
        "DAEDALUS_DATA": str(data),
        "DAEDALUS_PLAYGROUND": str(data / "playground"),
        "DAEDALUS_OFFLINE": "1",
    })
    for key in (
        "DAEDALUS_MENTOR",
        "DAEDALUS_RUN_BUDGET",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENROUTER_API_KEY",
        "NOVITA_API_KEY",
        "ALLTOKEN_API_KEY",
        "GITHUB_TOKEN",
    ):
        env.pop(key, None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
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
        out = proc.stdout.read().decode("utf-8", "replace") if proc.stdout else ""
        proc.kill()
        pytest.fail("studio server did not start\n" + out[-500:])
    game = _api(server, "POST", "/api/v1/playground/projects", {"name": "Old Game", "kind": "static"})
    other = _api(server, "POST", "/api/v1/playground/projects", {"name": "Other Game", "kind": "static"})
    server["game"] = game["project"]["slug"]
    server["other"] = other["project"]["slug"]
    yield server
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _guard(page):
    leaked = []

    def guard(route):
        req = route.request
        url = req.url
        if not url.startswith("http://127.0.0.1"):
            leaked.append(url.split("?", 1)[0])
            route.abort()
            return
        path = url.split("?", 1)[0]
        if req.method not in ("GET", "HEAD") and not path.endswith("/api/v1/daedalus/effort"):
            leaked.append(req.method + " " + path)
            route.abort()
            return
        route.fallback()

    page.route("**/*", guard)
    return leaked


def _hit(page, selector):
    loc = page.locator(selector)
    loc.scroll_into_view_if_needed()
    loc.evaluate(
        """(el) => {
          const pad = 16;
          let node = el.parentElement;
          while (node) {
            const style = getComputedStyle(node);
            if (/(auto|scroll)/.test(style.overflowY) && node.scrollHeight > node.clientHeight + 1) {
              const r = el.getBoundingClientRect();
              const limit = node.getBoundingClientRect();
              if (r.bottom > limit.bottom - pad) node.scrollTop += r.bottom - limit.bottom + pad;
              if (r.top < limit.top + pad) node.scrollTop -= (limit.top + pad) - r.top;
              break;
            }
            node = node.parentElement;
          }
        }"""
    )
    loc.click()


def test_radar_and_journal_review_flow(studio_server):
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    calls = []
    discoveries = []
    dialogs = []
    held = []
    accept = {"on": False}
    mode = {"scan": "hold", "list": "hold", "pause": "hold", "clear": "ok"}
    catalog = [{"id": "github-ai-agents", "type": "github", "label": "<img src=x onerror=alert(1)>", "query": "agents"}]
    catalog.extend(
        {"id": f"src-{i}", "type": "web" if i > 8 else "github", "label": f"Source {i}", "query": f"q{i}"}
        for i in range(2, 13)
    )
    state = {"last_scan": 1700000000, "scan_count": 1, "last_summary": {"sources_scanned": 12, "for_self": False}}
    scan = {
        "ok": True,
        "scan_count": 2,
        "sources_scanned": 12,
        "total_found": 3,
        "new_discoveries": 1,
        "elapsed_s": 1.2,
        "for_self": False,
        "ts": 1700000000,
        "sources": [
            {
                "id": "github-ai-agents",
                "type": "github",
                "label": "<img src=x onerror=alert(1)>",
                "ok": True,
                "found": 2,
                "new_discoveries": 1,
                "error": None,
            },
            {
                "id": "web-bytebytego",
                "type": "web",
                "label": "ByteByteGo",
                "ok": False,
                "found": 0,
                "new_discoveries": 0,
                "error": "source blew up",
            },
        ],
    }
    journals = {
        "ok": True,
        "budget": 150000,
        "threshold_pct": 80,
        "journals": [{
            "session_id": "sess-1",
            "task_name": "maze",
            "status": "active",
            "step_count": 2,
            "tokens_est": 40,
            "budget_pct": 0.1,
            "last_summary": "<img src=x onerror=alert(1)> summary",
            "updated_at": 1700000001,
            "pause_requested": False,
            "paused": False,
        }],
    }

    def note(route, kind):
        calls.append({
            "kind": kind,
            "method": route.request.method,
            "raw": route.request.post_data,
        })

    def radar(route):
        path = route.request.url.split("?", 1)[0]
        if path.endswith("/sources"):
            note(route, "catalog")
            _fulfill(route, 200, {"sources": catalog})
            return
        if path.endswith("/state"):
            note(route, "state")
            _fulfill(route, 200, state)
            return
        if path.endswith("/scan"):
            note(route, "scan")
            if mode["scan"] == "hold":
                held.append(route)
                return
            if mode["scan"] == "abort":
                route.abort("timedout")
                return
            _fulfill(route, 200, scan)
            return
        route.fallback()

    def journal(route):
        path = route.request.url.split("?", 1)[0]
        if path.endswith("/list"):
            note(route, "list")
            if mode["list"] == "hold":
                held.append(route)
                return
            if mode["list"] == "abort":
                route.abort("timedout")
                return
            _fulfill(route, 200, journals)
            return
        if path.endswith("/pause"):
            note(route, "pause")
            if mode["pause"] == "hold":
                held.append(route)
                return
            _fulfill(route, 200, {"ok": True})
            return
        if path.endswith("/clear"):
            note(route, "clear")
            if mode["clear"] == "hold":
                held.append(route)
                return
            _fulfill(route, 200, {"ok": True})
            return
        route.fallback()

    def discoveries_route(route):
        discoveries.append(route.request.url.split("?", 1)[0])
        _fulfill(route, 200, {"discoveries": []})

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        leaked = _guard(page)
        page.route("**/admin/daedalus/radar/**", radar)
        page.route("**/admin/daedalus/journal/**", journal)
        page.route("**/admin/daedalus/discoveries**", discoveries_route)
        def on_dialog(dialog):
            dialogs.append(dialog.message)
            if accept["on"]:
                dialog.accept()
            else:
                dialog.dismiss()

        page.on("dialog", on_dialog)
        try:
            page.goto(studio_server["url"] + "/?panel=review", wait_until="domcontentloaded")
            page.wait_for_selector("#radar-scan")
            page.wait_for_selector("#journal-load")
            assert calls == []
            _hit(page, "#radar-scan")
            assert calls == []
            assert "all 12" in dialogs[-1]
            assert "radar_state.json" in dialogs[-1]
            accept["on"] = True
            _hit(page, "#radar-scan")
            page.wait_for_function("() => document.getElementById('radar-scan').disabled")
            with pytest.raises(PlaywrightTimeout):
                page.locator("#radar-scan").click(timeout=400)
            assert calls == [{"kind": "scan", "method": "POST", "raw": None}]
            _hit(page, "#workspace-nav button[data-panel=build]")
            _hit(page, "#workspace-nav button[data-panel=review]")
            assert page.locator("#radar-scan").is_disabled()
            assert "not finished" in page.locator("#radar-status").inner_text()
            assert calls == [{"kind": "scan", "method": "POST", "raw": None}]
            page.locator(f"#plist [data-slug='{studio_server['other']}']").click()
            page.wait_for_function(
                """(slug) => {
                  const row = document.querySelector("#plist [data-slug='" + slug + "']");
                  return row && row.className.indexOf("active") >= 0;
                }""",
                arg=studio_server["other"],
            )
            assert len(calls) == 1
            _hit(page, "#workspace-nav button[data-panel=build]")
            discoveries_before_scan_result = len(discoveries)
            held[0].fulfill(status=200, content_type="application/json", body=json.dumps(scan))
            held.clear()
            page.wait_for_function("() => !document.getElementById('radar-scan').disabled")
            assert len(discoveries) == discoveries_before_scan_result
            hidden = page.locator("#radar-scan-view").inner_text()
            assert "source blew up" not in hidden
            assert "for_self" not in page.locator("#radar-status").inner_text()
            discoveries_while_hidden = len(discoveries)
            _hit(page, "#workspace-nav button[data-panel=review]")
            page.wait_for_function("() => document.getElementById('radar-scan-view').textContent.includes('source blew up')")
            view = page.locator("#radar-scan-view").inner_text()
            assert "ok: true" in view
            assert "for_self: false" in view
            assert "source blew up" in view
            assert "at least one source failed" in page.locator("#radar-status").inner_text()
            assert page.locator("#radar-controls img").count() == 0
            assert len(discoveries) == discoveries_while_hidden + 1
            mode["scan"] = "abort"
            _hit(page, "#radar-scan")
            page.wait_for_function("() => document.getElementById('radar-status').textContent.includes('Outcome unknown')")
            assert page.locator("#radar-scan").is_disabled()
            assert len([row for row in calls if row["kind"] == "scan"]) == 2
            _hit(page, "#radar-catalog")
            page.wait_for_function("() => document.getElementById('radar-catalog-list').textContent.includes('12 sources')")
            assert page.locator("#radar-controls input").count() == 0
            assert page.locator("#radar-controls img").count() == 0
            _hit(page, "#radar-state")
            page.wait_for_function("() => document.getElementById('radar-state-view').textContent.includes('last_scan: 1700000000')")
            state_text = page.locator("#radar-state-view").inner_text()
            assert "last_scan_ts" not in state_text
            assert "scan_count: 1" in state_text
            accept["on"] = False
            _hit(page, "#journal-load")
            assert not any(row["kind"] == "list" for row in calls)
            assert "24 hours" in dialogs[-1]
            assert "pause keys" in dialogs[-1]
            accept["on"] = True
            _hit(page, "#journal-load")
            page.wait_for_function("() => document.getElementById('journal-load').disabled")
            _hit(page, "#workspace-nav button[data-panel=build]")
            assert page.locator("#journal-list").inner_text() == ""
            held[0].fulfill(status=200, content_type="application/json", body=json.dumps(journals))
            held.clear()
            page.wait_for_function("() => !document.getElementById('journal-load').disabled")
            assert "sess-1" not in page.locator("#journal-list").inner_text()
            _hit(page, "#workspace-nav button[data-panel=review]")
            page.wait_for_function("() => document.getElementById('journal-list').textContent.includes('sess-1')")
            listed = page.locator("#journal-list").inner_text()
            assert "150000" in listed and "80" in listed
            assert "pause_requested: false" in listed
            assert "paused: false" in listed
            assert page.locator("#journal-controls img").count() == 0
            list_count = len([row for row in calls if row["kind"] == "list"])
            mode["pause"] = "hold"
            _hit(page, "#journal-list button[data-journal-action=pause]")
            assert "sess-1" in dialogs[-1]
            assert "cooperative" in dialogs[-1]
            page.wait_for_function("() => document.querySelector('#journal-list button[data-journal-action=clear]').disabled")
            with pytest.raises(PlaywrightTimeout):
                page.locator("#journal-list button[data-journal-action=pause]").click(timeout=400)
            assert [row for row in calls if row["kind"] == "pause"] == [{"kind": "pause", "method": "POST", "raw": None}]
            assert len([row for row in calls if row["kind"] == "list"]) == list_count
            _hit(page, "#workspace-nav button[data-panel=build]")
            held[0].fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True}))
            held.clear()
            page.wait_for_function(
                """() => !document.querySelector('#journal-list button[data-journal-action=pause]')
                  || !document.querySelector('#journal-list button[data-journal-action=pause]').disabled"""
            )
            assert "pause flag" not in page.locator("#journal-status").inner_text()
            _hit(page, "#workspace-nav button[data-panel=review]")
            page.wait_for_function("() => document.getElementById('journal-list').textContent.includes('pause_requested: true')")
            assert "paused: false" in page.locator("#journal-list").inner_text()
            assert "not paused immediately" in page.locator("#journal-status").inner_text()
            assert len([row for row in calls if row["kind"] == "list"]) == list_count
            mode["clear"] = "hold"
            _hit(page, "#journal-list button[data-journal-action=clear]")
            assert "sess-1" in dialogs[-1]
            assert "irreversibly" in dialogs[-1]
            page.wait_for_function("() => document.querySelector('#journal-list button[data-journal-action=pause]').disabled")
            assert len([row for row in calls if row["kind"] == "clear"]) == 1
            assert calls[-1]["raw"] is None
            held[0].fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True}))
            held.clear()
            page.wait_for_function("() => !document.getElementById('journal-list').textContent.includes('sess-1')")
            assert len([row for row in calls if row["kind"] == "list"]) == list_count
            mode["list"] = "abort"
            _hit(page, "#journal-load")
            page.wait_for_function("() => document.getElementById('journal-status').textContent.includes('Outcome unknown')")
            assert page.locator("#journal-load").is_disabled()
            assert page.locator("#journal-list button").count() == 0 or page.locator("#journal-list button").first.is_disabled()
            assert len([row for row in calls if row["kind"] == "list"]) == list_count + 1
            page.set_viewport_size({"width": 1280, "height": 800})
            _hit(page, "#settings-link")
            page.wait_for_function("() => new URL(location.href).searchParams.get('panel') === 'settings' && window.scrollY === 0")
            page.set_viewport_size({"width": 910, "height": 640})
            assert page.evaluate("() => window.scrollY") == 0
            _hit(page, "#workspace-nav button[data-panel=review]")
            _hit(page, ".dx-lang button[data-lang=bg]")
            page.wait_for_function("() => document.getElementById('radar-scan').textContent === 'Сканирай'")
            assert page.locator("#journal-load").inner_text() == "Зареди дневника"
            assert leaked == []
        finally:
            for route in held:
                try:
                    route.abort()
                except Exception:
                    pass
            browser.close()
