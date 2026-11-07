# Unified Game Studio

Status: written spec, waiting for Nikolay's review. Direction approved 2026-11-07 (`Одобрявам`). This file does not authorize implementation.

Base of this branch: `origin/main` at `7f1e8db` (merge of `cursor/windows-pythonw-utf8-ceb5`). MR114 (`cursor/studio-effort-api-persist-4549`) is not an ancestor of this branch and is not a dependency of any phase below.

## Problem

Daedalus already has one project store and one engine stack. The owner still meets three front doors that do the same job: Studio (`/`), Playground (`/admin/daedalus/playground`), and Game Studio (`/admin/daedalus/game-studio`), plus a Hub that only links to them. Setup is easy to miss. On the installed `main` build, wrapped Studio header controls can be painted outside the header's grid row, so the page underneath receives the click.

The product becomes one Game Studio at `/`, using the Studio visual system, with one lifecycle and the existing HTTP APIs. Old pages stay until the new shell can do the same work. Then they redirect. Backend route cleanup is a later phase.

## Decision

Three shapes were compared before this spec:

1. **One Studio shell, current APIs.** Recommended and approved. New panels call the handlers that already exist. No iframe, no second project database, no rewrite of `admin_compat.py` in the same change.
2. **UI merge and backend merge together.** Rejected for this work. `app/admin_compat.py` and classic chat are a second session model. Combining that with a new shell is how slugs and in-flight builds get lost.
3. **Rename Hub and embed the old pages.** Rejected. Three lifecycles would remain, and the header click bug would remain inside a frame.

## Non-goals

- No new runtime, database, package, or paid service.
- No translation of project names, chat, files, GDD bodies, or other user content.
- No move or rewrite of project directories, `secrets.env`, or kv files.
- No merge of MR114, and no import of its classic-chat extraction (`ui/static/classic_chat.js`).
- No claim that a Linux Chromium test is a Windows WebView2 test.
- Phase 4 (duplicate HTTP handlers) is specified here so it is not done early. It is not part of the first implementation plan.

## Product

Canonical entry is `GET /`, served by `app/static/studio.html`. The name in the header is **Game Studio**. The product name stays **Daedalus**. The mark stays `app/static/daedalus-mark.png`.

Layout, from `app/static/studio.css`:

- Background `#12100c`, raised panels `#1a1611`, text `#f2eadc`, gold `#c9a227` / `#e4c56a`.
- Desktop grid: left 280px, center fluid, right 360px, header across the top.
- Left: projects and, once a slug is open, its files.
- Center: conversation, and the active workflow panel.
- Right: tools (shell, flight, brain summary, world, playtest).

From phase 2 on, below 1100px CSS width the right column becomes a tab in the center. It is not removed. Phase 1 does not change that column. In every phase the header row may wrap, and its grid track grows with that wrap. Settings, Mentor, effort, budget, model, and EN/BG stay inside the header and inside the viewport without scrolling the page.

Lifecycle for one slug:

idea → plan / GDD → build (`guided`, `autopilot`, or `prototype`) → preview / run → verification / playtest → publish or Git push.

Non-game kinds stay in the same list. The list has three filters. **All** is the default, so nothing is hidden on first paint. Games versus Apps is first match:

1. `design/game-studio.json` exists → Games. An Expo or Vite slug created by the Game Studio start handler is a game.
2. Kind is `fastapi`, `streamlit`, `node`, or `expo` → Apps.
3. Anything else (`godot`, `unity`, `static`, `vite`, `vite-react`, `auto`, unknown) → Games.

A slug is in one bucket. The filter is a view. It does not write a registry column.

## Feature parity

Each row names the home in Game Studio and the handler to call. Phase 2 may not add a route if the row already has one.

| Capability | Home | Existing API |
|---|---|---|
| Project list, create, delete, open slug | Left rail | `GET/POST /api/v1/playground/projects`, `DELETE /api/v1/playground/projects/{slug}` |
| Files | Left rail | `GET/POST /admin/daedalus/playground/api/projects/{slug}/file` until phase 4 |
| Chat, stop | Center, panel `chat` | `POST /api/v1/daedalus/chat/jobs`, `GET .../jobs/{id}`, `POST .../cancel`, `GET .../stream` |
| Effort | Header | `POST /api/v1/daedalus/effort` with JSON `text` and `effort`. Selection itself is client state (see Data). |
| Mentor on/off, model, custom model | Header | `GET/POST /api/v1/daedalus/mentor` |
| Writer assist | Header | `GET/POST /api/v1/daedalus/writer-assist` |
| Run budget $0 / $5 / $20 / $50 | Header and Settings | `GET/POST /api/v1/daedalus/run-budget` |
| Language EN/BG | Header and Settings | Client dictionary `app/static/i18n.js` only |
| Brain, model wardrobe, offline | Settings and right rail | `/api/v1/llm/status`, `/api/v1/llm/local`, `/api/v1/llm/wardrobe`, `POST /api/v1/llm/wardrobe/{activate,pull,cloud,local}`, `GET/POST /api/v1/daedalus/offline` |
| Keys, GitHub, GitLab, MCP, connectors | Settings | `/admin/daedalus/connectors/config`, `/overview`, `/secrets`, `/mcp`, `/status`, `POST .../test-github`, `POST .../test-gitlab` |
| Brainstorm, GDD, preset, milestones, start | Center, panel `build` | `/admin/daedalus/game-studio/api/catalog`, `/brainstorm`, `/gdd`, `/start`, `/projects`, `/projects/{slug}`, `/gates`, `/verify`, `/milestones/advance`, `/build/guided` |
| Guided build, autopilot, logs, run/stop | Center `build`, right rail flight | Playground routes under `/admin/daedalus/playground/api/projects/{slug}/guided/*` and `/autopilot/*`; Studio autopilot `POST /api/v1/playground/projects/{slug}/autopilot` and its stream |
| Preview | Center, panel `preview` | `GET /api/v1/playground/projects/{slug}/preview/{path}` and `POST /admin/daedalus/playground/api/projects/{slug}/run` |
| Playtest, dissection, lab | Right rail and panel `world` | `/api/v1/daedalus/playtest`, `/dissect`, `/labs`, `/labs/{id}/install|start|stop` |
| Publish local, live, capsule | Center `preview` actions | `/api/v1/playground/projects/{slug}/publish-local`, `/live`, `/capsule` |
| Git push, PR/MR, clone, revive | Project rail menu, visible on every panel | `/admin/daedalus/playground/api/projects/{slug}/push`, `/create-upstream-pr`, `POST /admin/daedalus/playground/api/clone`, `POST /admin/daedalus/playground/api/revive`, GitHub/GitLab status routes |
| Proposals approve / reject / rollback | Center, panel `review` | `/admin/daedalus/proposals/api`, `/proposals/stats`, `/proposals/{id}`, `POST .../approve|reject|rollback` |
| Skills toggle | Review, tab skills | `GET /admin/daedalus/skills/api`, `POST /admin/daedalus/skills/{name}/toggle` |
| Bundles apply / reject | Review, tab bundles | `GET /admin/daedalus/bundles/api`, `POST /admin/daedalus/bundles/{id}/apply|reject` |
| Permissions | Review, tab permissions | `GET /admin/daedalus/permissions`, `POST .../grant`, `POST .../{class}/revoke` |
| Outbox | Review, tab outbox | `GET /admin/daedalus/outbox/api`, `POST .../{id}/apply|dismiss` |
| Shell | Right rail | `POST /api/v1/daedalus/shell` |
| Preferences, learned-skill drafts | Left rail, as today | `/api/v1/daedalus/preferences`, `/learned-skills` |

### Shell status on `9d716f784736fa36e593995f18fafbf3cdeda0e8`

This note records what the Studio shell actually calls. It does not add a route, a panel, or a redirect, and it does not claim the nine phase-3 checks.

Already in `#panel-review`, under the five tabs: blockers, planner draft/approve/delete, and discovery approve/reject. Settings and the shared slug are unchanged.

Still open on that planner/discovery slice:

- A reload clears the in-page hold maps. A list that still says draft or pending can show the action again.
- The status line names one pending id. If any id is unknown, that unknown sentence replaces the pending sentence. Each id in the map stays closed.
- Delete stays available while that plan’s approve is still unresolved.

Proposed next slice, not built. Two blocks inside the existing Review section, after discoveries. No new tab and no new page. Rows stay global. The selected slug is not a save target.

Skill draft uses the existing handlers only:

- `POST /admin/daedalus/playground/api/skill-creator/start` with `{idea, platform, context}`. Empty idea is 400 before the model. Platform is one of `cursor`, `claude`, `codex`, `generic`, `harness`; anything else is stored as `generic`.
- Start calls `llm_client.complete` once (configured provider, max 1200 tokens). It does not call `build_research_prompt`, the skill catalog, or web search. It stores `research_notes` as the typed idea and `tool_steps` as `[]`. `context` is saved as `extra_context` and is not inserted into the start, refine, or generate prompt. If `complete` throws, the handler still returns `ok: true` with that stub plan.
- `POST .../skill-creator/refine` needs `session_id` and `message` (400 otherwise, 404 if the session is missing). One configured-provider call, max 2000 tokens. A thrown call is HTTP 500.
- `POST .../skill-creator/generate` is one configured-provider call, max 8000 tokens. Without `force`, a session that is not `ready_for_generation` is HTTP 409 and the Bulgarian error string is server text. `force: true` spends the call anyway. `ok: true` can still mean zero parsed files plus a validation warning. Download uses the `files` object on that response. The multi-file control writes a `.txt` bundle. `GET .../skill-creator/session/{id}` returns a 4000-character preview and file names, not the full bodies.
- Sessions are written to kv `skill-creator:{id}` and to process memory. The 24h purge applies to the memory map. A kv row can still load after that.
- `POST .../skill-creator/github-gist` publishes a public gist and stays out of this slice.

Reference brief uses `POST /admin/daedalus/playground/api/reference-scout` only. Empty `reference` is 400. The handler passes `memory` and does not pass `save_slug`, so it does not write `assets/_reference.json`. It runs up to four DuckDuckGo searches with `record_discoveries=false`, fetches a URL when the reference is a URL, then calls `mesh.route` with model `alltoken:deepseek-v3.2` (max 1200). That is not the configured Studio model. Search or synthesis failure can still return `ok: true` with a generic stub. The project scout route is not this slice: it also does not save the file, and it uses the slug as the search name.

Each costly control confirms first, stays blocked across leaving Review while its outcome is pending or unknown, and does not send a second write for that same in-flight call. A received success updates the page-session result even when the hidden panel is not painted. A timeout stays unknown. The server calls are not idempotent. Tests use a temp data root, stub `complete`, `web_search_tool`, `web_fetch_tool`, and `mesh.route`, and do not publish.

Still out: radar scan (writes discoveries; the classic source checkboxes are not sent), journal list/pause/clear, manual plan create, step add/override/mark, pause/resume, progress polling, background run, discovery delete, and discovery regenerate-plan.

`/admin/llm-router` is not a fourth brain. Phase 2 Settings is the only brain UI. The old page redirects in phase 3.

Classic chat (`GET /admin/daedalus`, `POST /admin/daedalus/chat`) stays a real linked page until phase 3's nine parity checks pass. It is not embedded.

## Components

No new front-end framework. The units are sections in `studio.html`, `studio.css`, and `studio.js`. A section can be read without reading the others. They talk through the panel id and the selected slug, not through copied DOM from the old templates.

| Unit | Role | Depends on |
|---|---|---|
| HeaderBar | Mark, title, project chip, Mentor, assist, effort, budget, EN/BG, Settings. Server-backed pressed state updates only after a successful save. Language and effort update immediately because they are client state. | `i18n.js`, harness routes listed above |
| ProjectRail | List, All/Games/Apps, create, files for the open slug, and the git menu (clone, revive, push, PR/MR) | Playground project, file, and git routes |
| Workspace | Chat transcript and the active panel | Chat jobs; the panel unit |
| BuildPanel | Wizard and milestone controls | Game Studio and guided/autopilot routes |
| PreviewPanel | Run, stop, preview frame, publish, capsule | Preview, run, publish routes |
| ReviewPanel | Proposals, skills, bundles, permissions, outbox | Admin routes in the parity table |
| SettingsPanel | Brain, keys, connectors, budget, language. Phase 1 does not ship this panel. | Connector and llm routes |
| ToolRail | Shell, flight log, world, playtest | Shell, autopilot stream, world routes |

Deep link, read on load and updated when the owner changes panel: `/?panel=` with one of `chat` (default), `build`, `preview`, `review`, `settings`, `world`. Review tabs: `/?panel=review&tab=` with `proposals` (default), `skills`, `bundles`, `permissions`, `outbox`. Unknown values fall back to `chat` and `proposals`. The slug query is `/?slug=`. It selects an existing project. It does not create one.

## Data

No migration. Owners, keys, and slugs stay where they are.

| State | Where it lives on `main` | Behavior this design keeps |
|---|---|---|
| Projects registry | SQLite `playground_projects` via `daedalus/store.py` | Same rows. No new column for Games vs Apps. |
| Project files | `playground_projects_dir()` in `daedalus/paths.py`. Standalone: `$DAEDALUS_DATA/playground`. Otherwise `playground/projects`. | Call the helper. Do not hardcode a second root. Do not move trees. |
| GDD and Game Studio config | `design/gdd-lite.md`, `design/game-studio.json` inside the slug | Still written only by the existing start/GDD handlers. |
| Mentor on/off | `daedalus.kv` key `adn:daedalus_mentor` | JSON file under `$DAEDALUS_DATA/kv/` when Replit DB is absent. Not the SQLite file. |
| Mentor choice, cap, spend | kv keys written by `daedalus/mentor_helm.py` | Unchanged. |
| Run budget and spend | kv `adn:daedalus_run_budget`, `adn:daedalus_run_spend` | Unchanged. |
| Writer assist | kv `adn:daedalus_writer_assist` | Unchanged. |
| LLM and worker config | kv via the same facade (`adn:daedalus_llm_config` and worker keys) | Unchanged. |
| Secrets | `$DAEDALUS_DATA/secrets.env` | Unchanged. The UI still must not show a full key. |
| Language | `localStorage` `dx.lang` | Missing or invalid → `en`. `bg` only after the BG control. Not copied to kv. |
| Effort | `localStorage` `dx.effort` | Missing or invalid → `auto`. Allowed values: `auto`, `whisper`, `craft`, `epic`. Not copied to kv. |
| Last slug and chat session id | `localStorage` `dx.slug`, `dx.session` | Same keys. Clearing site data forgets them. The project files remain. |

Restart contract:

- Process restart keeps SQLite, kv JSON, `secrets.env`, and project files. It does not clear `localStorage`.
- Browser restart in the same profile keeps `dx.lang`, `dx.effort`, `dx.slug`, and `dx.session`.
- A new profile, or cleared site data, returns language to English and effort to `auto`. Mentor, budget, assist, keys, and projects stay, because they are not in the profile. That reset is the specified default, not a bug, and it is not a reason to copy those two keys into kv.

Env locks stay authoritative. `DAEDALUS_MENTOR`, `DAEDALUS_RUN_BUDGET`, and the writer-assist env lock still override the saved value. The header shows the locked value and disables the control. It does not pretend the click was saved.

## Errors, busy, cancel

- A control that writes server state waits for the response. `aria-pressed` changes only after success. On failure it stays on the previous value and the panel shows the HTTP status plus the server error string.
- `loadMentor`, `loadBudget`, and `loadAssist` on `main` catch a failed GET and paint Mentor on, budget Off, or assist Auto. That paint is a false reading. Phase 1 stops it. A failed load leaves the control unchanged and shows the error. It does not invent a default.
- Effort preview on `main` GETs `/api/v1/daedalus/effort` and, on failure, clears `#effortnote`. Phase 1 calls the existing POST with `{text, effort}` and writes the error into `#effortnote`. The pressed effort button still changes immediately, because effort is client state. The note is the server classification, not the save.
- Language applies immediately in the page. If `localStorage.setItem` throws, the previous language stays and the header says the browser refused the save.
- In-flight build, chat, preview run, and autopilot disable the button that started them and show a busy label. Stop calls the existing cancel or stop route. The UI does not report success until that route returns success.
- Missing API key, missing Godot binary, missing Unity, and a down preview stay the error strings those adapters already return. They are not turned into a green "done".
- A test double or an unwired provider is labeled **harness check** in the panel that shows it. That label is never "published" and never a successful game.
- Timeouts surface as timeout. The client does not retry the write by itself.

## Language

English strings in `app/static/i18n.js` are the source. Bulgarian is the `bg` map. New UI strings get both. User content is inserted as text, never as a key. The default for a profile with no `dx.lang` is English.

## Phases

Each phase is its own merge request from `main`, after the previous one is on `main`. Revert of that merge request is the rollback. None of them migrate data, so revert does not need a down migration.

### Phase 1 — header hits and a visible Settings control

Branch from `main` after this spec is approved. Do not branch from MR114. Do not take `classic_chat.js`, the Cyrillic audit, or the baseline inventory edit from MR114.

Allowed edits: `app/static/studio.css`, `app/static/studio.html`, `app/static/studio.js`, `app/static/i18n.js` (only the new Settings label and an effort-error string, EN and BG), and one Playwright test module.

Work:

1. Header grid row becomes `minmax(var(--header), auto)`. Header sits above the center and rails (`z-index` above them). A wrapped second line of chips is not covered by the chat column.
2. Settings is a header control. It is a link to the existing page `/admin/daedalus/connectors`. Phase 1 does not rebuild connectors inside Studio. Its box sits inside the viewport at `scrollY === 0` for CSS viewports 1280×800, 1366×768, and 910×640. The last size is the CSS viewport of a 1366×768 screen at 150% Windows scale. Chromium `deviceScaleFactor` is not that test, and none of these are a WebView2 run.
3. Mentor, budget, and assist failed loads no longer paint a fake current value. Effort preview uses POST and shows failure in `#effortnote`.
4. Tests use Playwright locator clicks. `force: true` and `element.click()` from `evaluate` do not count. The test asserts `elementFromPoint` on the Mentor, EN, BG, effort, budget, and Settings boxes is that control, at the three CSS viewports above.

Out of phase 1: Hub, Playground, Game Studio templates, redirects, new panels, `admin_compat.py`.

Known leftover: Hub's sidebar starts off-screen (`-translate-x-full` with no desktop override) and the section nav clips Setup. Phase 1 does not fix Hub. Hub is not the canonical page. Phase 3 removes it as a front door.

### Phase 2 — panels in the shell

Depends on phase 1 on `main`.

Build the units in the component table inside `studio.html`. Call only the APIs in the parity table. Read and write `panel`, `tab`, and `slug` query params as specified. SettingsPanel replaces the phase 1 destination: the same header control opens `panel=settings` and does not navigate away. The connectors page still works at its old URL until phase 3.

Center panels cover build, preview, review (all five tabs), and settings. Right rail keeps shell, flight, and world. Left rail gains the All/Games/Apps filter and the file list.

Do not delete old templates in this phase. Do not add redirects in this phase. Owners can still open Playground and classic chat.

A workflow that exists only on classic chat is in Review before phase 3: permissions and outbox, plus proposals, skills, and bundles. Chat jobs in the center use `/api/v1/daedalus/chat/jobs`, not `POST /admin/daedalus/chat`.

### Phase 3 — redirects after parity

Depends on phase 2 on `main`. Redirects are HTTP 302 from the old page route to the query on `/`. They are added only after the checks below pass against the new shell, with real clicks, on a slug that already existed before phase 2.

Parity checks (all must pass):

1. Open an existing slug from the left rail. Its files are still on disk at `playground_projects_dir()`.
2. Chat job starts, shows busy, and cancel calls the job cancel route.
3. Build panel runs brainstorm, GDD, and start through the game-studio routes and writes into that slug, not a new hidden directory.
4. Guided or autopilot start, stop, and log use the existing routes. A failed start stays an error.
5. Preview run and stop. Publish stays disabled or returns the existing error when the project has no publishable output.
6. Review: list and approve or reject a proposal; toggle a skill; apply or reject a bundle; grant and revoke a permission; apply or dismiss an outbox item. Each uses the admin route in the parity table.
7. Settings saves a connector field through the existing config route and shows the value again after process restart. A secret value is write-only on screen.
8. Header Settings is inside the viewport at the phase 1 sizes without scrolling.
9. EN default, BG opt-in, and a new browser profile returns to EN while mentor/budget stay.

Redirect map, added only after those nine checks:

| Old | New |
|---|---|
| `/admin/`, `/admin/daedalus/hub` | `/` |
| `/admin/daedalus/playground` | `/?panel=build` |
| `/admin/daedalus/game-studio` | `/?panel=build` |
| `/admin/daedalus/connectors` | `/?panel=settings` |
| `/admin/daedalus/proposals` | `/?panel=review&tab=proposals` |
| `/admin/daedalus/skills` | `/?panel=review&tab=skills` |
| `/admin/daedalus/bundles` | `/?panel=review&tab=bundles` |
| `/admin/llm-router` | `/?panel=settings` |
| `/admin/daedalus` | `/?panel=review` |

`/admin/daedalus` is in that map and waits on the same nine checks. Until the redirect commit it stays a real page and a labeled link. The redirect commit removes the link. API routes under `/admin/daedalus/...` are not redirected in this phase. Only the HTML pages are.

Rollback of phase 3 is revert of the redirect change. The shell from phase 2 keeps working. Old templates stay in the tree until the redirect commit, then the duplicate HTML can be deleted in that same commit only if the nine checks passed. If a check fails, the templates stay and that redirect is not added.

### Phase 4 — backend cleanup, separate plan

Depends on phase 3 on `main`. Not started from the phase 1 plan.

Collapse duplicate handlers onto the `/api/v1/...` routes the shell already uses, then delete the unused `/admin/daedalus/playground/api/...` and `/admin/daedalus/game-studio/api/...` aliases. One chat pipeline remains: chat jobs. Classic `POST /admin/daedalus/chat` goes away only after phase 3 has redirected the classic page. No project file rewrite. UI phases do not do this cleanup. If the alias list is too large for one plan, phase 4 splits by route family (`playground` aliases, then `game-studio` aliases, then classic chat). The split is still phase 4, still after phase 3, and still not part of the phase 1 plan.

## Compatibility

- Bookmarks to old HTML pages work through phase 2 as the old pages, and through phase 3 as redirects.
- Bookmarks to JSON APIs work through phase 3 unchanged.
- Slugs created by Playground, Game Studio, or Studio open in the left rail because they share `playground_projects`.
- `design/game-studio.json` is not required to open a project. Absence only affects the Games/Apps filter, as defined above.

## Acceptance

Phase 1 is accepted when all of the following are true on Linux Chromium via Playwright, without `force` clicks:

- At CSS viewports 1280×800, 1366×768, and 910×640, a wrapped header still hits Mentor, an effort button, a budget button, EN, BG, and Settings. `elementFromPoint` returns that node.
- Settings' box is inside the viewport with `scrollY === 0`.
- POST mentor off, set budget to 5, set effort to `craft`, set language to `bg`. Restart the process. Reload the same profile. Mentor is off, budget is 5, effort is `craft`, language is `bg`.
- A new browser context shows English and effort `auto`. Mentor is still off and budget is still 5.
- Stop the mentor GET with a 500. The header does not flip to the default "on" paint. The error text is visible.
- A project slug that existed before the test is still listed and its directory is unchanged.
- No test labels a mocked model response as a shipped game.

Phase 2 adds the nine parity checks. Phase 3 adds the redirect assertions (old URL returns 302 to the table target, and the target panel is the one named). Phase 4 is accepted when the shell tests still pass against `/api/v1` only and the removed alias returns 404.

Windows WebView2 on the installed desktop app is a separate check. Passing the Chromium tests does not close it. The desktop check, when it is run, uses the same pointer and the same viewports and is recorded on its own. This spec does not schedule that run.

## First plan after review

After Nikolay accepts this file, the next plan covers phase 1 only. Phases 2, 3, and 4 wait for their own plans.
