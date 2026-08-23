# Daedalus / Дедал

Independent coding agent — **games first**, apps second, self-improvement always.

This directory is the Daedalus **product**. It can run:

1. **Next to ArgosDataNexus** in this monorepo (ADN keeps its admin UI + Postgres).
2. **Alone** — laptop, Docker, or cloud — with SQLite + a direct OpenAI-compatible LLM.

ADN is an optional peer on the Iyveris mesh, not the agent's identity.

```
Daedalus/                 this product (also the standalone repo root after export)
  daedalus/               Python package
    kernel/               plan → code → test → verify
    identity*.py          games-first persona
    memory.py             Chroma session memory
    playground*.py        app/game builder
    agent.py              tool loop (files, search, workers, git, sandbox)
  app/main.py             FastAPI + Studio UI + mesh peer
  ui/                     copied Command Center (ADN templates stay in ADN)
  Dockerfile              local PC + cloud
```

## vs Hermes-class agents

Daedalus is built for the same job as [Hermes Agent](https://github.com/nousresearch/hermes-agent):
a long-horizon tool loop that writes real files, runs a shell, looks at the web,
and iterates until the thing works. Extra: **game factory**, **Playground**,
**autopilot verify**, **preference memory**, and **Iyveris mesh**.

| Capability | Daedalus |
|---|---|
| Chat (human speech) | Studio `/` · `POST /api/v1/daedalus/chat` |
| Native tools | files, shell, search, fetch, image gen, remember (NEED_* fallback) |
| Shell command center | Studio Shell · `POST /api/v1/daedalus/shell` |
| Local LLM | Ollama / LM Studio / llama.cpp / vLLM (`DAEDALUS_PROVIDER=ollama`) |
| Sub-agents | worker slots (fast / balanced / deep) |
| Files + projects | Playground `playground/projects/{slug}` |
| Autopilot | kernel `full_autopilot` + programmatic Definition of Done |
| Browser verify | Playwright smoke for visual kinds |
| Memory | Chroma + owner preferences (`запомни, че…`) + learned skills |
| Image gen | Novita / OpenRouter · native `generate_image` |
| LLM (cloud) | AllToken / OpenRouter / Novita / NVIDIA |

## Quick start (PC)

```bash
cd Daedalus
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
export DAEDALUS_STANDALONE=1
export ALLTOKEN_API_KEY=sk-at-...   # or OPENROUTER_API_KEY / NOVITA_API_KEY
python -m app.cli serve --port 8787
```

Check the LLM key without starting the UI:

```bash
python -m app.cli ping
python -m app.cli models    # probe Ollama / LM Studio / llama.cpp / vLLM
```

Open http://localhost:8787 — Studio (chat + shell + local LLM + logo).

Full Command Center (copied ADN UI):

- http://localhost:8787/admin/daedalus
- http://localhost:8787/admin/daedalus/playground
- http://localhost:8787/admin/daedalus/game-studio

LLM presets, workers, memory, tasks, and blockers are served from the same
`/admin/daedalus/*` URLs. Image gen uses Novita or OpenRouter when those keys
are set (no ADN MeshRouter).

```bash
# Docker
cp .env.example .env   # fill a key
docker compose up --build
```

SQLite, Playground files, and Chroma memory all live on the `daedalus-data`
volume (`DAEDALUS_DATA=/var/lib/daedalus`).

Without Docker, from this directory:

```bash
./scripts/smoke-local.sh
```

## Cloud

Same container. Set `ALLTOKEN_API_KEY` (or another provider key) and persist
`/var/lib/daedalus`. See `DEPLOY.md` for fly.io, Render, and GitLab Registry.

## Next to ADN (do not break ADN)

Leave `DAEDALUS_STANDALONE` **unset** in the ADN process. Imports stay:

```python
from argos_data_nexus.modules.daedalus import get_daedalus
```

Admin UI remains at ADN `/admin/daedalus/*`. The shim points at this package.
Never move `admin.py` playground routes out of ADN.

## What this agent is for

1. **Games** — Phaser, canvas, Vite, Unity scaffold, Expo. Real assets via
   `load.image` / `add.image`, not CSS-gradient placeholders.
2. **Applications** — FastAPI, React/Vite, Streamlit, static.
3. **Self-improvement** — proposals with owner-gated skills.

## Separate GitHub / GitLab repo

Standalone product remote (clone this for laptop/Docker/cloud — not the ADN monorepo):

https://gitlab.com/Blaue-Ente/deadalus-agent-0

```bash
git clone https://gitlab.com/Blaue-Ente/deadalus-agent-0.git
cd deadalus-agent-0
cp .env.example .env   # fill ALLTOKEN_API_KEY or OPENROUTER_API_KEY
docker compose up --build
```

Re-export from the ADN checkout:

```bash
GITLAB_TOKEN=glpat-… ./scripts/publish-standalone-repo.sh \
  https://gitlab.com/Blaue-Ente/deadalus-agent-0.git --force
```

GitLab `main` may be protected; Owner can unprotect briefly for `--force`.
ADN stays in `Data-Asset-Hub`. Do not use GitHub `Blaue-Ente/Deadalus-Projekt-`
(unrelated stub). You can rename the GitLab path to `Daedalus` in project settings.

See `MIGRATION.md` and `DEPLOY.md`.
