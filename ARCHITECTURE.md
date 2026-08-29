# Daedalus architecture

Daedalus is **one product**: coding agent + Studio + Playground. One FastAPI process, port **8787**, SQLite, a direct OpenAI-compatible LLM.

```
  Owner (BG / EN)  ·  Telegram bot (paired)
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
| Agent loop | `agent.py`, `native_tools.py`, `tools.py`, `workers.py`, `subagents.py` | Native tools by default; jailed sub-agents |
| Shell | `shell_center.py` | Argv-first runner; cwd jail; strip secrets. Not a container. |
| Owner auth | `owner_auth.py` | Bearer token on mutations; loopback bootstrap |
| Local LLM | `local_llm.py` + `llm_wardrobe.py` + `machine_profile.py` | Probe + hardware catalog |
| Offline | `offline.py` | Blocks network native tools |
| Addons | `addons.py` | MCP shelf → `mcp.json` |
| Capsule | `capsule.py` | Zip a playground project |
| Live | `live_publish.py` | `/apps/` + Netlify / GitLab Pages / 1h preview |
| Preferences | `preferences.py` | Owner speech → durable memory |
| Learned skills | `learned_skills.py` | Failure/success → SKILL.md |
| DoD | `job_contract.py` | Code without evidence is not done |
| Reach | `reach.py` | Jina Reader + optional CLIs |
| Playtest | `playtest.py` | Judge personas; no 1M dataset |
| MiroFish | `mirofish_client.py` | HTTP-only to a sidecar — never vendor |
| MCP | `mcp_client.py` | JSON-RPC stdio/HTTP from `mcp.json` |
| Browser | `playground_browser.py`, `browser_session.py` | Smoke + interactive session |
| Playground | `playground*.py`, `game_*.py` | static, vite, phaser-via-vite, expo, unity, godot |
| Autopilot | `builder_runtime.py` + `kernel/full_autopilot.py` | Kernel loop |
| Self-improve | `self_improve.py` | Gated skills; patches wait for Approve |
| Evolution | `evolution.py` | Opt-in idle gardener |
| Messaging | `messaging.py`, `telegram_gateway.py` | Telegram bot → same `agent.chat`; pairing / allowlist |
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
- Verify: smoke + browser render-and-see + playtest + quality critic.
- Unity is scaffold + Git, not an in-cloud Editor.
- Arcade craft (`arcade_craft.py` + `studio_shelf.py` + `playground_racer_scaffold.py`): 3D arcade uses Vite + Three.js WebGPU/TSL. A racing brief boots a playable M0 in `src/racer/` (not Phaser).
- Godot 4 (`playground_godot_scaffold.py`, `playground_godot3d_scaffold.py`, `godot_tools.py`): text `.tscn`/GDScript, local Editor. 3D M0 is a greybox room with a plate and a door.
- Blender jobs (`blender_jobs.py`, `python -m daedalus.blender_mcp`) **emit** a bpy script — they do not import `bpy` in the Daedalus process.
- Not: own engine, own 3D foundation model, five parallel Godot MCP servers, silent self-editing, “one sentence → finished game”.

## License boundary (MiroFish)

MiroFish-Offline is AGPL-3.0. `daedalus/mirofish_client.py` is an original HTTP client. Do not pull their backend into this process — keep it as a separate Docker stack if you use it at all.
