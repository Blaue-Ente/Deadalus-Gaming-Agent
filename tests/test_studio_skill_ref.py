"""Skill draft and reference brief in Review.

Browser posts are intercepted. The temporary-store test stubs complete,
web search, web fetch, and mesh.route. No live model call and no owner store.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
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
    "studio.skillTitle",
    "studio.skillPlatform",
    "studio.skillPlatformGeneric",
    "studio.skillPlatformCursor",
    "studio.skillPlatformClaude",
    "studio.skillPlatformCodex",
    "studio.skillPlatformHarness",
    "studio.skillIdea",
    "studio.skillMessage",
    "studio.skillStart",
    "studio.skillRefine",
    "studio.skillGenerate",
    "studio.skillForce",
    "studio.skillDownload",
    "studio.skillBundle",
    "studio.skillHint",
    "studio.skillNeedIdea",
    "studio.skillNeedMessage",
    "studio.skillStartConfirm",
    "studio.skillRefineConfirm",
    "studio.skillGenerateConfirm",
    "studio.skillForceConfirm",
    "studio.skillStartPending",
    "studio.skillRefinePending",
    "studio.skillGeneratePending",
    "studio.skillUnknown",
    "studio.skillNoSearch",
    "studio.skillNoteIsIdea",
    "studio.skillNoTools",
    "studio.skillPhaseLine",
    "studio.skillReadyYes",
    "studio.skillReadyNo",
    "studio.skillFallback",
    "studio.skillStarted",
    "studio.skillRefined",
    "studio.skillFilesReturned",
    "studio.skillNoFiles",
    "studio.skillFilesKept",
    "studio.skillNotOk",
    "studio.refTitle",
    "studio.refField",
    "studio.refScout",
    "studio.refHint",
    "studio.refNeed",
    "studio.refConfirm",
    "studio.refPending",
    "studio.refUnknown",
    "studio.refReturned",
    "studio.refUnverified",
    "studio.refStub",
    "studio.refNotOk",
)

IDEA = "A maze with <img src=x onerror=alert(1)>"
SKILL_MD = "---\nname: maze-review\ndescription: Walk a maze.\n---\n" + ("Place a tile and keep the exit open. " * 20)
NOTES = "The side passages stay dark.\n"
FILES = {
    "skills/maze-review/SKILL.md": SKILL_MD,
    "skills/maze-review/references/notes.md": NOTES,
}
FILES_NEXT = {
    "skills/maze-review/SKILL.md": SKILL_MD + "\nSecond pass.\n",
    "skills/maze-review/references/notes.md": NOTES,
}
NOT_READY = "Skill не е готов за генериране — отговори на уточняващите въпроси или използвай force=true"
REF = "Flappy"
STUB = {
    "ok": True,
    "reference": REF,
    "title": REF,
    "summary": f"Референция: {REF}. Вдъхнови се от механиките и визията.",
    "mechanics": [],
    "visual_style": "match reference game art style",
    "scope_hints": "Core loop first, polish later",
    "sources": [{"title": "<img src=x onerror=alert(1)>", "url": "https://example.test/flappy"}],
}


def _slice(src: str, start: str, end: str) -> str:
    at = src.index(start)
    return src[at:src.index(end, at + len(start))]


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _token(server) -> str:
    return (Path(server["data"]) / "owner.token").read_text(encoding="utf-8").strip()


def _api(server, method, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(server["url"] + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + _token(server))
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as res:
        raw = res.read().decode()
        return json.loads(raw) if raw else {}


def _fulfill(route, status, payload):
    body = "" if payload is None else json.dumps(payload)
    route.fulfill(status=status, content_type="application/json", body=body)


def _view(session_id, **extra):
    data = {
        "ok": True,
        "session_id": session_id,
        "phase": "ready",
        "platform": "generic",
        "idea": IDEA,
        "research_notes": IDEA,
        "tool_steps": [],
        "skill_name": "skill",
        "summary": IDEA,
        "clarifying_questions": [],
        "ready_for_generation": False,
        "validation_warnings": [],
    }
    data.update(extra)
    return data


@pytest.fixture(scope="module")
def studio_server(tmp_path_factory):
    pytest.importorskip("playwright")
    data = tmp_path_factory.mktemp("skill-ref-data")
    port = _free_port()
    env = dict(os.environ)
    env.update({
        "DAEDALUS_STANDALONE": "1",
        "DAEDALUS_DATA": str(data),
        "DAEDALUS_PLAYGROUND": str(data / "playground"),
        "DAEDALUS_OFFLINE": "1",
    })
    for key in (
        "DAEDALUS_MENTOR",
        "DAEDALUS_RUN_BUDGET",
        "FAL_KEY",
        "FAL_API_KEY",
        "TRIPO_API_KEY",
        "MESHY_API_KEY",
        "SUNO_API_KEY",
        "OPENAI_API_KEY",
        "ALLTOKEN_API_KEY",
        "OPENROUTER_API_KEY",
        "NOVITA_API_KEY",
        "GITHUB_TOKEN",
        "ANTHROPIC_API_KEY",
        "DAEDALUS_API_KEY",
    ):
        env.pop(key, None)
    server_log = tempfile.TemporaryFile()  # a file, never an unread pipe: a full pipe blocks the server
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT),
        env=env,
        stdout=server_log,
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
        server_log.seek(0)
        out = server_log.read().decode("utf-8", "replace")
        proc.kill()
        pytest.fail("studio server did not start\n" + out[-2000:])
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


def test_skill_and_reference_contract_names_the_real_routes():
    for key in KEYS:
        assert I18N.count(f'"{key}":') == 2, key
    review = HTML[HTML.find('id="panel-review"'):HTML.find('id="panel-settings"')]
    assert 'id="discovery-controls"' in review
    assert 'id="skill-controls"' in review
    assert 'id="ref-controls"' in review
    assert review.index("discovery-controls") < review.index("skill-controls") < review.index("ref-controls")
    assert "panel-skill" not in HTML
    assert "gist" not in review.lower()
    assert "innerHTML" not in review
    for key in (
        "studio.skillStartConfirm",
        "studio.skillRefineConfirm",
        "studio.skillGenerateConfirm",
        "studio.skillForceConfirm",
        "studio.refConfirm",
    ):
        line = next(item for item in I18N.splitlines() if f'"{key}":' in item)
        assert "Grok" not in line
        assert "Composer" not in line
    assert "DuckDuckGo" in I18N
    assert "alltoken:deepseek-v3.2" in I18N
    block = _slice(WORK, "function startSkill", "async function reviewAct")
    assert "setInterval" not in block
    assert "innerHTML" not in block
    assert "gist" not in block
    assert "/skill-creator/session" not in block
    assert "save_slug" not in block
    start = _slice(block, "function startSkill", "function refineSkill")
    assert "JSON.stringify({ idea, platform })" in start
    assert "context" not in start
    assert "studio.skillNoSearch" in _slice(WORK, "function describeSkillStart", "async function reviewAct")
    generate = _slice(block, "function generateSkill", "function downloadText")
    assert "JSON.stringify({ session_id: id })" in generate
    assert "JSON.stringify({ session_id: id, force: true })" in generate
    scout = _slice(WORK, "function scoutReference", "async function reviewAct")
    assert "JSON.stringify({ reference })" in scout
    assert "/projects/" not in scout
    leave = _slice(WORK, 'if (currentPanel === "review" && panel !== "review")', "currentPanel = panel;")
    assert "skillGen += 1" in leave
    assert "refGen += 1" in leave
    assert "skillOp = null" not in leave
    assert "refOp = null" not in leave
    slug = _slice(WORK, "function onSlug", '$("workspace-nav")')
    assert "skill-creator" not in slug
    assert "reference-scout" not in slug


def test_temp_skill_and_reference_handlers_are_stubbed(tmp_path, monkeypatch):
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
    calls = []
    search_calls = []
    fetch_calls = []
    mesh_calls = []
    claude_calls = []
    mode = {"complete": "boom", "reply": "", "mesh": "ok"}

    async def fake_complete(**kwargs):
        calls.append(kwargs)
        if mode["complete"] == "boom":
            raise RuntimeError("llm down")
        return {"ok": True, "reply": mode["reply"]}

    async def fake_search(*args, **kwargs):
        search_calls.append({"args": args, "kwargs": kwargs})
        return {
            "ok": True,
            "results": [{"title": "Flappy", "url": "https://example.test/flappy", "snippet": "bird"}],
            "result_count": 1,
        }

    async def fake_fetch(url, max_chars=10000):
        fetch_calls.append({"url": url, "max_chars": max_chars})
        return {"ok": True, "text": "A bird hops between pipes."}

    async def fake_route(**kwargs):
        mesh_calls.append(kwargs)
        if mode["mesh"] == "boom":
            raise RuntimeError("mesh down")
        return {"content": [{"text": json.dumps({
            "title": "Flappy",
            "summary": "A small bird game.",
            "mechanics": ["tap"],
            "visual_style": "flat",
            "scope_hints": "one screen",
        })}]}

    async def fake_claude(*_args, **_kwargs):
        claude_calls.append("called")
        return "stub"

    monkeypatch.setattr("daedalus.llm_client.complete", fake_complete)
    monkeypatch.setattr("daedalus.tools.web_search_tool", fake_search)
    monkeypatch.setattr("daedalus.tools.web_fetch_tool", fake_fetch)
    monkeypatch.setattr("daedalus.daedalus_mesh.route", fake_route)
    monkeypatch.setattr("daedalus.agent._call_claude", fake_claude)
    from daedalus import playground_skill_creator as sc

    sc._sessions.clear()
    try:
        from fastapi.testclient import TestClient

        from app.main import app
        from daedalus import kv

        with TestClient(app) as client:
            start_url = "/admin/daedalus/playground/api/skill-creator/start"
            refine_url = "/admin/daedalus/playground/api/skill-creator/refine"
            generate_url = "/admin/daedalus/playground/api/skill-creator/generate"
            scout_url = "/admin/daedalus/playground/api/reference-scout"
            empty = client.post(start_url, json={"idea": "  "})
            assert empty.status_code == 400
            assert calls == []
            assert search_calls == []
            idea = "Remember the maze exits"
            started = client.post(start_url, json={
                "idea": idea,
                "platform": "generic",
                "context": "SECRET-CONTEXT-TOKEN",
            })
            assert started.status_code == 200
            body = started.json()
            # A0-h: the provider call failed, so the typed-idea stub is reported as a failure.
            assert body["ok"] is False and body["error"] == "llm_failed" and body["stub"] is True
            assert body["skill_name"] == "skill"
            assert body["ready_for_generation"] is False
            assert body["phase"] == "ready"
            assert body["research_notes"] == idea
            assert body["tool_steps"] == []
            assert len(calls) == 1
            assert calls[0]["max_tokens"] == 1200
            assert "SECRET-CONTEXT-TOKEN" not in calls[0]["system"]
            assert "SECRET-CONTEXT-TOKEN" not in calls[0]["user"]
            assert search_calls == []
            assert mesh_calls == []
            assert kv.get(f"skill-creator:{body['session_id']}")["idea"] == idea
            mode["complete"] = "ok"
            mode["reply"] = json.dumps({
                "skill_name": "maze-review",
                "summary": "A maze skill",
                "clarifying_questions": [{"question": "How large?", "why": "scope"}],
                "ready_for_generation": False,
            })
            planned = client.post(start_url, json={"idea": idea, "platform": "cursor"})
            assert planned.status_code == 200
            plan = planned.json()
            sid = plan["session_id"]
            assert plan["ready_for_generation"] is False
            assert plan["clarifying_questions"][0]["question"] == "How large?"
            before = len(calls)
            blocked = client.post(generate_url, json={"session_id": sid})
            assert blocked.status_code == 409
            assert "не е готов" in blocked.json()["error"]
            assert len(calls) == before
            skill_md = (
                "---\nname: maze-review\ndescription: Review a maze.\n---\n"
                + ("Place one tile and walk the path. " * 180)
            )
            notes = "Remember the exits.\n" * 20
            mode["reply"] = (
                "===FILE: SKILL.md===\n" + skill_md + "\n===END===\n"
                "===FILE: references/notes.md===\n" + notes + "\n===END===\n"
            )
            forced = client.post(generate_url, json={"session_id": sid, "force": True})
            assert forced.status_code == 200
            generated = forced.json()
            assert calls[-1]["max_tokens"] == 8000
            assert calls[-1]["client_id"] == f"skill-gen/{sid}"
            assert generated["files"][".cursor/skills/maze-review/SKILL.md"] == skill_md
            assert generated["files"][".cursor/skills/maze-review/references/notes.md"] == notes
            fetched = client.get(f"/admin/daedalus/playground/api/skill-creator/session/{sid}")
            assert fetched.status_code == 200
            session = fetched.json()
            assert "files" not in session
            assert session["generated_files"] == ["SKILL.md", "references/notes.md"]
            assert session["skill_md_preview"] == skill_md[:4000]
            assert session["skill_md_preview"] != skill_md
            mode["reply"] = "no files here"
            empty_files = client.post(generate_url, json={"session_id": sid, "force": True})
            assert empty_files.status_code == 200
            assert empty_files.json()["ok"] is True
            assert empty_files.json()["files"] == {}
            assert any("празен" in item for item in empty_files.json()["validation_warnings"])
            before = len(calls)
            missing_message = client.post(refine_url, json={"session_id": sid, "message": "  "})
            assert missing_message.status_code == 400
            missing_session = client.post(refine_url, json={"session_id": "missing-session", "message": "hello"})
            assert missing_session.status_code == 404
            assert len(calls) == before
            blank = client.post(scout_url, json={"reference": "  "})
            assert blank.status_code == 400
            assert search_calls == []
            found = client.post(scout_url, json={"reference": "Flappy"})
            assert found.status_code == 200
            assert found.json()["ok"] is True
            assert found.json()["summary"] == "A small bird game."
            assert search_calls
            assert all(item["kwargs"]["record_discoveries"] is False for item in search_calls)
            assert all(item["kwargs"]["for_self"] is True for item in search_calls)
            assert mesh_calls[-1]["model"] == "alltoken:deepseek-v3.2"
            assert mesh_calls[-1]["max_tokens"] == 1200
            assert list((tmp_path / "projects").rglob("_reference.json")) == []
            mode["mesh"] = "boom"
            stubbed = client.post(scout_url, json={"reference": "Pong"})
            assert stubbed.status_code == 200
            # A0-h: a failed synthesis is not a success and never yields a brief block.
            assert stubbed.json()["ok"] is False
            assert stubbed.json()["degraded"] is True and stubbed.json()["error"] == "synthesis_failed"
            assert stubbed.json()["brief_block"] == "" and stubbed.json()["summary"] == ""
            assert stubbed.json()["fallback"]["summary"] == "Референция: Pong. Вдъхнови се от механиките и визията."
            assert stubbed.json()["fallback"]["visual_style"] == "match reference game art style"
            fetch_before = len(fetch_calls)
            page = client.post(scout_url, json={"reference": "https://example.test/game"})
            assert page.status_code == 200
            assert any(item["url"] == "https://example.test/game" for item in fetch_calls[fetch_before:])
            project = client.post(
                "/admin/daedalus/playground/api/projects/demo-slug/reference-scout",
                json={"reference": "Asteroids"},
            )
            assert project.status_code == 200
            assert list(tmp_path.rglob("_reference.json")) == []
            assert claude_calls == []
            assert len(calls) == before
    finally:
        sc._sessions.clear()
        Daedalus._instance = None
        DaedalusMemory._instance = None
        reset_sqlite_pool()
        data_root.cache_clear()
        playground_projects_dir.cache_clear()


def _guard(page):
    leaked = []

    def guard(route):
        req = route.request
        url = req.url
        if not url.startswith("http://127.0.0.1"):
            leaked.append(url)
            route.abort()
            return
        path = url.split("?", 1)[0]
        if req.method not in ("GET", "HEAD") and not path.endswith("/api/v1/daedalus/effort"):
            leaked.append(req.method + " " + url)
            route.abort()
            return
        route.fallback()

    page.route("**/*", guard)
    return leaked


def _arm(page, state, held, posts):
    def skills(route):
        req = route.request
        path = req.url.split("?", 1)[0]
        raw = req.post_data
        body = json.loads(raw) if raw else None
        if req.method == "GET":
            posts.append({"kind": "session-get", "body": None})
            route.abort()
            return
        if path.endswith("/start"):
            posts.append({"kind": "start", "body": body})
            if state.get("hold_start"):
                held.setdefault("start", []).append(route)
                return
            _fulfill(route, 200, state["start"])
            return
        if path.endswith("/refine"):
            posts.append({"kind": "refine", "body": body})
            if state.get("hold_refine"):
                held.setdefault("refine", []).append(route)
                return
            _fulfill(route, 200, state["refine"])
            return
        if path.endswith("/generate"):
            posts.append({"kind": "generate", "body": body})
            if state.get("hold_generate"):
                held.setdefault("generate", []).append(route)
                return
            _fulfill(route, state.get("generate_status", 200), state["generate"])
            return
        route.fallback()

    def scout(route):
        req = route.request
        path = req.url.split("?", 1)[0]
        if "/projects/" in path:
            posts.append({"kind": "project-scout", "body": req.post_data})
            route.abort()
            return
        raw = req.post_data
        posts.append({"kind": "scout", "body": json.loads(raw) if raw else None})
        if state.get("abort_scout"):
            route.abort("timedout")
            return
        if state.get("hold_scout"):
            held.setdefault("scout", []).append(route)
            return
        _fulfill(route, 200, state["scout"])

    page.route("**/playground/api/skill-creator/**", skills)
    page.route("**/playground/api/reference-scout", scout)
    page.route("**/playground/api/projects/**/reference-scout", scout)


def _release(held, key, status, payload):
    routes = list(held.get(key) or [])
    held[key] = []
    for route in routes:
        _fulfill(route, status, payload)


def _drain(held):
    for key in list(held):
        for route in held.get(key) or []:
            try:
                route.fulfill(status=200, content_type="application/json", body="{}")
            except Exception:
                pass
        held[key] = []


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
    box = loc.bounding_box()
    assert box is not None and box["width"] > 8 and box["height"] > 8
    point = loc.evaluate(
        """(el) => {
          const r = el.getBoundingClientRect();
          const x = r.left + r.width / 2;
          const y = r.top + r.height / 2;
          const top = document.elementFromPoint(x, y);
          return {ok: top === el || (top && el.contains(top)), tag: top && top.tagName};
        }"""
    )
    assert point["ok"], selector + " covered by " + str(point)
    viewport = page.viewport_size
    assert box["y"] + box["height"] <= viewport["height"]
    assert box["x"] + box["width"] <= viewport["width"]
    loc.click()


def _status_has(page, element, text):
    page.wait_for_function(
        """([element, text]) => (document.getElementById(element).textContent || '').includes(text)""",
        arg=[element, text],
    )


def _enabled(page, element):
    page.wait_for_function(
        """(id) => {
          const el = document.getElementById(id);
          return el && !el.disabled;
        }""",
        arg=element,
    )


def _download(page, selector):
    with page.expect_download() as got:
        _hit(page, selector)
    download = got.value
    return download.suggested_filename, Path(download.path()).read_bytes()


def _bundle(name, files):
    text = "# Skill bundle: " + name + "\n\n"
    for path, content in files.items():
        text += "\n--- " + path + " ---\n" + content + "\n"
    return text.encode()


def test_skill_and_reference_review_flow(studio_server):
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    state = {
        "start": _view("sess1"),
        "refine": _view(
            "sess1",
            ready_for_generation=True,
            phase="ready",
            skill_name="maze-review",
            summary="clear",
            clarifying_questions=[{"question": "<img src=x onerror=alert(1)>", "why": "scope"}],
        ),
        "generate": _view("sess1", phase="generated", skill_name="maze-review", files=FILES),
        "generate_status": 200,
        "scout": STUB,
        "hold_start": True,
        "hold_refine": False,
        "hold_generate": False,
        "hold_scout": False,
        "abort_scout": False,
    }
    held = {"start": [], "refine": [], "generate": [], "scout": []}
    posts = []
    dialogs = []
    accept = {"on": False}

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        leaked = _guard(page)
        _arm(page, state, held, posts)
        page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.accept() if accept["on"] else dialog.dismiss()))
        try:
            page.goto(studio_server["url"] + "/?panel=review", wait_until="domcontentloaded")
            page.wait_for_selector("#skill-start")
            _hit(page, "#skill-start")
            _status_has(page, "skill-status", "Enter an idea. Nothing was sent.")
            assert posts == []
            assert dialogs == []
            page.locator("#skill-idea").fill("  " + IDEA + "  ")
            _hit(page, "#skill-start")
            assert "configured provider" in dialogs[-1]
            assert "1200" in dialogs[-1]
            assert "Grok" not in dialogs[-1]
            assert "Composer" not in dialogs[-1]
            assert posts == []
            accept["on"] = True
            _hit(page, "#skill-start")
            page.wait_for_function("() => document.getElementById('skill-start').disabled")
            with pytest.raises(PlaywrightTimeout):
                page.locator("#skill-start").click(timeout=400)
            assert posts == [{"kind": "start", "body": {"idea": IDEA, "platform": "generic"}}]
            _hit(page, "#workspace-nav button[data-panel=build]")
            _hit(page, "#workspace-nav button[data-panel=review]")
            assert page.locator("#skill-start").is_disabled()
            assert "still starting" in page.locator("#skill-status").inner_text()
            assert len(posts) == 1
            page.locator(f"#plist [data-slug='{studio_server['other']}']").click()
            page.wait_for_timeout(50)
            assert len(posts) == 1
            _hit(page, "#workspace-nav button[data-panel=build]")
            _release(held, "start", 200, state["start"])
            _enabled(page, "skill-start")
            page.evaluate("() => new Promise((resolve) => setTimeout(resolve, 50))")
            hidden = page.locator("#skill-status").evaluate("el => el.textContent")
            assert "is stored" not in hidden
            assert "local fallback" not in hidden
            _hit(page, "#workspace-nav button[data-panel=review]")
            _status_has(page, "skill-status", "Session sess1 is stored.")
            assert "No web search ran" in page.locator("#skill-status").inner_text()
            assert "Generation unlocked: no" in page.locator("#skill-phase").inner_text()
            assert "local fallback plan" in page.locator("#skill-status").inner_text()
            assert page.locator("#skill-controls img").count() == 0
            assert page.locator("#skill-generate").is_disabled()
            assert page.locator("#skill-force").is_enabled()
            assert len(posts) == 1
            _hit(page, "#skill-refine")
            _status_has(page, "skill-status", "Enter a refine message. Nothing was sent.")
            assert len(posts) == 1
            state["hold_generate"] = True
            state["generate"] = _view("sess1", phase="generated", skill_name="maze-review", files=FILES)
            _hit(page, "#skill-force")
            assert "force true" in dialogs[-1]
            assert "8000" in dialogs[-1]
            page.wait_for_function("() => document.getElementById('skill-force').disabled")
            with pytest.raises(PlaywrightTimeout):
                page.locator("#skill-force").click(timeout=400)
            assert posts[-1]["kind"] == "generate"
            assert posts[-1]["body"] == {"session_id": "sess1", "force": True}
            assert len([item for item in posts if item["kind"] == "generate"]) == 1
            _hit(page, "#workspace-nav button[data-panel=build]")
            _release(held, "generate", 200, state["generate"])
            _enabled(page, "skill-force")
            page.evaluate("() => new Promise((resolve) => setTimeout(resolve, 50))")
            assert "included files" not in page.locator("#skill-status").evaluate("el => el.textContent")
            _hit(page, "#workspace-nav button[data-panel=review]")
            _status_has(page, "skill-status", "This reply included files.")
            name, body = _download(page, "#skill-download")
            assert name == "SKILL.md"
            assert body.decode() == SKILL_MD
            bundle_name, bundle = _download(page, "#skill-bundle")
            assert bundle_name.endswith(".txt")
            assert not bundle.startswith(b"PK")
            assert bundle == _bundle("maze-review", FILES)
            state["hold_refine"] = True
            page.locator("#skill-message").fill("Answer the size")
            _hit(page, "#skill-refine")
            page.wait_for_function("() => document.getElementById('skill-refine').disabled")
            name, body = _download(page, "#skill-download")
            assert body.decode() == SKILL_MD
            with pytest.raises(PlaywrightTimeout):
                page.locator("#skill-refine").click(timeout=400)
            assert len([item for item in posts if item["kind"] == "refine"]) == 1
            assert posts[-1]["body"] == {"session_id": "sess1", "message": "Answer the size"}
            _hit(page, "#workspace-nav button[data-panel=build]")
            _release(held, "refine", 200, state["refine"])
            _enabled(page, "skill-generate")
            page.evaluate("() => new Promise((resolve) => setTimeout(resolve, 50))")
            assert "was refined" not in page.locator("#skill-status").evaluate("el => el.textContent")
            _hit(page, "#workspace-nav button[data-panel=review]")
            _status_has(page, "skill-status", "Session sess1 was refined.")
            assert page.locator("#skill-controls img").count() == 0
            assert "<img src=x" in page.locator("#skill-questions").inner_text()
            name, body = _download(page, "#skill-download")
            assert body.decode() == SKILL_MD
            state["hold_generate"] = False
            state["generate_status"] = 409
            state["generate"] = {"ok": False, "error": NOT_READY}
            _hit(page, "#skill-generate")
            _status_has(page, "skill-status", NOT_READY)
            assert "Try again" not in page.locator("#skill-status").inner_text()
            generated = [item for item in posts if item["kind"] == "generate"]
            assert generated[-1]["body"] == {"session_id": "sess1"}
            assert page.locator("#skill-force").is_enabled()
            state["generate_status"] = 200
            state["generate"] = _view(
                "sess1",
                phase="generated",
                ready_for_generation=True,
                skill_name="maze-review",
                summary="clear",
                files=FILES_NEXT,
            )
            _hit(page, "#skill-force")
            assert posts[-1]["body"] == {"session_id": "sess1", "force": True}
            _status_has(page, "skill-status", "This reply included files.")
            name, body = _download(page, "#skill-download")
            assert body.decode() == FILES_NEXT["skills/maze-review/SKILL.md"]
            state["generate"] = _view(
                "sess1",
                phase="generated",
                ready_for_generation=True,
                skill_name="maze-review",
                summary="clear",
                files={},
                validation_warnings=["SKILL.md е празен"],
            )
            _hit(page, "#skill-generate")
            _status_has(page, "skill-status", "This reply included no files.")
            assert "SKILL.md е празен" in page.locator("#skill-status").inner_text()
            name, body = _download(page, "#skill-download")
            assert body.decode() == FILES_NEXT["skills/maze-review/SKILL.md"]
            state["generate"] = _view(
                "sess1",
                phase="generated",
                ready_for_generation=True,
                skill_name="maze-review",
                files={"skills/maze-review/SKILL.md": ""},
                validation_warnings=["partial"],
            )
            _hit(page, "#skill-generate")
            _status_has(page, "skill-status", "This reply included no files.")
            name, body = _download(page, "#skill-download")
            assert body.decode() == FILES_NEXT["skills/maze-review/SKILL.md"]
            _hit(page, "#ref-scout")
            _status_has(page, "ref-status", "Enter a reference. Nothing was sent.")
            assert not any(item["kind"] == "scout" for item in posts)
            page.locator("#ref-input").fill("  " + REF + "  ")
            accept["on"] = False
            _hit(page, "#ref-scout")
            assert "DuckDuckGo" in dialogs[-1]
            assert "alltoken:deepseek-v3.2" in dialogs[-1]
            assert "Grok" not in dialogs[-1]
            assert not any(item["kind"] == "scout" for item in posts)
            accept["on"] = True
            state["hold_scout"] = True
            _hit(page, "#ref-scout")
            page.wait_for_function("() => document.getElementById('ref-scout').disabled")
            with pytest.raises(PlaywrightTimeout):
                page.locator("#ref-scout").click(timeout=400)
            scouts = [item for item in posts if item["kind"] == "scout"]
            assert scouts == [{"kind": "scout", "body": {"reference": REF}}]
            _hit(page, "#workspace-nav button[data-panel=build]")
            _hit(page, "#workspace-nav button[data-panel=review]")
            assert page.locator("#ref-scout").is_disabled()
            assert "still being gathered" in page.locator("#ref-status").inner_text()
            assert len([item for item in posts if item["kind"] == "scout"]) == 1
            _hit(page, "#workspace-nav button[data-panel=build]")
            _release(held, "scout", 200, STUB)
            _enabled(page, "ref-scout")
            page.evaluate("() => new Promise((resolve) => setTimeout(resolve, 50))")
            assert "A brief was returned" not in page.locator("#ref-status").evaluate("el => el.textContent")
            _hit(page, "#workspace-nav button[data-panel=review]")
            _status_has(page, "ref-status", "A brief was returned.")
            assert "not saved on the project" in page.locator("#ref-status").inner_text()
            assert "not verified research" in page.locator("#ref-brief").inner_text()
            assert "generic fallback" in page.locator("#ref-brief").inner_text()
            assert page.locator("#ref-controls img").count() == 0
            assert "<img src=x" in page.locator("#ref-brief").inner_text()
            state["hold_scout"] = False
            state["abort_scout"] = True
            _hit(page, "#ref-scout")
            _status_has(page, "ref-status", "Outcome unknown")
            assert "Not safe to retry" in page.locator("#ref-status").inner_text()
            assert "Try again" not in page.locator("#ref-status").inner_text()
            assert "generic fallback" in page.locator("#ref-brief").inner_text()
            assert page.locator("#ref-scout").is_disabled()
            posted = len([item for item in posts if item["kind"] == "scout"])
            with pytest.raises(PlaywrightTimeout):
                page.locator("#ref-scout").click(timeout=400)
            _hit(page, "#workspace-nav button[data-panel=build]")
            _hit(page, "#workspace-nav button[data-panel=review]")
            assert page.locator("#ref-scout").is_disabled()
            assert "Outcome unknown" in page.locator("#ref-status").inner_text()
            assert len([item for item in posts if item["kind"] == "scout"]) == posted
            assert not any(item["kind"] == "project-scout" for item in posts)
            assert not any(item["kind"] == "session-get" for item in posts)
            page.locator("#skill-idea").fill(IDEA)
            for width, height in ((1280, 800), (910, 640)):
                page.set_viewport_size({"width": width, "height": height})
                _hit(page, "#settings-link")
                page.wait_for_function("() => new URL(location.href).searchParams.get('panel') === 'settings'")
                assert page.url.startswith(studio_server["url"] + "/")
                assert page.locator("#panel-settings").is_visible()
                assert page.locator("#skill-idea").input_value() == IDEA
                assert page.evaluate("() => window.scrollY") == 0
            page.locator(".dx-lang button[data-lang=bg]").click()
            page.wait_for_function("() => document.getElementById('skill-start').textContent === 'Започни'")
            assert page.locator("#ref-scout").inner_text() == "Разузнай"
            assert leaked == []
        finally:
            _drain(held)
            browser.close()
