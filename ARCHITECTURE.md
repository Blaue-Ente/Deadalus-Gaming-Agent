# Daedalus architecture

Daedalus is an **independent coding agent**. ADN is an optional host for LLM
routing, Postgres, and the current admin UI — not the agent's identity.

```
                 ┌─────────────────────────────────────┐
  Owner          │  Daedalus product                    │
  (BG / EN)      │  memory · persona · playground       │
                 │  autopilot · self-improve · kernel   │
                 └───────────┬───────────┬──────────────┘
                             │           │
                    LLM ports│           │ Iyveris mesh
                             ▼           ▼
                 OpenAI-compat     ADN / sister apps
                 AllToken, OR,     (equal peers)
                 Novita, NVIDIA
```

## Layers

| Layer | Location | Notes |
|-------|----------|--------|
| Identity | `daedalus/identity.py` + `identity_standalone.py` | Compact tool contract when standalone; full Iyveris persona next to ADN |
| Memory | `memory.py`, `knowledge.py` | Chroma collections under `data/daedalus/` |
| Agent loop | `agent.py`, `native_tools.py`, `tools.py`, `workers.py`, `subagents.py` | Native tools by default; NEED_* fallback; tool-using sub-agents |
| Shell | `shell_center.py` | Jail + secret stripping; Studio command center |
| Local LLM | `local_llm.py` + `llm_wardrobe.py` + `machine_profile.py` | Probe backends; hardware-aware catalog; persist active model |
| Offline | `offline.py` | Blocks network native tools; wardrobe forces local |
| Addons | `addons.py` | Curated MCP shelf → `mcp.json` |
| Capsule | `capsule.py` | Zip a playground project for USB / file send |
| Live publish | `live_publish.py` | /apps/ + Netlify / GitLab Pages / 1h preview |
| Preferences | `preferences.py` | Owner speech → durable memory |
| Learned skills | `learned_skills.py` | Failure/success → SKILL.md |
| DoD | `job_contract.py` | Coding work is not complete without evidence |
| Reach | `reach.py` | Jina Reader + optional CLIs (Agent Reach pattern) |
| Playtest | `playtest.py` | MatrAIx-style personas; no 1M dataset |
| MiroFish | `mirofish_client.py` | HTTP-only to AGPL sidecar — never vendor |
| MCP | `mcp_client.py` | JSON-RPC stdio/HTTP from `mcp.json` |
| Browser | `playground_browser.py`, `browser_session.py` | Smoke verify + interactive session |
| Playground | `playground*.py`, `game_*.py` | Kinds: static, vite, phaser, expo, unity |
| Autopilot | `builder_runtime.py` + `kernel/full_autopilot.py` | ADN `admin.py` Phase 0–6 when hosted; kernel loop when standalone |
| Self-improve | `self_improve.py` | Gated skills; may patch `Daedalus/` and ADN |
| Evolution | `evolution.py` | Opt-in idle gardener; radar + GitHub study; proposals stay pending |
| Kernel | `daedalus/kernel/` | Portable plan→code→test from Daedalus-Agent |
| HTTP | `Daedalus/app/main.py` | Studio, `/admin/daedalus/*` Command Center, health, chat, playground API, `/api/iyveris` |
| Windows | `app/desktop.py`, `app/windows_gui.py`, `packaging/windows/` | Per-user Start Menu app; window via WebView2 / Tk; `daedalus desktop` |
| UI copy | `Daedalus/ui/` | ADN templates copied, not moved |
| Store | `daedalus/store.py` | SQLite when standalone; ADN Postgres otherwise |
| ADN shim | `argos_data_nexus/modules/daedalus/` | Import alias only |

## Ports

`daedalus/host.py` — lazy ADN imports (db pool, storage, auth, MeshRouter).
`daedalus/paths.py` — repo / product / data / playground roots.

## Games doctrine

- Asset gate: generated `assets/*.png` must be referenced (`load.image` / `<img>`).
- Verify: smoke + browser render-and-see + playtest personas + quality critic.
- Unity is scaffold + Git, not in-cloud Editor.

## License boundary (MiroFish)

MiroFish-Offline is AGPL-3.0. `daedalus/mirofish_client.py` is an original HTTP
client. Shipping or linking their backend inside this process would taint the
MIT tree — keep it as a separate Docker/compose stack.
