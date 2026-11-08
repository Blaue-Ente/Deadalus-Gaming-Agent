"""A6.1c: kernel export and the upgrade-pool scan (API level)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from a0_support import a0_tmp  # noqa: F401
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def client(a0_tmp):  # noqa: F811
    from app.main import app

    return TestClient(app)


# ------------------------------------------------------------------------------------------------ config
def test_defaults_point_at_this_repository_on_a_branch_of_its_own(client):
    body = client.get("/api/v1/harness/kernel").json()
    assert body["repo"] == "Blaue-Ente/Deadalus-Gaming-Agent" and body["branch"] == "kernel-export"
    assert body["branch"] not in body["protected_branches"] and "main" in body["protected_branches"]
    assert body["missing_paths"] == []                                         # every default path exists in this checkout
    assert "argos_data_nexus" not in " ".join(body["paths"]) and "Daedalus/daedalus" not in body["paths"]
    legacy = client.get("/admin/daedalus/kernel/config").json()                # the old page sees the same defaults
    assert legacy["repo"] == body["repo"] and legacy["branch"] == body["branch"]


@pytest.mark.parametrize("repo,branch,paths,code", [
    ("not a repo", "x", ["daedalus"], "bad_repo"),
    ("a/b/c", "x", ["daedalus"], "bad_repo"),
    ("o/r", "../evil", ["daedalus"], "bad_branch"),
    ("o/r", "feat/", ["daedalus"], "bad_branch"),
    ("o/r", "main", ["daedalus"], "protected_branch"),
    ("o/r", "MASTER", ["daedalus"], "protected_branch"),
    ("o/r", "kernel-export", [], "bad_paths"),
    ("o/r", "kernel-export", ["../outside"], "bad_paths"),
    ("o/r", "kernel-export", ["/etc/passwd"], "bad_paths"),
    ("o/r", "kernel-export", ["C:/Windows"], "bad_paths"),
    ("o/r", "kernel-export", ["~/x"], "bad_paths"),
    ("o/r", "kernel-export", ["."], "bad_paths"),
    ("o/r", "kernel-export", ["daedalus/.env"], "secret_path"),
    ("o/r", "kernel-export", ["data/secrets.env"], "secret_path"),
    ("o/r", "refs/heads/main", ["daedalus"], "bad_branch"),
    ("o/r", "heads/main", ["daedalus"], "bad_branch"),
    ("o/r", "origin/main", ["daedalus"], "bad_branch"),
    ("o/r", "tags/v1", ["daedalus"], "bad_branch"),
    ("o/r", "kernel-export", ["data"], "protected_path"),
    ("o/r", "kernel-export", ["data/kv"], "protected_path"),
    ("o/r", "kernel-export", [".git"], "protected_path"),
    ("o/r", "kernel-export", ["playground/projects"], "protected_path"),
    ("o/" + "r" * 300, "kernel-export", ["daedalus"], "bad_repo"),
    ("o/r", "b" * 200, ["daedalus"], "bad_branch"),
    ("o/r", "kernel-export", ["certs/server.pem"], "secret_path"),
])
def test_unsafe_kernel_config_is_refused_and_nothing_is_saved(client, repo, branch, paths, code):
    res = client.post("/api/v1/harness/kernel/config", json={"repo": repo, "branch": branch, "paths": paths})
    assert res.status_code in (400, 409) and res.json()["error"] == code
    assert client.get("/api/v1/harness/kernel").json()["repo"] == "Blaue-Ente/Deadalus-Gaming-Agent"   # unchanged


def test_a_symlink_that_leaves_the_product_is_refused(client, a0_tmp, monkeypatch):  # noqa: F811
    outside = a0_tmp / "outside"
    outside.mkdir()
    root = a0_tmp / "product"
    root.mkdir()
    (root / "link").symlink_to(outside)
    monkeypatch.setattr("daedalus.paths.repo_root", lambda: root)
    res = client.post("/api/v1/harness/kernel/config", json={"repo": "o/r", "branch": "kernel-export", "paths": ["link"]})
    assert res.status_code == 400 and res.json()["error"] == "bad_paths"


def test_a_valid_config_is_saved_and_the_old_page_sees_it(client):
    res = client.post("/api/v1/harness/kernel/config",
                      json={"repo": "me/mine", "branch": "snapshots/daily", "paths": ["daedalus", "app/", "README.md", "daedalus"]})
    assert res.status_code == 200
    body = res.json()
    assert (body["repo"], body["branch"], body["paths"]) == ("me/mine", "snapshots/daily", ["daedalus", "app", "README.md"])
    assert client.get("/admin/daedalus/kernel/config").json()["repo"] == "me/mine"


# ------------------------------------------------------------------------------------------------ push
def _token(monkeypatch, on=True):
    monkeypatch.setattr("daedalus.playground_github.is_configured", lambda: on)


def test_push_needs_an_explicit_confirm_and_a_token(client, monkeypatch):
    calls = []

    async def fake(**kw):
        calls.append(kw)
        return {"ok": True, "repo": kw["repo_full_name"], "html_url": "u", "files_exported": ["daedalus/"]}

    monkeypatch.setattr("daedalus.playground_github.push_kernel", fake)
    _token(monkeypatch)
    assert client.post("/api/v1/harness/kernel/push", json={}).json()["error"] == "confirm_required"
    assert client.post("/api/v1/harness/kernel/push", json={"force": True}).status_code == 400
    assert calls == []                                                          # nothing was exported
    _token(monkeypatch, on=False)
    res = client.post("/api/v1/harness/kernel/push", json={"confirm": True})
    assert res.status_code == 424 and res.json()["error"] == "not_configured" and calls == []


def test_push_goes_to_the_export_branch_without_force_unless_asked(client, monkeypatch):
    calls = []

    async def fake(**kw):
        calls.append(kw)
        return {"ok": True, "repo": kw["repo_full_name"], "html_url": "https://github.com/x/y", "files_exported": ["a/", "b"]}

    monkeypatch.setattr("daedalus.playground_github.push_kernel", fake)
    _token(monkeypatch)
    out = client.post("/api/v1/harness/kernel/push", json={"confirm": True}).json()
    assert out["ok"] is True and out["branch"] == "kernel-export" and out["files"] == 2 and out["forced"] is False
    assert calls[0]["force"] is False and calls[0]["branch"] == "kernel-export"
    out = client.post("/api/v1/harness/kernel/push", json={"confirm": True, "force": True}).json()
    assert out["forced"] is True and calls[1]["force"] is True


def test_a_rejected_plain_push_asks_for_force_instead_of_forcing(client, monkeypatch):
    from daedalus.git_auth import NonFastForward

    async def reject(**kw):
        raise NonFastForward("diverged")

    monkeypatch.setattr("daedalus.playground_github.push_kernel", reject)
    _token(monkeypatch)
    res = client.post("/api/v1/harness/kernel/push", json={"confirm": True})
    assert res.status_code == 409 and res.json() == {"ok": False, "error": "non_fast_forward", "needs_force": True}


def test_a_failed_push_answers_a_bare_code_with_an_error_status(client, monkeypatch):
    async def boom(**kw):
        raise OSError("connection reset by peer: secret-looking detail")

    monkeypatch.setattr("daedalus.playground_github.push_kernel", boom)
    _token(monkeypatch)
    res = client.post("/api/v1/harness/kernel/push", json={"confirm": True})
    assert res.status_code == 502 and res.json() == {"ok": False, "error": "push_failed"}      # no detail key, nothing leaked
    assert "secret-looking" not in res.text


def test_a_protected_branch_saved_before_the_rules_existed_is_still_refused(client, monkeypatch):
    calls = []

    async def fake(**kw):
        calls.append(kw)
        return {"ok": True}

    monkeypatch.setattr("daedalus.playground_github.push_kernel", fake)
    _token(monkeypatch)
    from daedalus import kv

    kv.set("adn:daedalus_kernel_config", {"repo": "o/r", "branch": "main", "paths": ["daedalus"]})   # saved before the rules existed
    res = client.post("/api/v1/harness/kernel/push", json={"confirm": True, "force": True})
    assert res.status_code == 409 and res.json()["error"] == "protected_branch" and calls == []


def test_push_kernel_itself_refuses_a_protected_branch_for_every_caller(a0_tmp, monkeypatch):  # noqa: F811
    from daedalus import playground_github as gh

    monkeypatch.setattr(gh, "is_configured", lambda: True)
    for branch in ("main", "master", " Main "):
        with pytest.raises(ValueError, match="protected_branch"):
            asyncio.run(gh.push_kernel(repo_full_name="o/r", paths=["daedalus"], branch=branch, force=True))


def test_the_export_never_contains_secret_files_git_data_or_dependency_trees(a0_tmp, monkeypatch):  # noqa: F811
    from daedalus import playground_github as gh

    root = a0_tmp / "product"
    for rel, text in {"daedalus/ok.py": "x", "daedalus/.env": "KEY=1", "daedalus/secrets.env": "K=1",
                      "daedalus/certs/server.pem": "p", "daedalus/.git/config": "[remote]", "daedalus/node_modules/a.js": "j",
                      "daedalus/__pycache__/m.pyc": "c", "daedalus/.env.example": "EXAMPLE=", "README.md": "r", ".env": "K=2"}.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    monkeypatch.setattr(gh, "_WORKSPACE_ROOT", root)
    monkeypatch.setattr(gh, "is_configured", lambda: True)
    seen = {}

    async def fake_run(args, *, cwd=None, timeout=0):
        if args[1:2] == ["add"] or args[-3:-1] == ["-u", "origin"] or "push" in args:
            seen["files"] = sorted(str(p.relative_to(cwd)).replace("\\", "/") for p in Path(cwd).rglob("*") if p.is_file() and ".git/" not in str(p).replace("\\", "/").split("daedalus-kernel-")[-1])
        return 0, ""

    monkeypatch.setattr(gh, "_run", fake_run)
    with pytest.raises(ValueError, match="secret_path"):                            # named outright: refused up front
        asyncio.run(gh.push_kernel(repo_full_name="o/r", paths=["daedalus", ".env"], branch="kernel-export"))
    res = asyncio.run(gh.push_kernel(repo_full_name="o/r", paths=["daedalus", "README.md"], branch="kernel-export"))
    assert res["ok"] is True
    files = seen["files"]
    assert "daedalus/ok.py" in files and "README.md" in files and "daedalus/.env.example" in files
    for banned in ("daedalus/.env", "daedalus/secrets.env", "daedalus/certs/server.pem", ".env"):
        assert banned not in files, banned
    assert not [f for f in files if "node_modules" in f or "__pycache__" in f or f.startswith("daedalus/.git/")]


# ------------------------------------------------------------------------------------------------ upgrade pool
def test_the_upgrade_scan_runs_one_at_a_time_and_remembers_how_it_ended(a0_tmp, monkeypatch):  # noqa: F811
    from daedalus import upgrade_pool_scanner as ups
    from daedalus.harness import maintenance

    monkeypatch.setattr(ups, "get_adn_candidates", lambda pool=None: [{"id": "a"}, {"id": "b"}])
    monkeypatch.setattr("daedalus.agent.get_daedalus", lambda: object())
    maintenance._scan.update(task=None, last=None, session_id="")

    async def scenario():
        release = asyncio.Event()

        async def slow(agent, session_id=""):
            await release.wait()
            return {"ok": True}

        monkeypatch.setattr(ups, "run_upgrade_pool_scan", slow)
        first = await maintenance.start_scan()
        second = await maintenance.start_scan()
        running = maintenance.scan_status()
        release.set()
        await maintenance._scan["task"]
        return first, second, running, maintenance.scan_status()

    first, second, running, after = asyncio.run(scenario())
    assert first["started"] is True and first["candidates"] == 2 and first["session_id"].startswith("bg-upgrade-")
    assert second == {"ok": False, "error": "already_running", "session_id": first["session_id"]}
    assert running["running"] is True and running["candidates"] == 2
    assert after["running"] is False and after["last"]["ok"] is True


def test_a_failed_scan_is_recorded_as_failed_not_forgotten(a0_tmp, monkeypatch):  # noqa: F811
    from daedalus import upgrade_pool_scanner as ups
    from daedalus.harness import maintenance

    monkeypatch.setattr(ups, "get_adn_candidates", lambda pool=None: [{"id": "a"}])
    monkeypatch.setattr("daedalus.agent.get_daedalus", lambda: object())
    maintenance._scan.update(task=None, last=None, session_id="")

    async def boom(agent, session_id=""):
        raise RuntimeError("provider down")

    monkeypatch.setattr(ups, "run_upgrade_pool_scan", boom)

    async def scenario():
        await maintenance.start_scan()
        await maintenance._scan["task"]
        return maintenance.scan_status()

    status = asyncio.run(scenario())
    assert status["running"] is False and status["last"]["ok"] is False and status["last"]["error"] == "RuntimeError"


def test_no_candidates_starts_nothing(client, monkeypatch):
    from daedalus import upgrade_pool_scanner as ups
    from daedalus.harness import maintenance

    monkeypatch.setattr(ups, "get_adn_candidates", lambda pool=None: [])
    maintenance._scan.update(task=None, last=None, session_id="")
    out = client.post("/api/v1/harness/upgrade-pool/scan").json()
    assert out == {"ok": True, "started": False, "candidates": 0}
    assert client.get("/api/v1/harness/upgrade-pool").json()["running"] is False


# ------------------------------------------------------------------------------------------------ UI (real clicks)
import json  # noqa: E402
import re  # noqa: E402

from test_a61_settings import _open  # noqa: E402
from test_studio_phase2_workspace import _fulfill, _hit, studio_server  # noqa: E402,F401  (fixture and helpers)

NOTE = ".cfg-note:not([hidden])"


def _open_maint(page, server, lang="en"):
    _open(page, server, lang)
    page.wait_for_selector(".cfg-row[data-id='kernel-actions']")
    page.wait_for_selector(".cfg-row[data-id='pool-scan']")
    # Settings loads in two waves; the second one replaces the "loading" line at the top (hidden, or an error on a
    # machine with no model configured) and so moves everything. The "loading" state is a shown line that is not an error.
    # Wait for the settled page, or a position measured before that is measured on a layout that is about to change.
    page.wait_for_function(
        "() => { const s = document.getElementById('settings-status'); return s.hidden || s.dataset.state === 'error'; }")


def _note(page, row, text=None):
    sel = f".cfg-row[data-id='{row}'] {NOTE}"
    page.wait_for_function("(s) => document.querySelector(s) !== null", arg=sel)
    if text:
        page.wait_for_function("([s, t]) => { const e = document.querySelector(s); return e && e.textContent.includes(t); }", arg=[sel, text])
    return page.inner_text(sel)


def _dialogs(page, decisions):
    asked: list[str] = []
    queue = list(decisions)

    def handle(dialog):
        asked.append(dialog.message)
        (dialog.accept if queue.pop(0) else dialog.dismiss)()

    page.on("dialog", handle)
    return asked


def _pool_stub(page, *, candidates=3, running=False, last=None, scan=None):
    seen = {"scans": 0, "state": {"candidates": candidates, "running": running, "last": last}}

    def on_status(route):
        _fulfill(route, 200, {"ok": True, "session_id": "", **seen["state"]})

    def on_scan(route):
        seen["scans"] += 1
        status, body = scan or (200, {"ok": True, "started": True, "candidates": candidates, "session_id": "bg-x"})
        if status == 200 and body.get("started"):
            seen["state"]["running"] = True
        _fulfill(route, status, body)

    page.route("**/api/v1/harness/upgrade-pool", on_status)
    page.route("**/api/v1/harness/upgrade-pool/scan", on_scan)
    return seen


def test_the_kernel_fields_show_the_real_defaults_and_every_refusal_is_explained(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _pool_stub(page)
        _open_maint(page, studio_server)
        repo = page.locator(".cfg-row[data-id='kernel-repo'] input")
        branch = page.locator(".cfg-row[data-id='kernel-branch'] input")
        paths = page.locator(".cfg-row[data-id='kernel-paths'] textarea")
        assert repo.input_value() == "Blaue-Ente/Deadalus-Gaming-Agent" and branch.input_value() == "kernel-export"
        assert "daedalus" in paths.input_value().split("\n") and "README.md" in paths.input_value().split("\n")
        save = page.locator(".cfg-row[data-id='kernel-actions'] button", has_text="Save")
        branch.fill("main")
        save.click()
        assert "never goes to a main branch" in _note(page, "kernel-actions", "main branch")
        assert page.locator(".cfg-row[data-id='kernel-actions']").get_attribute("data-state") == "error"
        branch.fill("feature/x")
        paths.fill("daedalus\ndaedalus/.env")
        save.click()
        assert "secret file" in _note(page, "kernel-actions", "secret")
        paths.fill("daedalus\n../outside")
        save.click()
        assert "leaves the product" in _note(page, "kernel-actions", "leaves the product")
        repo.fill("not a repo")
        paths.fill("daedalus")
        save.click()
        assert "owner/name" in _note(page, "kernel-actions", "owner/name")
        browser.close()


def test_a_valid_kernel_config_is_saved_and_survives_a_reload(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _pool_stub(page)
        _open_maint(page, studio_server)
        page.locator(".cfg-row[data-id='kernel-repo'] input").fill("me/mine")
        page.locator(".cfg-row[data-id='kernel-branch'] input").fill("snapshots/daily")
        page.locator(".cfg-row[data-id='kernel-paths'] textarea").fill("daedalus\napp/\n\nREADME.md")
        page.locator(".cfg-row[data-id='kernel-actions'] button", has_text="Save").click()
        assert "Saved" in _note(page, "kernel-actions", "Saved")
        assert page.locator(".cfg-row[data-id='kernel-paths'] textarea").input_value() == "daedalus\napp\nREADME.md"
        page.reload(wait_until="domcontentloaded")
        _open_maint(page, studio_server)
        assert page.locator(".cfg-row[data-id='kernel-repo'] input").input_value() == "me/mine"
        assert page.locator(".cfg-row[data-id='kernel-branch'] input").input_value() == "snapshots/daily"
        # the server is shared by the tests of this module: put the defaults back so no test depends on the order
        page.locator(".cfg-row[data-id='kernel-repo'] input").fill("Blaue-Ente/Deadalus-Gaming-Agent")
        page.locator(".cfg-row[data-id='kernel-branch'] input").fill("kernel-export")
        page.locator(".cfg-row[data-id='kernel-paths'] textarea").fill("daedalus\napp\nui\nscripts\nassets\nREADME.md\nARCHITECTURE.md\nDEPLOY.md\nCONTRIBUTING.md\npyproject.toml\nDockerfile")
        page.locator(".cfg-row[data-id='kernel-actions'] button", has_text="Save").click()
        _note(page, "kernel-actions", "Saved")
        browser.close()


def test_declining_the_first_question_sends_nothing(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _pool_stub(page)
        posts: list[str] = []
        page.route("**/api/v1/harness/kernel/push", lambda r: (posts.append("x"), _fulfill(r, 200, {"ok": True, "repo": "r", "branch": "b", "files": 1})))
        _open_maint(page, studio_server)
        asked = _dialogs(page, [False])
        page.locator(".cfg-row[data-id='kernel-actions'] button", has_text="Push export").click()
        page.wait_for_timeout(300)
        assert posts == [] and len(asked) == 1
        shown_repo = page.locator(".cfg-row[data-id='kernel-repo'] input").input_value()
        shown_branch = page.locator(".cfg-row[data-id='kernel-branch'] input").input_value()
        assert shown_repo in asked[0] and shown_branch in asked[0] and "Secret files are never included" in asked[0]
        assert page.locator(".cfg-row[data-id='kernel-actions'] .cfg-note:not([hidden])").count() == 0     # nothing to report
        browser.close()


def test_a_diverged_export_branch_is_replaced_only_after_the_second_yes(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _pool_stub(page)
        posts: list[dict] = []
        answers = [(409, {"ok": False, "error": "non_fast_forward", "needs_force": True}),
                   (409, {"ok": False, "error": "non_fast_forward", "needs_force": True}),
                   (200, {"ok": True, "repo": "Blaue-Ente/Deadalus-Gaming-Agent", "branch": "kernel-export", "files": 9, "forced": True})]

        def on_push(route):
            posts.append(json.loads(route.request.post_data or "{}"))
            status, body = answers.pop(0)
            _fulfill(route, status, body)

        page.route("**/api/v1/harness/kernel/push", on_push)
        _open_maint(page, studio_server)
        push = page.locator(".cfg-row[data-id='kernel-actions'] button", has_text="Push export")
        asked = _dialogs(page, [True, False, True, True])
        push.click()                                              # yes to the push, rejected, no to the replace
        assert "Not pushed" in _note(page, "kernel-actions", "Not pushed")
        assert posts == [{"confirm": True}] and "different history" in asked[1]
        push.click()                                              # yes, rejected, yes to the replace
        assert "Replaced the branch and pushed 9 items" in _note(page, "kernel-actions", "Replaced")
        assert posts[1:] == [{"confirm": True}, {"confirm": True, "force": True}]
        assert page.locator(".cfg-row[data-id='kernel-actions']").get_attribute("data-state") == "ok"
        browser.close()


def test_unsaved_kernel_fields_are_never_pushed(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _pool_stub(page)
        posts: list[str] = []
        page.route("**/api/v1/harness/kernel/push", lambda r: (posts.append("x"), _fulfill(r, 200, {"ok": True, "repo": "r", "branch": "b", "files": 1})))
        _open_maint(page, studio_server)
        asked = _dialogs(page, [True])
        page.locator(".cfg-row[data-id='kernel-branch'] input").fill("some-other-branch")
        page.locator(".cfg-row[data-id='kernel-actions'] button", has_text="Push export").click()
        assert "Save them before pushing" in _note(page, "kernel-actions", "Save them")
        assert posts == [] and asked == []                       # no question, no request: the server pushes what it has saved
        browser.close()


def test_a_missing_token_and_a_failed_push_are_explained_not_swallowed(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _pool_stub(page)
        answers = [(424, {"ok": False, "error": "not_configured"}), (502, {"ok": False, "error": "push_failed"}), (200, {"ok": False, "error": "no_files"})]
        page.route("**/api/v1/harness/kernel/push", lambda r: _fulfill(r, *answers.pop(0)))
        _open_maint(page, studio_server)
        _dialogs(page, [True, True, True])
        push = page.locator(".cfg-row[data-id='kernel-actions'] button", has_text="Push export")
        push.click()
        assert "No GitHub token is set" in _note(page, "kernel-actions", "token")
        push.click()
        assert "The push failed" in _note(page, "kernel-actions", "push failed")
        push.click()                                  # even a 200 that says ok:false is never shown as pushed
        assert "No listed path matched" in _note(page, "kernel-actions", "No listed path")
        assert "Pushed" not in page.inner_text(".cfg-row[data-id='kernel-actions']")
        assert page.locator(".cfg-row[data-id='kernel-actions']").get_attribute("data-state") == "error"
        browser.close()


def test_the_upgrade_scan_asks_first_runs_once_and_shows_that_it_is_running(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        seen = _pool_stub(page, candidates=3)
        _open_maint(page, studio_server)
        row = ".cfg-row[data-id='pool-scan']"
        assert "3 candidates" in page.inner_text(row) and "idle" in page.inner_text(row)
        asked = _dialogs(page, [False, True])
        page.locator(row + " button").click()
        page.wait_for_timeout(300)
        assert seen["scans"] == 0 and "model calls" in asked[0]                 # declined: nothing started
        page.locator(row + " button").click()
        assert "Scan started for 3 candidates" in _note(page, "pool-scan", "Scan started")
        page.wait_for_function("() => document.querySelector(\".cfg-row[data-id='pool-scan'] button\").disabled")
        assert "scanning" in page.inner_text(row)
        assert "Scan started for 3" in page.inner_text(row + " .cfg-note")      # the result survives the status repaint
        assert seen["scans"] == 1
        browser.close()


@pytest.mark.parametrize("answer,expect", [
    ((409, {"ok": False, "error": "already_running", "session_id": "s"}), "already running"),
    ((200, {"ok": True, "started": False, "candidates": 0}), "no new candidates"),
])
def test_the_upgrade_scan_reports_busy_and_empty_honestly(studio_server, answer, expect):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _pool_stub(page, scan=answer)
        _open_maint(page, studio_server)
        _dialogs(page, [True])
        page.locator(".cfg-row[data-id='pool-scan'] button").click()
        assert expect in _note(page, "pool-scan", expect.split()[0]).lower()
        browser.close()


def test_a_failed_last_scan_is_shown_as_failed(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _pool_stub(page, last={"ok": False, "error": "RuntimeError", "finished_at": 1})
        _open_maint(page, studio_server)
        assert "Last scan failed: RuntimeError" in page.inner_text(".cfg-row[data-id='pool-scan']")
        browser.close()


def test_bulgarian_shows_the_new_sections_in_bulgarian_without_raw_keys(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _pool_stub(page)
        _open_maint(page, studio_server, lang="bg")
        page.locator(".cfg-row[data-id='kernel-branch'] input").fill("main")
        page.locator(".cfg-row[data-id='kernel-actions'] button", has_text="Запази").click()
        assert "главен клон" in _note(page, "kernel-actions", "главен клон")
        text = page.inner_text("#panel-settings")
        low = text.lower()
        for heading in ("експорт на ядрото", "пул за подобрения", "изпрати експорта", "сканирай сега"):
            assert heading in low, heading
        assert not re.search(r"\b(studio|settings)\.[A-Za-z]", text), re.findall(r"\b(?:studio|settings)\.[A-Za-z.]+", text)
        assert "kernel export" not in low and "upgrade pool" not in low
        browser.close()


@pytest.mark.parametrize("size", [(1280, 800), (1366, 768), (910, 640)])
def test_kernel_and_pool_sections_fit_and_their_controls_are_really_clickable(studio_server, size):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": size[0], "height": size[1]})
        _pool_stub(page)
        _open_maint(page, studio_server)
        over = page.evaluate("""() => { const p = document.getElementById('panel-settings');
          return {sw: p.scrollWidth, cw: p.clientWidth, docw: document.documentElement.scrollWidth, vw: window.innerWidth}; }""")
        assert over["sw"] <= over["cw"] + 1 and over["docw"] <= over["vw"] + 1, over
        for selector in (".cfg-row[data-id='kernel-repo'] input", ".cfg-row[data-id='kernel-branch'] input",
                         ".cfg-row[data-id='kernel-paths'] textarea", ".cfg-row[data-id='kernel-actions'] button",
                         ".cfg-row[data-id='pool-scan'] button"):
            # scroll_into_view_if_needed leaves a partly visible element where it is; its centre can then be off screen
            page.locator(selector).first.evaluate("el => el.scrollIntoView({block: 'center'})")
            _hit(page, selector)
        browser.close()


# ------------------------------------------------------------------------------------------------ review fixes
def test_too_long_fields_answer_a_code_not_a_422_the_browser_cannot_read(client):
    res = client.post("/api/v1/harness/kernel/config", json={"repo": "o/" + "r" * 400, "branch": "kernel-export", "paths": ["daedalus"]})
    assert res.status_code == 400 and res.json()["error"] == "bad_repo"
    res = client.post("/api/v1/harness/kernel/config", json={"repo": "o/r", "branch": "kernel-export", "paths": ["a%d" % i for i in range(60)]})
    assert res.status_code == 400 and res.json()["error"] == "bad_paths"


def test_the_state_directories_of_this_install_are_never_exportable(client, a0_tmp, monkeypatch):  # noqa: F811
    from daedalus.paths import data_root, playground_projects_dir

    inside = a0_tmp / "product"
    (inside / "state").mkdir(parents=True)
    monkeypatch.setattr("daedalus.paths.repo_root", lambda: inside)
    monkeypatch.setattr("daedalus.paths.data_root", lambda: inside / "state")
    monkeypatch.setattr("daedalus.paths.playground_projects_dir", lambda: inside / "state" / "projects")
    res = client.post("/api/v1/harness/kernel/config", json={"repo": "o/r", "branch": "kernel-export", "paths": ["state"]})
    assert res.status_code == 400 and res.json()["error"] == "protected_path"


def test_the_old_page_cannot_save_or_push_what_the_new_one_refuses(client, monkeypatch):
    calls = []

    async def fake_run(args, *, cwd=None, timeout=0):
        calls.append(args)
        return 0, ""

    monkeypatch.setattr("daedalus.playground_github._run", fake_run)
    _token(monkeypatch)
    for bad in ({"repo": "o/r", "branch": "main", "paths": ["daedalus"]},
                {"repo": "o/r", "branch": "refs/heads/main", "paths": ["daedalus"]},
                {"repo": "attacker/x", "branch": "kernel-export", "paths": ["../../etc", "data"]},
                {"repo": "o/r", "branch": "kernel-export", "paths": ["daedalus/.env"]}):
        res = client.post("/admin/daedalus/kernel/config", json=bad)
        assert res.status_code == 400 and res.json()["ok"] is False, bad
        res = client.post("/admin/daedalus/kernel/push", json={**bad, "force": True})
        assert res.status_code == 400 and res.json()["ok"] is False, bad
    assert calls == []                                                           # git was never even started
    assert client.get("/admin/daedalus/kernel/config").json()["repo"] == "Blaue-Ente/Deadalus-Gaming-Agent"


@pytest.mark.skipif(not hasattr(__import__("os"), "symlink"), reason="no symlinks")
def test_a_link_inside_an_exported_folder_is_not_followed(a0_tmp, monkeypatch):  # noqa: F811
    import os

    from daedalus import playground_github as gh

    root = a0_tmp / "product"
    (root / "daedalus").mkdir(parents=True)
    (root / "daedalus" / "ok.py").write_text("x")
    secret = a0_tmp / "elsewhere" / "id_rsa_notes"
    secret.parent.mkdir()
    secret.write_text("PRIVATE")
    try:
        os.symlink(secret, root / "daedalus" / "innocent.txt")
        os.symlink(a0_tmp / "elsewhere", root / "daedalus" / "folder")
    except OSError:
        pytest.skip("cannot create links here")
    monkeypatch.setattr(gh, "_WORKSPACE_ROOT", root)
    monkeypatch.setattr(gh, "is_configured", lambda: True)
    seen = {}

    async def fake_run(args, *, cwd=None, timeout=0):
        if "add" in args:
            seen["files"] = sorted(str(p.relative_to(cwd)).replace("\\", "/") for p in Path(cwd).rglob("*") if p.is_file() and ".git" not in p.parts)
        return 0, ""

    monkeypatch.setattr(gh, "_run", fake_run)
    asyncio.run(gh.push_kernel(repo_full_name="o/r", paths=["daedalus"], branch="kernel-export"))
    assert "daedalus/ok.py" in seen["files"]
    assert not [f for f in seen["files"] if "innocent" in f or "folder" in f or "id_rsa" in f], seen["files"]
    assert "PRIVATE" not in "".join(p.read_text(errors="ignore") for p in [])        # nothing read through a link


def test_a_scan_that_reports_failure_in_its_result_is_recorded_as_failed(a0_tmp, monkeypatch):  # noqa: F811
    from daedalus import upgrade_pool_scanner as ups
    from daedalus.harness import maintenance

    monkeypatch.setattr(ups, "get_adn_candidates", lambda pool=None: [{"id": "a"}])
    monkeypatch.setattr("daedalus.agent.get_daedalus", lambda: object())
    maintenance._scan.update(task=None, last=None, session_id="")

    async def quietly_failed(agent, session_id=""):
        return {"ok": False, "error": "model call failed"}                  # what the scanner really does: no raise

    monkeypatch.setattr(ups, "run_upgrade_pool_scan", quietly_failed)

    async def scenario():
        await maintenance.start_scan()
        await maintenance._scan["task"]
        return maintenance.scan_status()

    status = asyncio.run(scenario())
    assert status["last"]["ok"] is False and status["last"]["error"] == "scan_failed"


def test_the_scan_button_comes_back_and_the_result_shows_when_the_scan_ends(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        polls = {"n": 0, "started": False}

        def status(route):
            if polls["started"]:
                polls["n"] += 1
            running = polls["started"] and polls["n"] < 3                       # two looks later it is over
            last = {"ok": True, "finished_at": 1} if polls["started"] and not running else None
            _fulfill(route, 200, {"ok": True, "candidates": 3, "running": running, "session_id": "", "last": last})

        def scan(route):
            polls["started"] = True
            _fulfill(route, 200, {"ok": True, "started": True, "candidates": 3, "session_id": "bg-x"})

        page.route("**/api/v1/harness/upgrade-pool", status)
        page.route("**/api/v1/harness/upgrade-pool/scan", scan)
        _open_maint(page, studio_server)
        _dialogs(page, [True])
        row = ".cfg-row[data-id='pool-scan']"
        page.locator(row + " button").click()
        _note(page, "pool-scan", "Scan started")
        page.wait_for_function("() => document.querySelector(\".cfg-row[data-id='pool-scan'] button\").disabled")
        assert "scanning" in page.inner_text(row)
        page.wait_for_function("() => !document.querySelector(\".cfg-row[data-id='pool-scan'] button\").disabled", timeout=15000)
        text = page.inner_text(row)
        assert "idle" in text and "Last scan finished without error" in text and "scanning" not in text
        assert "Scan started for 3" in page.inner_text(row + " .cfg-note")      # the owner's own result is still there
        browser.close()


def test_unsaved_kernel_edits_survive_leaving_and_reopening_settings(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _pool_stub(page)
        _open_maint(page, studio_server)
        page.locator(".cfg-row[data-id='kernel-branch'] input").fill("work-in-progress")
        page.locator(".cfg-row[data-id='kernel-paths'] textarea").fill("daedalus\napp\nscripts")
        page.locator("#workspace-nav button[data-panel=chat]").click()
        page.locator("#workspace-nav button[data-panel=settings]").click()
        page.wait_for_selector(".cfg-row[data-id='kernel-actions']")
        page.wait_for_timeout(500)
        assert page.locator(".cfg-row[data-id='kernel-branch'] input").input_value() == "work-in-progress"
        assert page.locator(".cfg-row[data-id='kernel-paths'] textarea").input_value() == "daedalus\napp\nscripts"
        posts: list[str] = []
        page.route("**/api/v1/harness/kernel/push", lambda r: (posts.append("x"), _fulfill(r, 200, {"ok": True})))
        _dialogs(page, [True])
        page.locator(".cfg-row[data-id='kernel-actions'] button", has_text="Push export").click()
        assert "Save them before pushing" in _note(page, "kernel-actions", "Save them")      # still not what the server has
        assert posts == []
        browser.close()


def test_the_actions_row_names_what_push_will_do_and_follows_a_save(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _pool_stub(page)
        _open_maint(page, studio_server)
        title = ".cfg-row[data-id='kernel-actions'] .cfg-name b"
        before = page.inner_text(title)
        assert "→" in before and "Kernel export" not in before                      # not a copy of the section heading
        page.locator(".cfg-row[data-id='kernel-branch'] input").fill("export-two")
        page.locator(".cfg-row[data-id='kernel-actions'] button", has_text="Save").click()
        _note(page, "kernel-actions", "Saved")
        assert page.inner_text(title).endswith("→ export-two")
        page.locator(".cfg-row[data-id='kernel-branch'] input").fill("kernel-export")           # put the default back
        page.locator(".cfg-row[data-id='kernel-actions'] button", has_text="Save").click()
        page.wait_for_function("() => document.querySelector(\".cfg-row[data-id='kernel-actions'] b\").textContent.endsWith('→ kernel-export')")
        browser.close()


def test_a_state_directory_is_refused_with_a_reason(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _pool_stub(page)
        _open_maint(page, studio_server)
        page.locator(".cfg-row[data-id='kernel-paths'] textarea").fill("daedalus\ndata")
        page.locator(".cfg-row[data-id='kernel-actions'] button", has_text="Save").click()
        assert "own state" in _note(page, "kernel-actions", "own state")
        page.locator(".cfg-row[data-id='kernel-branch'] input").fill("refs/heads/main")
        page.locator(".cfg-row[data-id='kernel-paths'] textarea").fill("daedalus")
        page.locator(".cfg-row[data-id='kernel-actions'] button", has_text="Save").click()
        assert "not a usable branch name" in _note(page, "kernel-actions", "usable branch")
        page.locator(".cfg-row[data-id='kernel-branch'] input").fill("kernel-export")
        browser.close()
