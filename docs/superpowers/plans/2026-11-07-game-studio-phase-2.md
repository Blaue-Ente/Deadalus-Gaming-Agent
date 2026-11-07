# Game Studio Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `/` into the canonical Game Studio shell: the existing three-column Studio keeps chat and the phase 1 header, and the new center panels call the APIs already listed in the approved spec.

**Architecture:** `studio.js` stays the chat, header, and project-list kernel. It exposes `window.dxStudio` (slug, project list, `api`). `studio-workspace.js` owns the panel query, Games/Apps filter, files, git actions, and the build, preview, review, and settings panels. They share the selected slug and the panel id. No iframe of an old page, no new service, no second project store.

**Tech Stack:** Existing FastAPI routes, `app/static/studio.html`, `studio.css`, `studio.js`, `studio-workspace.js`, `i18n.js`, Playwright (already in `pyproject.toml`).

## Global Constraints

- Canonical entry stays `GET /` (`app/static/studio.html`). Header title is Game Studio. Product name stays Daedalus. Mark stays `app/static/daedalus-mark.png`.
- Colors stay `#12100c`, `#1a1611`, `#f2eadc`, `#c9a227`, `#e4c56a`. Desktop grid stays left 280px, center fluid, right 360px.
- Below 1100px CSS width the right rail becomes a center tab. It is not removed. Header controls stay inside the viewport at `scrollY === 0` for 1280×800, 1366×768, and 910×640.
- Call only the existing APIs in the spec parity table. Do not add routes. Do not delete templates. Do not add redirects. Do not edit `admin_compat.py`.
- Settings in the header opens `panel=settings` and does not navigate away. `/admin/daedalus/connectors` still serves the old page.
- Deep link: `/?panel=` is `chat` (default), `build`, `preview`, `review`, `settings`, or `world`. Unknown panel falls back to `chat`. `/?panel=review&tab=` is `proposals` (default), `skills`, `bundles`, `permissions`, or `outbox`. Unknown tab falls back to `proposals`. `/?slug=` selects an existing project and does not create one.
- Games versus Apps is a view. First match: `design/game-studio.json` (the game-studio project list) → Games; else kind `fastapi`, `streamlit`, `node`, or `expo` → Apps; else Games. **All** is the default.
- English strings in `i18n.js` are the source. Every new string has a `bg` entry. User content is `textContent`, never a dictionary key.
- A failed GET does not paint a success default. `aria-pressed` and success copy change only after the response says success. A harness double is labeled with `studio.harnessCheck` and is never a shipped game.
- Secrets are write-only. The settings panel never writes `mask`, `api_key`, or the typed secret into the DOM.
- No new package, no paid call, no real git push, and no live publish in tests. UI posts are fine; tests intercept them.
- Linux Chromium is not Windows WebView2. Playwright clicks are locator clicks without `force` and without `element.click()` inside `evaluate`.
- MR114 (`cursor/studio-effort-api-persist-4549`) is not an ancestor and is not edited.
- Phase 3 redirects and phase 4 alias deletion are out of this plan.

This run implements the shell workflows below. It does not claim the long tail (asset generators, Unity/capacitor/firebase wizards, skill creator, planner, radar, journal). Those stay on their existing pages and are listed as remainder in the self-review.

---

### Task 1: Kernel hook and header Settings

**Files:**
- Modify: `app/static/studio.js`
- Modify: `app/static/studio.html`
- Modify: `app/static/studio.css`
- Modify: `app/static/i18n.js`
- Test: `tests/test_studio_phase1_header.py`

**Interfaces:**
- Consumes: phase 1 `api`, `refreshProjects`, `#settings-link`
- Produces: `window.dxStudio` with `api`, `t`, `text`, `errText`, `busy`, `getSlug()`, `projects()`, `selectSlug(slug)`, `watchSlug(fn)`, `setKindFilter(filter)`, `getKindFilter()`, `setGameSlugs(slugs)`, `kindOf(project)`

- [x] **Step 1:** Change `#settings-link` from an anchor to `<button type="button" id="settings-link" class="chip">`. Keep the connectors URL only as the settings-panel footnote, outside `<header>`.
- [x] **Step 2:** Set the header `h1` to `studio.headerName` (`Game Studio` / `Игрово студио`). Leave the document title and the mark alt as Daedalus.
- [x] **Step 3:** Add `projectBucket` in `studio.js`. App kinds are exactly `fastapi`, `streamlit`, `node`, `expo`. A slug in `state.gameSlugs` is Games even when the kind is an app kind. `gameSlugs === null` means the index has not loaded; Games and Apps buttons stay disabled and the list stays All.
- [x] **Step 4:** Update the phase 1 hit test so Settings stays on `/` with `panel=settings` and `#panel-settings` visible. `elementFromPoint` still has to hit the control.

### Task 2: Workspace panels

**Files:**
- Create: `app/static/studio-workspace.js`
- Modify: `app/static/studio.html`
- Modify: `app/static/studio.css`
- Test: `tests/test_studio_phase2_workspace.py`

**Interfaces:**
- Consumes: `window.dxStudio`
- Produces: panels `#panel-chat`, `#panel-build`, `#panel-preview`, `#panel-review`, `#panel-settings`; nav `#workspace-nav`; filter `#kind-filter`; files `#filelist`; git `#git-menu`

Center panels and the rail:

| Control | Request |
|---|---|
| File list | `GET /admin/daedalus/playground/api/projects/{slug}/files-snapshot` |
| File body | `GET /admin/daedalus/playground/api/projects/{slug}/file?path=` |
| Assets | `GET /admin/daedalus/playground/api/projects/{slug}/assets` |
| Delete | `DELETE /api/v1/playground/projects/{slug}` after `confirm` |
| Clone / revive | `POST /admin/daedalus/playground/api/clone` and `/revive` with `{url}` |
| Push / PR | `POST .../projects/{slug}/push` and `.../create-upstream-pr` |
| Catalog / brainstorm / GDD / start | `/admin/daedalus/game-studio/api/catalog`, `/brainstorm`, `/gdd`, `/start` |
| Workspace | `GET /admin/daedalus/game-studio/api/projects/{slug}` |
| Guided / stop | `POST .../build/guided` and `POST .../playground/api/projects/{slug}/guided/stop` |
| Autopilot / stop | `POST /api/v1/playground/projects/{slug}/autopilot` with `{description, mode, effort}` and `POST .../autopilot/stop` |
| Gates / verify / advance | `POST .../gates`, `.../verify`, `.../milestones/advance` |
| Run / stop / logs | `POST .../run`, `POST .../stop`, `GET .../logs` |
| Preview | `GET /api/v1/playground/projects/{slug}/preview/index.html` only after that GET returns success |
| Publish | `POST /api/v1/playground/projects/{slug}/publish-local`, `POST .../live`, `GET .../capsule.json` |
| Review | proposals, skills, bundles, permissions, outbox routes from the spec table |
| Settings | `GET /api/v1/llm/status`, `GET /admin/daedalus/connectors/overview`, `GET/POST /admin/daedalus/connectors/config`, `POST /admin/daedalus/connectors/secrets` |
| Game index | `GET /admin/daedalus/game-studio/api/projects` |

Start sends the open slug so GDD and `design/game-studio.json` are written into that project. Busy buttons disable until the response. Stop reports success only when `ok` is true. `harness_check: true` renders `studio.harnessCheck`.

- [x] **Step 1:** Write the failing Playwright tests in `tests/test_studio_phase2_workspace.py` for deep link, filter, same-slug start, honest 500s, review actions, write-only secret, git failure, BG versus project name, reload, and the three viewport screenshots.
- [x] **Step 2:** Run `pytest tests/test_studio_phase2_workspace.py -q --tb=line` and confirm the new assertions fail.
- [x] **Step 3:** Implement the panels and CSS, including the 1100px tools tab. Replace the old `max-width: 1040px` stack so the rail is hidden until Tools or `panel=world`.
- [x] **Step 4:** Re-run the phase 2 file and `tests/test_studio_phase1_header.py`. Both pass.
- [x] **Step 5:** Run the full suite. If the baseline function count moves, run `python3 scripts/record_baseline.py --refresh-inventory` once before the commit. Do not refresh again to stamp the new commit SHA.

## Self-review

Spec phase 2 asks for every parity-table row inside the shell. This plan covers the rows with a real control and an existing handler. Remainder, not built here:

- Asset generate, pipeline, and auto-pack.
- Unity, capacitor, firebase, and hybrid-mobile wizards.
- Skill creator, reference scout, planner, discoveries, radar, journal, and blockers.
- Shell and flight have no query value. `panel=world` opens the world tab. The narrow Tools tab is session state.
- Phase 3 redirects and phase 4 route deletion.
- Hub sidebar. Windows WebView2. MR114 classic-chat localization.
- Tests do not perform a real provider call, git push, or live publish.

Placeholder scan: tasks name files, request bodies, and assertions. No TBD. `window.dxStudio` names match Task 1 and Task 2.
