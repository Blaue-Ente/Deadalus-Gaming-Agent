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
| Identity | `daedalus/identity.py` + `identity_standalone.py` | Games-first preamble when standalone |
| Memory | `memory.py`, `knowledge.py` | Chroma collections under `data/daedalus/` |
| Agent loop | `agent.py`, `tools.py`, `workers.py` | NEED_* markers, parallel workers |
| Playground | `playground*.py`, `game_*.py` | Kinds: static, vite, phaser, expo, unity |
| Autopilot | `builder_runtime.py` + `kernel/full_autopilot.py` | ADN `admin.py` Phase 0–6 when hosted; kernel loop when standalone |
| Self-improve | `self_improve.py` | Gated skills; may patch `Daedalus/` and ADN |
| Kernel | `daedalus/kernel/` | Portable plan→code→test from Daedalus-Agent |
| HTTP | `Daedalus/app/main.py` | Studio, `/admin/daedalus/*` Command Center, health, chat, playground API, `/api/iyveris` |
| UI copy | `Daedalus/ui/` | ADN templates copied, not moved |
| Store | `daedalus/store.py` | SQLite when standalone; ADN Postgres otherwise |
| ADN shim | `argos_data_nexus/modules/daedalus/` | Import alias only |

## Ports

`daedalus/host.py` — lazy ADN imports (db pool, storage, auth, MeshRouter).
`daedalus/paths.py` — repo / product / data / playground roots.

## Games doctrine

- Asset gate: generated `assets/*.png` must be referenced (`load.image` / `<img>`).
- Verify: smoke + browser render-and-see + quality critic.
- Unity is scaffold + Git, not in-cloud Editor.
