# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).  
The version in `pyproject.toml` is the source of truth for the package.

## [Unreleased]

### Agent

- **Build loop (S1)** — the plan drives the model. `daedalus/build_loop.py` observes every native tool result during a Playground/build turn, marks plan steps ✓/✗/☐, and when the model goes quiet scores the Definition of Done *inside* the native round loop. Incomplete → the kernel injects PLAN PROGRESS (failed calls, missing required steps, NEXT tool with slug/args) and grants bounded fix rounds (`DAEDALUS_BUILD_FIX_ROUNDS`, default 3). The turn ends complete or with an honest BUILD LOOP summary. `job_contract.evaluate(strict=True)` scores the plan without a "готово" claim; a failed call repaired by a later successful call of the same tool is no longer a blocker. Progress: `docs/BUILD_LOOP_PROGRESS.md`.
- **Tool results as navigation (S3)** — `playground_new` / `write_file` results carry the project dir, preview URL, and `slug=` usage; `read_file` and `list_dir` take `slug`; a `run_command cwd=<slug>` resolves into the Playground project instead of the product root; jail refusals point to the slug. Inside a build turn the kernel fills a forgotten `slug` (visibly) and appends `NEXT (plan): …` or `PLAN: all required steps done` to every tool result.
- **Write-time quality gate (S4)** — `write_file` parses what it saves (`daedalus/write_gate.py`): `node --check` for JS and inline `<script>` blocks (with the real line), `compile` for Python, `json.loads`; a duplicate `let` is `── write_file FAILED syntax … ──` with the line, not OK. `game_lint` now covers JS/HTML canvas games: double game loop, canvas without input, start cell outside the grid — reported as `lint:` next to the write. A failed write is repaired only by a later good write of the same file. `DAEDALUS_WRITE_GATE=0` disables.
- **One build layer + model roles (S5)** — after the native build the persona no longer re-plans over the whole context: `daedalus/build_wrap_up.py` makes one short call over the kernel's ledger and strips `<think>`, NEED_* markers, JSON/XML tool dumps, SQL and rule stickers from the closing message (kernel-written summary when nothing usable is left). The NEED_* re-roll loop, `NEED_PROPOSE` and the post-hoc DoD sticker are skipped for wrapped builds. `DAEDALUS_MODEL_BUILD` / `DAEDALUS_MODEL_WRAP_UP` pick a model per phase (same provider and key).
- **Mentor + harness that meets a weak model half-way (S7)** — `daedalus/build_mentor.py`: a second model (`DAEDALUS_MODEL_MENTOR`, role `mentor`) the kernel consults at decision points — the same tool failing twice in a row, or the builder going quiet with the plan incomplete. It sees the ask, the plan with status, the ledger, the failing tool's exact schema and RAG knowledge retrieved for the *error*, and answers with a diagnosis, an imperative instruction and optionally one read-only `consult` (math3d / spatial / game_canon / game_lint / read_file / list_dir) that the kernel runs itself and hands to the builder. Bounded (`DAEDALUS_MENTOR_MAX_CALLS`, 3), fail-open, `DAEDALUS_BUILD_MENTOR=0` disables. Deterministic fixes from the live eval: `math3d` exposes every op's arguments (`perspective(fovy, aspect, near, far)`), `catalog` returns signatures with examples, a flat list under `a`/`eye` is spread positionally and a fovy > π is read as degrees; tool-call arguments that are fenced, contain raw newlines or a trailing comma are repaired, a truncated payload fails with "cut off — split the file" instead of a silent `{"raw": …}`; a failure that never named a file is repaired by any later good call of that tool; shell policy refusals are `BLOCKED` (with the reason) not `FAILED`, `cat/head/tail <file>` is rerouted to `read_file` and `node --check x.html` to `game_lint`; math3d/crowd demos skip the arcade fun gate; "не прави 3D" no longer trips the heavy-3D check; the outer DoD adopts the build loop's verdict. The builder's user turn now starts with the owner request, followed by a bounded RAG block and the DoD (`daedalus/build_context.py`) — it was a prefix of the persona context that cut the request off once knowledge was loaded. No post-hoc "Self-review" note on wrapped builds. A build turn has a wall-clock budget (`DAEDALUS_BUILD_TIME_BUDGET_S`, 420 s): a slow builder ends with `time budget exhausted` and a wrap-up instead of a client timeout; the playtest heuristic scans whole source files (the 1800-char judge snapshot missed a `requestAnimationFrame` at line 400 and reported a missing game loop); a missing browser is `BLOCKED`, and CSS `:hover` no longer makes a snake game a racer.
- **Router negations + explicit merge (S6)** — "Не прави 3D, quaternion или octree" no longer routes to the 3D playbook: negated clauses are stripped before matching and shown to the model as `Owner excluded`. Two playbooks merge only when the ask joins them (`slerp and octree`); a rendering-target mention ("on a canvas") no longer drags `arcade` into a 3D demo. An explicit Godot/Unity kind leads a merge. Self-improvement reflects on a build turn only when the build loop gave up.
- **Kernel scaffold + verify (S2)** — for Playground builds the kernel creates the project before the model talks (deterministic name from the ask, or the session's project), removes `playground_new` from the model's tools, and when the plan's writes are in runs `game_lint` over every written file plus the playtest heuristic (optional Chromium verify behind `DAEDALUS_BUILD_BROWSER_VERIFY=1`). Blockers come back inside the build loop as `VERIFY (kernel ran it)` with `NEXT: fix <file>`; a good re-write repairs them. Harness `list_dir` during a build is redirected to the project. `DAEDALUS_BUILD_SCAFFOLD=0` / `DAEDALUS_BUILD_VERIFY=0` disable.
- **Tool catalog + router** — one catalog of every native tool, NEED_* leftover, skill, and knowledge file (`daedalus/tool_catalog.py`). Task playbooks are graphs (`daedalus/task_playbooks.py`): arcade, 3D math/SLERP, crowd/spatial, Godot, Unity, epic world, harness patch. The kernel matches the ask before the LLM talks, injects a TASK PLAN with docs, auto-runs consult tools (`spatial` advise, `game_canon` study, skill load), and exposes only that subset so cheap models stop hunting `daedalus/agent.py`. `job_contract` scores required steps. Inspect: `GET /api/v1/daedalus/tools` and `/api/v1/daedalus/tools/route?q=…`.
- **Play Lab computers** — local HTTP still always serves the specimen. Optional **SmolVM** guest Chromium (`pip install smolvm`, not vendored) adds a live viewer + CDP so the owner can watch. `DAEDALUS_LAB_BACKEND=auto|local|smolvm|docker`. Explicit `smolvm` without the package fails closed; `auto` falls back to local. Windows guests are not started. Studio World → Watch build.
- **Connectors** — Studio / Command Center one screen for the brain (Ollama or cloud), GitHub/GitLab, Netlify, MCP servers, mesh, Play Lab, 3D keys. Values go to `$DAEDALUS_DATA/secrets.env` (masked in the UI, never git). Recommendations say which key to set first (OpenRouter/AllToken vs GitLab vs GitHub PAT).
- **One brain** — activating Ollama (wardrobe, Connectors **Brain**, or `DAEDALUS_PROVIDER=ollama`) routes Studio chat, Command Center, workers, autopilot, and CLI. Workers inherit the local model instead of staying on OpenRouter/NVIDIA defaults. A leftover cloud `DAEDALUS_MODEL` is not sent to Ollama. Studio **Brain** is a compact switcher; keys live on Connectors.
- **Git forges** — clone GitHub **or** GitLab; playground push honours `provider`; GitHub Pull Request and GitLab Merge Request (`create-upstream-pr`, native tool `git_forge`). Playground menu 🔀 PR / MR.
- **Skill memory** — catalog (name + description) plus at most one SKILL.md body per turn. `draft_from_failure` / `draft_from_success` write pending JSON; they stay out of the next prompt until owner Approve in Studio **Уроци**. Native `skill` is catalog/load/pending only — the agent cannot approve itself.
- **Hard-job slices** — playground work that is already multi-file keeps a job ledger (`original ask`, acceptance, named artifacts) under `$DAEDALUS_DATA/jobs/`. `spawn_subagent` takes `role=explorer|coder|verify` (explorer/verify cannot write), injects only the asked artifacts, and refuses `TASK_COMPLETE` that contradicts a failure. Definition of Done blocks "готово" while slices remain or the reply admits it did not finish. Whisper chat and greenfield playable slices are unchanged.
- **Studio chat jobs** — Studio starts `POST /api/v1/daedalus/chat/jobs` and listens on SSE (`…/stream`) with live tool labels and **Спри**. The blocking `POST /api/v1/daedalus/chat` stays for API/mesh clients.

### Security

- Peer HTTP ask/chat refuses an empty mesh key (fail closed). Ping on loopback still works without one. Compose no longer ships a default peer secret.
- Sandbox child processes use the same secret-stripped env as the Studio shell.
- Unpublished `/apps/{slug}/db/{key}` writes return 404. Clearing the whole KV store needs the owner token. Published games keep anonymous single-key writes.

### Studio

- Canonical chat/build UI: Godot / Unity / node / auto in the kind dropdown; Command Center is proposals, GDD, playground. Fetch calls check `res.ok`, show errors, and disable buttons while in flight.
- Command Center list pages escape `innerHTML`. Markdown links in the old chat only allow `http(s):`.
- Owner-facing copy no longer points at Replit Secrets or a foreign product name in titles.
- Command Center CSS is local (`/admin/static/admin.css`) — no Tailwind CDN, no Google Fonts.
- One top nav (Studio / Playground / Game Studio / Proposals / Setup). Desktop sidebar is gone; mobile uses the same five items. Command Center uses Studio gold instead of cyan.
- UI language is English by default. Bulgarian is an explicit EN|BG choice (`localStorage dx.lang`) and does not mix with English on the same screen.

### Delivery

- Docker image health in CI sets `DAEDALUS_TOKEN` so a public bind is allowed. Ruff F401 on `chat_jobs` and the new hardening tests.

### Games

- **Senior game-engineer kit** — native `math3d` (stdlib SLERP / Möller–Trumbore / look-at / projection; optional numpy cross-check; `claimed` verifies a formula before C++). `spatial` picks grid/quadtree/octree/BVH and scans nested collision loops. `game_lint` runs ruff, clang-tidy/format when installed, C#/Unity heuristics, Godot-3 API smells; format does not write. `profiler` parses NUnit/Unity Test XML, Chrome traces, Tracy CSV (`kind=tracy`), FPS logs; `unity_tests` needs `UNITY_PATH` and is not Play. `game_canon` studies DOOM 3 BFG, Mindustry, Game Programming Patterns, tinyrenderer, stb, GLM, DirectXMath, and Tracy (clone under `$DAEDALUS_DATA/canon/`, allowlisted files) — steal structure, never copy. RenderDoc is owner-side frame capture. Skills + `daedalus/knowledge/` (including `game_dev_agent_knowledge_base.md`) cover DOD, transform math, netcode, spatial/SIMD. The kit is wired into chat (`GAME ENGINEER CONTRACT`), Playground/Studio planner addenda, full autopilot, `eng-check` (warn, not block before files), and the job contract (heavy 3D/physics claims of "готово" require at least one eng tool; Snake/Tetris do not).
- **Game canon roadmaps** — `game_canon` also covers Milo Yip's study path (`milo-path`: readme + isbn.csv, not PDFs), CIS 2024 roadmap, `awesome-game-engine-dev`, Azurelol C++ architecture samples (`chunk` by class/struct with a Context comment), and Unity Chop Chop **wiki** pages (`wiki/…` fetches the GitHub wiki). `kurong00/GameProgramBooks` is listed only to **refuse** clone/open (copyrighted ebook dump). Compact RAG notes in `daedalus/knowledge/game_roadmaps.md`. Still never copy assets, books, or Chop Chop into a Playground game.
- **Honesty suite** — `python -m daedalus.game_agent_bench` (and `GET /api/v1/daedalus/bench`) runs in-process checks against this harness: skip is not a playtest, a racer “ship” is not a store claim, empty world/systems JSON fail closed, Unity without an Editor is an honest refuse, explorer cannot write. Results may land under `$DAEDALUS_DATA/bench/`. This is not a vendor bake-off.
- **Unity MCP or honest refuse** — `engine` on a Unity project talks to `DAEDALUS_UNITY_MCP` (owner-installed local Editor plugin) or `UNITY_PATH` batchmode (import is not Play). Without either it returns an honest non-promise. We do not vendor Unity. Gate `unity-check` does not block guided racing.
- **Production layer** — M0–M3 stays a labelled prototype. `design/systems.json` plus persist and pause/settings is the production layer. Native `production`. Gate `production-check` does not block guided racing before files exist. Empty systems JSON is not a production layer. Store-ready / готово за стора on a prototype fails (a racer “ship” mesh does not).
- **World kit runtime** — epic projects ship a playable campaign slice: save `{version, region, act, flags, inventory, quests}`, `world/quests.json`, `characters/*.dialogue.json`, gated `levels/graph.json`, and `src/world/runtime.js`. `world({action: prove})` walks talk → flag → quest → travel. Hollow map/graph JSON fails. Gate `world-check` does not block guided builds that have no files yet.
- **Asset pipeline v2** — `assets({action: atlas|tileset|sfx|font})` packs a 2D kit (PNG atlas + tileset, procedural WAV, 5×7 font) with no paid APIs. `assets({action: mesh})` submits Tripo/Meshy/fal only when `FAL_KEY` / `TRIPO_API_KEY` / `MESHY_API_KEY` exist; otherwise `{need_key: true}` and no fake hero GLB. Greybox is a labelled cube. Suno is a file drop. Gate `asset-check` does not block guided racing before files exist.
- **Gameplay bot** — visual web kinds run `playtest/scenario.json` (hold keys → canvas pixel-diff, required win/lose). `requestAnimationFrame` FPS and `performance.memory` are gates when the sample is real; a thin headless sample warns. `playtest/balance.json` is a 1000-round economy/combat sim: a resource that only grows, or a fight that never/always wins, fails. Click-Start is not a playtest. Godot/Unity stay on the engine runner.

### LLM

- **OpenRouter Free** preset (`or_free`) — all slots on `$0` OpenRouter ids (`poolside/laguna-s-2.1:free` + `openrouter/free`). Applying any preset now also pins planner/coder/reviewer/analysis/claude to that preset’s core models, so an eco/free mix cannot silently call paid Claude defaults.
- Autopilot coder/reviewer no longer stall on ungated `deep_worker` permission: they fall back to `coder` / `reviewer`. Static HTML scaffolds escape the project description so a brief that mentions `<canvas>` cannot fake a game element. Coder output in ```html fences is ingested (weaker `:free` models rarely emit `===FILE===` blocks).

## [0.3.8] — 2026-08-29

### Games

- **Dissection** — a specimen pipeline for “make it like this game”. Give Daedalus a name, a URL, a git repo, or files in a Play Lab. It fingerprints the stack, optionally probes with Chromium, and writes `assets/_dissection.json` + `design/dissection.md` onto the project being built. GDD, planner, guided build, and autopilot read that specimen. Inspiration only — not a clone of code or assets.
- **Play Lab** — Daedalus’s virtual computer for those specimens. Always a jailed tree under `$DAEDALUS_DATA/labs/{id}/` plus local HTTP on 127.0.0.1. Optional Docker HTTP VM if the image is already local (`DAEDALUS_LAB_PULL=1` to pull). Native `.exe`/`.msi`/`.dmg` installers are refused.
- Native tools: `dissect_game`, `play_lab`. Studio **Свят**, Game Studio, and Playground expose the same flow.

## [0.3.7] — 2026-08-29

### Autopilot

- One builder pipeline on standalone: Studio, API, and `mode=light` all run `run_full_autopilot`. The host admin adapter stays when Daedalus is embedded in a host app.
- Visual critic is **on by default** (`DAEDALUS_QUALITY=0` to opt out). `playground_quality.evaluate` is actually called; an unparsed critic is not a pass.
- Playwright gameplay (click / canvas / keyboard) is required for visual web kinds. A screenshot or persona playtest is not a substitute. Skipped Chromium fails closed.
- Godot kinds must pass a headless probe (`GODOT_BIN`). Unity kinds must run Editor `-batchmode` (`UNITY_PATH`). Missing binaries fail; scaffold is not a playtest.
- Studio verify and playground revive treat skipped runtime smoke as a fail, not a pass. Missing engine binaries are not LLM-repaired.

### Delivery

- GitLab CI: mypy on the new kernel modules; pytest prints coverage (no percentage gate).
- `uv.lock` is committed; CI runs `uv lock --check`.

## [0.3.6] — 2026-08-29

### Agent

- One typed tool protocol (`daedalus.tool_protocol`). Native names are canonical; NEED_* markers are a text encoding of the same calls. No new marker names.
- The agent loop scans markers via `daedalus.need_scan` instead of a 230-line inline parse. Native discovery runs up to 8 rounds; NEED_BATCH planning is skipped when native tools are on.
- Effort overlay is atomic: provider + model + base URL + credentials move together. A wardrobe/Ollama tag is not sent to the owner's cloud provider.

### Autopilot

- Durable run log under `$DAEDALUS_DATA/runs/` (JSONL events + checkpoint). Orphan recovery keeps the log and tells you to resume, not start over. `resume=true` on `/api/v1/playground/projects/{slug}/autopilot` skips completed stages.
- Skip is not success. A skipped browser check does not flip `ok` and is not ship evidence. `DAEDALUS_QUALITY=1` turns the optional critic on; it stays off by default.

### Playground

- Studio `POST /api/v1/playground/projects/{slug}/assets/generate`. The scaffold `scripts/generate-asset.js` calls that URL (admin-compat path is a 404 fallback).

### Delivery

- GitLab CI `ruff check` on the new modules plus syntax/undefined-name rules for the tree. Do not treat this as a format of `agent.py`.

## [0.3.5] — 2026-08-29

### Security

- Studio and Command Center mutations require an owner token (`DAEDALUS_TOKEN` or `$DAEDALUS_DATA/owner.token`). Loopback Studio bootstraps it; a public bind without a token refuses to start.
- `daedalus serve` defaults to `127.0.0.1`. Docker/VPS still bind `0.0.0.0` and must set the token.
- Shell is argv-first (`shell=False`). Metacharacters (`|;&$()`) are refused unless `DAEDALUS_SHELL_LEGACY=1`. A public bind disables the runner unless `DAEDALUS_SHELL_ISOLATION=1`. The cwd jail is not a container.

## [0.3.4] — 2026-08-24

Current product state in this GitLab repository.

### Agent

- FastAPI Studio at `/` and Command Center at `/admin/daedalus/*`.
- Native tools (files, shell, search, browser, MCP, sub-agents, playtest).
- SQLite + Chroma memory; preferences and learned skills.
- Autopilot with Definition of Done; proposals with an Approve gate.
- Optional evolution (idle gardener) and offline kit.

### Games

- Playground kinds: static, vite, vite-react, fastapi, streamlit, node, expo, unity, godot.
- Arcade craft: Vite + Three.js WebGPU racers (`src/racer/`).
- Godot 4: 2D scaffold and 3D greybox room (plate → door), headless probe if a Godot binary exists.
- Blender jobs: emit-only (no `bpy` in the Daedalus process).
- Capsules and Live (Netlify / GitLab Pages / 1h preview).

### Delivery

- CLI: `daedalus serve | ping | models | desktop`.
- Docker Compose + GitLab CI image.
- Windows: `install.ps1` / `Daedalus-Setup.exe` with private CPython and a `Daedalus.exe` launcher (WebView2 / Tk).
- LLM: AllToken, OpenRouter, Novita, NVIDIA, Ollama and other local backends.

### Documentation and brand

- Public GitLab docs are English and describe only Daedalus.
- [docs/INSTALL.md](docs/INSTALL.md) — ZIP/git, venv, Docker, Windows `Daedalus.exe` extra option.
- Mark: labyrinth + compass-bird — [assets/logo](assets/logo/README.md).
