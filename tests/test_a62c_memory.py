"""A6.2c: Review → Memory (API level)."""
from __future__ import annotations

import asyncio
import time

import pytest
from a0_support import a0_tmp  # noqa: F401
from fastapi.testclient import TestClient

M = "/api/v1/harness/memory"


class Col:
    def __init__(self, items=None):
        self.items = dict(items or {})

    def count(self):
        return len(self.items)

    def get(self, ids=None, limit=None, where=None):
        keys = [i for i in (ids if ids is not None else list(self.items)) if i in self.items]
        return {"ids": keys, "documents": [self.items[k][0] for k in keys], "metadatas": [self.items[k][1] for k in keys]}

    def delete(self, ids):
        for i in ids:
            self.items.pop(i, None)


class Episodic:
    def __init__(self, col):
        self._col = col

    def count(self):
        return self._col.count()

    def all_facts(self, limit=200):
        res = self._col.get()
        return [{"id": i, "content": d, "metadata": m} for i, d, m in zip(res["ids"], res["documents"], res["metadatas"])][:limit]

    def delete(self, ids):
        self._col.delete(ids)
        return len(ids)


class Mem0:
    def __init__(self):
        self.episodic = Episodic(Col({"f1": ("likes pixel art", {"kind": "preference", "ts": time.time() - 7200, "importance": 2})}))
        self.rebuilt = 0

    def rebuild_bm25_indices(self):
        self.rebuilt += 1


class Memory:
    def __init__(self):
        for name in ("global", "sessions", "artifacts", "lessons", "episodes", "discoveries"):
            setattr(self, f"{name}_col", Col({"a": ("x", {})}))
        self.turns = {"s1": [{"id": "t1", "content": "hello", "metadata": {"role": "owner"}},
                             {"id": "t2", "content": "hi", "metadata": {"role": "daedalus"}}]}

    def list_sessions(self, limit=60):
        return [{"session_id": "s1", "turn_count": 2, "preview": "hello", "first_ts": "", "last_ts": ""}][:limit]

    def session_history(self, session_id, limit=500):
        return self.turns.get(session_id, [])[-limit:]


class Agent:
    def __init__(self, with_mem0=True):
        self.memory = Memory()
        self.mem0 = Mem0() if with_mem0 else None
        self.searches = []

    async def web_search(self, query, limit=5, **kw):
        self.searches.append(("web", query, limit))
        return {"ok": True, "results": [{"title": "Godot docs", "url": "https://docs.godotengine.org", "snippet": "s"}],
                "discoveries_recorded": 1}

    async def github_search(self, query, limit=5, **kw):
        self.searches.append(("github", query, limit))
        return {"ok": False, "error": "github_search_failed: 403", "results": []}


@pytest.fixture
def agent(monkeypatch):
    a = Agent()
    monkeypatch.setattr("daedalus.agent.get_daedalus", lambda: a)
    from daedalus.harness import memory_actions

    memory_actions._jobs.clear()
    memory_actions._last.clear()
    return a


@pytest.fixture
def client(a0_tmp, agent):  # noqa: F811
    from app.main import app

    return TestClient(app)


def test_overview_reads_every_part_and_says_what_cannot_be_read(client, monkeypatch):
    async def no_table():
        return {}

    monkeypatch.setattr("daedalus.clarification_tracker.get_clarification_stats", no_table)
    data = client.get(M).json()
    assert data["ok"] is True and data["memory"]["available"] is True
    assert data["memory"]["counts"]["episodic_facts"] == 1 and data["memory"]["counts"]["lessons"] == 1
    assert data["mode"]["mode"] in data["mode"]["valid"] and "total_calls" in data["navigator"]
    assert data["clarifications"] == {"available": False}                        # not a row of zeros
    assert data["jobs"] == {"running": [], "last": {}} and isinstance(data["consolidations"], list)


def test_a_broken_memory_is_unavailable_not_empty(client, monkeypatch):
    def broken():
        raise RuntimeError("chroma down")

    monkeypatch.setattr("daedalus.agent.get_daedalus", broken)
    data = client.get(M).json()
    assert data["memory"] == {"available": False, "reason": "RuntimeError"}
    assert client.get(M + "/episodic").json() == {"ok": True, "available": False, "facts": []}
    assert client.get(M + "/sessions").json()["available"] is False


def test_episodic_facts_and_deleting_one_needs_a_confirm_and_says_when_it_is_gone(client, agent):
    facts = client.get(M + "/episodic").json()
    assert facts["available"] is True and facts["facts"][0]["id"] == "f1" and facts["facts"][0]["age_hours"] == 2.0
    url = M + "/episodic/f1/delete"
    assert client.post(url, json={}).status_code == 400 and agent.mem0.episodic.count() == 1
    assert client.post(url, json={"confirm": True}).json() == {"ok": True, "fact_id": "f1"}
    assert agent.mem0.episodic.count() == 0 and agent.mem0.rebuilt == 1
    assert client.post(url, json={"confirm": True}).status_code == 404                   # gone: not "ok" again


def test_without_episodic_memory_delete_is_unavailable(client, monkeypatch):
    monkeypatch.setattr("daedalus.agent.get_daedalus", lambda: Agent(with_mem0=False))
    assert client.post(M + "/episodic/f1/delete", json={"confirm": True}).status_code == 503
    assert client.get(M + "/episodic").json()["available"] is False


def test_navigator_reset_needs_a_confirm(client):
    from daedalus import navigator

    navigator._stats["total_calls"] = 7
    assert client.post(M + "/navigator/reset", json={}).status_code == 400 and navigator._stats["total_calls"] == 7
    assert client.post(M + "/navigator/reset", json={"confirm": True}).json() == {"ok": True}
    assert navigator._stats["total_calls"] == 0


def test_mode_is_set_only_to_a_known_value(client):
    assert client.post(M + "/mode", json={"mode": "rm"}).status_code == 400
    assert client.post(M + "/mode", json={"mode": "talk"}).json() == {"ok": True, "mode": "talk"}
    assert client.get(M).json()["mode"]["mode"] == "talk"


def test_progress_is_read_and_cleared(client):
    from daedalus import outbox

    outbox.set_progress("build", 0.5, "half way")
    p = client.get(M).json()["progress"]
    assert p["active"] is True and p["task"] == "build" and p["message"] == "half way" and p["pct"] == 0.5
    assert client.post(M + "/progress/clear").json() == {"ok": True}
    assert client.get(M).json()["progress"]["active"] is False


def test_task_ledger_lists_and_creates_with_limits(client):
    assert client.post(M + "/tasks", json={"title": " "}).status_code == 400
    assert client.post(M + "/tasks", json={"title": "x" * 201}).status_code == 400
    made = client.post(M + "/tasks", json={"title": "Fix the jump", "summary": "too floaty"}).json()
    assert made["ok"] is True and made["task_id"].startswith("task_")
    rows = client.get(M + "/tasks").json()["tasks"]
    assert any(r["id"] == made["task_id"] and r["title"] == "Fix the jump" for r in rows)
    assert client.get(M + "/tasks?status=nope").status_code == 400


def test_sessions_and_one_sessions_turns(client):
    assert client.get(M + "/sessions").json()["sessions"][0]["session_id"] == "s1"
    turns = client.get(M + "/sessions/s1").json()
    assert turns["ok"] is True and [t["role"] for t in turns["turns"]] == ["owner", "daedalus"]
    assert client.get(M + "/sessions/none").status_code == 404


def test_manual_search_is_bounded_and_honest_about_failures(client, agent):
    assert client.post(M + "/search/web", json={"query": ""}).status_code == 400
    assert client.post(M + "/search/web", json={"query": "q" * 301}).status_code == 400
    assert client.post(M + "/search/mesh", json={"query": "x"}).status_code == 404
    web = client.post(M + "/search/web", json={"query": "godot tilemap", "limit": 99}).json()
    assert web["ok"] is True and web["results"][0]["url"] == "https://docs.godotengine.org" and web["discoveries_recorded"] == 1
    assert agent.searches[-1] == ("web", "godot tilemap", 10)                            # clamped
    gh = client.post(M + "/search/github", json={"query": "godot"})
    assert gh.status_code == 502 and gh.json() == {"ok": False, "error": "search_failed"}


# ------------------------------------------------------------------------------------------------ jobs
def _run(coro):
    return asyncio.run(coro)


def test_consolidation_is_confirmed_single_flight_and_reports_how_it_ended(a0_tmp, agent, monkeypatch):  # noqa: F811
    from daedalus.harness import memory_actions

    async def scenario():
        gate = asyncio.Event()

        async def cycle(llm, d):
            await gate.wait()
            return {"scanned": 3, "merged": 1, "deleted": 0, "promoted": 1, "pruned": 0, "errors": []}

        monkeypatch.setattr("daedalus.reflection.run_consolidation_cycle", cycle)
        refused = await memory_actions.start_job("consolidate", False)
        first = await memory_actions.start_job("consolidate", True)
        second = await memory_actions.start_job("consolidate", True)
        running = memory_actions.jobs_status()
        gate.set()
        await asyncio.gather(*memory_actions._jobs.values())
        return refused, first, second, running, memory_actions.jobs_status()

    refused, first, second, running, done = _run(scenario())
    assert refused == {"ok": False, "error": "confirm_required"} and first["ok"] is True
    assert second == {"ok": False, "error": "already_running"} and running["running"] == ["consolidate"]
    last = done["last"]["consolidate"]
    assert done["running"] == [] and last["ok"] is True and last["merged"] == 1 and last["promoted"] == 1


def test_a_consolidation_with_errors_is_not_success(a0_tmp, agent, monkeypatch):  # noqa: F811
    from daedalus.harness import memory_actions

    async def cycle(llm, d):
        return {"scanned": 0, "errors": ["llm down"]}

    monkeypatch.setattr("daedalus.reflection.run_consolidation_cycle", cycle)

    async def scenario():
        await memory_actions.start_job("consolidate", True)
        await asyncio.gather(*memory_actions._jobs.values())
        return memory_actions.jobs_status()["last"]["consolidate"]

    last = _run(scenario())
    assert last["ok"] is False and last["error"] == "cycle_errors" and last["errors"] == 1


def test_a_style_lesson_says_when_there_was_nothing_to_learn_and_when_it_failed(a0_tmp, agent, monkeypatch):  # noqa: F811
    from daedalus.harness import memory_actions

    answers = iter([{"ok": True, "lesson": None, "reason": "no pending clarifications"},
                    {"ok": False, "reason": "empty LLM response"},
                    {"ok": True, "lesson": "ask less", "processed": 4}])

    async def lesson(agent):
        return next(answers)

    monkeypatch.setattr("daedalus.clarification_tracker.generate_daily_lesson", lesson)

    async def once():
        await memory_actions.start_job("lesson", True)
        await asyncio.gather(*memory_actions._jobs.values())
        return {k: v for k, v in memory_actions.jobs_status()["last"]["lesson"].items() if k not in ("finished_at", "seconds")}

    assert _run(once()) == {"ok": True, "nothing_to_learn": True}
    assert _run(once()) == {"ok": False, "error": "lesson_failed"}
    assert _run(once()) == {"ok": True, "processed": 4}


def test_job_routes_answer_with_codes(client):
    assert client.post(M + "/jobs/consolidate", json={}).status_code == 400
    assert client.post(M + "/jobs/nope", json={"confirm": True}).status_code == 404
    assert client.get(M + "/jobs").json() == {"ok": True, "running": [], "last": {}}


# ------------------------------------------------------------------------------------------------ UI (real clicks)
import json  # noqa: E402
import re  # noqa: E402

from test_a61c_maintenance import _dialogs  # noqa: E402
from test_a62a_review import _open_review, _record, _until  # noqa: E402
from test_a62b_planner import _status_has, _uncovered  # noqa: E402
from test_studio_phase2_workspace import _fulfill, studio_server  # noqa: E402,F401  (fixture and helpers)

OVER = {"ok": True,
        "memory": {"available": True, "counts": {"global": 1, "sessions": 12, "artifacts": 0, "lessons": 4, "episodes": 2, "discoveries": 3,
                                                 "episodic_facts": 1}, "episodic": True},
        "navigator": {"total_calls": 9, "fast_path_pct": 33.3, "avg_latency_ms": 12.5, "enabled": True},
        "mode": {"mode": "auto", "valid": ["talk", "build", "auto"]},
        "progress": {"active": True, "task": "build", "message": "half way", "pct": 0.5, "updated_at": 1},
        "clarifications": {"available": False}, "consolidations": [], "jobs": {"running": [], "last": {}}}
FACTS = {"ok": True, "available": True, "facts": [{"id": "f1", "content": "Owner likes pixel art", "kind": "preference", "importance": 2,
                                                   "access_count": 3, "promoted": False, "age_hours": 2.0}]}
TASKS = {"ok": True, "tasks": [{"id": "task_1", "title": "Fix the jump", "status": "open", "summary": "too floaty"}]}
SESSIONS = {"ok": True, "available": True, "sessions": [{"session_id": "s1", "turn_count": 2, "preview": "hello there"}]}


def _mem_page(page, over=None, facts=None, tasks=None, sessions=None):
    """Stub the reads (an answer may be a list: one per call, the last repeats)."""
    def serve(answer):
        calls = {"n": 0}

        def handler(route):
            if route.request.method != "GET":
                return route.fallback()
            seq = answer if isinstance(answer, list) else [answer]
            body = seq[min(calls["n"], len(seq) - 1)]
            calls["n"] += 1
            _fulfill(route, 200, body)
        return handler, calls

    out = {}
    for name, pattern, answer in (("over", r".*/api/v1/harness/memory(\?.*)?$", over or OVER),
                                  ("facts", r".*/api/v1/harness/memory/episodic(\?.*)?$", facts or FACTS),
                                  ("tasks", r".*/api/v1/harness/memory/tasks(\?.*)?$", tasks or TASKS),
                                  ("sessions", r".*/api/v1/harness/memory/sessions(\?.*)?$", sessions or SESSIONS)):
        handler, calls = serve(answer)
        page.route(re.compile(pattern), handler)
        out[name] = calls
    return out


def _open_memory(page, server, lang="en"):
    _open_review(page, server, "memory", lang=lang, wait="#review-list .mem-section[data-section='search']")


def _sec(role):
    return f"#review-list .mem-section[data-section='{role}']"


def _browser(pw, size=(1280, 800)):
    browser = pw.chromium.launch()
    return browser, browser.new_page(viewport={"width": size[0], "height": size[1]})


def test_the_memory_tab_shows_each_part_and_says_what_cannot_be_read(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        _mem_page(page, facts={"ok": True, "available": False, "facts": []})
        _open_memory(page, studio_server)
        assert page.locator("#review-tabs [data-tab=memory]").get_attribute("aria-selected") == "true"
        over = page.inner_text(_sec("overview"))
        assert "Lessons 4" in over and "Facts 1" in over and "Working: build · 50% · half way" in over
        assert "Navigator on: 9 calls" in over and "Clarification stats cannot be read right now." in over
        assert page.locator(_sec("overview") + " select").input_value() == "auto"
        assert "Episodic memory is not available." in page.inner_text(_sec("facts"))
        assert "Fix the jump" in page.inner_text(_sec("tasks")) and "hello there" in page.inner_text(_sec("sessions"))
        browser.close()


def test_a_whole_memory_that_cannot_be_read_is_not_shown_as_empty(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        _mem_page(page, over={**OVER, "memory": {"available": False, "reason": "RuntimeError"}},
                  sessions={"ok": True, "available": False, "sessions": []})
        _open_memory(page, studio_server)
        assert "Memory cannot be read right now." in page.inner_text(_sec("overview"))
        assert "Lessons 0" not in page.inner_text(_sec("overview"))
        assert "Sessions cannot be read right now." in page.inner_text(_sec("sessions"))
        browser.close()


def test_deleting_a_fact_asks_first_and_the_list_follows(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        calls = _mem_page(page, facts=[FACTS, {"ok": True, "available": True, "facts": []}])
        deletes = _record(page, "**/api/v1/harness/memory/episodic/f1/delete", [(200, {"ok": True, "fact_id": "f1"})])
        _open_memory(page, studio_server)
        asked = _dialogs(page, [False, True])
        btn = _sec("facts") + " .item[data-fact='f1'] button[data-mem='fact-delete']"
        page.locator(btn).click()
        assert _until(page, lambda: asked)
        page.wait_for_timeout(200)
        assert deletes == [] and "cannot be undone" in asked[0]
        page.locator(btn).click()
        _status_has(page, "review-status", "Fact deleted.")
        assert deletes == [{"confirm": True}]
        page.wait_for_selector(_sec("facts") + " .empty")
        assert calls["facts"]["n"] == 2
        assert "Fact deleted." in page.inner_text("#review-status")                         # the refresh keeps the outcome
        browser.close()


def test_a_fact_that_is_already_gone_is_said_so(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        _mem_page(page)
        _record(page, "**/api/v1/harness/memory/episodic/f1/delete", [(404, {"ok": False, "error": "not_found"})])
        _open_memory(page, studio_server)
        _dialogs(page, [True])
        page.locator(_sec("facts") + " button[data-mem='fact-delete']").click()
        _status_has(page, "review-status", "It is no longer there.")
        assert page.locator("#review-status").get_attribute("data-state") == "error"
        browser.close()


def test_resetting_the_navigator_asks_and_saving_a_mode_sends_the_choice(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        _mem_page(page)
        resets = _record(page, "**/api/v1/harness/memory/navigator/reset", [(200, {"ok": True})])
        modes = _record(page, "**/api/v1/harness/memory/mode", [(200, {"ok": True, "mode": "talk"})])
        _open_memory(page, studio_server)
        asked = _dialogs(page, [False, True])
        page.locator(_sec("overview") + " button[data-mem='navigator-reset']").click()
        assert _until(page, lambda: asked)
        page.wait_for_timeout(200)
        assert resets == [] and "numbers so far are lost" in asked[0]
        page.locator(_sec("overview") + " button[data-mem='navigator-reset']").click()
        _status_has(page, "review-status", "Navigator counters reset.")
        assert resets == [{"confirm": True}]
        page.locator(_sec("overview") + " select").select_option("talk")
        page.locator(_sec("overview") + " button[data-mem='mode']").click()
        _status_has(page, "review-status", "Mode saved: Talk")
        assert modes == [{"mode": "talk"}] and len(asked) == 2                              # saving a mode asks nothing
        browser.close()


def test_consolidation_asks_runs_on_the_server_and_shows_how_it_ended(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        done = {**OVER, "jobs": {"running": [], "last": {"consolidate": {"ok": True, "scanned": 5, "merged": 2, "deleted": 0, "promoted": 1}}}}
        running = {**OVER, "jobs": {"running": ["consolidate"], "last": {}}}
        _mem_page(page, over=[OVER, running, done])
        starts = _record(page, "**/api/v1/harness/memory/jobs/consolidate", [(200, {"ok": True, "started": True, "job": "consolidate"})])
        polls = {"n": 0}

        def jobs(route):
            polls["n"] += 1
            _fulfill(route, 200, {"ok": True, "running": [] if polls["n"] > 1 else ["consolidate"], "last": {}})

        page.route("**/api/v1/harness/memory/jobs", jobs)
        _open_memory(page, studio_server)
        asked = _dialogs(page, [False, True])
        btn = _sec("overview") + " button[data-mem='job-consolidate']"
        page.locator(btn).click()
        assert _until(page, lambda: asked)
        page.wait_for_timeout(200)
        assert starts == [] and "model turn" in asked[0]
        page.locator(btn).click()
        _status_has(page, "review-status", "Started on the server")
        assert starts == [{"confirm": True}]
        page.wait_for_function("(s) => { const b = document.querySelector(s); return b && b.disabled && b.textContent.includes('Running'); }", arg=btn)
        page.wait_for_selector(_sec("overview") + " .mem-job[data-job='consolidate']", timeout=15000)
        assert "5 facts scanned, 2 merged, 1 promoted" in page.inner_text(_sec("overview"))
        assert page.locator(btn).is_enabled()
        browser.close()


def test_a_style_lesson_with_nothing_to_learn_and_a_failed_job_are_said_so(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        _mem_page(page, over={**OVER, "jobs": {"running": [], "last": {"lesson": {"ok": True, "nothing_to_learn": True},
                                                                         "consolidate": {"ok": False, "error": "cycle_errors"}}}})
        _open_memory(page, studio_server)
        text = page.inner_text(_sec("overview"))
        assert "No style lesson: there were no new clarifications to learn from." in text
        assert "It did not work: cycle_errors." in text
        assert page.locator(_sec("overview") + " .mem-job[data-job='consolidate']").get_attribute("data-state") == "error"
        browser.close()


def test_adding_a_task_sends_the_fields_and_reports_a_missing_title(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        _mem_page(page)
        adds = _record(page, "**/api/v1/harness/memory/tasks", [(400, {"ok": False, "error": "title_required"}), (200, {"ok": True, "task_id": "task_2"})])
        _open_memory(page, studio_server)
        btn = _sec("tasks") + " button[data-mem='task-add']"
        page.locator(btn).click()
        _status_has(page, "review-status", "A title is needed")
        page.locator(_sec("tasks") + " input").nth(0).fill("Tune the camera")
        page.locator(_sec("tasks") + " input").nth(1).fill("less shake")
        page.locator(btn).click()
        _status_has(page, "review-status", "Task added to the ledger.")
        assert adds == [{"title": "", "summary": ""}, {"title": "Tune the camera", "summary": "less shake"}]
        browser.close()


def test_a_session_opens_and_hides_its_turns(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        _mem_page(page)
        page.route("**/api/v1/harness/memory/sessions/s1?limit=100",
                   lambda r: _fulfill(r, 200, {"ok": True, "session_id": "s1", "turns": [{"role": "owner", "content": "<b>hi</b>"},
                                                                                         {"role": "daedalus", "content": "hello"}]}))
        _open_memory(page, studio_server)
        btn = _sec("sessions") + " .item[data-session='s1'] button[data-mem='session-open']"
        page.locator(btn).click()
        page.wait_for_selector(_sec("sessions") + " .mem-turns")
        first = page.locator(_sec("sessions") + " .mem-turns p").first
        assert first.text_content() == "owner: <b>hi</b>" and first.locator("b").count() == 1     # text, never markup
        assert page.locator(btn).inner_text() == "Hide turns"
        page.locator(btn).click()
        assert page.locator(_sec("sessions") + " .mem-turns").count() == 0
        browser.close()


def test_a_search_shows_links_only_for_web_addresses_and_says_what_was_saved(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        _mem_page(page)
        webs = _record(page, "**/api/v1/harness/memory/search/web", [(200, {"ok": True, "kind": "web", "discoveries_recorded": 1, "results": [
            {"title": "Godot docs", "url": "https://docs.godotengine.org", "snippet": "tilemaps"},
            {"title": "Bad", "url": "javascript:alert(1)", "snippet": ""}]})])
        _record(page, "**/api/v1/harness/memory/search/github", [(502, {"ok": False, "error": "search_failed"})])
        _open_memory(page, studio_server)
        page.locator(_sec("search") + " input").fill("godot tilemap")
        page.locator(_sec("search") + " button[data-mem='search-web']").click()
        _status_has(page, "review-status", "2 results · 1 saved as discoveries.")
        assert webs == [{"query": "godot tilemap", "limit": 5}]
        links = page.locator(_sec("search") + " .mem-results a")
        assert links.nth(0).get_attribute("href") == "https://docs.godotengine.org" and links.nth(0).get_attribute("rel") == "noopener noreferrer"
        assert links.nth(1).get_attribute("href") is None
        page.locator(_sec("search") + " button[data-mem='search-github']").click()
        _status_has(page, "review-status", "The search did not work.")
        browser.close()


def test_one_memory_action_at_a_time_and_a_late_result_after_leaving_is_dropped(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        _mem_page(page)
        held = []
        page.route("**/api/v1/harness/memory/progress/clear", lambda r: held.append(r))
        _open_memory(page, studio_server)
        page.locator(_sec("overview") + " button[data-mem='progress-clear']").click()
        assert _until(page, lambda: held)
        assert page.locator(_sec("tasks") + " button[data-mem='task-add']").is_disabled()   # the others wait
        page.locator("#review-tabs [data-tab=outbox]").click()
        held[0].fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True}))
        page.wait_for_timeout(400)
        assert "Progress line cleared." not in (page.inner_text("#review-status") or "")
        browser.close()


def test_a_task_made_through_the_real_server_is_in_the_ledger(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        _open_memory(page, studio_server)                                                      # real reads, no stubs
        title = f"Real task {time.time_ns()}"
        page.locator(_sec("tasks") + " input").nth(0).fill(title)
        page.locator(_sec("tasks") + " button[data-mem='task-add']").click()
        _status_has(page, "review-status", "Task added to the ledger.")
        page.wait_for_selector(_sec("tasks") + f" .item:has-text('{title}')")
        browser.close()


def test_bulgarian_shows_the_memory_tab_without_raw_keys(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw, (910, 640))
        _mem_page(page)
        _open_memory(page, studio_server, lang="bg")
        text = page.inner_text("#panel-review")
        low = text.lower()
        for expected in ("памет", "общ преглед", "епизодични факти", "дневник на задачите", "сесии", "ръчно търсене",
                         "консолидирай паметта", "нулирай броячите", "изтрий"):
            assert expected in low, expected
        assert not re.search(r"\bstudio\.[A-Za-z]", text), re.findall(r"\bstudio\.[A-Za-z.]+", text)
        assert "episodic facts" not in low and "task ledger" not in low
        browser.close()


@pytest.mark.parametrize("size", [(1280, 800), (1366, 768), (910, 640)])
def test_memory_controls_fit_and_are_really_clickable(studio_server, size):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw, size)
        _mem_page(page)
        _open_memory(page, studio_server)
        over = page.evaluate("""() => { const p = document.getElementById('panel-review');
          return {sw: p.scrollWidth, cw: p.clientWidth, docw: document.documentElement.scrollWidth, vw: window.innerWidth}; }""")
        assert over["sw"] <= over["cw"] + 1 and over["docw"] <= over["vw"] + 1, over
        for role in ("mode", "progress-clear", "navigator-reset", "job-consolidate", "job-lesson", "fact-delete", "task-add",
                     "session-open", "search-web", "search-github"):
            selector = f"#review-list button[data-mem='{role}']"
            page.locator(selector).first.evaluate("el => el.scrollIntoView({block: 'center'})")
            _uncovered(page, selector)
        browser.close()
