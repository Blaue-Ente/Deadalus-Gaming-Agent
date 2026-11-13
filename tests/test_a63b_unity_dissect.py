"""A6.3b: Unity attach-template, Unity/proof waivers, saved dissection and labs in the Studio; secret-safe lab installs."""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

import pytest
from a0_support import a0_tmp  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "app/static/studio.html").read_text(encoding="utf-8")


# ------------------------------------------------------------------------------------------------ lab installs
@pytest.fixture
def lab(a0_tmp):  # noqa: F811
    from daedalus import play_lab

    return play_lab, play_lab.create_lab(title="t")["id"]


def _installed(play_lab, lab_id):
    return sorted(str(p.relative_to(play_lab.install_dir(lab_id))) for p in play_lab.install_dir(lab_id).rglob("*") if p.is_file())


def test_a_folder_install_never_copies_secret_files(lab, a0_tmp):  # noqa: F811
    play_lab, lab_id = lab
    src = a0_tmp / "src" / "game"
    (src / "js").mkdir(parents=True)
    (src / "index.html").write_text("<canvas></canvas>")
    (src / "js" / "main.js").write_text("1")
    (src / ".env").write_text("TOKEN=x")
    (src / "js" / "server.key").write_text("k")
    (src / "secrets.env").write_text("s")
    (src / ".env.d").mkdir()
    (src / ".env.d" / "a.txt").write_text("secret-folder")                 # a secret-named FOLDER at the top is left out too
    outside = a0_tmp / "src" / "private.json"
    outside.write_text("{}")
    (src / "linked.json").symlink_to(outside)                              # links could point anywhere: never followed
    (src / "js" / "deep").symlink_to(a0_tmp / "src")
    play_lab.install_into_lab(lab_id, src)
    assert _installed(play_lab, lab_id) == ["index.html", "js/main.js"]


def test_a_zip_install_never_extracts_secret_files(lab, a0_tmp):  # noqa: F811
    play_lab, lab_id = lab
    z = a0_tmp / "src" / "g.zip"
    z.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("index.html", "<canvas></canvas>")
        zf.writestr("conf/.env", "A=1")
        zf.writestr("id_rsa", "k")
    play_lab.install_into_lab(lab_id, z)
    assert _installed(play_lab, lab_id) == ["index.html"]


def test_the_data_folder_itself_and_a_single_secret_file_are_refused(lab, a0_tmp):  # noqa: F811
    play_lab, lab_id = lab
    from daedalus.paths import data_root

    data_root().mkdir(parents=True, exist_ok=True)
    (data_root() / "secrets.env").write_text("x")
    (data_root() / "kv").mkdir(exist_ok=True)
    (data_root() / "kv" / "store.json").write_text("{}")
    (data_root() / "owner.token").write_text("t")
    with pytest.raises(ValueError, match="Daedalus's own data"):
        play_lab.install_into_lab(lab_id, data_root())
    with pytest.raises(ValueError, match="secret file"):
        play_lab.install_into_lab(lab_id, data_root() / "secrets.env")
    for inside in (data_root() / "kv", data_root() / "owner.token"):      # ordinary names inside the data folder
        with pytest.raises(ValueError, match="Daedalus's own data"):
            play_lab.install_into_lab(lab_id, inside)
    assert _installed(play_lab, lab_id) == []


def test_a_project_folder_can_still_be_installed(lab, a0_tmp):  # noqa: F811
    import asyncio

    from daedalus import playground as pg

    play_lab, lab_id = lab
    slug = asyncio.run(pg.create_project("Lab Me", kind="static"))["slug"]
    pg.write_file(slug, "index.html", "<canvas></canvas>")
    play_lab.install_into_lab(lab_id, pg.project_dir(slug))
    assert "index.html" in _installed(play_lab, lab_id)


def test_a_lab_marked_running_before_a_restart_is_listed_as_not_served(lab):
    play_lab, lab_id = lab
    meta = play_lab.load_lab(lab_id)
    meta["vm"] = {**meta["vm"], "status": "running", "backend": "local-http", "port": 4321, "url": "http://127.0.0.1:4321/"}
    play_lab.save_meta(lab_id, meta)
    row = next(r for r in play_lab.list_labs() if r["id"] == lab_id)
    assert row["vm"]["status"] == "stopped" and row["vm"]["url"] == ""


# ------------------------------------------------------------------------------------------------ catalogs and workspace
def _options(select_id):
    block = HTML[HTML.index(f'id="{select_id}"'):]
    block = block[:block.index("</select>")]
    return re.findall(r'<option value="([^"]+)"', block)


def test_the_ship_menus_lists_are_exactly_the_servers_templates_and_presets():
    from daedalus.playground_integration_catalog import list_unity_templates
    from daedalus.playground_studio_mode import list_presets

    assert sorted(_options("ship-template")) == sorted(t["id"] for t in list_unity_templates())
    assert sorted(_options("ship-preset")) == sorted(p["id"] for p in list_presets())
    block = HTML[HTML.index('id="ship-template"'):]
    block = block[:block.index("</select>")]
    marked = sorted(re.findall(r'<option value="([^"]+)" data-clone="1"', block))
    assert marked == sorted(t["id"] for t in list_unity_templates() if t.get("repo"))     # Attach only where a repo exists


def test_the_workspace_says_whether_a_proof_waiver_is_possible(a0_tmp, monkeypatch):  # noqa: F811
    import asyncio

    from daedalus import game_studio
    from daedalus import playground as pg

    slug = asyncio.run(pg.create_project("Wv", kind="static"))["slug"]
    pg.write_file(slug, "design/game-studio.json", json.dumps({"build_mode": "guided", "studio_preset": "custom"}))
    ctx = asyncio.run(game_studio.get_workspace_context(slug))
    assert ctx["can_proof_waiver"] is False and ctx["can_unity_waiver"] is False
    monkeypatch.setattr(game_studio, "can_advance_with_proof_waiver", lambda cfg, plan: True)
    assert asyncio.run(game_studio.get_workspace_context(slug))["can_proof_waiver"] is True
    from fastapi.testclient import TestClient

    from app.main import app

    served = TestClient(app).get(f"/admin/daedalus/game-studio/api/projects/{slug}").json()   # the route the Studio reads
    assert served["can_proof_waiver"] is True


# ------------------------------------------------------------------------------------------------ UI (real clicks)
from test_a61c_maintenance import _dialogs  # noqa: E402
from test_a62a_review import _record, _until  # noqa: E402
from test_a62b_planner import _status_has, _uncovered  # noqa: E402
from test_a63a_build_controls import CTL, G, _browser, _build_page, _game  # noqa: E402
from test_studio_phase2_workspace import _fulfill, studio_server  # noqa: E402,F401  (fixture and helpers)

WS = {"project": {"slug": G}, "can_guided": True, "can_autopilot": True, "can_advance": False, "milestones": []}


def _workspace(page, ws):
    page.route(re.compile(rf".*/game-studio/api/projects/{G}$"),
               lambda r: _fulfill(r, 200, ws) if r.request.method == "GET" else r.fallback())


def _open(page, server, lang="en", ws=None):
    assert _game(server) == G
    _build_page(page, ctl=CTL)
    _workspace(page, ws or WS)                                   # registered last: wins over _build_page's workspace stub
    page.add_init_script(f"try {{ localStorage.setItem('dx.lang', '{lang}'); }} catch (e) {{}}")
    page.goto(server["url"] + f"/?panel=build&subtab=run&slug={G}", wait_until="domcontentloaded")
    page.wait_for_selector("#build-controls:not([hidden]) button[data-ctl]")


def _tab(page, tab):
    page.locator(f'#build-tabs button[data-tab="{tab}"]').click()


def test_waivers_are_offered_only_where_the_studio_allows_one_and_ask_first(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        advances = _record(page, f"**/game-studio/api/projects/{G}/milestones/advance",
                           [(200, {"ok": True, "previous": "M1", "current_milestone": "M2"})])
        _open(page, studio_server, ws={**WS, "can_unity_waiver": True, "can_proof_waiver": True})
        page.wait_for_selector("#build-waive-unity:not([hidden])")
        assert page.locator("#build-waive-proof").is_hidden()                       # one waiver at a time: Unity wins
        asked = _dialogs(page, [False, True])
        page.locator("#build-waive-unity").click()
        assert _until(page, lambda: asked)
        page.wait_for_timeout(150)
        assert advances == [] and "WITHOUT that proof" in asked[0]
        page.locator("#build-waive-unity").click()
        _status_has(page, "build-status", "Advanced M1 → M2 with a recorded waiver (not a verified pass).")
        assert advances == [{"unity_waiver": True}]
        browser.close()


def test_a_proof_waiver_sends_its_own_flag_and_nothing_shows_when_advance_is_possible(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        advances = _record(page, f"**/game-studio/api/projects/{G}/milestones/advance",
                           [(200, {"ok": True, "previous": "M0", "current_milestone": "M1"})])
        _open(page, studio_server, ws={**WS, "can_proof_waiver": True})
        page.wait_for_selector("#build-waive-proof:not([hidden])")
        _dialogs(page, [True])
        page.locator("#build-waive-proof").click()
        _status_has(page, "build-status", "with a recorded waiver")
        assert advances == [{"proof_waiver": True}]
        browser.close()
    with sync_playwright() as pw:
        browser, page = _browser(pw)
        _open(page, studio_server, ws={**WS, "can_advance": True, "can_unity_waiver": True, "can_proof_waiver": True})
        page.wait_for_function("() => !document.getElementById('build-advance').disabled")
        assert page.locator("#build-waive-unity").is_hidden() and page.locator("#build-waive-proof").is_hidden()
        browser.close()


def test_attach_template_shows_only_for_cloneable_templates_asks_and_reports(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        attaches = _record(page, f"**/playground/api/projects/{G}/unity/attach-template",
                           [(200, {"ok": True, "cloned": "Assets/UGF", "integrations": []}),
                            (200, {"ok": True, "cloned": "Assets/UGF", "status": "already_present"})])
        _open(page, studio_server)
        _tab(page, "export")
        assert page.locator("#ship-unity-attach").is_hidden()                       # daedalus_minimal has no repository
        page.locator("#ship-template").select_option("unity_game_framework")
        page.wait_for_selector("#ship-unity-attach:not([hidden])")
        asked = _dialogs(page, [False, True, True])
        page.locator("#ship-unity-attach").evaluate("el => el.scrollIntoView({block: 'center'})")
        page.locator("#ship-unity-attach").click()
        assert _until(page, lambda: asked)
        page.wait_for_timeout(150)
        assert attaches == [] and "network download" in asked[0]
        page.locator("#ship-unity-attach").click()
        _status_has(page, "ship-status", "Assets/UGF")
        assert attaches == [{"template_id": "unity_game_framework"}]
        page.wait_for_function("() => !document.getElementById('ship-unity-attach').disabled")
        page.locator("#ship-unity-attach").click()
        _status_has(page, "ship-status", "already in the project")
        browser.close()


def _world(page):
    page.locator("[data-pane='world']").first.click()


def test_the_world_rail_shows_the_saved_dissection_and_the_labs(studio_server):
    from playwright.sync_api import sync_playwright

    labs = {"ok": True, "labs": [{"id": "lab-1", "title": "Flappy", "source_kind": "url", "playable": "index.html",
                                  "vm": {"status": "running"}},
                                 {"id": "lab-2", "title": "Empty", "source_kind": "", "playable": "", "vm": {"status": "stopped"}}]}
    with sync_playwright() as pw:
        browser, page = _browser(pw)
        page.route(re.compile(r".*/api/v1/daedalus/labs$"), lambda r: _fulfill(r, 200, labs) if r.request.method == "GET" else r.fallback())
        page.route(f"**/api/v1/playground/projects/{G}/dissection", lambda r: _fulfill(r, 200, {"ok": True, "dissection": {
            "title": "Flappy Bird", "source_kind": "name", "stack": {"engine": "canvas"}, "summary": "Tap to flap <b>x</b>"}}))
        _open(page, studio_server)
        _world(page)
        page.wait_for_selector("#dissectsaved [data-dissection]")
        saved = page.inner_text("#dissectsaved")
        assert "Flappy Bird" in saved and "name · canvas" in saved and "Tap to flap <b>x</b>" in saved      # text, not markup
        page.wait_for_selector("#labsbox [data-lab='lab-2']")
        assert "playable: index.html" in page.inner_text("#labsbox [data-lab='lab-1']")
        assert "served on this machine" in page.inner_text("#labsbox [data-lab='lab-1']")
        assert "nothing playable found" in page.inner_text("#labsbox [data-lab='lab-2']")
        browser.close()


def test_no_saved_dissection_and_unreadable_labs_are_said_so(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        page.route(re.compile(r".*/api/v1/daedalus/labs$"), lambda r: _fulfill(r, 500, {"detail": "labs down"}))
        page.route(f"**/api/v1/playground/projects/{G}/dissection", lambda r: _fulfill(r, 200, {"ok": True, "dissection": None}))
        _open(page, studio_server)
        _world(page)
        page.wait_for_function("() => document.getElementById('dissectsaved').textContent.includes('no saved dissection')")
        page.wait_for_function("() => document.getElementById('labsbox').textContent.includes('labs down')")
        assert "The labs could not be read" in page.inner_text("#labsbox")
        browser.close()


def test_bulgarian_shows_the_new_controls_without_raw_keys(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw, (910, 640))
        _open(page, studio_server, lang="bg", ws={**WS, "can_unity_waiver": True})
        page.wait_for_selector("#build-waive-unity:not([hidden])")
        assert page.inner_text("#build-waive-unity") == "Напред с изключение за Unity"
        _tab(page, "export")
        page.locator("#ship-template").select_option("daedalus_ugf")
        page.wait_for_selector("#ship-unity-attach:not([hidden])")
        assert page.inner_text("#ship-unity-attach") == "Прикачи шаблон"
        text = page.inner_text("body")
        rail = page.text_content("#pane-world") or ""                               # the World pane is not on screen yet
        assert "Запазена дисекция" in rail and "Лаборатории" in rail
        assert not re.search(r"\bstudio\.[A-Za-z]", text)
        browser.close()


@pytest.mark.parametrize("size", [(1280, 800), (1366, 768), (910, 640)])
def test_the_new_buttons_fit_and_are_really_clickable(studio_server, size):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw, size)
        _open(page, studio_server, ws={**WS, "can_unity_waiver": True})
        _tab(page, "export")
        page.locator("#ship-template").select_option("unity_game_framework")
        for tab, selector in (("run", "#build-waive-unity"), ("export", "#ship-unity-attach")):
            _tab(page, tab)
            page.wait_for_selector(selector + ":not([hidden])")
            page.locator(selector).evaluate("el => el.scrollIntoView({block: 'center'})")
            _uncovered(page, selector)
        over = page.evaluate("() => ({docw: document.documentElement.scrollWidth, vw: window.innerWidth})")
        assert over["docw"] <= over["vw"] + 1, over
        browser.close()


def test_waivers_stay_disabled_while_full_auto_owns_the_project(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        live = {"ok": True, "run": {"id": "r1", "state": "running", "milestone": "M1", "reason": "", "events": []}}
        _build_page(page, ctl=[{**CTL, "busy": "full_auto"}], fa=[live])
        _workspace(page, {**WS, "can_unity_waiver": True})
        page.goto(studio_server["url"] + f"/?panel=build&subtab=run&slug={G}", wait_until="domcontentloaded")
        page.wait_for_selector("#build-waive-unity:not([hidden])")
        page.wait_for_function("() => document.getElementById('build-waive-unity').disabled")
        page.locator("#build-gates").evaluate("el => el.disabled = false")
        page.route(f"**/game-studio/api/projects/{G}/gates", lambda r: _fulfill(r, 200, {"ok": True}))
        page.locator("#build-gates").click()                                       # a repaint of the workspace...
        page.wait_for_timeout(400)
        assert page.locator("#build-waive-unity").is_disabled()                      # ...keeps them off
        browser.close()


def test_the_lab_list_is_read_only_when_the_world_pane_is_opened(studio_server):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser, page = _browser(pw)
        reads = []
        page.route(re.compile(r".*/api/v1/daedalus/labs$"),
                   lambda r: (reads.append(1), _fulfill(r, 200, {"ok": True, "labs": []})) if r.request.method == "GET" else r.fallback())
        _open(page, studio_server)
        page.wait_for_timeout(300)
        assert reads == []                                                          # it probes the host: not on every page load
        _world(page)
        page.wait_for_function("() => document.getElementById('labsbox').textContent.includes('No labs')")
        assert len(reads) == 1
        browser.close()
