# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).  
The version in `pyproject.toml` is the source of truth for the package.

## [Unreleased]

### Games

- **Godot runtime bridge** — when `GODOT_BIN` (or `godot` on PATH) exists, a Godot slice is not done on static files. Autoload `DaedalusBridge` speaks JSON-lines on 127.0.0.1 (`scene_tree`, `inspect_node`, `send_input`, `errors`, `fps_sample`, `screenshot`). Native tool `engine({action, slug})`. A `.tscn` `[connection]` whose method is missing fails DoD without a binary. PNG/GLB get `.import` sidecars; `godot --import` runs before the probe. Optional `DAEDALUS_GODOT_MCP` uses one MCP server from `mcp.json` and falls back to headless. Godot 4.3 API pin lives in `skills/godot_api.md`.

### Agent

- **Hard-job slices** — playground work that is already multi-file keeps a job ledger (`original ask`, acceptance, named artifacts) under `$DAEDALUS_DATA/jobs/`. `spawn_subagent` takes `role=explorer|coder|verify` (explorer/verify cannot write), injects only the asked artifacts, and refuses `TASK_COMPLETE` that contradicts a failure. Definition of Done blocks "готово" while slices remain or the reply admits it did not finish. Whisper chat and greenfield playable slices are unchanged.

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
- `uv.lock` is committed; CI runs `uv lock --check`. GameAgentBench stays a gate list — this repo does not ship a vendor bake-off.

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
