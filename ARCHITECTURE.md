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
| Shell | `shell_center.py` | Argv-first runner; cwd jail; strip secrets. Not a container. |
| Owner auth | `owner_auth.py` | Bearer token on mutations; loopback bootstrap |
| Local LLM | `local_llm.py` + `llm_wardrobe.py` + `machine_profile.py` | Probe + hardware catalog |
| Offline | `offline.py` | Blocks network native tools |
| Addons | `addons.py` | MCP shelf → `mcp.json` |
| Connectivity | `connectivity.py` | Studio → Свързаност; `$DAEDALUS_DATA/secrets.env` |
| Capsule | `capsule.py` | Zip a playground project |
| Live | `live_publish.py` | `/apps/` + Netlify / GitLab Pages / 1h preview |
| Preferences | `preferences.py` | Owner speech → durable memory |
| Learned skills | `learned_skills.py` | Catalog first; pending drafts wait for Approve |
| DoD | `job_contract.py` | Code without evidence is not done |
| Reach | `reach.py` | Jina Reader + optional CLIs |
| Playtest | `playtest.py` | Judge personas; no 1M dataset |
| Dissection | `dissection.py` + `play_lab.py` | Specimen from name/URL/git/install; Play Lab is the VM |
| MiroFish | `mirofish_client.py` | HTTP-only to a sidecar — never vendor |
| MCP | `mcp_client.py` | JSON-RPC stdio/HTTP from `mcp.json` |
| Git forges | `playground_github.py`, `playground_gitlab.py`, `playground_git_push.py` | Clone, push, GitHub PR, GitLab MR |
| Engine session | `engine_session.py`, `godot_bridge.py` | Headless Godot TCP bridge; optional Godot MCP adapter |
| Browser | `playground_browser.py`, `browser_session.py` | Smoke + interactive session |
| Playground | `playground*.py`, `game_*.py` | static, vite, phaser-via-vite, expo, unity, godot |
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
| Docker data | `/var/lib/daedalus` |
| Windows data | `%LOCALAPPDATA%\Daedalus` |

## Game doctrine

- Asset gate: generated `assets/*.png` must be referenced (`load.image` / `<img>`).
- Verify: smoke + browser + scenario bot (`playtest/scenario.json`, pixel-diff, FPS/memory) + playtest personas + quality critic.
- Unity is scaffold + Git, not an in-cloud Editor. Native `engine` on a Unity slug uses `DAEDALUS_UNITY_MCP` (local Editor plugin) or `UNITY_PATH` batchmode (import ≠ Play). Without either it refuses honestly. We do not vendor Unity. Gate `unity-check` does not block guided racing.
- Arcade craft (`arcade_craft.py` + `studio_shelf.py` + `playground_racer_scaffold.py`): 3D arcade uses Vite + Three.js WebGPU/TSL. A racing brief boots a playable M0 in `src/racer/` (not Phaser).
- Dissection: analyze a reference (link, name, git, or files in Play Lab) and inject a specimen into GDD/planner/builder. Do not copy proprietary assets or code.
- Play Lab (`$DAEDALUS_DATA/labs/{id}/`): jailed install tree + local HTTP. Optional Docker. Not QEMU Windows and not Steam.
- Godot 4 (`playground_godot_scaffold.py`, `playground_godot3d_scaffold.py`, `godot_tools.py`, `godot_bridge.py`): text `.tscn`/GDScript. When `GODOT_BIN` exists, a TCP autoload (`DaedalusBridge`) is the playtest — scene tree, input, errors — not only file DoD. Optional `DAEDALUS_GODOT_MCP` adapter; we do not vendor an Editor.
- World kit (`world_kit.py`, `world_runtime.py`): epic games get a save schema, quest engine, dialogue runner, and gated level graph. `world({action: prove})` must pass. Empty JSON is not a campaign.
- Production layer (`production_layer.py`): M0–M3 is a labelled prototype. Store-ready needs `design/systems.json` with real files, persist, and pause/settings. Empty JSON is not a production layer. Native `production`. Gate `production-check` does not block guided racing before files exist.
- Asset pipeline v2 (`atlas.py`, `sfx_kit.py`, `mesh_jobs.py`): 2D atlas/tileset/SFX/font with stdlib PNG (no Pillow). 3D jobs fail closed without keys; greybox is a cube, not a hero. Native tool `assets`.
- Blender jobs (`blender_jobs.py`, `python -m daedalus.blender_mcp`) **emit** a bpy script — they do not import `bpy` in the Daedalus process.
- Not: own engine, own 3D foundation model, five parallel Godot MCP servers, silent self-editing, “one sentence → finished game”.

## License boundary (MiroFish)

MiroFish-Offline is AGPL-3.0. `daedalus/mirofish_client.py` is an original HTTP client. Do not pull their backend into this process — keep it as a separate Docker stack if you use it at all.
