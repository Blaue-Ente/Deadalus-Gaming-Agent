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
| Native tools | files, shell, search, fetch, Jina `web_read`, image gen, remember |
| Shell command center | Studio Shell · `POST /api/v1/daedalus/shell` |
| Local LLM | Studio **Гардероб** — хардуерен профил + Ollama pull + превключване; или `DAEDALUS_PROVIDER=ollama` |
| Offline kit | `DAEDALUS_OFFLINE=1` / Studio бутон — без Jina/търсене/картинки, с локален модел и капсули |
| Sub-agents | worker slots (no tools) **and** `spawn_subagent` (files + shell, jailed) |
| Files + projects | Playground `playground/projects/{slug}` |
| Autopilot | kernel `full_autopilot` + Definition of Done + playtest |
| Browser | Playwright smoke **and** interactive session (goto/click/type/extract) |
| MCP | `$DAEDALUS_DATA/mcp.json` + Studio addon shelf |
| Memory | Chroma + owner preferences (`запомни, че…`) + learned skills |
| Image gen | Novita / OpenRouter · native `generate_image` |
| LLM (cloud) | AllToken / OpenRouter / Novita / NVIDIA |

Standalone persona is a **compact tool contract** (fits local/small models). Set
`DAEDALUS_FULL_PERSONA=1` for the full ADN/Iyveris prompt. Next to ADN, the full
persona is always used.

## Integrations (adapters, not vendored dumps)

Three upstream projects plug in as **capability layers**. Daedalus does not
copy their trees.

| Upstream | License | What Daedalus does |
|---|---|---|
| [Agent Reach](https://github.com/Panniantong/Agent-Reach) | MIT | Jina Reader `web_read`; optional `yt-dlp` / `gh` / `agent-reach doctor` |
| [MatrAIx Persona-8B](https://github.com/MatrAIx-ai/MatrAIx-Persona-8B) | MIT | Tiny player panel + playtest after game builds (no 1M dataset) |
| [MiroFish-Offline](https://github.com/nikmcfly/MiroFish-Offline) | **AGPL-3.0** | HTTP client to `MIROFISH_URL` (default `http://127.0.0.1:5001`). Run their Docker stack yourself. **Do not copy that source into this MIT repo.** |

Studio tab **Свят** shows Reach / MiroFish / MCP status and a Playtest button.

## Ship a game (test → publish)

1. **Smoke** — compile / HTTP probe (`playground_runner.smoke_test`).
2. **Browser** — Chromium render-and-see (blank screen, console, screenshot).
3. **Playtest** — 4–6 simulated players (Studio → Свят, or after autopilot). Fun gate: wayfinding, fail, visible pickups.
4. **Arcade craft** — 3D / F-Zero-class games follow `arcade_craft` (vertical slice, Three.js WebGPU, Tripo/Meshy/Hunyuan for hero meshes). Phaser is the 2D default, not the 3D one. Racing Vite projects boot a playable M0 (`src/racer/`) instead of a hello-world page.
5. **Ship**
   - **Капсула** — `GET /api/v1/playground/projects/{slug}/capsule` → zip (USB / прати файл). Static: unzip, open `index.html`.
   - **Локално /apps/** — Studio „Към /apps/“ (same Daedalus process).
   - **Наживо** — Studio button: local `/apps/` plus a public `https` (Netlify token → GitLab Pages → 1-hour Netlify preview). Static / vite / expo-web only.
   - **Git** — `<NEED_PLAYGROUND_PUSH: slug>` (GitLab if `GITLAB_TOKEN`, else GitHub).
   - Unity stays Git + local Editor. Expo web can publish as static.

## Offline atelier (local LLM wardrobe)

Daedalus does **not** ship 7GB weights. It **recommends** a Qwen/coder size from RAM/VRAM, then:

```bash
# optional: install Ollama once
# Studio → LLM → click a recommended row (pull + activate)
python -m app.cli models    # prints machine class + recommendations
```

Toggle **Офлайн кит** so search/Jina/image-gen refuse. Chat, files, shell, localhost browser, heuristic playtest, and capsules keep working.

## Evolution (opt-in idle gardener)

Self-improve already exists: Daedalus **proposes** patches, you **Approve** in Command Center. That is not silent self-editing.

**Еволюция** is a separate mode (Studio tab, default **off**). While the process is up — even with no open chat — a gardener uses spare CPU/RAM to:

1. Scan two rotating radar sources (GitHub/web: agents, MCP, memory, local LLM).
2. Study one new public GitHub repo via the API (README + tree + license).
3. Write a journal + a learned-skill note under `$DAEDALUS_DATA`.
4. Optionally shallow-clone into `data/daedalus/evolution/research/` (never into `daedalus/`). AGPL/SSPL is refused.

It **skips** when offline kit is on, load is high, RAM is tight, or autopilot is running. Harness patches still wait for Approve. `edit_modules` stays off unless you turn it on; auto-apply never includes it.

```bash
# optional: force on at boot (Studio toggle still writes settings.json)
# DAEDALUS_EVOLUTION=1
# DAEDALUS_EVOLUTION=0   # kill switch
```

## Windows app (Start Menu)

Per-user install — **no Administrator**. Python 3.11+ on PATH, then:

```powershell
.\packaging\windows\install.ps1
# optional: -DesktopShortcut  -Startup
```

Start → **Дедал** opens Studio at http://127.0.0.1:8787. Data lives in
`%LOCALAPPDATA%\Daedalus` (`.env`, memory, games). Or compile
`packaging\windows\Daedalus.iss` with Inno Setup → `dist\Daedalus-Setup.exe`.

Details: `packaging/windows/README.md`.

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

GitLab `main` may be protected; you need **Maintainer** (or Owner) to merge.
The live merge request is on **GitLab**, not GitHub:

https://gitlab.com/Blaue-Ente/deadalus-agent-0/-/merge_requests/1

If the Merge button is disabled: you are on the wrong site (GitHub stub
`Blaue-Ente/Deadalus-Projekt-` is unrelated), the MR was still Draft, or your
account is Developer-only on a protected `main`. Pipeline green ≠ permission
to merge. ADN stays in `Data-Asset-Hub`. You can rename the GitLab path to
`Daedalus` in project settings.

See `MIGRATION.md` and `DEPLOY.md`.
