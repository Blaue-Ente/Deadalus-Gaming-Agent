# Daedalus / Дедал

<p align="center">
  <img src="assets/logo/daedalus.svg" width="128" height="128" alt="Daedalus mark: labyrinth and compass-bird" />
</p>

Independent coding agent. **Games first**, apps second, verify is part of building.

[![pipeline status](https://gitlab.com/Blaue-Ente/deadalus-agent-0/badges/main/pipeline.svg)](https://gitlab.com/Blaue-Ente/deadalus-agent-0/-/pipelines)
[![Latest Release](https://gitlab.com/Blaue-Ente/deadalus-agent-0/-/badges/release.svg)](https://gitlab.com/Blaue-Ente/deadalus-agent-0/-/releases)

Daedalus writes real files, runs a shell, builds games and apps, then **checks** that they work — smoke, browser, scenario bot, playtest. It is not an advice chatbot. It runs on a laptop, Docker, or a VPS, with SQLite and a direct LLM (cloud or local Ollama).

| | |
|---|---|
| Repository | https://gitlab.com/Blaue-Ente/deadalus-agent-0 |
| License | [MIT](LICENSE) |
| Python | 3.11+ |
| Studio | http://127.0.0.1:8787 |
| Package | `daedalus` · CLI `daedalus` |
| Mark | [assets/logo](assets/logo/README.md) |

---

## Get Daedalus on your PC

The GitLab tree **is** the product. Copy the whole repo (ZIP or `git clone`). Git is optional.

| How | Windows | Linux / macOS |
|---|---|---|
| Extra `.exe` option | `install.ps1` or `Daedalus-Setup.exe` → Start Menu **Daedalus.exe**. Private CPython is fetched if you have none. Not a PyInstaller one-file freeze. | — |
| Developer | Python 3.11+ venv (below) | Python 3.11+ venv (below) |
| Docker | `docker compose up --build` | same |

**ZIP (no git):** [deadalus-agent-0-main.zip](https://gitlab.com/Blaue-Ente/deadalus-agent-0/-/archive/main/deadalus-agent-0-main.zip) or **Code → Download source code**. Unzip, then:

```powershell
# Windows — Start Menu + Daedalus.exe (downloads CPython if PATH has no 3.11+)
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\packaging\windows\install.ps1 -DesktopShortcut
```

Build the wizard installer on a Windows PC: `.\packaging\windows\build-setup.ps1` → `dist\Daedalus-Setup.exe`.

Step-by-step: [docs/INSTALL.md](docs/INSTALL.md) · [packaging/windows/README.md](packaging/windows/README.md).

GitLab project avatar (not set by git): **Settings → General → Project avatar** ← `assets/logo/daedalus-avatar.png`.

---

## Contents

- [Get Daedalus on your PC](#get-daedalus-on-your-pc)
- [What Daedalus is](#what-daedalus-is)
- [What it can do](#what-it-can-do)
- [Requirements](#requirements)
- [Install](#install)
  - [Linux and macOS](#linux-and-macos)
  - [Windows (Start Menu)](#windows-start-menu)
  - [Docker](#docker)
- [First run](#first-run)
- [LLM — cloud and local](#llm--cloud-and-local)
- [Playground and Game Studio](#playground-and-game-studio)
- [Verify and ship](#verify-and-ship)
- [Offline atelier](#offline-atelier)
- [Evolution](#evolution)
- [CLI](#cli)
- [Tests and CI](#tests-and-ci)
- [Repository layout](#repository-layout)
- [Documentation](#documentation)
- [Security and contributing](#security-and-contributing)
- [License](#license)

---

## What Daedalus is

Daedalus is **one process**: FastAPI + Studio UI + the agent loop (plan → code → test → verify).

- Chat is human: a greeting gets an answer; “make a snake game” writes files.
- Tools are real: read/write, jailed shell, search, browser, memory.
- Games are **played**, not described. Phaser/canvas/Vite in the browser; Godot 4 for desktop 2D/3D; Unity is scaffold + local Editor.
- Self-improvement goes through **Approve** in Command Center — Daedalus does not silent-edit itself.

Data lives under `$DAEDALUS_DATA` (default `data/daedalus/` on the machine, `%LOCALAPPDATA%\Daedalus` on Windows, `/var/lib/daedalus` in Docker). Projects are `playground/projects/{slug}/`.

---

## What it can do

| Capability | Where |
|---|---|
| Chat (Bulgarian / English) | Studio `/` · `POST /api/v1/daedalus/chat` |
| Files and projects | Playground `playground/projects/{slug}` |
| Shell | Studio Shell · `POST /api/v1/daedalus/shell` (jail, secrets stripped from env) |
| Native tools | `read_file`, `write_file`, `run_command`, `web_search`, `web_fetch`, `web_read`, `generate_image`, `remember`, `code_search`, `browser`, `mcp_call`, `spawn_subagent`, `playtest`, `play_lab`, `dissect_game`, `engine`, `world`, `assets`, `skill`, `production`, `git_forge`, `math3d`, `game_lint`, `profiler`, `game_canon` |
| MCP | Studio → **Connectors** (`$DAEDALUS_DATA/mcp.json` + `secrets.env`) |
| Honesty suite | `python -m daedalus.game_agent_bench` · `GET /api/v1/daedalus/bench` — this harness, not a vendor bake-off |
| Local LLM | Connectors **Brain** (`/admin/daedalus/connectors#brain`) — Ollama wardrobe for the **whole process** (chat, workers, autopilot, CLI), not Studio-only. Or `DAEDALUS_PROVIDER=ollama` |
| Offline kit | `DAEDALUS_OFFLINE=1` / Studio toggle — no search/images; local model + capsules still work |
| Sub-agents | worker slots **and** `spawn_subagent` (files + shell, jailed) |
| Autopilot | kernel `full_autopilot` + Definition of Done + playtest |
| Browser | Playwright smoke **and** an interactive session (goto / click / type) |
| Memory | Chroma + preferences (“remember that…”) + learned skills |
| Images | Novita / OpenRouter · `generate_image` |
| Cloud LLM | AllToken / OpenRouter / Novita / NVIDIA |
| Games — browser | Phaser, canvas, Vite; 3D arcade / racing → Vite + Three.js WebGPU (`src/racer/`) |
| Games — Godot 4 | kind `godot`: `.tscn` + GDScript; 2D scaffold or 3D greybox room |
| Games — senior tools | `math3d` (SLERP / rays / look-at), `game_lint` (ruff / clang-tidy / Roslyn heuristics), `profiler` (NUnit / Chrome / FPS logs), `game_canon` (study OSS trees, never copy) |
| Dissection | name / URL / git / Play Lab install → specimen used as a **мостра** (loop, feel, stack — not a clone) |
| Play Lab | virtual computer: `$DAEDALUS_DATA/labs/{id}/` + local HTTP; optional SmolVM guest browser (live viewer); optional Docker |
| Meshes | `python -m daedalus.blender_mcp` or `blender_jobs.emit_job` (no `bpy` in the cloud) |
| Live publish | local `/apps/{slug}/` + Netlify / GitLab Pages / 1-hour preview |
| Capsule | zip for USB — `GET /api/v1/playground/projects/{slug}/capsule` |
| Windows app | Start Menu **Daedalus** (WebView2 / Tk) |
| Evolution | optional idle gardener; patches still wait for Approve |

Studio tab **World** shows Reach / playtest / MCP / dissection / computer / honesty suite.

---

## Requirements

**Required**

- Python 3.11 or newer
- At least one LLM: a cloud key **or** local Ollama / LM Studio / llama.cpp / vLLM

**Optional**

| For | What |
|---|---|
| Browser verify | `pip install -e ".[playwright]"` + `python -m playwright install chromium` |
| Windows window | `pip install -e ".[gui]"` (Edge WebView2) |
| Vite / Phaser games | Node.js 18+ and npm |
| Godot projects | [Godot 4.3](https://godotengine.org/download) on the owner’s machine |
| Blender jobs | [Blender 4.x](https://www.blender.org/download/) locally — Daedalus only **emits** a script |
| Docker | Docker Engine + Compose |
| Live publish | `NETLIFY_AUTH_TOKEN` and/or `GITLAB_TOKEN`, or Node (`npx`) for a 1-hour preview |

Daedalus does **not** ship 7 GB of weights. The wardrobe recommends a Qwen/coder size from RAM/VRAM; you pull the model.

---

## Install

Clone or unzip **this** repository. That folder is the product. Details: [docs/INSTALL.md](docs/INSTALL.md).

```bash
git clone https://gitlab.com/Blaue-Ente/deadalus-agent-0.git
cd deadalus-agent-0
```

### Linux and macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
# fill at least one key, or leave blank and use Ollama
export DAEDALUS_STANDALONE=1
python -m app.cli serve --port 8787
```

Binds `127.0.0.1` by default. Docker/VPS (`HOST=0.0.0.0`) needs `DAEDALUS_TOKEN` — see [SECURITY.md](SECURITY.md).

Equivalent after `pip install -e .`:

```bash
daedalus serve --host 127.0.0.1 --port 8787
```

Probe the LLM without the UI:

```bash
python -m app.cli ping
python -m app.cli models
```

Local smoke without Docker:

```bash
./scripts/smoke-local.sh
```

### Windows (Start Menu)

No Administrator. Extra option next to ZIP/git:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\packaging\windows\install.ps1 -DesktopShortcut
# wizard installer (build on Windows):  .\packaging\windows\build-setup.ps1
```

If Python 3.11+ is not on PATH, a **private** CPython is installed under the app folder (not on PATH). Start → **Daedalus** opens a window on http://127.0.0.1:8787 (WebView2, else Tk, else the browser).

| What | Where |
|---|---|
| `.env` and keys | `%LOCALAPPDATA%\Daedalus\` |
| Games and memory | `%LOCALAPPDATA%\Daedalus\` |
| `Daedalus.exe`, venv, private CPython | `%LOCALAPPDATA%\Programs\Daedalus\` |

[packaging/windows/README.md](packaging/windows/README.md)

### Docker

```bash
cp .env.example .env
# fill ALLTOKEN_API_KEY or OPENROUTER_API_KEY (or another key)
# Docker binds 0.0.0.0 — also set DAEDALUS_TOKEN
docker compose up --build
```

SQLite, Playground, and Chroma live in volume `daedalus-data` (`DAEDALUS_DATA=/var/lib/daedalus`).

Cloud (fly.io, Render, GitLab Registry): see [DEPLOY.md](DEPLOY.md).

---

## First run

Open http://127.0.0.1:8787 — that is **Studio** (chat, shell, wardrobe, mark).

Command Center (same process, same brain):

| Page | URL |
|---|---|
| Home | http://127.0.0.1:8787/admin/daedalus |
| Playground | http://127.0.0.1:8787/admin/daedalus/playground |
| Game Studio | http://127.0.0.1:8787/admin/daedalus/game-studio |
| Proposals (Approve) | http://127.0.0.1:8787/admin/daedalus/proposals |
| Skills | http://127.0.0.1:8787/admin/daedalus/skills |
| Connectors (brain, keys, MCP) | http://127.0.0.1:8787/admin/daedalus/connectors |
| OpenAPI | http://127.0.0.1:8787/docs |
| Health | http://127.0.0.1:8787/health |

LLM presets, workers, memory, tasks, and blockers share the same `/admin/daedalus/*` URLs. The brain is set once in Connectors — not a second form in Studio.

---

## LLM — cloud and local

Fill **at least one** in `.env` or Connectors (see [docs/ENV.md](docs/ENV.md)):

```
ALLTOKEN_API_KEY=
OPENROUTER_API_KEY=
NOVITA_API_KEY=
NVIDIA_API_KEY=
```

Local, no key — this is the brain for **all of Daedalus**, not only Studio games:

```bash
export DAEDALUS_PROVIDER=ollama
export DAEDALUS_MODEL=qwen2.5-coder:7b
# or click a recommended row in Connectors → Brain / Studio → Brain (pull + activate)
```

`python -m app.cli models` prints the machine class and what to pull.

OpenRouter **without** a chosen model still defaults to paid Claude Sonnet. For a $0 run, apply Studio preset **OpenRouter Free** (`or_free`) — models whose ids end in `:free` — or set `DAEDALUS_PROVIDER=openrouter` and `DAEDALUS_MODEL=poolside/laguna-s-2.1:free`. The `:free` pool is rate-limited; some ids return 429 or empty completions.

---

## Playground and Game Studio

Each project is `playground/projects/{slug}/` with a kind:

| Kind | For |
|---|---|
| `static` | HTML / CSS / JS with no build |
| `vite` | Vite + vanilla JS; Phaser/Pixi for 2D; Three.js WebGPU for 3D arcade / racer |
| `vite-react` | Vite + React 18 |
| `fastapi` | Python API |
| `streamlit` | Streamlit app |
| `node` | Node / Express |
| `expo` | Expo / React Native (web export is static) |
| `unity` | C# scaffold — open in Unity Hub locally |
| `godot` | Godot 4.3, text `.tscn` / GDScript; 2D or 3D greybox room |

**Game factory (current state)**

- Browser 3D / F-Zero class: Vite + Three.js, vertical slice, playable M0 in `src/racer/`. Phaser is not the 3D path.
- Godot 2D: `project.godot`, scene, player, Game autoload.
- Godot 3D: greybox room, CharacterBody3D, plate → door, camera, HUD; `studio/scene_index.json` + `studio/asset_contract.json`. Headless probe if `godot` is on PATH.
- Blender: emit-only jobs (`set_pivot`, `collision_hint`, GLB). The cloud process does not import `bpy`.
- Daedalus does not write its own engine, does not host a 3D foundation model, and does not run five Godot MCP bridges.

---

## Verify and ship

1. **Smoke** — compile / HTTP probe.
2. **Browser** — Chromium: blank screen, console, screenshot.
3. **Playtest** — 4–6 simulated players (Studio → World, or after autopilot). Fun gate: wayfinding, fail state, visible pickups.
4. **Ship**
   - **Capsule** — zip (USB / send a file). Static: unzip, open `index.html`.
   - **Local /apps/** — Studio “To /apps/”.
   - **Live** — public `https`: Netlify token → GitLab Pages → 1-hour Netlify preview. Static / vite / expo-web only.
   - **Git** — push to GitLab if `GITLAB_TOKEN` is set, else GitHub. Clone from either host. Open a GitHub Pull Request or GitLab Merge Request from Playground (🔀 PR / MR) or the `git_forge` tool. Tokens live in Studio → **Connectors**.
   - Unity and Godot: open the folder in the matching Editor.

---

## Offline atelier

```bash
export DAEDALUS_OFFLINE=1
# or the Studio toggle
```

Search, Jina, and image-gen refuse. Chat, files, shell, localhost browser, heuristic playtest, and capsules keep working. You need a local model in the wardrobe.

---

## Evolution

A separate mode (Studio tab, **off** by default). While the process is up — even with no open chat — a gardener uses spare CPU/RAM to:

1. Scan two rotating radar sources (agents, MCP, memory, local LLM).
2. Study one public GitHub repo (README + tree + license).
3. Write a journal and skill note under `$DAEDALUS_DATA`.
4. Optionally shallow-clone into `data/daedalus/evolution/research/` (never into `daedalus/`). AGPL/SSPL is refused.

It skips when the offline kit is on, load is high, RAM is tight, or autopilot is running. Harness patches still wait for **Approve**. `edit_modules` stays off until you turn it on.

```bash
# DAEDALUS_EVOLUTION=1    # force on at boot
# DAEDALUS_EVOLUTION=0    # kill switch
```

---

## CLI

After `pip install -e .` the command is `daedalus` (same as `python -m app.cli`).

| Command | What it does |
|---|---|
| `daedalus serve [--host] [--port] [--reload]` | HTTP Studio + API (default bind `127.0.0.1`) |
| `daedalus ping [--message …]` | One LLM call, no UI |
| `daedalus models` | Effective provider + probe of local backends |
| `daedalus desktop [--ui auto\|webview\|tk\|browser\|none]` | Window + local server |
| `daedalus computer` | Play Lab backends (local HTTP, optional SmolVM) |

---

## Tests and CI

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python3 -m pytest tests -q
```

GitLab CI (`.gitlab-ci.yml`) runs the same tests and builds a Docker image; when `CI_REGISTRY` is set it pushes `$CI_REGISTRY_IMAGE:latest`.

---

## Repository layout

```
deadalus-agent-0/
  daedalus/               agent package
    kernel/               plan → code → test → verify
    identity*.py          persona (games first)
    memory.py             Chroma session memory
    playground*.py        app/game builder
    agent.py              tool loop
    arcade_craft.py       3D browser racers
    blender_jobs.py       emit Blender scripts
    game_factory.py       Godot 3D room doctrine
  app/                    FastAPI, CLI, desktop
  ui/                     Command Center (HTML / JS / CSS)
  assets/logo/            mark (SVG, PNG, ICO)
  playground/projects/    working projects (gitignored)
  tests/                  pytest
  packaging/windows/      Start Menu installer
  scripts/smoke-local.sh  process smoke
  Dockerfile              + docker-compose.yml
```

---

## Documentation

| File | Topic |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Layers, paths, game doctrine |
| [DEPLOY.md](DEPLOY.md) | PC, Docker, Windows, fly.io, Render |
| [docs/INSTALL.md](docs/INSTALL.md) | Copy the repo, Windows `.exe` option, venv, Docker |
| [docs/ENV.md](docs/ENV.md) | Environment variables |
| [CONTRIBUTING.md](CONTRIBUTING.md) | How to contribute |
| [SECURITY.md](SECURITY.md) | How to report issues |
| [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | Participation rules |
| [CHANGELOG.md](CHANGELOG.md) | Version history |
| [AGENTS.md](AGENTS.md) | Short brief for coding agents in this repo |
| [packaging/windows/README.md](packaging/windows/README.md) | Windows app |
| [assets/logo/README.md](assets/logo/README.md) | Mark and GitLab avatar |
| [LICENSE](LICENSE) | MIT |

---

## Security and contributing

Vulnerabilities: see [SECURITY.md](SECURITY.md).  
Merge requests: see [CONTRIBUTING.md](CONTRIBUTING.md).  
Issues: [Issues](https://gitlab.com/Blaue-Ente/deadalus-agent-0/-/issues).

---

## License

[MIT](LICENSE) © 2026 Daedalus Contributors.

The optional MiroFish sidecar (if you run it yourself) is **AGPL-3.0**. Daedalus talks to it over HTTP only — that source does **not** enter this repo.
