"""A6.2a: Review actions (bulk permissions, blocker fixes, remediation, details, approve-all). API level."""
from __future__ import annotations

import asyncio

import pytest
from a0_support import a0_tmp  # noqa: F401
from fastapi.testclient import TestClient


@pytest.fixture
def client(a0_tmp):  # noqa: F811
    from app.main import app

    return TestClient(app)


def _active(perm: str) -> bool:
    from daedalus import outbox

    return any(p.get("perm_class") == perm and p.get("active") for p in outbox.list_permissions())


# ------------------------------------------------------------------------------------------------ permissions
def test_grant_all_needs_a_confirm_and_grants_nothing_without_it(client):
    from daedalus import outbox

    res = client.post("/api/v1/harness/review/permissions/grant-all", json={"hours": 24})
    assert res.status_code == 400 and res.json() == {"ok": False, "error": "confirm_required"}
    assert not any(_active(p) for p in outbox.KNOWN_PERMISSION_IDS)


def test_grant_all_grants_every_known_class_for_a_clamped_time(client):
    from daedalus import outbox

    known = list(outbox.KNOWN_PERMISSION_IDS)
    for sent, expected in ((9999, 72.0), (-5, 0.25), (0, 24.0), (6, 6.0)):
        out = client.post("/api/v1/harness/review/permissions/grant-all", json={"hours": sent, "confirm": True}).json()
        assert out["ok"] is True and out["hours"] == expected and out["count"] == len(known), sent
    assert all(_active(p) for p in known)


def test_renew_only_touches_what_is_missing_or_expired(client):
    from daedalus import outbox

    view = client.get("/api/v1/harness/review/permissions").json()
    assert view["known"] == list(outbox.KNOWN_PERMISSION_IDS) and view["max_hours"] == 72.0
    client.post("/api/v1/harness/review/permissions/grant-all", json={"hours": 5, "confirm": True})
    assert client.get("/api/v1/harness/review/permissions").json()["renewable"] == []
    outbox.revoke_permission("web_search")
    assert "web_search" in client.get("/api/v1/harness/review/permissions").json()["renewable"]
    assert client.post("/api/v1/harness/review/permissions/renew-expired", json={"hours": 3}).status_code == 400
    out = client.post("/api/v1/harness/review/permissions/renew-expired", json={"hours": 3, "confirm": True}).json()
    assert out["ok"] is True and "web_search" in out["granted"] and out["hours"] == 3.0
    assert _active("web_search")


# ------------------------------------------------------------------------------------------------ blockers
def test_recommended_permissions_come_from_the_server_never_from_the_request(client):
    from daedalus import outbox

    assert client.post("/api/v1/harness/review/blockers/grant-recommended", json={"hours": 8}).status_code == 400
    res = client.post("/api/v1/harness/review/blockers/grant-recommended",
                      json={"hours": 8, "confirm": True, "permissions": ["run_command", "execute_raw_sql"]})
    out = res.json()
    preview = client.get("/api/v1/harness/review/blockers").json()
    assert out["ok"] is True and out["granted"] == preview["recommended"]
    assert "execute_raw_sql" not in out["granted"] and not _active("execute_raw_sql")      # the body cannot name a permission


def test_auto_fix_one_blocker_grants_only_what_its_text_names(client):
    from daedalus import blockers

    bid = blockers.push_blocker("Cannot write file: needs modify_files", "tried", "next")
    other = blockers.push_blocker("Needs web search to continue", "", "")
    assert client.post("/api/v1/harness/review/blockers/auto-fix", json={"id": bid}).status_code == 400
    out = client.post("/api/v1/harness/review/blockers/auto-fix", json={"id": bid, "hours": 2, "confirm": True}).json()
    assert out["ok"] is True and out["granted"] == ["modify_files"] and out["id"] == bid and out["hours"] == 2.0
    assert _active("modify_files") and not _active("web_search")                      # the other blocker was left alone
    again = client.post("/api/v1/harness/review/blockers/auto-fix", json={"id": bid, "confirm": True})
    assert again.status_code == 409 and again.json()["error"] == "already_resolved"
    missing = client.post("/api/v1/harness/review/blockers/auto-fix", json={"id": "blk_nope", "confirm": True})
    assert missing.status_code in (404, 409) and missing.json()["ok"] is False
    assert other


def test_a_missing_id_never_widens_auto_fix_to_every_blocker(client):
    from daedalus import blockers

    blockers.push_blocker("Cannot write file: needs modify_files", "", "")
    for body in ({"confirm": True}, {"id": "", "confirm": True}, {"id": "   ", "hours": 4, "confirm": True}):
        res = client.post("/api/v1/harness/review/blockers/auto-fix", json=body)
        assert res.status_code == 400 and res.json()["error"] == "id_required", body
    assert not _active("modify_files")                                          # nothing was granted, nothing resolved
    assert len(blockers.list_blockers(status="open")) == 1


def test_auto_fix_all_needs_the_explicit_flag_and_reports_counts_as_a_result(client):
    from daedalus import blockers

    blockers.push_blocker("Something vague that nobody understands", "", "")
    out = client.post("/api/v1/harness/review/blockers/auto-fix", json={"all": True, "confirm": True})
    assert out.status_code == 200                                               # a result to read, not a failure to hide
    assert out.json()["ok"] is False and out.json()["error"] == "no_fix_available"
    assert out.json()["fixed"] == 0 and out.json()["skipped"] == 1
    assert client.post("/api/v1/harness/review/blockers/auto-fix", json={"all": True}).status_code == 400   # still needs confirm
    blockers.push_blocker("Cannot write file: needs modify_files", "", "")
    out = client.post("/api/v1/harness/review/blockers/auto-fix", json={"all": True, "confirm": True, "hours": 4}).json()
    assert out["ok"] is True and out["fixed"] == 1 and out["skipped"] == 1 and out["hours"] == 4.0


# ------------------------------------------------------------------------------------------------ remediation
def _npm_blocker():
    from daedalus import blockers

    return blockers.push_blocker("npm install blocked by the firewall for slug: my-game", "npm ci", "use static")


def test_remediation_is_previewed_and_changing_the_kind_needs_its_own_confirmation(client, monkeypatch):
    calls = []

    async def get_project(slug):
        return {"slug": slug, "kind": "vite"}

    async def update_project(slug, **fields):
        calls.append((slug, fields))
        return {"slug": slug, **fields}

    monkeypatch.setattr("daedalus.playground.get_project", get_project)
    monkeypatch.setattr("daedalus.playground.update_project", update_project)
    bid = _npm_blocker()
    prev = client.get(f"/api/v1/harness/review/blockers/{bid}/remediation").json()
    assert prev == {"ok": True, "slug": "my-game", "old_kind": "vite", "new_kind": "static", "changes_kind": True}
    assert calls == []                                                          # a preview changes nothing
    refused = client.post(f"/api/v1/harness/review/blockers/{bid}/remediate", json={})
    assert refused.status_code == 400 and refused.json()["error"] == "confirm_required"
    assert refused.json()["old_kind"] == "vite" and refused.json()["new_kind"] == "static" and calls == []
    out = client.post(f"/api/v1/harness/review/blockers/{bid}/remediate", json={"confirm_kind_change": True}).json()
    assert out["ok"] is True and out["old_kind"] == "vite" and out["new_kind"] == "static"
    assert calls == [("my-game", {"kind": "static"})]


def test_a_project_with_no_kind_is_previewed_as_a_change_exactly_like_the_real_thing(client, monkeypatch):
    calls = []

    async def get_project(slug):
        return {"slug": slug, "kind": None}

    async def update_project(slug, **fields):
        calls.append((slug, fields))

    monkeypatch.setattr("daedalus.playground.get_project", get_project)
    monkeypatch.setattr("daedalus.playground.update_project", update_project)
    bid = _npm_blocker()
    assert client.get(f"/api/v1/harness/review/blockers/{bid}/remediation").json()["changes_kind"] is True
    assert client.post(f"/api/v1/harness/review/blockers/{bid}/remediate", json={}).status_code == 400 and calls == []
    assert client.post(f"/api/v1/harness/review/blockers/{bid}/remediate", json={"confirm_kind_change": True}).json()["ok"] is True
    assert calls == [("my-game", {"kind": "static"})]


def test_remediate_infra_blocker_dry_run_changes_nothing(a0_tmp, monkeypatch):  # noqa: F811
    from daedalus import blockers

    calls = []

    async def get_project(slug):
        return {"slug": slug, "kind": "vite"}

    async def update_project(slug, **fields):
        calls.append(slug)

    monkeypatch.setattr("daedalus.playground.get_project", get_project)
    monkeypatch.setattr("daedalus.playground.update_project", update_project)
    bid = blockers.push_blocker("npm install blocked by the firewall for slug: my-game", "", "")
    res = asyncio.run(blockers.remediate_infra_blocker(bid, dry_run=True))
    assert res == {"ok": True, "dry_run": True, "slug": "my-game", "old_kind": "vite", "kind": "static", "changes_kind": True}
    assert calls == [] and blockers.list_blockers(status="open")[0]["id"] == bid            # still open, project untouched


def test_remediation_refuses_what_is_not_an_infra_blocker_or_has_no_project(client, monkeypatch):
    from daedalus import blockers

    async def get_project(slug):
        return None

    monkeypatch.setattr("daedalus.playground.get_project", get_project)
    plain = blockers.push_blocker("Cannot write file: needs modify_files", "", "")
    assert client.get(f"/api/v1/harness/review/blockers/{plain}/remediation").json()["error"] in {"no_project", "not_infra"}
    ghost = _npm_blocker()
    res = client.get(f"/api/v1/harness/review/blockers/{ghost}/remediation")
    assert res.status_code == 404 and res.json()["error"] == "project_not_found"
    assert client.get("/api/v1/harness/review/blockers/blk_nope/remediation").status_code == 404


def test_a_static_project_needs_no_confirmation(client, monkeypatch):
    async def get_project(slug):
        return {"slug": slug, "kind": "static"}

    monkeypatch.setattr("daedalus.playground.get_project", get_project)
    bid = _npm_blocker()
    out = client.post(f"/api/v1/harness/review/blockers/{bid}/remediate", json={}).json()
    assert out["ok"] is True and out["already_static"] is True


# ------------------------------------------------------------------------------------------------ details
def test_proposal_detail_is_trimmed_and_marks_a_cut_diff(client, monkeypatch):
    big = "\n".join("+" + "x" * 80 for _ in range(1500))
    monkeypatch.setattr("daedalus.self_improve.get_proposal", lambda pid: {
        "id": pid, "status": "pending", "file_path": "daedalus/a.py", "reason": "why", "skill": "s", "proposed_by": "me",
        "risk_level": 2, "risk_rationale": "small", "created_at": 1.0, "stats": {"additions": 1500},
        "old_content": "SECRET-OLD" * 100, "new_content": "SECRET-NEW" * 100, "diff": big})
    out = client.get("/api/v1/harness/review/proposals/prop_1").json()
    assert out["ok"] is True and out["truncated"] is True and len(out["diff"]) == 60_000
    assert "old_content" not in out and "new_content" not in out and "SECRET" not in str(out)
    monkeypatch.setattr("daedalus.self_improve.get_proposal", lambda pid: None)
    res = client.get("/api/v1/harness/review/proposals/prop_x")
    assert res.status_code == 404 and res.json()["error"] == "not_found"


def test_bundle_detail_shows_each_file_with_its_diff(client, a0_tmp, monkeypatch):  # noqa: F811
    from daedalus import bundle

    root = a0_tmp / "product"
    root.mkdir()
    (root / "a.txt").write_text("one\ntwo\n")
    monkeypatch.setattr(bundle, "ADN_ROOT", root)

    async def make():
        bid = await bundle.create_bundle("my bundle")
        await bundle.stage_file(bid, "a.txt", "replace", "tweak", "one\nTWO\nthree\n")
        await bundle.stage_file(bid, "new.txt", "create", "fresh", "hello\n")
        return bid

    bid = asyncio.run(make())
    out = client.get(f"/api/v1/harness/review/bundles/{bid}").json()
    assert out["ok"] is True and out["title"] == "my bundle" and len(out["files"]) == 2
    by = {f["file_path"]: f for f in out["files"]}
    assert "-two" in by["a.txt"]["diff"] and "+TWO" in by["a.txt"]["diff"] and by["new.txt"]["action"] == "create"
    assert client.get("/api/v1/harness/review/bundles/nope").status_code == 404


def test_an_unreadable_bundle_store_is_a_code_not_a_500(client, monkeypatch):
    async def boom(bundle_id):
        raise RuntimeError("no pool")

    monkeypatch.setattr("daedalus.bundle.get_bundle", boom)
    res = client.get("/api/v1/harness/review/bundles/b1")
    assert res.status_code == 503 and res.json() == {"ok": False, "error": "unavailable"}


# ------------------------------------------------------------------------------------------------ approve all
def test_approve_all_approves_exactly_the_confirmed_ids_and_reports_partial_honestly(client, monkeypatch):
    seen = []

    def lp(status=None):
        return [{"id": "p1"}, {"id": "p2"}, {"id": "p3"}, {"id": "p_new"}]            # p_new arrived after the owner looked

    def ap(pid, approved_by=""):
        seen.append(pid)
        if pid == "p2":
            return {"ok": False, "error": "path blocked"}
        if pid == "p3":
            raise RuntimeError("disk full")
        return {"ok": True}

    monkeypatch.setattr("daedalus.self_improve.list_proposals", lp)
    monkeypatch.setattr("daedalus.self_improve.approve_proposal", ap)
    url = "/api/v1/harness/review/proposals/approve-all"
    assert client.post(url, json={"ids": ["p1"]}).status_code == 400 and seen == []                 # no confirm
    for body in ({"confirm": True}, {"confirm": True, "ids": []}):
        res = client.post(url, json=body)
        assert res.status_code == 400 and res.json()["error"] == "ids_required", body            # no ids: never "everything"
    assert seen == []
    out = client.post(url, json={"ids": ["p1", "p2", "p3", "p_gone"], "confirm": True}).json()
    assert out["ok"] is False and out["partial"] is True and out["applied"] == 1 and out["skipped"] == 1   # p_gone is no longer pending
    assert out["failed"] == [{"id": "p2", "error": "path blocked"}, {"id": "p3", "error": "RuntimeError"}]
    assert "p_new" not in seen                                                                       # not on the owner's list: untouched


def test_the_old_page_route_keeps_approving_whatever_is_pending(client, monkeypatch):
    seen = []
    monkeypatch.setattr("daedalus.self_improve.list_proposals", lambda status=None: [{"id": "a"}, {"id": "b"}])
    monkeypatch.setattr("daedalus.self_improve.approve_proposal", lambda pid, approved_by="": (seen.append(pid), {"ok": True})[1])
    out = client.post("/admin/daedalus/proposals/approve-all-pending").json()
    assert out["ok"] is True and out["applied"] == 2 and seen == ["a", "b"]


# ------------------------------------------------------------------------------------------------ UI (real clicks)
import json  # noqa: E402
import re  # noqa: E402

from test_a61c_maintenance import _dialogs  # noqa: E402
from test_studio_phase2_workspace import _fulfill, _hit, studio_server  # noqa: E402,F401  (fixture and helpers)

BLOCKERS = {"blockers": [{"id": "blk_a", "status": "open", "reason": "npm firewall blocked install for slug: my-game"},
                         {"id": "blk_done", "status": "resolved", "reason": "old", "resolution_note": "ok"}]}


def _until(page, predicate, timeout=8.0):
    """Wait for something the page did and the test records on the Python side (a dialog asked, a request sent)."""
    import time as _t

    deadline = _t.time() + timeout
    while _t.time() < deadline:
        if predicate():
            return True
        page.wait_for_timeout(25)
    return predicate()


def _open_review(page, server, tab, lang="en", wait="#review-list .item"):
    page.add_init_script(f"try {{ localStorage.setItem('dx.lang', '{lang}'); }} catch (e) {{}}")
    page.goto(server["url"] + f"/?panel=review&tab={tab}", wait_until="domcontentloaded")
    page.wait_for_selector(wait)


def _bulk_note(page, box, text=None):
    sel = f".item.bulk[data-id='{box}'] .cfg-note:not([hidden])"
    page.wait_for_function("(s) => document.querySelector(s) !== null", arg=sel)
    if text:
        page.wait_for_function("([s, t]) => { const e = document.querySelector(s); return e && e.textContent.includes(t); }", arg=[sel, text])
    return page.inner_text(sel)


def _record(page, pattern, answers):
    """Stub a POST: record each body, answer from a list (last answer repeats)."""
    seen: list[dict] = []

    def handler(route):
        seen.append(json.loads(route.request.post_data or "{}"))
        status, body = answers[min(len(seen) - 1, len(answers) - 1)]
        _fulfill(route, status, body)

    page.route(pattern, handler)
    return seen


def _blockers_page(page, rows=None):
    page.route(re.compile(r".*/admin/daedalus/blockers(\?.*)?$"), lambda r: _fulfill(r, 200, rows or BLOCKERS))


def test_grant_all_asks_with_the_real_count_and_sends_the_chosen_hours(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        posts = _record(page, "**/api/v1/harness/review/permissions/grant-all", [(200, {"ok": True, "granted": ["a"] * 19, "count": 19, "hours": 4.0})])
        _open_review(page, studio_server, "permissions")
        box = ".item.bulk[data-id='bulk-permissions']"
        asked = _dialogs(page, [False, True])
        page.locator(box + " select").select_option("4")
        page.locator(box + " button", has_text="Grant all").click()
        assert _until(page, lambda: asked)                       # the question was asked...
        page.wait_for_timeout(200)                               # ...and a wrong request would have left by now
        from daedalus.outbox import KNOWN_PERMISSION_CLASSES
        real = len(KNOWN_PERMISSION_CLASSES)                    # the real catalog (18 since R5 removed mesh_call)
        assert posts == [] and len(asked) == 1 and f"{real} permission classes" in asked[0] and "4 hours" in asked[0]      # declined: nothing sent
        page.locator(box + " button", has_text="Grant all").click()
        assert "Granted 19 classes for 4 hours" in _bulk_note(page, "bulk-permissions", "Granted")
        assert posts == [{"hours": 4.0, "confirm": True}]
        assert page.locator(box).get_attribute("data-state") == "ok"                   # the result survives the list reload
        browser.close()


def test_renew_says_nothing_to_renew_without_asking_and_otherwise_asks_with_the_count(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        posts = _record(page, "**/api/v1/harness/review/permissions/renew-expired", [(200, {"ok": True, "granted": ["a", "b"], "count": 2, "hours": 24.0})])
        state = {"renewable": []}
        real = {}

        def preview(route):
            body = route.fetch().json() if not real else real
            real.update(body)
            _fulfill(route, 200, {**body, "renewable": state["renewable"]})

        page.route("**/api/v1/harness/review/permissions", preview)
        _open_review(page, studio_server, "permissions")
        box = ".item.bulk[data-id='bulk-permissions']"
        asked = _dialogs(page, [True])
        page.locator(box + " button", has_text="Renew expired").click()
        assert "Nothing to renew" in _bulk_note(page, "bulk-permissions", "Nothing")
        assert posts == [] and asked == []
        state["renewable"] = ["web_search", "modify_files"]
        page.locator(box + " button", has_text="Renew expired").click()
        assert "Granted 2 classes for 24 hours" in _bulk_note(page, "bulk-permissions", "Granted 2")
        assert "2 expired or missing" in asked[0] and posts == [{"hours": 24.0, "confirm": True}]
        browser.close()


def test_a_refused_grant_is_shown_as_a_reason_not_as_success(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _record(page, "**/api/v1/harness/review/permissions/grant-all", [(400, {"ok": False, "error": "confirm_required"})])
        _open_review(page, studio_server, "permissions")
        _dialogs(page, [True])
        page.locator(".item.bulk[data-id='bulk-permissions'] button", has_text="Grant all").click()
        assert "needs your confirmation" in _bulk_note(page, "bulk-permissions", "confirmation")
        assert page.locator(".item.bulk[data-id='bulk-permissions']").get_attribute("data-state") == "error"
        browser.close()


def test_blocker_auto_fix_asks_first_and_sends_only_the_id_and_hours(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _blockers_page(page)
        posts = _record(page, "**/api/v1/harness/review/blockers/auto-fix", [(200, {"ok": True, "id": "blk_a", "granted": ["modify_files", "web_search"], "hours": 4.0})])
        _open_review(page, studio_server, "proposals", wait="#blocker-list .item")
        page.wait_for_selector("#blocker-list button[data-role='autofix']")
        assert page.locator("#blocker-list button[data-role='autofix']").count() == 1      # only the open blocker has actions
        asked = _dialogs(page, [False, True])
        page.locator("#blocker-hours").select_option("4")
        page.locator("#blocker-list button[data-role='autofix']").click()
        assert _until(page, lambda: asked)                       # the question was asked...
        page.wait_for_timeout(200)                               # ...and a wrong request would have left by now
        assert posts == [] and "4 hours" in asked[0]
        page.locator("#blocker-list button[data-role='autofix']").click()
        page.wait_for_function("() => document.getElementById('blocker-status').textContent.includes('Granted 2 permissions for 4 hours')")
        assert posts == [{"id": "blk_a", "hours": 4.0, "confirm": True}]
        browser.close()


def test_remediation_shows_what_it_will_change_and_asks_only_when_the_kind_changes(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _blockers_page(page)
        preview = {"answer": (200, {"ok": True, "slug": "my-game", "old_kind": "vite", "new_kind": "static", "changes_kind": True})}
        page.route("**/api/v1/harness/review/blockers/blk_a/remediation", lambda r: _fulfill(r, *preview["answer"]))
        posts = _record(page, "**/api/v1/harness/review/blockers/blk_a/remediate",
                        [(200, {"ok": True, "slug": "my-game", "old_kind": "vite", "new_kind": "static", "already_static": False}),
                         (200, {"ok": True, "slug": "my-game", "old_kind": "static", "new_kind": "static", "already_static": True})])
        _open_review(page, studio_server, "proposals", wait="#blocker-list .item")
        page.wait_for_selector("#blocker-list button[data-role='remediate']")
        asked = _dialogs(page, [False, True, True])
        button = "#blocker-list button[data-role='remediate']"
        page.locator(button).click()
        assert _until(page, lambda: asked)                       # the question was asked...
        page.wait_for_timeout(200)                               # ...and a wrong request would have left by now
        assert posts == [] and "my-game" in asked[0] and "vite" in asked[0] and "static" in asked[0]      # declined: no change
        page.locator(button).click()
        page.wait_for_function("() => document.getElementById('blocker-status').textContent.includes('my-game is now static (it was vite)')")
        assert posts == [{"confirm_kind_change": True}]
        preview["answer"] = (200, {"ok": True, "slug": "my-game", "old_kind": "static", "new_kind": "static", "changes_kind": False})
        page.locator(button).click()                                       # nothing would change: no question, and no confirm flag
        page.wait_for_function("() => document.getElementById('blocker-status').textContent.includes('my-game was already static')")
        assert posts[1] == {"confirm_kind_change": False} and len(asked) == 2
        browser.close()


def test_a_blocker_that_cannot_be_remediated_says_why(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _blockers_page(page)
        page.route("**/api/v1/harness/review/blockers/blk_a/remediation", lambda r: _fulfill(r, 404, {"ok": False, "error": "project_not_found", "slug": "my-game"}))
        posts = _record(page, "**/api/v1/harness/review/blockers/blk_a/remediate", [(200, {"ok": True})])
        _open_review(page, studio_server, "proposals", wait="#blocker-list .item")
        page.wait_for_selector("#blocker-list button[data-role='remediate']")
        page.locator("#blocker-list button[data-role='remediate']").click()
        page.wait_for_function("() => document.getElementById('blocker-status').textContent.includes('project no longer exists')")
        assert posts == []
        browser.close()


def test_auto_fix_all_and_grant_recommended_show_what_they_will_grant(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _blockers_page(page)
        page.route("**/api/v1/harness/review/blockers", lambda r: _fulfill(r, 200, {
            "ok": True, "open": 3, "auto_fixable": 2, "infra_remediable": 1, "recommended": ["modify_files", "web_search"], "default_hours": 8.0}))
        fix_all = _record(page, "**/api/v1/harness/review/blockers/auto-fix", [(200, {"ok": True, "fixed": 2, "skipped": 1, "hours": 8.0})])
        rec = _record(page, "**/api/v1/harness/review/blockers/grant-recommended", [(200, {"ok": True, "granted": ["modify_files", "web_search"], "count": 2, "hours": 8.0})])
        _open_review(page, studio_server, "proposals", wait="#blocker-list .item")
        asked = _dialogs(page, [True, True])
        page.locator("#blocker-autofix-all").click()
        page.wait_for_function("() => document.getElementById('blocker-status').textContent.includes('Fixed 2 blockers, skipped 1')")
        assert fix_all == [{"all": True, "hours": 8.0, "confirm": True}] and "3 open" in asked[0]
        page.locator("#blocker-grant-rec").click()
        page.wait_for_function("() => document.getElementById('blocker-status').textContent.includes('Granted 2 permissions for 8 hours')")
        assert rec == [{"hours": 8.0, "confirm": True}] and "modify_files, web_search" in asked[1] and "2 recommended" in asked[1]
        browser.close()


def test_proposal_details_open_and_close_inline_and_a_diff_is_never_markup(studio_server):
    from playwright.sync_api import sync_playwright

    rows = {"proposals": [{"id": "p1", "reason": "tidy", "status": "pending"}]}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.route("**/admin/daedalus/proposals/api", lambda r: _fulfill(r, 200, rows))
        page.route("**/api/v1/harness/review/proposals/p1", lambda r: _fulfill(r, 200, {
            "ok": True, "id": "p1", "status": "pending", "file_path": "daedalus/a.py", "reason": "tidy", "risk_level": 2,
            "risk_rationale": "small change", "diff": "--- a\n+++ b\n+<img src=x onerror=\"window.pwned=1\">\n+added line", "truncated": True}))
        _open_review(page, studio_server, "proposals")
        item = "#review-list .item:has(button[data-act='detail-proposal'])"
        page.locator(item + " button[data-act='detail-proposal']").click()
        page.wait_for_selector(item + " .detail")
        text = page.inner_text(item + " .detail")
        assert "daedalus/a.py" in text and "risk 2" in text and "+added line" in text and "The diff is cut here" in text
        assert "<img src=x" in text                                                   # shown as text...
        assert page.locator(item + " .detail img").count() == 0 and page.evaluate("() => window.pwned") is None     # ...never parsed
        assert page.locator(item + " button[data-act='detail-proposal']").inner_text() == "Hide details"
        page.locator(item + " button[data-act='detail-proposal']").click()
        assert page.locator(item + " .detail").count() == 0
        browser.close()


def test_bundle_details_list_each_file_with_its_change(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.route("**/admin/daedalus/bundles/api", lambda r: _fulfill(r, 200, {"bundles": [{"id": "b1", "title": "my bundle", "status": "ready"}], "count": 1}))
        page.route("**/api/v1/harness/review/bundles/b1", lambda r: _fulfill(r, 200, {
            "ok": True, "id": "b1", "title": "my bundle", "status": "ready", "files": [
                {"file_path": "a.txt", "action": "replace", "reason": "tweak", "bytes": 5, "diff": "-two\n+TWO", "truncated": False},
                {"file_path": "new.txt", "action": "create", "reason": "fresh", "bytes": 6, "diff": "+hello", "truncated": False}]}))
        _open_review(page, studio_server, "bundles")
        item = "#review-list .item:has(button[data-act='detail-bundle'])"
        page.locator(item + " button[data-act='detail-bundle']").click()
        page.wait_for_selector(item + " .detail")
        text = page.inner_text(item + " .detail")
        assert "# replace a.txt  (tweak)" in text and "+TWO" in text and "# create new.txt" in text and "+hello" in text
        browser.close()


def test_a_missing_detail_is_explained(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.route("**/admin/daedalus/proposals/api", lambda r: _fulfill(r, 200, {"proposals": [{"id": "p9", "reason": "gone", "status": "pending"}]}))
        page.route("**/api/v1/harness/review/proposals/p9", lambda r: _fulfill(r, 404, {"ok": False, "error": "not_found"}))
        _open_review(page, studio_server, "proposals")
        page.locator("#review-list button[data-act='detail-proposal']").click()
        page.wait_for_function("() => document.getElementById('review-status').textContent.includes('Not found')")
        assert page.locator("#review-list .detail").count() == 0
        browser.close()


def test_approve_all_pending_asks_with_the_count_and_reports_a_partial_result_honestly(studio_server):
    from playwright.sync_api import sync_playwright

    rows = {"proposals": [{"id": "p1", "reason": "one", "status": "pending"}, {"id": "p2", "reason": "two", "status": "pending"},
                          {"id": "p3", "reason": "three", "status": "approved"}]}
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.route("**/admin/daedalus/proposals/api", lambda r: _fulfill(r, 200, rows))
        posts = _record(page, "**/api/v1/harness/review/proposals/approve-all",
                        [(200, {"ok": False, "partial": True, "applied": 1, "failed": [{"id": "p2", "error": "path blocked"}]})])
        _open_review(page, studio_server, "proposals")
        box = ".item.bulk[data-id='bulk-approve-all']"
        asked = _dialogs(page, [False, True])
        page.locator(box + " button").click()
        assert _until(page, lambda: asked)                       # the question was asked...
        page.wait_for_timeout(200)                               # ...and a wrong request would have left by now
        assert posts == [] and "2 pending proposals" in asked[0]
        page.locator(box + " button").click()
        assert "Applied 1, 1 failed" in _bulk_note(page, "bulk-approve-all", "Applied 1")
        assert page.locator(box).get_attribute("data-state") == "error" and posts == [{"ids": ["p1", "p2"], "confirm": True}]   # what the question showed
        browser.close()


def test_approve_all_with_nothing_pending_does_not_ask_or_send(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.route("**/admin/daedalus/proposals/api", lambda r: _fulfill(r, 200, {"proposals": [{"id": "p3", "reason": "three", "status": "approved"}]}))
        posts = _record(page, "**/api/v1/harness/review/proposals/approve-all", [(200, {"ok": True, "applied": 0, "failed": []})])
        _open_review(page, studio_server, "proposals")
        asked = _dialogs(page, [True])
        page.locator(".item.bulk[data-id='bulk-approve-all'] button").click()
        assert "no pending proposals" in _bulk_note(page, "bulk-approve-all", "no pending").lower()
        assert posts == [] and asked == []
        browser.close()


def test_bulgarian_shows_the_new_review_actions_in_bulgarian_without_raw_keys(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _blockers_page(page)
        _open_review(page, studio_server, "permissions", lang="bg")
        page.wait_for_selector("#blocker-list button[data-role='autofix']")
        text = page.inner_text("#panel-review")
        low = text.lower()
        for expected in ("всички класове разрешения", "дай всички", "поднови изтеклите", "оправи всички", "дай препоръчаните", "оправи автоматично", "смени на static"):
            assert expected in low, expected
        assert not re.search(r"\b(studio|settings)\.[A-Za-z]", text), re.findall(r"\b(?:studio|settings)\.[A-Za-z.]+", text)
        assert "grant all" not in low and "auto-fix all" not in low
        browser.close()


@pytest.mark.parametrize("size", [(1280, 800), (1366, 768), (910, 640)])
def test_review_actions_fit_and_are_really_clickable(studio_server, size):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": size[0], "height": size[1]})
        _blockers_page(page)
        _dialogs(page, [False] * 20)                    # the bulk buttons ask first: click #dx-confirm-cancel, as the browser did
        _open_review(page, studio_server, "permissions")
        page.wait_for_selector("#blocker-list button[data-role='autofix']")
        page.wait_for_function("() => { const s = document.getElementById('review-status'); return s.hidden; }")
        over = page.evaluate("""() => { const p = document.getElementById('panel-review');
          return {sw: p.scrollWidth, cw: p.clientWidth, docw: document.documentElement.scrollWidth, vw: window.innerWidth}; }""")
        assert over["sw"] <= over["cw"] + 1 and over["docw"] <= over["vw"] + 1, over
        for selector in (".item.bulk[data-id='bulk-permissions'] button", "#blocker-autofix-all", "#blocker-grant-rec",
                         "#blocker-list button[data-role='autofix']", "#blocker-list button[data-role='remediate']"):
            page.locator(selector).first.evaluate("el => el.scrollIntoView({block: 'center'})")
            _hit(page, selector)
        browser.close()


def test_fix_all_that_fixes_nothing_shows_the_counts_not_a_vague_error(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _blockers_page(page)
        page.route("**/api/v1/harness/review/blockers", lambda r: _fulfill(r, 200, {"ok": True, "open": 2, "recommended": []}))
        _record(page, "**/api/v1/harness/review/blockers/auto-fix", [(200, {"ok": False, "error": "no_fix_available", "fixed": 0, "skipped": 2, "hours": 8.0})])
        _open_review(page, studio_server, "proposals", wait="#blocker-list .item")
        _dialogs(page, [True])
        page.locator("#blocker-autofix-all").click()
        page.wait_for_function("() => document.getElementById('blocker-status').textContent.includes('Fixed 0 blockers, skipped 2')")
        assert page.locator("#blocker-status").get_attribute("data-state") == "error"
        browser.close()


def test_a_running_blocker_action_locks_the_other_buttons_and_a_late_result_is_dropped_after_leaving(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        _blockers_page(page)
        held = {}
        page.route("**/api/v1/harness/review/blockers/auto-fix", lambda r: held.update(route=r))
        _open_review(page, studio_server, "proposals", wait="#blocker-list .item")
        page.wait_for_selector("#blocker-list button[data-role='autofix']")
        _dialogs(page, [True])
        page.locator("#blocker-list button[data-role='autofix']").click()
        assert _until(page, lambda: "route" in held)
        # while it runs: Resolve, the other actions and the panel buttons cannot be clicked (one request at a time)
        for selector in ("#blocker-list button[data-gen]", "#blocker-list button[data-role='remediate']", "#blocker-autofix-all",
                         "#blocker-grant-rec", "#blocker-refresh"):
            assert page.locator(selector).is_disabled(), selector
        page.locator("#workspace-nav button[data-panel=chat]").click()               # the owner leaves Review...
        _fulfill(held["route"], 200, {"ok": True, "id": "blk_a", "granted": ["modify_files"], "hours": 8.0})
        page.wait_for_timeout(500)
        assert "Granted" not in page.locator("#blocker-status").text_content()        # ...so the late answer writes nothing
        page.locator("#workspace-nav button[data-panel=review]").click()
        page.wait_for_selector("#blocker-list .item")
        # the reload that draws the list holds the lock for a moment; a lock that never lets go would fail this wait
        page.wait_for_function("() => !document.getElementById('blocker-autofix-all').disabled && !document.getElementById('blocker-refresh').disabled", timeout=10000)
        browser.close()
