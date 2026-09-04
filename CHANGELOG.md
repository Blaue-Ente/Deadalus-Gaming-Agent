# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).  
The version in `pyproject.toml` is the source of truth for the package.

## [Unreleased]

### Agent

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
