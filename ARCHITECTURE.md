# Daedalus architecture

Daedalus is **one product**: coding agent + Studio + Playground. One FastAPI process, port **8787**, SQLite, a direct OpenAI-compatible LLM.

```
  Owner (BG / EN)
           │
           ▼
  ┌─────────────────────────────────────┐
  │  Daedalus                            │
  │  memory · persona · playground       │
  │  autopilot · self-improve · kernel   │
  └──────────────┬──────────────────────┘
                 │
                 ▼
        OpenAI-compatible LLM
        AllToken · OpenRouter · Novita · NVIDIA
        or Ollama / LM Studio / llama.cpp / vLLM
```

## Layers

| Layer | Where | Notes |
|------|------|---------|
| Identity | `daedalus/identity.py` + `identity_standalone.py` | Compact tool contract in this product |
| Memory | `memory.py`, `knowledge.py` | Chroma collections under `$DAEDALUS_DATA` |
| Agent loop | `agent.py`, `native_tools.py`, `tools.py`, `workers.py`, `subagents.py` | Native tools by default; jailed explorer/coder/verify sub-agents |
| Job ledger | `job_ledger.py` | Original ask, acceptance, slices, named artifacts on disk |
| Shell | `shell_center.py` | Argv-first runner; cwd jail; strip secrets. Default is the host. `DAEDALUS_EXEC_BACKEND=docker` is the command sandbox. |
| Owner auth | `owner_auth.py` | Bearer token on mutations; loopback bootstrap |
| Local LLM | `local_llm.py` + `llm_wardrobe.py` + `machine_profile.py` | Probe + hardware catalog; Ollama is process-wide |
| Offline | `offline.py` | Blocks network native tools |
| Addons | `addons.py` | MCP shelf → `mcp.json` |
| Connectivity | `connectivity.py` | Studio → Connectors; `$DAEDALUS_DATA/secrets.env`; Brain panel |
| Capsule | `capsule.py` | Zip a playground project |
| Live | `live_publish.py` | `/apps/` + Netlify / GitLab Pages / 1h preview |
| Preferences | `preferences.py` | Owner speech → durable memory |
| Learned skills | `learned_skills.py` | Catalog first; pending drafts wait for Approve |
| DoD | `job_contract.py` | Code without evidence is not done |
| Tool router | `tool_catalog.py`, `task_playbooks.py`, `tool_router.py` | Kernel matches the ask to a playbook graph, exposes the right native tools, injects docs, scores the run. The LLM is not the router. |
| Build loop | `build_loop.py` | Walks the playbook during the native rounds: marks steps, scores DoD when the model goes quiet, injects PLAN PROGRESS, bounded fix rounds. Ends complete or honest. |
| Senior game tools | `math3d.py`, `spatial.py`, `game_lint.py`, `profiler.py`, `game_canon.py`, `game_engineer.py` | 3D referee, spatial advisor, C++/C# lint, log/Tracy parsers, OSS study jail, prompt/gate wiring |
| Reach | `reach.py` | Jina Reader + optional CLIs |
| Playtest | `playtest.py` | Judge personas; no 1M dataset |
| Dissection | `dissection.py` + `play_lab.py` | Specimen from name/URL/git/install; Play Lab is the VM |
| Computer | `computer.py` + `computer_smolvm.py` | Optional SmolVM guest browser + live viewer; local HTTP default |
| MiroFish | `mirofish_client.py` | HTTP-only to a sidecar — never vendor |
| MCP | `mcp_client.py` | JSON-RPC stdio/HTTP from `mcp.json` |
| Git forges | `playground_github.py`, `playground_gitlab.py`, `playground_git_push.py` | Clone, push, GitHub PR, GitLab MR |
| Engine session | `engine_session.py`, `godot_bridge.py` | Headless Godot TCP bridge; optional Godot MCP adapter |
| Engine adapter | `engine_adapter.py` | Leaf host contract for Godot/Unity discover and preflight. Godot `run(live=True)` wraps `godot_first.runtime_verify`. Unity `run(live=True)` wraps `unity_runtime.runtime_verify` (batchmode, not Play). Not Studio I/O. Registry `AdapterSpec.name` stays `"EngineAdapter"` (Godot-only). |
| Context budget | `context_budget.py` | Leaf window accountant. Char/4 estimate; resolve window (request → `DAEDALUS_CONTEXT_WINDOW` → static table); observational `account()`; optional `pack(trim=False)`. Does not replace `context_assembler`, compaction, the scratch-journal pause, or USD `run_budget`. Not on Run. Not Studio I/O. |
| Memory provenance | `memory_provenance.py` | Leaf observational labeler. Maps already-retrieved memory rows to `source_id` + kind/origin (metadata first, then prefix). `ep:` collision is documented via `collection` — ids are not migrated. Does not rewrite Chroma / Mem0 / `capture_*`. Not wired to assembler or ContextBudget. Not on Run. Not Studio I/O. |
| OpenTelemetry | `otel.py` | Leaf tracer/meter setup. `DAEDALUS_OTEL` off is a no-op and does not set the global provider. Memory exporter for tests; OTLP gRPC when an endpoint is set. `annotate()` copies an allowlist only (run/session + later ContextBudget / MemoryProvenance keys). Not wired to agent, ToolLoop, or FastAPI. Not on Run. Not Studio I/O. Do not set `CHROMA_OTEL_*`. |
| Browser | `playground_browser.py`, `browser_session.py` | Smoke + interactive session |
| Playground | `playground*.py`, `game_*.py` | static, vite, phaser-via-vite, expo, unity, godot; Capacitor wraps vite/static `dist/` |
| Autopilot | `builder_runtime.py` + `kernel/full_autopilot.py` | Kernel loop |
| Self-improve | `self_improve.py` | Gated skills; patches wait for Approve |
| Evolution | `evolution.py` | Opt-in idle gardener |
| Kernel | `daedalus/kernel/` | plan → code → test → verify |
| HTTP | `app/main.py` | Studio, `/admin/daedalus/*`, health, chat, playground API |
| Windows | `app/desktop.py`, `app/windows_gui.py`, `packaging/windows/` | Start Menu; WebView2 / Tk |
| UI | `ui/` | Command Center templates and static files |
| Store | `daedalus/store.py` | SQLite |

## Paths

`daedalus/paths.py` — repo / product / data / playground roots.

Defaults:

| What | Path |
|---|---|
| Code | git clone root |
| SQLite + Chroma + proposals | `$DAEDALUS_DATA` → `data/daedalus/` |
| Projects | `playground/projects/{slug}/` |
| Run worktrees | `$DAEDALUS_DATA/worktrees/{run_id}/` — opt-in `DAEDALUS_WORKTREE=1` |
| Pre-edit baselines | `$DAEDALUS_DATA/baselines/{run_id}/` — optional persist from `BaselineVerifier` |
| Context budget reports | `$DAEDALUS_DATA/context/{run_id}.json` — optional persist from `ContextBudget` |
| Memory provenance reports | `$DAEDALUS_DATA/memory/{run_id}.json` — optional persist from `MemoryProvenance` |
| OpenTelemetry reports | `$DAEDALUS_DATA/otel/{run_id}.json` — optional persist from `OpenTelemetry` |
| Docker data | `/var/lib/daedalus` |
| Windows data | `%LOCALAPPDATA%\Daedalus` |

## Game doctrine

- Asset gate: generated `assets/*.png` must be referenced (`load.image` / `<img>`).
- Verify: smoke + browser + scenario bot (`playtest/scenario.json`, pixel-diff, FPS/memory) + playtest personas + quality critic.
- Unity is scaffold + Git, not an in-cloud Editor. Native `engine` on a Unity slug uses `DAEDALUS_UNITY_MCP` (local Editor plugin) or `UNITY_PATH` batchmode (import ≠ Play). Without either it refuses honestly. We do not vendor Unity. Gate `unity-check` does not block guided racing.
- Capacitor ship (`playground_capacitor.py`): wrap an existing vite/static/vite-react `dist/` as iOS+Android. Same canvas. Not a new kind. Ionic UI is optional for app chrome. Cloud does not run Xcode or Android Studio. PakePlus-iOS remains an iOS-only fallback under `hybrid_mobile`.
- Dissection: analyze a reference (link, name, git, or files in Play Lab) and inject a specimen into GDD/planner/builder. Do not copy proprietary assets or code.
- Play Lab (`$DAEDALUS_DATA/labs/{id}/`): jailed install tree + local HTTP. Optional SmolVM guest browser (not vendored). Optional Docker. Not a Windows guest and not Steam.
- Godot 4 (`godot_first.py`, `playground_godot_scaffold.py`, `playground_godot3d_scaffold.py`, `godot_tools.py`, `godot_bridge.py`): text `.tscn`/GDScript. Kind `godot` never falls to Vite/HTML. Pipeline order: preflight → contract → context → slice → runtime → visual → review → export → publish. Skip is fail. Preview HTML is not Play. When `GODOT_BIN` exists, a TCP autoload (`DaedalusBridge`) is the playtest. Optional `DAEDALUS_GODOT_MCP` adapter; we do not vendor an Editor. A campaign `world/` kit waits until one small slice is green, unless effort is epic. Godot is the EngineAdapter of the shared capability pipeline — not the only adapter. Leaf `engine_adapter.py` unifies observational discover/preflight; Godot `run(live=True)` wraps `godot_first.runtime_verify`; Unity `run(live=True)` wraps `unity_runtime.runtime_verify` (batchmode import, not Play).
- Capability kernel (`capability_registry.py`, `capability.py`, `web_first.py`, `mobile_first.py`, `platform_acceptance.py`): registry is the only source of truth for platforms `web` | `godot` | `expo` | `capacitor`. One order (Intent → Plan → Context → Build → Verify → Polish → Export → Publish) with per-target checks. Web = Chromium. Godot = Godot 4.3. Expo/Capacitor = emulator/device (`device_smoke`). Missing runner is FAIL. Unknown platforms fail closed — never Vite/HTML. Native `production({action: pipeline})` collects evidence; `production({action: qa})` returns the fail-closed Platform Acceptance QA verdict bound to `project_id + target + revision + artifact_hash`. Skip is not success. No Godot→HTML or Expo-web-as-native success. Publish needs hashed immutable artifacts and `pass` evidence for the same revision. No parallel writers on the same file. Skill: `daedalus/skills/platform_acceptance_qa.md`.
- World kit (`world_kit.py`, `world_runtime.py`): epic games get a save schema, quest engine, dialogue runner, and gated level graph. `world({action: prove})` must pass. Empty JSON is not a campaign.
- Production layer (`production_layer.py`): M0–M3 is a labelled prototype. Store-ready needs `design/systems.json` with real files, persist, and pause/settings. Empty JSON is not a production layer. Native `production`. Gate `production-check` does not block guided racing before files exist.
- Honesty suite (`game_agent_bench.py`): `run_suite()` executes in-process checks against this harness (15 cases). `run_game_suite()` adds hermetic game_v1 tasks (10 Godot / 4 Unity / 3 Blender / 3 build) without live editors. CLI: `python -m daedalus.game_agent_bench` (honesty) or `--suite game|all`. `--live` / `DAEDALUS_BENCH_LIVE=1` is fail-closed and not a CI merge gate. Flags still record whether a comparison *could* be honest; the suite actually runs one — against Daedalus, not other products. Not a vendor bake-off. P0.22 (`scripts/p0_release_gate.py`) aggregates honesty + game_v1 snapshots plus pack/CI evidence; this module is not that gate. Human merge of the P0 MR stack and GitLab branch protection are owner settings, not this suite.
- Tool router (`tool_catalog.py` + `task_playbooks.py` + `tool_router.py`): one catalog of native tools, NEED_* leftovers, skills, and knowledge files. Each task kind (arcade, SLERP camera, crowd physics, Godot, Unity, epic world, harness patch) is a small graph of steps with required tools and docs. The kernel matches the owner ask *before* the LLM talks, injects a TASK PLAN, runs consult nodes (`spatial` advise, `game_canon` study, skill load), and exposes only that tool subset. `job_contract` scores the run against the plan. Inspect: `GET /api/v1/daedalus/tools` and `/tools/route?q=…`. RIPER `NEED_PLAYBOOK` texts in `playbooks.py` remain the optional process overlay.
- Build loop (`build_loop.py`): the router picks the graph, the loop walks it. Each native tool result is observed and mapped onto plan steps; when the model stops calling tools the kernel evaluates `job_contract` in strict mode plus the plan score. After a visual write it **opens the running game** (`visual_proof.py` — Chromium or Godot headless) and copies frames to `studio/proof/`; skip is not a pass. Tool results name the Playground slug path and a NEXT call (S3). `write_file` runs a syntax/arcade gate (`write_gate.py`, S4) — duplicate `let` is FAILED, not a silent write. Incomplete → PLAN PROGRESS becomes the next user turn; at most `DAEDALUS_BUILD_FIX_ROUNDS` (3) nudges. Stages S5–S6 (one build layer, leftover router merges) are tracked in `docs/BUILD_LOOP_PROGRESS.md`.
- Senior game tools (`math3d.py`, `spatial.py`, `game_lint.py`, `profiler.py`, `game_canon.py`, `game_engineer.py`): numeric referee for SLERP/rays/look-at; spatial advise/scan so collision is not O(N²); ruff/clang-tidy/Roslyn-or-heuristic lint; parse Unity/NUnit/Chrome/Tracy/FPS logs; study OSS trees (GLM, DirectXMath, Tracy, Milo Yip path, engine-architecture samples, Chop Chop wiki) under `$DAEDALUS_DATA/canon/` — never copy assets, ebook dumps, or the book into a Playground game. `chunk` splits C++ class/struct and markdown headings for RAG. Knowledge lives in `daedalus/knowledge/` (RAG) and `daedalus/skills/`. Chat, autopilot, Game Studio gates (`eng-check`), and the job contract inject the same contract so the tools actually run on a game build.
- Asset pipeline v2 (`atlas.py`, `sfx_kit.py`, `mesh_jobs.py`): 2D atlas/tileset/SFX/font with stdlib PNG (no Pillow). 3D jobs fail closed without keys; greybox is a cube, not a hero. Native tool `assets`.
- Blender jobs (`blender_jobs.py`, `python -m daedalus.blender_mcp`) **emit** a bpy script — they do not import `bpy` in the Daedalus process.
- Not: own engine, own 3D foundation model, five parallel Godot MCP servers, silent self-editing, “one sentence → finished game”.

## License boundary (MiroFish)

MiroFish-Offline is AGPL-3.0. `daedalus/mirofish_client.py` is an original HTTP client. Do not pull their backend into this process — keep it as a separate Docker stack if you use it at all.
