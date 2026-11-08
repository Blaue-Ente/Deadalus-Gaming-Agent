"""Planner draft/approve/delete and discovery approve/reject in Review.

Browser posts are intercepted. The temporary-store test stubs chat and the
provider call. No live model turn and no owner store.
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
    "studio.planGenerateConfirm",
    "studio.planApproveConfirm",
    "studio.planDeleteConfirm",
    "studio.planDraft",
    "studio.planApproved",
    "studio.planDeleted",
    "studio.planUnknown",
    "studio.planGeneratePending",
    "studio.planApprovePending",
    "studio.discoveryApproveConfirm",
    "studio.discoveryScheduled",
    "studio.discoveryUnscheduled",
    "studio.discoveryRejectConfirm",
    "studio.discoveryUnknown",
    "studio.discoveryApprovePending",
    "studio.discoveryForSelf",
)

GOAL = "Build a small maze"
TITLE = "Maze <img src=x onerror=alert(1)>"
PLAN = {
    "id": "plan_demo",
    "title": TITLE,
    "description": "a draft",
    "status": "draft",
    "steps": [{"id": "step_1", "step_order": 1, "title": "Lay tiles", "description": "place", "status": "pending"}],
}
PLAN_KEEP = {
    "id": "plan_keep",
    "title": "Keep title",
    "description": "stays",
    "status": "active",
    "steps": [],
}
DISC_PART = {
    "id": "disc_part",
    "content": "useful <img src=x onerror=alert(1)>",
    "metadata": {
        "title": "Find <img src=x onerror=alert(1)>",
        "url": "https://example.test/tiles",
        "status": "pending",
        "kind": "library",
        "for_self": True,
    },
}
DISC_PEND = {
    "id": "disc_pend",
    "content": "later",
    "metadata": {"title": "Later", "url": "https://example.test/later", "status": "pending", "kind": "article"},
}
DISC_NO = {
    "id": "disc_no",
    "content": "skip",
    "metadata": {"title": "Skip", "url": "https://example.test/skip", "status": "pending", "kind": "tool"},
}
DISC_FILE = {
    "id": "disc_file",
    "content": "file",
    "metadata": {"title": "File plan", "url": "https://example.test/file", "status": "pending", "kind": "doc"},
}
DISC_BG = {
    "id": "disc_bg",
    "content": "bg",
    "metadata": {"title": "Keep find", "url": "https://example.test/bg", "status": "pending", "kind": "article"},
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


@pytest.fixture(scope="module")
def studio_server(tmp_path_factory):
    pytest.importorskip("playwright")
    data = tmp_path_factory.mktemp("plan-discovery-data")
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


def test_plan_discovery_contract_names_the_real_dispatch():
    for key in KEYS:
        assert I18N.count(f'"{key}":') == 2, key
    review = HTML[HTML.find('id="panel-review"'):HTML.find('id="panel-settings"')]
    assert 'id="plan-controls"' in review
    assert 'id="discovery-controls"' in review
    assert 'id="blocker-controls"' in review
    for banned in ("panel-planner", "panel-discoveries", "/progress"):
        assert banned not in review
    block = _slice(WORK, "function reviewUnresolved", "async function reviewAct")
    assert "setInterval" not in block
    assert "innerHTML" not in block
    assert "/progress" not in block
    assert 'plan_id === "pending"' in block
    assert "JSON.stringify({ goal })" in block
    assert "Grok" not in block
    assert "Composer" not in block
    assert "studio.planUnknown" in block
    assert "studio.discoveryUnknown" in block
    assert "timed out" in block
    assert "failed to fetch" in block
    approve = _slice(block, "function approvePlan", "function deletePlan")
    delete = _slice(block, "function deletePlan", "function discoveryStill")
    assert "body" not in approve
    assert "body" not in delete
    discovery = _slice(block, "function approveDiscovery", "function rejectDiscovery")
    recorded = discovery.index("discoveryApproved.add(id)")
    assert "discoveryStill(gen)" in discovery[recorded:]
    assert 'discoveryApproveOp.set(id, "pending")' in discovery
    assert 'discoveryApproveOp.set(id, "unknown")' in discovery
    assert 'planGenerateOp = "unknown"' in block
    assert 'planApproveOp.set(id, "pending")' in approve
    assert 'planApproveOp.set(id, "unknown")' in approve
    assert "setInterval" not in block
    assert "retry" not in discovery
    assert 'method: "DELETE"' in delete
    leave = _slice(WORK, "function showPanel", "function slugPath")
    assert "planFlight = 0" in leave
    assert "discoveryFlight = 0" in leave
    assert "planApproveOp" not in leave
    assert "discoveryApproveOp" not in leave
    assert "planGenerateOp" not in leave
    confirm = I18N[I18N.find('"studio.discoveryApproveConfirm"'):I18N.find('"studio.discoveryForSelf"')]
    assert "OpenRouter Claude Sonnet" in confirm
    assert "Novita DeepSeek" in confirm
    assert "Grok" not in confirm
    assert "Composer" not in confirm
    assert "free or local" in confirm
    generate = I18N[I18N.find('"studio.planGenerateConfirm"'):I18N.find('"studio.planDraft"')]
    assert "full agent turn" in generate or "пълен агентен ход" in generate
    assert "for_self" in generate


def test_temp_plan_and_discovery_do_not_call_the_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.setenv("DAEDALUS_OFFLINE", "1")
    monkeypatch.delenv("DAEDALUS_USE_ADN_DB", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("NOVITA_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DAEDALUS_API_KEY", raising=False)
    from daedalus.agent import Daedalus
    from daedalus.memory import DaedalusMemory
    from daedalus.paths import data_root, playground_projects_dir
    from daedalus.store import reset_sqlite_pool

    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    reset_sqlite_pool()
    Daedalus._instance = None
    DaedalusMemory._instance = None
    chat_calls = []
    claude_calls = []
    schedule_calls = []
    mode = {
        "reply": json.dumps({
            "title": "Maze",
            "description": "walk",
            "steps": [{"title": "Lay tiles", "description": "place them"}],
        }),
        "schedule": "pending",
    }

    async def fake_chat(self, message, session_id=None, for_self=False, **kwargs):
        chat_calls.append({"message": message, "session_id": session_id, "for_self": for_self})
        return {"reply": mode["reply"]}

    async def fake_claude(*_args, **_kwargs):
        claude_calls.append("called")
        return "stub"

    def fake_schedule(self, discovery):
        schedule_calls.append(discovery.get("id"))
        if mode["schedule"] == "boom":
            raise RuntimeError("mesh missing")
        return "pending"

    monkeypatch.setattr(Daedalus, "chat", fake_chat)
    monkeypatch.setattr(Daedalus, "_schedule_plan_generation", fake_schedule)
    monkeypatch.setattr("daedalus.agent._call_claude", fake_claude)
    try:
        from fastapi.testclient import TestClient

        from app.main import app
        from daedalus.agent import get_daedalus
        from daedalus.continuity import list_tasks

        with TestClient(app) as client:
            empty = client.post("/admin/daedalus/planner/plans/auto", json={"goal": "   "})
            assert empty.status_code == 400
            long_goal = "x" * 2001
            too_long = client.post("/admin/daedalus/planner/plans/auto", json={"goal": long_goal})
            assert too_long.status_code == 400
            assert chat_calls == []
            created = client.post("/admin/daedalus/planner/plans/auto", json={"goal": "Build a maze"})
            assert created.status_code == 200
            body = created.json()
            assert body["ok"] is True
            assert body["status"] == "draft"
            pid = body["plan_id"]
            assert chat_calls[0]["for_self"] is True
            assert str(chat_calls[0]["session_id"]).startswith("autoplan_")
            assert "Build a maze" in chat_calls[0]["message"]
            assert list_tasks() == []
            stored = client.get(f"/admin/daedalus/planner/plans/{pid}")
            assert stored.status_code == 200
            assert stored.json()["status"] == "draft"
            assert len(stored.json()["steps"]) == 1
            mode["reply"] = "not json"
            before = len(chat_calls)
            rejected = client.post("/admin/daedalus/planner/plans/auto", json={"goal": "nope"})
            assert rejected.status_code == 422
            assert rejected.json()["ok"] is False
            assert len(chat_calls) == before + 1
            listed = client.get("/admin/daedalus/planner/plans")
            assert [row["id"] for row in listed.json()["plans"]] == [pid]
            approved = client.post(f"/admin/daedalus/planner/plans/{pid}/approve")
            assert approved.status_code == 200
            assert approved.json()["ok"] is True
            assert approved.json()["status"] == "active"
            assert len(approved.json()["tasks_created"]) == 1
            tasks = list_tasks()
            assert len(tasks) == 1
            assert "Lay tiles" in tasks[0]["title"]
            assert f"[plan:{pid}]" in tasks[0]["summary"]
            assert len(chat_calls) == before + 1
            again = client.post(f"/admin/daedalus/planner/plans/{pid}/approve")
            assert again.status_code == 409
            assert len(list_tasks()) == 1
            removed = client.delete(f"/admin/daedalus/planner/plans/{pid}")
            assert removed.status_code == 200
            assert removed.json()["ok"] is True
            missing = client.get(f"/admin/daedalus/planner/plans/{pid}")
            assert missing.status_code == 404
            import sqlite3
            steps = sqlite3.connect(tmp_path / "data" / "daedalus.sqlite").execute(
                "SELECT COUNT(*) FROM daedalus_plan_steps WHERE plan_id = ?",
                (pid,),
            ).fetchone()[0]
            assert steps == 0
            assert len(list_tasks()) == 1
            agent = get_daedalus()
            lessons_before = agent.memory.lessons_col.count()
            kept = agent.memory.capture_discovery(
                "Find tiles", "https://example.test/tiles", "manual", "useful", kind="library",
            )
            other = agent.memory.capture_discovery(
                "Other find", "https://example.test/other", "manual", "also", kind="article",
            )
            assert kept and other
            partial = client.post(f"/admin/daedalus/discoveries/{other}/approve")
            assert partial.status_code == 200
            assert partial.json()["approved"] is True
            assert partial.json()["plan_id"] == "pending"
            assert schedule_calls == [other]
            assert agent.memory.lessons_col.count() == lessons_before + 1
            mode["schedule"] = "boom"
            broken = client.post(f"/admin/daedalus/discoveries/{kept}/approve")
            assert broken.status_code == 200
            assert broken.json()["approved"] is True
            assert broken.json()["plan_id"] is None
            assert agent.memory.lessons_col.count() == lessons_before + 2
            lesson_docs = agent.memory.lessons_col.get().get("documents") or []
            assert any("Find tiles" in (doc or "") for doc in lesson_docs)
            assert list(data_root().joinpath("plans").glob("plan:*.json")) == []
            skipped = agent.memory.capture_discovery(
                "Skip me", "https://example.test/skip", "manual", "no", kind="tool",
            )
            lessons_now = agent.memory.lessons_col.count()
            scheduled_now = len(schedule_calls)
            denied = client.post(f"/admin/daedalus/discoveries/{skipped}/reject")
            assert denied.status_code == 200
            assert denied.json()["rejected"] is True
            assert agent.memory.lessons_col.count() == lessons_now
            assert len(schedule_calls) == scheduled_now
            row = agent.memory.list_discoveries()
            saved = next(item for item in row if item["id"] == skipped)
            assert saved["metadata"]["status"] == "rejected"
            assert len(list_tasks()) == 1
            assert claude_calls == []
    finally:
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
    def plans(route):
        req = route.request
        path = req.url.split("?", 1)[0]
        if req.method == "POST" and path.endswith("/plans/auto"):
            raw = req.post_data
            posts.append({"kind": "auto", "body": json.loads(raw) if raw else None})
            if state["hold_auto"]:
                held["auto"].append(route)
                return
            _fulfill(route, state["auto_status"], state["auto"])
            return
        if req.method == "POST" and path.endswith("/approve"):
            posts.append({"kind": "approve", "body": req.post_data})
            if state["hold_approve"]:
                held["approve"].append(route)
                return
            _fulfill(route, state["approve_status"], state["approve"])
            return
        if req.method == "DELETE" and "/planner/plans/" in path:
            posts.append({"kind": "delete", "body": None})
            _fulfill(route, state["delete_status"], state["delete"])
            return
        if req.method == "GET" and path.endswith("/planner/plans"):
            if state["hold_list"]:
                held["list"].append(route)
                return
            _fulfill(route, state["list_status"], state["list"])
            return
        if req.method == "GET" and "/planner/plans/" in path:
            _fulfill(route, state["detail_status"], state["detail"])
            return
        route.fallback()

    def discoveries(route):
        req = route.request
        path = req.url.split("?", 1)[0]
        if req.method == "POST" and path.endswith("/approve"):
            posts.append({"kind": "disc-approve", "url": path, "body": req.post_data})
            if state.get("disc_abort"):
                route.abort("timedout")
                return
            if state.get("hold_disc_approve"):
                held.setdefault("disc_approve", []).append(route)
                return
            _fulfill(route, 200, state["disc_approve"])
            return
        if req.method == "POST" and path.endswith("/reject"):
            posts.append({"kind": "disc-reject", "url": path, "body": req.post_data})
            _fulfill(route, 200, state["disc_reject"])
            return
        if req.method == "GET" and path.endswith("/discoveries"):
            if state["hold_disc"]:
                held["disc"].append(route)
                return
            _fulfill(route, state["disc_status"], state["disc"])
            return
        route.fallback()

    page.route("**/admin/daedalus/planner/**", plans)
    page.route("**/admin/daedalus/discoveries**", discoveries)


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


def _settled(page, refresh_id):
    page.wait_for_function(
        """(id) => {
          const refresh = document.getElementById(id);
          return refresh && !refresh.disabled;
        }""",
        arg=refresh_id,
    )


def _wait_len(page, items, count):
    for _ in range(50):
        if len(items) >= count:
            return
        page.wait_for_timeout(20)
    raise AssertionError(len(items))


def test_review_confirms_plan_and_discovery_and_hides_approved_rows(studio_server):
    from playwright.sync_api import sync_playwright

    state = {
        "list": {"plans": [PLAN, PLAN_KEEP]},
        "list_status": 200,
        "detail": PLAN,
        "detail_status": 200,
        "auto": {"ok": True, "plan_id": "plan_demo", "status": "draft", "plan": PLAN},
        "auto_status": 200,
        "approve": {"ok": True, "plan_id": "plan_demo", "status": "active", "tasks_created": ["task_1"]},
        "approve_status": 200,
        "delete": {"ok": True},
        "delete_status": 200,
        "disc": {"discoveries": [DISC_PART, DISC_PEND, DISC_NO, DISC_FILE, DISC_BG], "count": 5},
        "disc_status": 200,
        "disc_approve": {"discovery_id": "disc_part", "approved": True, "plan_id": None},
        "disc_reject": {"discovery_id": "disc_no", "rejected": True},
        "hold_list": False,
        "hold_auto": False,
        "hold_approve": False,
        "hold_disc": False,
    }
    held = {"list": [], "auto": [], "approve": [], "disc": []}
    posts = []
    dialogs = []
    accept = {"on": False}

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        leaked = _guard(page)
        _arm(page, state, held, posts)
        page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.accept() if accept["on"] else dialog.dismiss()))
        page.goto(studio_server["url"] + "/?panel=review", wait_until="domcontentloaded")
        page.wait_for_function(
            """(title) => document.getElementById('plan-list').textContent.includes(title)""",
            arg=TITLE,
        )
        assert page.locator("#plan-controls img").count() == 0
        assert page.locator("#discovery-controls img").count() == 0
        assert "Find <img" in page.locator("#discovery-list").inner_text()
        _hit(page, "#plan-generate")
        _status_has(page, "plan-status", "Enter a goal.")
        assert posts == []
        assert dialogs == []
        page.locator("#plan-goal").fill("x" * 2001)
        _hit(page, "#plan-generate")
        _status_has(page, "plan-status", "over 2000")
        assert posts == []
        page.locator("#plan-goal").fill("  " + GOAL + "  ")
        _hit(page, "#plan-generate")
        assert GOAL in dialogs[-1]
        assert "full agent turn" in dialogs[-1]
        assert "for_self" in dialogs[-1]
        assert "does not create tasks" in dialogs[-1]
        assert "does not run the steps" in dialogs[-1]
        assert posts == []
        accept["on"] = True
        _hit(page, "#plan-generate")
        _status_has(page, "plan-status", "Draft plan_demo is stored.")
        assert posts[0] == {"kind": "auto", "body": {"goal": GOAL}}
        assert "Tasks created" not in page.locator("#plan-status").inner_text()
        _settled(page, "plan-refresh")
        accept["on"] = False
        _hit(page, "#plan-list button[data-act=approve]")
        assert "Approve plan plan_demo?" in dialogs[-1]
        assert "does not call the model" in dialogs[-1]
        assert "does not run the steps" in dialogs[-1]
        assert [item["kind"] for item in posts] == ["auto"]
        accept["on"] = False
        posted = len(posts)
        _hit(page, "#plan-list button[data-act=approve]")
        assert len(posts) == posted
        accept["on"] = True
        _hit(page, "#plan-list button[data-act=approve]")
        _status_has(page, "plan-status", "Tasks created: 1")
        assert "were not run" in page.locator("#plan-status").inner_text()
        assert posts[-1]["kind"] == "approve"
        assert posts[-1]["body"] in (None, "")
        page.wait_for_function(
            """() => {
              const refresh = document.getElementById('plan-refresh');
              return refresh && !refresh.disabled
                && document.querySelectorAll('#plan-list button[data-act=approve]').length === 0
                && document.querySelector('#plan-list button[data-act=delete]');
            }"""
        )
        state["auto"] = None
        page.locator("#plan-goal").fill("another goal")
        _hit(page, "#plan-generate")
        _status_has(page, "plan-status", "The response did not succeed.")
        assert "is stored" not in page.locator("#plan-status").inner_text()
        state["auto_status"] = 422
        state["auto"] = {"ok": False, "error": "no json"}
        _hit(page, "#plan-generate")
        _status_has(page, "plan-status", "no json")
        assert "is stored" not in page.locator("#plan-status").inner_text()
        state["auto_status"] = 200
        state["auto"] = {"ok": True, "plan_id": "plan_demo", "status": "draft", "plan": PLAN}
        _hit(page, "#plan-list [data-id=plan_demo] button[data-act=delete]")
        assert "Delete plan plan_demo?" in dialogs[-1]
        assert "Tasks already created stay" in dialogs[-1]
        _status_has(page, "plan-status", "Plan plan_demo was deleted.")
        page.wait_for_function(
            """() => {
              const text = document.getElementById('plan-list').textContent || '';
              const refresh = document.getElementById('plan-refresh');
              return refresh && !refresh.disabled && text.includes('plan_keep') && !text.includes('plan_demo');
            }"""
        )
        state["disc_approve"] = {"discovery_id": "disc_part", "approved": True, "plan_id": None}
        _hit(page, "#discovery-list [data-id=disc_part] button[data-act=approve]")
        assert "disc_part" in dialogs[-1]
        assert "OpenRouter Claude Sonnet" in dialogs[-1]
        assert "Novita DeepSeek" in dialogs[-1]
        assert "Daedalus itself" in dialogs[-1]
        assert "Grok" not in dialogs[-1]
        assert "Composer" not in dialogs[-1]
        _status_has(page, "discovery-status", "was not scheduled")
        assert "The id is pending" not in page.locator("#discovery-status").inner_text()
        page.wait_for_function(
            """() => {
              const refresh = document.getElementById('discovery-refresh');
              return refresh && !refresh.disabled
                && document.querySelectorAll('#discovery-list [data-id=disc_part] button[data-act=approve]').length === 0
                && document.querySelector('#discovery-list [data-id=disc_pend] button[data-act=approve]');
            }"""
        )
        state["disc_approve"] = {"discovery_id": "disc_pend", "approved": True, "plan_id": "pending"}
        _hit(page, "#discovery-list [data-id=disc_pend] button[data-act=approve]")
        _status_has(page, "discovery-status", "The id is pending")
        assert "No planner plan and no task were created." in page.locator("#discovery-status").inner_text()
        page.wait_for_function(
            """() => {
              const refresh = document.getElementById('discovery-refresh');
              return refresh && !refresh.disabled
                && document.querySelectorAll('#discovery-list [data-id=disc_pend] button[data-act=approve]').length === 0;
            }"""
        )
        state["disc_approve"] = {"discovery_id": "disc_file", "approved": True, "plan_id": "plan:abc"}
        _hit(page, "#discovery-list [data-id=disc_file] button[data-act=approve]")
        _status_has(page, "discovery-status", "was not scheduled")
        assert "The id is pending" not in page.locator("#discovery-status").inner_text()
        page.wait_for_function(
            """() => document.querySelectorAll('#discovery-list [data-id=disc_file] button[data-act=approve]').length === 0"""
        )
        state["disc_approve"] = {"discovery_id": "disc_no", "approved": False, "plan_id": None}
        _hit(page, "#discovery-list [data-id=disc_no] button[data-act=approve]")
        _status_has(page, "discovery-status", "was not approved")
        assert page.locator("#discovery-list [data-id=disc_no] button[data-act=approve]").count() == 1
        state["disc_reject"] = {"discovery_id": "disc_no", "rejected": False}
        _hit(page, "#discovery-list [data-id=disc_no] button[data-act=reject]")
        _status_has(page, "discovery-status", "did not succeed")
        assert page.locator("#discovery-list [data-id=disc_no] button[data-act=reject]").count() == 1
        state["disc_reject"] = {"discovery_id": "disc_no", "rejected": True}
        _hit(page, "#discovery-list [data-id=disc_no] button[data-act=reject]")
        assert "only changes the status" in dialogs[-1]
        assert "does not write a lesson" in dialogs[-1]
        _status_has(page, "discovery-status", "No lesson was written.")
        _settled(page, "discovery-refresh")
        assert page.locator("#discovery-list [data-id=disc_no] button[data-act]").count() == 0
        state["disc"] = {"discoveries": [], "count": 0, "error": "disc-down"}
        _hit(page, "#discovery-refresh")
        _status_has(page, "discovery-status", "disc-down")
        assert "No discoveries." not in page.locator("#discovery-list").inner_text()
        state["disc"] = {"discoveries": [], "count": 0}
        _hit(page, "#discovery-refresh")
        page.wait_for_function(
            """() => document.getElementById('discovery-list').textContent.includes('No discoveries.')
              && document.getElementById('discovery-status').hidden"""
        )
        state["list"] = {}
        _hit(page, "#plan-refresh")
        _status_has(page, "plan-status", "The response did not succeed.")
        assert "No plans." not in page.locator("#plan-list").inner_text()
        state["list"] = {"plans": [PLAN, PLAN_KEEP]}
        state["disc"] = {"discoveries": [DISC_BG], "count": 1}
        page.locator(".dx-lang button[data-lang=bg]").click()
        page.wait_for_function("() => document.getElementById('plan-generate').textContent === 'Чернова'")
        _hit(page, "#plan-refresh")
        _hit(page, "#discovery-refresh")
        page.wait_for_function(
            """() => {
              const plans = document.getElementById('plan-list').textContent;
              const discs = document.getElementById('discovery-list').textContent;
              const del = document.querySelector('#plan-list button[data-act=delete]');
              const approve = document.querySelector('#discovery-list button[data-act=approve]');
              const reject = document.querySelector('#discovery-list button[data-act=reject]');
              return plans.includes('plan_keep') && plans.includes('Keep title')
                && !plans.includes('plan_demo')
                && discs.includes('disc_bg') && discs.includes('Keep find')
                && del && del.textContent === 'Изтрий'
                && approve && approve.textContent === 'Одобри'
                && reject && reject.textContent === 'Отхвърли';
            }"""
        )
        assert page.locator("#plan-list button[data-act=approve]").count() == 0
        assert "Keep title" in page.locator("#plan-list").inner_text()
        assert "Keep find" in page.locator("#discovery-list").inner_text()
        assert page.locator("#plan-goal").input_value() == "another goal"
        assert leaked == []
        browser.close()


def test_leaving_review_drops_late_plan_replies(studio_server):
    from playwright.sync_api import sync_playwright

    state = {
        "list": {"plans": [PLAN]},
        "list_status": 200,
        "detail": PLAN,
        "detail_status": 200,
        "auto": {"ok": True, "plan_id": "plan_demo", "status": "draft", "plan": PLAN},
        "auto_status": 200,
        "approve": {"ok": True, "plan_id": "plan_demo", "status": "active", "tasks_created": ["task_1"]},
        "approve_status": 200,
        "delete": {"ok": True},
        "delete_status": 200,
        "disc": {"discoveries": [DISC_PEND], "count": 1},
        "disc_status": 200,
        "disc_approve": {"approved": True, "plan_id": "pending"},
        "disc_reject": {"rejected": True},
        "hold_list": True,
        "hold_auto": False,
        "hold_approve": False,
        "hold_disc": False,
    }
    held = {"list": [], "auto": [], "approve": [], "disc": []}
    posts = []
    dialogs = []
    accept = {"on": True}

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        leaked = _guard(page)
        _arm(page, state, held, posts)
        page.on("dialog", lambda dialog: (dialogs.append(dialog.message), dialog.accept() if accept["on"] else dialog.dismiss()))
        page.goto(studio_server["url"] + "/?panel=review", wait_until="domcontentloaded")
        _wait_len(page, held["list"], 1)
        _hit(page, "#workspace-nav button[data-panel=build]")
        state["hold_list"] = False
        state["list"] = {"plans": [{"id": "plan_late", "title": "late-payload", "status": "draft"}]}
        _fulfill(held["list"].pop(), 200, state["list"])
        page.evaluate("() => new Promise((resolve) => setTimeout(resolve, 50))")
        assert "late-payload" not in page.locator("#plan-list").evaluate("el => el.textContent")
        state["list"] = {"plans": [PLAN]}
        _hit(page, "#workspace-nav button[data-panel=review]")
        page.wait_for_function(
            """(title) => document.getElementById('plan-list').textContent.includes(title)
              && !document.getElementById('plan-list').textContent.includes('late-payload')""",
            arg=TITLE,
        )
        state["hold_approve"] = True
        _hit(page, "#plan-list button[data-act=approve]")
        _wait_len(page, posts, 1)
        assert posts[0]["kind"] == "approve"
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        with pytest.raises(PlaywrightTimeout):
            page.locator("#plan-list button[data-act=approve]").click(timeout=500)
        assert len(posts) == 1
        _hit(page, "#workspace-nav button[data-panel=build]")
        _hit(page, "#workspace-nav button[data-panel=review]")
        page.wait_for_function(
            """(title) => {
              const status = document.getElementById('plan-status').textContent || '';
              return document.getElementById('plan-list').textContent.includes(title)
                && document.querySelectorAll('#plan-list button[data-act=approve]').length === 0
                && status.includes('not finished');
            }""",
            arg=TITLE,
        )
        assert len(posts) == 1
        _hit(page, "#workspace-nav button[data-panel=build]")
        state["hold_approve"] = False
        _fulfill(held["approve"].pop(), 200, state["approve"])
        page.evaluate("() => new Promise((resolve) => setTimeout(resolve, 50))")
        assert "Tasks created" not in page.locator("#plan-status").evaluate("el => el.textContent")
        _hit(page, "#workspace-nav button[data-panel=review]")
        page.wait_for_function(
            """(title) => document.getElementById('plan-list').textContent.includes(title)
              && document.querySelectorAll('#plan-list button[data-act=approve]').length === 0""",
            arg=TITLE,
        )
        assert "Tasks created" not in page.locator("#plan-status").evaluate("el => el.textContent")
        posted = len(posts)
        page.locator(f"#plist [data-slug='{studio_server['other']}']").click()
        page.wait_for_function(
            """(slug) => {
              const row = document.querySelector('#plist [data-slug="' + slug + '"]');
              return row && row.classList.contains('active');
            }""",
            arg=studio_server["other"],
        )
        assert TITLE in page.locator("#plan-list").evaluate("el => el.textContent")
        assert len(posts) == posted
        _hit(page, "#workspace-nav button[data-panel=build]")
        page.evaluate("() => { document.getElementById('panel-review').hidden = false; }")
        dialogs.clear()
        assert page.locator("#plan-list button[data-act=approve]").count() == 0
        assert dialogs == []
        assert len(posts) == posted
        _hit(page, "#workspace-nav button[data-panel=review]")
        page.wait_for_function(
            """(title) => document.getElementById('plan-list').textContent.includes(title)""",
            arg=TITLE,
        )
        page.locator("#plan-goal").fill(GOAL)
        for width, height in ((1280, 800), (910, 640)):
            page.set_viewport_size({"width": width, "height": height})
            _hit(page, "#settings-link")
            page.wait_for_function("() => new URL(location.href).searchParams.get('panel') === 'settings'")
            assert page.url.startswith(studio_server["url"] + "/")
            assert page.locator("#panel-settings").is_visible()
            assert page.locator("#plan-goal").input_value() == GOAL
            assert page.evaluate("() => window.scrollY") == 0
        assert leaked == []
        browser.close()


def test_unresolved_costly_actions_stay_blocked_across_navigation(studio_server):
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    state = {
        "list": {"plans": [PLAN]},
        "list_status": 200,
        "detail": PLAN,
        "detail_status": 200,
        "auto": {"ok": True, "plan_id": "plan_demo", "status": "draft", "plan": PLAN},
        "auto_status": 200,
        "approve": {"ok": True, "plan_id": "plan_demo", "status": "active", "tasks_created": ["task_1"]},
        "approve_status": 200,
        "delete": {"ok": True},
        "delete_status": 200,
        "disc": {"discoveries": [DISC_PEND, DISC_BG], "count": 2},
        "disc_status": 200,
        "disc_approve": {"discovery_id": "disc_pend", "approved": True, "plan_id": "pending"},
        "disc_reject": {"rejected": False},
        "hold_list": False,
        "hold_auto": False,
        "hold_approve": False,
        "hold_disc": False,
        "hold_disc_approve": True,
        "disc_abort": False,
    }
    held = {"list": [], "auto": [], "approve": [], "disc": [], "disc_approve": []}
    posts = []
    accept = {"on": True}

    def kind(name):
        return [item for item in posts if item["kind"] == name]

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        leaked = _guard(page)
        _arm(page, state, held, posts)
        page.on("dialog", lambda dialog: dialog.accept() if accept["on"] else dialog.dismiss())
        page.goto(studio_server["url"] + "/?panel=review", wait_until="domcontentloaded")
        page.wait_for_function(
            """(title) => document.getElementById('plan-list').textContent.includes(title)
              && document.getElementById('discovery-list').textContent.includes('disc_pend')""",
            arg=TITLE,
        )
        _hit(page, "#discovery-list [data-id=disc_pend] button[data-act=approve]")
        _wait_len(page, posts, 1)
        _hit(page, "#workspace-nav button[data-panel=build]")
        _hit(page, "#workspace-nav button[data-panel=review]")
        page.wait_for_function(
            """() => document.querySelectorAll('#discovery-list [data-id=disc_pend] button[data-act=approve]').length === 0
              && document.querySelector('#discovery-list [data-id=disc_bg] button[data-act=approve]')
              && (document.getElementById('discovery-status').textContent || '').includes('not finished')"""
        )
        assert len(kind("disc-approve")) == 1
        with pytest.raises(PlaywrightTimeout):
            page.locator("#discovery-list [data-id=disc_pend] button[data-act=approve]").click(timeout=400)
        assert len(kind("disc-approve")) == 1
        _hit(page, "#workspace-nav button[data-panel=build]")
        state["hold_disc_approve"] = False
        _fulfill(held["disc_approve"].pop(), 200, {"approved": True, "plan_id": "pending", "discovery_id": "disc_pend"})
        page.evaluate("() => new Promise((resolve) => setTimeout(resolve, 50))")
        assert "The id is pending" not in page.locator("#discovery-status").evaluate("el => el.textContent")
        page.evaluate(
            """() => {
              const row = document.querySelector('#plan-list .item');
              if (row) row.dataset.seen = 'old';
            }"""
        )
        _hit(page, "#workspace-nav button[data-panel=review]")
        page.wait_for_function(
            """() => {
              const row = document.querySelector('#plan-list .item');
              const refresh = document.getElementById('plan-refresh');
              return row && row.dataset.seen !== 'old' && refresh && !refresh.disabled
                && document.querySelector('#plan-list button[data-act=approve]')
                && document.getElementById('discovery-list').textContent.includes('disc_pend')
                && document.querySelectorAll('#discovery-list [data-id=disc_pend] button[data-act=approve]').length === 0;
            }"""
        )
        assert len(kind("disc-approve")) == 1
        state["hold_approve"] = True
        _hit(page, "#plan-list button[data-act=approve]")
        _wait_len(page, posts, 2)
        _hit(page, "#workspace-nav button[data-panel=build]")
        _hit(page, "#workspace-nav button[data-panel=review]")
        page.wait_for_function(
            """() => document.querySelectorAll('#plan-list button[data-act=approve]').length === 0
              && (document.getElementById('plan-status').textContent || '').includes('Approval of plan plan_demo')"""
        )
        assert len(kind("approve")) == 1
        with pytest.raises(PlaywrightTimeout):
            page.locator("#plan-list button[data-act=approve]").click(timeout=400)
        assert len(kind("approve")) == 1
        _hit(page, "#workspace-nav button[data-panel=build]")
        state["hold_approve"] = False
        _fulfill(held["approve"].pop(), 200, state["approve"])
        page.evaluate("() => new Promise((resolve) => setTimeout(resolve, 50))")
        assert "Tasks created" not in page.locator("#plan-status").evaluate("el => el.textContent")
        _hit(page, "#workspace-nav button[data-panel=review]")
        page.wait_for_function(
            """(title) => document.getElementById('plan-list').textContent.includes(title)
              && document.querySelectorAll('#plan-list button[data-act=approve]').length === 0""",
            arg=TITLE,
        )
        assert len(kind("approve")) == 1
        state["hold_auto"] = True
        page.locator("#plan-goal").fill(GOAL)
        _hit(page, "#plan-generate")
        _wait_len(page, posts, 3)
        assert kind("auto")[0]["body"] == {"goal": GOAL}
        _hit(page, "#workspace-nav button[data-panel=build]")
        _hit(page, "#workspace-nav button[data-panel=review]")
        page.wait_for_function(
            """() => document.getElementById('plan-generate').disabled
              && (document.getElementById('plan-status').textContent || '').includes('still being generated')"""
        )
        with pytest.raises(PlaywrightTimeout):
            page.locator("#plan-generate").click(timeout=400)
        assert len(kind("auto")) == 1
        _hit(page, "#workspace-nav button[data-panel=build]")
        state["hold_auto"] = False
        _fulfill(held["auto"].pop(), 200, state["auto"])
        page.evaluate("() => new Promise((resolve) => setTimeout(resolve, 50))")
        assert "is stored" not in page.locator("#plan-status").evaluate("el => el.textContent")
        _hit(page, "#workspace-nav button[data-panel=review]")
        page.wait_for_function("() => !document.getElementById('plan-generate').disabled")
        assert len(kind("auto")) == 1
        state["disc_abort"] = True
        before = len(kind("disc-approve"))
        seen = len(posts)
        _hit(page, "#discovery-list [data-id=disc_bg] button[data-act=approve]")
        _wait_len(page, posts, seen + 1)
        _status_has(page, "discovery-status", "Outcome unknown")
        assert "Not safe to retry" in page.locator("#discovery-status").inner_text()
        assert "Try again" not in page.locator("#discovery-status").inner_text()
        with pytest.raises(PlaywrightTimeout):
            page.locator("#discovery-list [data-id=disc_bg] button[data-act=approve]").click(timeout=400)
        assert len(kind("disc-approve")) == before + 1
        _hit(page, "#discovery-refresh")
        page.wait_for_function(
            """() => (document.getElementById('discovery-status').textContent || '').includes('Outcome unknown')
              && document.querySelectorAll('#discovery-list [data-id=disc_bg] button[data-act]').length === 0
              && document.getElementById('discovery-list').textContent.includes('disc_bg')"""
        )
        _hit(page, "#workspace-nav button[data-panel=build]")
        _hit(page, "#workspace-nav button[data-panel=review]")
        page.wait_for_function(
            """() => (document.getElementById('discovery-status').textContent || '').includes('Not safe to retry')
              && document.querySelectorAll('#discovery-list [data-id=disc_bg] button[data-act]').length === 0
              && document.querySelectorAll('#discovery-list [data-id=disc_pend] button[data-act=approve]').length === 0"""
        )
        assert len(kind("disc-approve")) == before + 1
        assert len(kind("auto")) == 1
        assert len(kind("approve")) == 1
        assert leaked == []
        browser.close()
