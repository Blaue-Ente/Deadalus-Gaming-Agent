# Game Studio Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** On `GET /`, wrapped header controls receive ordinary pointer clicks, Settings stays on screen and opens the existing connectors page, and Mentor, budget, and assist do not paint a fake value when their GET fails.

**Architecture:** Stay on `app/static/studio.html`, `studio.css`, and `studio.js`. Grow the header grid row, put Settings in that header as a link to `/admin/daedalus/connectors`, and replace the three load `catch` blocks that invent a status. Effort preview calls the existing POST. One new Playwright module proves hits, errors, and persistence. No new package.

**Tech Stack:** Existing FastAPI app, `studio.css` tokens, `app/static/i18n.js`, pytest, Playwright Chromium already declared in `pyproject.toml` (`playwright` extra).

**Spec:** `docs/superpowers/specs/2026-11-07-unified-game-studio-design.md` at commit `859d6cf0e4c5aa42e410a310bb21be82396e7b2d` (MR115). This branch does not contain that commit. Do not merge MR114 and do not copy `ui/static/classic_chat.js`.

## Global Constraints

- Branch from `origin/main` (`7f1e8db`). MR114 is not an ancestor.
- Allowed product edits: `app/static/studio.css`, `app/static/studio.html`, `app/static/studio.js`, `app/static/i18n.js` (only `studio.settings` and `studio.effortPreviewErr`, English and Bulgarian), and one test module `tests/test_studio_phase1_header.py`.
- This plan file is documentation, not a product edit.
- Header grid row: `minmax(var(--header), auto)`. Header `z-index` above the columns.
- Settings `href` is exactly `/admin/daedalus/connectors`. Phase 1 does not build an in-shell settings panel and does not redirect old routes.
- English is the default. Bulgarian only after the BG control. Do not translate project names, chat, or files.
- Mentor, run budget, and writer assist stay in `daedalus.kv`. Language stays `localStorage` key `dx.lang`. Effort stays `localStorage` key `dx.effort`. No migration.
- Do not edit `app/admin_compat.py`, Hub, Playground, Game Studio templates, or baseline artifacts unless `tests/test_baseline_pack.py` fails only because the new test module changed `tests.modules` / `tests.functions`. Then, and only then, run `python3 scripts/record_baseline.py --refresh-inventory`. Do not delete tests to make the pack pass.
- Playwright actions use `locator.click()` with no `force=True` and no `element.click()` inside `page.evaluate`.
- CSS viewports: 1280×800, 1366×768, 910×640. `deviceScaleFactor` is not the scale test. Linux Chromium is not Windows WebView2.
- No new dependency and no paid model call.

## File map

| File | Responsibility |
|---|---|
| `app/static/studio.css` | Header row grows with wrapped chips. Header stacks above the columns. Narrow gap so 910×640 still shows Settings. Error line style. |
| `app/static/studio.html` | Settings link. `#header-status`. Mentor, assist, and budget buttons start with `aria-pressed="false"` so a failed load cannot look like a successful default. |
| `app/static/studio.js` | `reportHeaderError`, `clearHeaderError`, honest `loadMentor` / `loadAssist` / `loadBudget`, POST `previewEffort`. |
| `app/static/i18n.js` | `studio.settings`, `studio.effortPreviewErr` in `en` and `bg`. |
| `tests/test_studio_phase1_header.py` | Source contract, Playwright hit tests, 500 load, persistence. |

**Interfaces produced**

- DOM: `#settings-link` anchor, `#header-status` (`role="status"`, `hidden` when empty), `#effortnote`.
- JS functions inside the studio IIFE (not exported): `reportHeaderError(key: string, label: string, err: unknown): void`, `clearHeaderError(key: string): void`.
- `headerErrors` is a plain object keyed `mentor`, `assist`, `budget`.

---

### Task 1: Header hit target and Settings link

**Files:**
- Modify: `app/static/studio.css` (`.shell` grid row, `header.app`)
- Modify: `app/static/studio.html` (header, after the language group)
- Modify: `app/static/i18n.js` (two keys, both locales)
- Test: `tests/test_studio_phase1_header.py`

**Interfaces:**
- Consumes: existing `.chip`, `.dx-lang`, `/admin/daedalus/connectors`
- Produces: `#settings-link`, grown header row, `studio.settings`

- [x] **Step 1: Write the failing source contract and the Playwright hit test**

Create `tests/test_studio_phase1_header.py` with the server fixture and `test_header_css_contract`, `test_settings_string_pair`, and `test_header_hits_at_laptop_viewports` as specified in the test body at the bottom of this plan. Do not add the 500 or persistence tests yet.

- [x] **Step 2: Run the source tests and confirm they fail**

Run: `python3 -m pytest tests/test_studio_phase1_header.py::test_header_css_contract tests/test_studio_phase1_header.py::test_settings_string_pair -q`

Expected: FAIL because `minmax(var(--header), auto)` and `studio.settings` are absent.

- [x] **Step 3: Implement the CSS, the link, and the strings**

In `app/static/studio.css`, replace the `.shell` grid row and add header stacking:

```css
.shell {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr) 360px;
  grid-template-rows: minmax(var(--header), auto) 1fr;
  height: 100%;
}
header.app {
  grid-column: 1 / -1;
  position: relative;
  z-index: 2;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 1rem;
  padding: 0 1.1rem;
  border-bottom: 1px solid var(--line);
  background: var(--bg-raise);
}
```

Add after `.dx-lang button[aria-pressed="true"]`:

```css
a#settings-link {
  flex: 0 0 auto;
  color: var(--ink);
  text-decoration: none;
  position: relative;
  z-index: 1;
}
@media (max-width: 960px) {
  header.app { gap: 0.35rem 0.45rem; padding: 0.3rem 0.55rem; }
  header.app .brand h1 { font-size: 1.15rem; }
}
```

In `app/static/studio.html`, after the language `div` and before `<div class="pills" id="pills">`:

```html
      <a id="settings-link" class="chip" href="/admin/daedalus/connectors" data-i18n="studio.settings">Settings</a>
```

In `app/static/i18n.js`, next to `studio.budgetAria` in the English map:

```js
      "studio.settings": "Settings",
```

Next to the Bulgarian `studio.budgetAria`:

```js
      "studio.budgetAria": "Бюджет на хода",
      "studio.settings": "Настройки",
```

Add only the settings line. Do not add `studio.effortPreviewErr` in this task.

- [x] **Step 4: Run source tests, then the browser hit test**

Run: `python3 -m pytest tests/test_studio_phase1_header.py::test_header_css_contract tests/test_studio_phase1_header.py::test_settings_string_pair tests/test_studio_phase1_header.py::test_header_hits_at_laptop_viewports -q`

Expected: PASS. If Playwright or Chromium is missing, install the existing extra (`pip install -e ".[playwright]"` and `python3 -m playwright install chromium`). Do not add a package to `pyproject.toml`.

- [x] **Step 5: Commit**

```bash
git add app/static/studio.css app/static/studio.html app/static/i18n.js tests/test_studio_phase1_header.py
git commit -m "fix(studio): keep wrapped header controls clickable and show Settings"
```

---

### Task 2: Honest Mentor, budget, and assist loads

**Files:**
- Modify: `app/static/studio.html` (initial `aria-pressed`, `#header-status`)
- Modify: `app/static/studio.css` (`.header-status`)
- Modify: `app/static/studio.js` (`loadMentor`, `loadAssist`, `loadBudget`, `paintMentor`, `paintAssist`, `paintBudget`)
- Test: `tests/test_studio_phase1_header.py`

**Interfaces:**
- Consumes: `api`, `errText`, `t`, `paintMentor`, `paintAssist`, `paintBudget`
- Produces: `reportHeaderError(key, label, err)`, `clearHeaderError(key)`, `#header-status`

- [x] **Step 1: Add the failing tests**

Add `test_load_catch_does_not_invent_status` and `test_mentor_get_500_shows_error` to the same module. The source test fails while `paintMentor({ enabled: true, env_locked: false })` remains. The browser test fails until `#header-status` shows `mentor down` and the Mentor ON button stays `aria-pressed="false"`.

- [x] **Step 2: Run the new tests and confirm failure**

Run: `python3 -m pytest tests/test_studio_phase1_header.py::test_load_catch_does_not_invent_status tests/test_studio_phase1_header.py::test_mentor_get_500_shows_error -q`

Expected: FAIL.

- [x] **Step 3: Implement honest loads**

In `studio.html`, set every Mentor, assist, and budget button to `aria-pressed="false"`. After `#settings-link`:

```html
      <p id="header-status" class="header-status" role="status" hidden></p>
```

In `studio.css`:

```css
.header-status {
  flex: 1 0 100%;
  margin: 0;
  color: var(--err);
  font-size: 0.75rem;
  white-space: pre-wrap;
}
```

In `studio.js`, before `paintMentor`:

```javascript
  const headerErrors = {};
  function renderHeaderErrors() {
    const el = $("header-status");
    if (!el) return;
    const lines = Object.keys(headerErrors).map((key) => headerErrors[key]);
    el.hidden = lines.length === 0;
    text(el, lines.join("\n"));
  }
  function reportHeaderError(key, label, err) {
    headerErrors[key] = label + ": " + errText(err);
    renderHeaderErrors();
  }
  function clearHeaderError(key) {
    delete headerErrors[key];
    renderHeaderErrors();
  }
```

At the start of `paintMentor`, `paintAssist`, and `paintBudget`, call `clearHeaderError("mentor")`, `clearHeaderError("assist")`, and `clearHeaderError("budget")` respectively.

Replace the three load catches:

```javascript
  async function loadMentor() {
    try {
      const data = await api("/api/v1/daedalus/mentor", { timeoutMs: 8000 });
      paintMentor(data);
    } catch (e) {
      reportHeaderError("mentor", t("studio.mentorAria"), e);
    }
  }
```

```javascript
  async function loadAssist() {
    try {
      const data = await api("/api/v1/daedalus/writer-assist", { timeoutMs: 8000 });
      paintAssist(data);
    } catch (e) {
      reportHeaderError("assist", t("studio.assistAria"), e);
    }
  }
```

```javascript
  async function loadBudget() {
    try {
      const data = await api("/api/v1/daedalus/run-budget", { timeoutMs: 8000 });
      paintBudget(data);
    } catch (e) {
      reportHeaderError("budget", t("studio.budgetAria"), e);
    }
  }
```

Delete the old catches that call `paintMentor({ enabled: true, env_locked: false })`, `paintAssist({ mode: "auto", env_locked: false })`, and `paintBudget({ usd: 0, env_locked: false })`.

- [x] **Step 4: Re-run Task 2 tests and the Task 1 hit test**

Run: `python3 -m pytest tests/test_studio_phase1_header.py -q -k "not persistence and not effort"`

Expected: PASS for the tests that exist so far.

- [x] **Step 5: Commit**

```bash
git add app/static/studio.css app/static/studio.html app/static/studio.js tests/test_studio_phase1_header.py
git commit -m "fix(studio): show Mentor, budget, and assist load errors"
```

---

### Task 3: Effort POST and persistence

**Files:**
- Modify: `app/static/studio.js` (`previewEffort`)
- Modify: `app/static/i18n.js` (`studio.effortPreviewErr` in `en` and `bg`)
- Test: `tests/test_studio_phase1_header.py`

**Interfaces:**
- Consumes: `POST /api/v1/daedalus/effort` body `{text: string, effort: string}` returning `{tier: string, ...}` from `daedalus.task_effort.classify`
- Produces: `#effortnote` text `t("studio.effortPreviewErr") + ": " + errText(e)` on failure

- [x] **Step 1: Add failing tests**

Add `test_effort_preview_posts_and_shows_error` and `test_settings_persist_across_process_and_browser_restart`. The persistence test's second browser context is the new-profile check.

- [x] **Step 2: Run them and confirm failure**

Run: `python3 -m pytest tests/test_studio_phase1_header.py::test_effort_preview_posts_and_shows_error tests/test_studio_phase1_header.py::test_settings_persist_across_process_and_browser_restart -q`

Expected: FAIL. The live page still GETs `/api/v1/daedalus/effort`.

- [x] **Step 3: Implement POST preview and the error string**

Replace `previewEffort` with:

```javascript
  async function previewEffort() {
    const q = $("prompt").value.trim();
    try {
      const data = await api("/api/v1/daedalus/effort", {
        method: "POST",
        body: JSON.stringify({
          text: q.slice(0, 8000),
          effort: state.effort || "auto",
        }),
        timeoutMs: 8000,
      });
      const tier = data.tier || "whisper";
      text(
        $("effortnote"),
        t("studio.effort." + (state.effort || "auto")) + " — " + t("studio.effortNote." + tier)
      );
    } catch (e) {
      text($("effortnote"), t("studio.effortPreviewErr") + ": " + errText(e));
    }
  }
```

English, beside `studio.settings`:

```js
      "studio.effortPreviewErr": "Effort preview failed",
```

Bulgarian, beside `studio.settings`:

```js
      "studio.effortPreviewErr": "Прегледът на усилието не мина",
```

`setEffort` still writes `dx.effort` and updates `aria-pressed` before `previewEffort`. Do not move effort into kv.

- [x] **Step 4: Run the whole module**

Run: `python3 -m pytest tests/test_studio_phase1_header.py -q`

Expected: PASS. Screenshots written by the hit test under `/opt/cursor/artifacts/phase1-header-1280.png`, `phase1-header-1366.png`, and `phase1-header-910.png`.

- [x] **Step 5: Commit**

```bash
git add app/static/studio.js app/static/i18n.js tests/test_studio_phase1_header.py
git commit -m "fix(studio): post effort preview and keep header settings"
```

---

### Task 4: Regression and baseline

**Files:**
- Modify only if the pack check demands it: `artifacts/baseline/architecture-inventory.json`, `artifacts/baseline/dependency-snapshot.txt` via the sanctioned script.

- [x] **Step 1: Focused tests**

Run: `python3 -m pytest tests/test_studio_phase1_header.py tests/test_studio_hardening.py tests/test_task_effort.py tests/test_baseline_pack.py -q`

Expected: PASS. If `test_baseline_pack_check_passes` fails only on `tests.modules` or `tests.functions`, run Step 2. Any other failure is a product bug. Do not edit the check.

- [x] **Step 2: Refresh inventory only when Step 1 names the test count**

Run: `python3 scripts/record_baseline.py --refresh-inventory`

Then re-run: `python3 -m pytest tests/test_baseline_pack.py -q`

Expected: PASS. Commit the two artifact files with message `chore(baseline): refresh inventory for phase 1 header test`.

- [x] **Step 3: Full suite**

Run: `python3 -m pytest -q`

Expected: PASS. Windows WebView2 is not part of this command.

---

## Test module

Put this in `tests/test_studio_phase1_header.py`. Later tasks append tests; they do not replace the fixture.

```python
"""Phase 1 Studio header: real clicks, visible Settings, honest load errors."""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "app" / "static" / "studio.css").read_text(encoding="utf-8")
HTML = (ROOT / "app" / "static" / "studio.html").read_text(encoding="utf-8")
JS = (ROOT / "app" / "static" / "studio.js").read_text(encoding="utf-8")
I18N = (ROOT / "app" / "static" / "i18n.js").read_text(encoding="utf-8")

VIEWPORTS = ((1280, 800), (1366, 768), (910, 640))
SHOTS = {
    (1280, 800): "/opt/cursor/artifacts/phase1-header-1280.png",
    (1366, 768): "/opt/cursor/artifacts/phase1-header-1366.png",
    (910, 640): "/opt/cursor/artifacts/phase1-header-910.png",
}


def test_header_css_contract():
    assert "grid-template-rows: minmax(var(--header), auto) 1fr;" in CSS
    assert "grid-template-rows: var(--header) 1fr;" not in CSS
    header = CSS.split("header.app {", 1)[1].split("}", 1)[0]
    assert "z-index: 2;" in header
    assert 'id="settings-link"' in HTML
    assert 'href="/admin/daedalus/connectors"' in HTML


def test_settings_string_pair():
    assert '"studio.settings": "Settings"' in I18N
    assert '"studio.settings": "Настройки"' in I18N


def test_load_catch_does_not_invent_status():
    assert "paintMentor({ enabled: true, env_locked: false })" not in JS
    assert "paintAssist({ mode: \"auto\", env_locked: false })" not in JS
    assert "paintBudget({ usd: 0, env_locked: false })" not in JS
    assert "function reportHeaderError" in JS
    assert 'id="header-status"' in HTML


def test_effort_preview_posts():
    assert '"studio.effortPreviewErr": "Effort preview failed"' in I18N
    assert '"studio.effortPreviewErr": "Прегледът на усилието не мина"' in I18N
    start = JS.index("async function previewEffort")
    body = JS[start:start + 700]
    assert 'method: "POST"' in body
    assert "/api/v1/daedalus/effort?" not in JS


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture(scope="module")
def studio_server(tmp_path_factory):
    pytest.importorskip("playwright")
    data = tmp_path_factory.mktemp("phase1-data")
    port = _free_port()
    env = dict(**{k: v for k, v in __import__("os").environ.items()})
    env.update({
        "DAEDALUS_STANDALONE": "1",
        "DAEDALUS_DATA": str(data),
        "DAEDALUS_PLAYGROUND": str(data / "playground"),
    })
    env.pop("DAEDALUS_MENTOR", None)
    env.pop("DAEDALUS_RUN_BUDGET", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    deadline = time.time() + 30
    url = f"http://127.0.0.1:{port}"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "/health", timeout=1) as res:
                if res.status == 200:
                    break
        except Exception:
            time.sleep(0.2)
    else:
        out = proc.stdout.read().decode("utf-8", "replace") if proc.stdout else ""
        proc.kill()
        pytest.fail("studio server did not start\n" + out[-2000:])
    yield {"url": url, "data": data, "proc": proc, "env": env, "port": port}
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def _hit(page, selector: str) -> None:
    loc = page.locator(selector).first
    box = loc.bounding_box()
    assert box is not None and box["width"] > 8 and box["height"] > 8
    point = loc.evaluate(
        """(el) => {
          const r = el.getBoundingClientRect();
          const x = r.left + r.width / 2;
          const y = r.top + r.height / 2;
          const top = document.elementFromPoint(x, y);
          return {x, y, ok: top === el || (top && el.contains(top)), tag: top && top.tagName};
        }"""
    )
    assert point["ok"], selector + " covered by " + str(point)
    viewport = page.viewport_size
    assert box["y"] + box["height"] <= viewport["height"]
    assert box["x"] + box["width"] <= viewport["width"]
    loc.click()


def test_header_hits_at_laptop_viewports(studio_server):
    from playwright.sync_api import sync_playwright
    url = studio_server["url"] + "/"
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for width, height in VIEWPORTS:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_selector("#settings-link")
            assert page.evaluate("() => window.scrollY") == 0
            for sel in (
                ".mentor button[data-mentor='on']",
                ".effort button[data-effort='craft']",
                ".budget button[data-budget='5']",
                ".dx-lang button[data-lang='en']",
                ".dx-lang button[data-lang='bg']",
                "#settings-link",
            ):
                if sel == "#settings-link":
                    box = page.locator(sel).bounding_box()
                    assert box is not None
                    assert box["y"] + box["height"] <= height
                    assert page.evaluate("() => window.scrollY") == 0
                    continue
                _hit(page, sel)
            page.screenshot(path=SHOTS[(width, height)])
            page.close()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.goto(url, wait_until="domcontentloaded")
        page.locator("#settings-link").click()
        page.wait_for_url("**/admin/daedalus/connectors")
        browser.close()


def test_mentor_get_500_shows_error(studio_server):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        def handle(route):
            if route.request.method == "GET":
                route.fulfill(status=500, content_type="application/json", body='{"detail":"mentor down"}')
            else:
                route.fallback()
        page.route("**/api/v1/daedalus/mentor", handle)
        page.goto(studio_server["url"] + "/", wait_until="domcontentloaded")
        status = page.locator("#header-status")
        status.wait_for(state="visible")
        text = status.inner_text()
        assert "500" in text or "mentor down" in text
        assert page.locator(".mentor button[data-mentor='on']").get_attribute("aria-pressed") == "false"
        browser.close()


def test_effort_preview_posts_and_shows_error(studio_server):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        seen = {}
        def handle(route):
            seen["method"] = route.request.method
            seen["body"] = route.request.post_data or ""
            route.fulfill(status=500, content_type="application/json", body='{"detail":"effort down"}')
        page.route("**/api/v1/daedalus/effort", handle)
        page.goto(studio_server["url"] + "/", wait_until="domcontentloaded")
        page.locator(".effort button[data-effort='craft']").click()
        page.wait_for_function("() => document.getElementById('effortnote').textContent.includes('effort down')")
        assert seen.get("method") == "POST"
        assert "craft" in seen.get("body", "")
        assert page.locator(".effort button[data-effort='craft']").get_attribute("aria-pressed") == "true"
        browser.close()


def _restart(server):
    server["proc"].terminate()
    try:
        server["proc"].wait(timeout=10)
    except subprocess.TimeoutExpired:
        server["proc"].kill()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(server["port"])],
        cwd=str(ROOT),
        env=server["env"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    server["proc"] = proc
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(server["url"] + "/health", timeout=1) as res:
                if res.status == 200:
                    return
        except Exception:
            time.sleep(0.2)
    pytest.fail("restart failed")


def test_settings_persist_across_process_and_browser_restart(studio_server):
    from playwright.sync_api import sync_playwright
    url = studio_server["url"] + "/"
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded")
        page.locator(".mentor button[data-mentor='off']").click()
        page.locator(".budget button[data-budget='5']").click()
        page.locator(".effort button[data-effort='craft']").click()
        page.locator(".dx-lang button[data-lang='bg']").click()
        page.wait_for_function("() => document.querySelector('.mentor button[data-mentor=off]').getAttribute('aria-pressed') === 'true'")
        page.wait_for_function("() => document.querySelector('.budget button[data-budget=\"5\"]').getAttribute('aria-pressed') === 'true'")
        _restart(studio_server)
        page.reload(wait_until="domcontentloaded")
        page.wait_for_function("() => document.querySelector('.mentor button[data-mentor=off]').getAttribute('aria-pressed') === 'true'")
        assert page.locator(".mentor button[data-mentor='off']").get_attribute("aria-pressed") == "true"
        assert page.locator(".budget button[data-budget='5']").get_attribute("aria-pressed") == "true"
        assert page.locator(".effort button[data-effort='craft']").get_attribute("aria-pressed") == "true"
        assert page.evaluate("() => localStorage.getItem('dx.lang')") == "bg"
        fresh = browser.new_context(viewport={"width": 1280, "height": 800})
        page2 = fresh.new_page()
        page2.goto(url, wait_until="domcontentloaded")
        page2.wait_for_function("() => document.querySelector('.mentor button[data-mentor=off]').getAttribute('aria-pressed') === 'true'")
        assert page2.evaluate("() => localStorage.getItem('dx.lang')") in (None, "en")
        assert page2.locator(".dx-lang button[data-lang='en']").get_attribute("aria-pressed") == "true"
        assert page2.locator(".effort button[data-effort='auto']").get_attribute("aria-pressed") == "true"
        assert page2.locator(".mentor button[data-mentor='off']").get_attribute("aria-pressed") == "true"
        assert page2.locator(".budget button[data-budget='5']").get_attribute("aria-pressed") == "true"
        browser.close()


```

The fresh browser context inside `test_settings_persist_across_process_and_browser_restart` is the new-profile check. There is no second test that depends on pytest order.

## Self-review notes

- Spec phase 1 items 1–4 map to Tasks 1–3. Hub is untouched. Redirects, panels, and `admin_compat.py` are untouched.
- Initial `aria-pressed="false"` is required so a 500 cannot look like the old fake `enabled: true` paint. A successful GET still paints the kv value.
- Screenshots are artifacts, not git files.
