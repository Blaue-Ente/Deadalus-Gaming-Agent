# Дедал / Daedalus

Независим coding агент. **Игри първо**, приложения второ, проверката е част от строежа.

Independent coding agent. **Games first**, apps second, verify is part of building.

[![pipeline status](https://gitlab.com/Blaue-Ente/deadalus-agent-0/badges/main/pipeline.svg)](https://gitlab.com/Blaue-Ente/deadalus-agent-0/-/pipelines)
[![Latest Release](https://gitlab.com/Blaue-Ente/deadalus-agent-0/-/badges/release.svg)](https://gitlab.com/Blaue-Ente/deadalus-agent-0/-/releases)

Дедал пише реални файлове, пуска shell, строи игри и приложения, после **проверява** че работят — smoke, браузър, playtest. Не е чатбот за съвети. Работи на лаптоп, Docker или облак, с SQLite и директен LLM (облак или локален Ollama).

| | |
|---|---|
| Репозиторий | https://gitlab.com/Blaue-Ente/deadalus-agent-0 |
| Лиценз | [MIT](LICENSE) |
| Python | 3.11+ |
| Studio | http://127.0.0.1:8787 |
| Пакет | `daedalus` · CLI `daedalus` |

---

## Съдържание

- [Какво е Дедал](#какво-е-дедал)
- [Какво може](#какво-може)
- [Изисквания](#изисквания)
- [Инсталация](#инсталация)
  - [Linux и macOS](#linux-и-macos)
  - [Windows (Start Menu)](#windows-start-menu)
  - [Docker](#docker)
- [Първо стартиране](#първо-стартиране)
- [LLM — облак и локално](#llm--облак-и-локално)
- [Playground и Game Studio](#playground-и-game-studio)
- [Проверка и публикуване](#проверка-и-публикуване)
- [Офлайн ателие](#офлайн-ателие)
- [Еволюция](#еволюция)
- [CLI](#cli)
- [Тестове и CI](#тестове-и-ci)
- [Структура на репото](#структура-на-репото)
- [Документация](#документация)
- [Сигурност и принос](#сигурност-и-принос)
- [Лиценз](#лиценз)

---

## Какво е Дедал

Дедал е **един процес**: FastAPI + Studio UI + агентски цикъл (план → код → тест → проверка).

- Чатът е човешки: поздрав и въпрос получават отговор; „направи змия“ пише файлове.
- Инструментите са реални: четене/запис, shell в jail, търсене, браузър, памет.
- Игрите се **играят**, не се описват. Phaser/canvas/Vite за браузър; Godot 4 за десктоп 2D/3D; Unity е scaffold + локален Editor.
- Самоподобрението минава през **Approve** в Command Center — Дедал не редактира себе си мълчаливо.

Данните живеят под `$DAEDALUS_DATA` (по подразбиране `data/daedalus/` на машината, `%LOCALAPPDATA%\Daedalus` на Windows, `/var/lib/daedalus` в Docker). Проектите са в `playground/projects/{slug}/`.

---

## Какво може

| Възможност | Къде |
|---|---|
| Чат (български / английски) | Studio `/` · `POST /api/v1/daedalus/chat` |
| Файлове и проекти | Playground `playground/projects/{slug}` |
| Shell | Studio Shell · `POST /api/v1/daedalus/shell` (jail, без тайни в env) |
| Нативни инструменти | `read_file`, `write_file`, `run_command`, `web_search`, `web_fetch`, `web_read`, `generate_image`, `remember`, `code_search`, `browser`, `mcp_call`, `spawn_subagent`, `playtest` |
| Локален LLM | Studio **Гардероб** — хардуерен профил + Ollama pull; или `DAEDALUS_PROVIDER=ollama` |
| Офлайн кит | `DAEDALUS_OFFLINE=1` / бутон в Studio — без търсене/картинки, с локален модел и капсули |
| Подагенти | worker слотове **и** `spawn_subagent` (файлове + shell, в jail) |
| Автопилот | kernel `full_autopilot` + Definition of Done + playtest |
| Браузър | Playwright smoke **и** интерактивна сесия (goto / click / type) |
| MCP | `$DAEDALUS_DATA/mcp.json` + рафт с добавки в Studio |
| Памет | Chroma + предпочитания („запомни, че…“) + научени умения |
| Картинки | Novita / OpenRouter · `generate_image` |
| Облачен LLM | AllToken / OpenRouter / Novita / NVIDIA |
| Игри — браузър | Phaser, canvas, Vite; 3D аркада / състезания → Vite + Three.js WebGPU (`src/racer/`) |
| Игри — Godot 4 | kind `godot`: `.tscn` + GDScript; 2D scaffold или 3D greybox стая |
| Мешове | `python -m daedalus.blender_mcp` или `blender_jobs.emit_job` (без `bpy` в облака) |
| Живо публикуване | локално `/apps/{slug}/` + Netlify / GitLab Pages / 1-часов preview |
| Капсула | zip за USB — `GET /api/v1/playground/projects/{slug}/capsule` |
| Windows приложение | Start Menu **Дедал** (WebView2 / Tk) |
| Еволюция | опционален idle gardener; пачове пак чакат Approve |

Studio таб **Свят** показва Reach / playtest / MCP статус.

---

## Изисквания

**Задължително**

- Python 3.11 или по-нов
- Поне един LLM: облачен ключ **или** локален Ollama / LM Studio / llama.cpp / vLLM

**По желание**

| За | Какво |
|---|---|
| Браузърна проверка | `pip install -e ".[playwright]"` + `python -m playwright install chromium` |
| Windows прозорец | `pip install -e ".[gui]"` (Edge WebView2) |
| Vite / Phaser игри | Node.js 18+ и npm |
| Godot проекти | [Godot 4.3](https://godotengine.org/download) на машината на собственика |
| Blender jobs | [Blender 4.x](https://www.blender.org/download/) локално — Дедал само **излъчва** скрипт |
| Docker | Docker Engine + Compose |
| Живо публикуване | `NETLIFY_AUTH_TOKEN` и/или `GITLAB_TOKEN`, или Node (`npx`) за 1-часов preview |

Дедал **не** праща 7 GB тежести. Гардеробът препоръчва Qwen/coder според RAM/VRAM, ти дърпаш модела.

---

## Инсталация

Клонирай **това** репо. То е продуктът.

```bash
git clone https://gitlab.com/Blaue-Ente/deadalus-agent-0.git
cd deadalus-agent-0
```

### Linux и macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
# попълни поне един ключ, или остави празно и ползвай Ollama
export DAEDALUS_STANDALONE=1
python -m app.cli serve --port 8787
```

Еквивалент след `pip install -e .`:

```bash
daedalus serve --host 127.0.0.1 --port 8787
```

Проверка на LLM без UI:

```bash
python -m app.cli ping
python -m app.cli models
```

Локален дим без да пипаш Docker:

```bash
./scripts/smoke-local.sh
```

### Windows (Start Menu)

Без Administrator. Python 3.11+ с **Add python.exe to PATH**, после в папката на репото:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\packaging\windows\install.ps1
# по желание: -DesktopShortcut  -Startup
```

Старт → **Дедал** отваря прозорец към http://127.0.0.1:8787 (WebView2 ако има `pywebview`, иначе Tk, иначе браузър).

| Какво | Къде |
|---|---|
| Ключове и `.env` | `%LOCALAPPDATA%\Daedalus\` |
| Игри и памет | `%LOCALAPPDATA%\Daedalus\` |
| Код + venv | `%LOCALAPPDATA%\Programs\Daedalus\` |

Inno Setup: `iscc packaging\windows\Daedalus.iss` → `dist\Daedalus-Setup.exe`.

Пълни стъпки: [`packaging/windows/README.md`](packaging/windows/README.md).

### Docker

```bash
cp .env.example .env
# попълни ALLTOKEN_API_KEY или OPENROUTER_API_KEY (или друг ключ)
docker compose up --build
```

SQLite, Playground и Chroma са в volume `daedalus-data` (`DAEDALUS_DATA=/var/lib/daedalus`).

Облак (fly.io, Render, GitLab Registry): виж [`DEPLOY.md`](DEPLOY.md).

---

## Първо стартиране

Отвори http://127.0.0.1:8787 — това е **Studio** (чат, shell, гардероб, лого).

Command Center (същият процес):

| Страница | URL |
|---|---|
| Начало | http://127.0.0.1:8787/admin/daedalus |
| Playground | http://127.0.0.1:8787/admin/daedalus/playground |
| Game Studio | http://127.0.0.1:8787/admin/daedalus/game-studio |
| Предложения (Approve) | http://127.0.0.1:8787/admin/daedalus/proposals |
| Умения | http://127.0.0.1:8787/admin/daedalus/skills |
| OpenAPI | http://127.0.0.1:8787/docs |
| Здраве | http://127.0.0.1:8787/health |

LLM пресети, работници, памет, задачи и блокери са на същите `/admin/daedalus/*` адреси.

---

## LLM — облак и локално

Попълни **поне едно** в `.env` (виж [`docs/ENV.md`](docs/ENV.md)):

```
ALLTOKEN_API_KEY=
OPENROUTER_API_KEY=
NOVITA_API_KEY=
NVIDIA_API_KEY=
```

Локално, без ключ:

```bash
# Ollama на машината, после:
export DAEDALUS_PROVIDER=ollama
export DAEDALUS_MODEL=qwen2.5-coder:7b
# или кликни препоръчан ред в Studio → LLM (дърпа + активира)
```

`python -m app.cli models` показва клас на машината и какво да дърпаш.

---

## Playground и Game Studio

Всеки проект е папка `playground/projects/{slug}/` с kind:

| Kind | За какво |
|---|---|
| `static` | HTML / CSS / JS без build |
| `vite` | Vite + vanilla JS; Phaser/Pixi за 2D; Three.js WebGPU за 3D аркада / racer |
| `vite-react` | Vite + React 18 |
| `fastapi` | Python API |
| `streamlit` | Streamlit приложение |
| `node` | Node / Express |
| `expo` | Expo / React Native (web export е статичен) |
| `unity` | C# scaffold — отваряш в Unity Hub локално |
| `godot` | Godot 4.3, текст `.tscn` / GDScript; 2D или 3D greybox стая |

**Игрова фабрика (реално състояние)**

- Браузърни 3D / F-Zero клас: Vite + Three.js, вертикален slice, playable M0 в `src/racer/`. Phaser не е 3D пътят.
- Godot 2D: `project.godot`, сцена, играч, Game autoload.
- Godot 3D: greybox стая, CharacterBody3D, плоча → врата, камера, HUD; `studio/scene_index.json` + `studio/asset_contract.json`. Headless probe ако има `godot` в PATH.
- Blender: emit-only jobs (`set_pivot`, `collision_hint`, GLB). Облакът не импортира `bpy`.
- Дедал не си пише собствен енджин, не държи 3D foundation model и не пуска пет MCP моста към Godot.

---

## Проверка и публикуване

1. **Smoke** — compile / HTTP probe.
2. **Браузър** — Chromium: празен екран, конзола, скрийншот.
3. **Playtest** — 4–6 симулирани играчи (Studio → Свят, или след автопилот). Fun gate: ориентиране, fail state, видими pickup-и.
4. **Ship**
   - **Капсула** — zip (USB / прати файл). Статично: unzip, отвори `index.html`.
   - **Локално /apps/** — Studio „Към /apps/“.
   - **Наживо** — публичен `https`: Netlify токен → GitLab Pages → 1-часов Netlify preview. Само static / vite / expo-web.
   - **Git** — push към GitLab ако има `GITLAB_TOKEN`, иначе GitHub.
   - Unity и Godot: папката се отваря в съответния Editor.

---

## Офлайн ателие

```bash
export DAEDALUS_OFFLINE=1
# или бутонът в Studio
```

Търсене, Jina и image-gen се отказват. Чат, файлове, shell, localhost браузър, heuristic playtest и капсули продължават. Нужен е локален модел в гардероба.

---

## Еволюция

Отделна опция (Studio таб, **изключена** по подразбиране). Докато процесът работи — дори без отворен чат — gardener ползва свободен CPU/RAM да:

1. Сканира две въртящи се radar точки (агенти, MCP, памет, локален LLM).
2. Прегледа едно публично GitHub репо (README + дърво + лиценз).
3. Запише журнал и skill note под `$DAEDALUS_DATA`.
4. По желание shallow-clone в `data/daedalus/evolution/research/` (никога в `daedalus/`). AGPL/SSPL се отказват.

Спира при офлайн кит, високо натоварване, малко RAM или работещ автопилот. Пачове към самия Дедал пак чакат **Approve**. `edit_modules` е изключен, докато не го включиш.

```bash
# DAEDALUS_EVOLUTION=1    # принудително при boot
# DAEDALUS_EVOLUTION=0    # kill switch
```

---

## CLI

След `pip install -e .` командата е `daedalus` (същото като `python -m app.cli`).

| Команда | Какво прави |
|---|---|
| `daedalus serve [--host] [--port] [--reload]` | HTTP Studio + API |
| `daedalus ping [--message …]` | Един LLM call без UI |
| `daedalus models` | Ефективен провайдър + probe на локални бекенди |
| `daedalus desktop [--ui auto\|webview\|tk\|browser\|none]` | Прозорец + локален сървър |

---

## Тестове и CI

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python3 -m pytest tests -q
```

GitLab CI (`.gitlab-ci.yml`) пуска същите тестове и строи Docker образ; при наличен Container Registry го push-ва като `$CI_REGISTRY_IMAGE:latest`.

---

## Структура на репото

```
deadalus-agent-0/
  daedalus/               пакетът на агента
    kernel/               план → код → тест → verify
    identity*.py          персона (игри първо)
    memory.py             Chroma сесийна памет
    playground*.py        builder за приложения и игри
    agent.py              tool loop
    arcade_craft.py       3D браузърни racers
    blender_jobs.py       emit на Blender скриптове
    game_factory.py       доктрина за Godot 3D стая
  app/                    FastAPI, CLI, desktop
    main.py               Studio + API
    cli.py                daedalus serve / ping / models / desktop
  ui/                     Command Center (HTML / JS / CSS)
  playground/projects/    работни проекти (gitignored)
  tests/                  pytest
  packaging/windows/      Start Menu инсталатор
  scripts/smoke-local.sh  дим на здравето на процеса
  Dockerfile              + docker-compose.yml
```

---

## Документация

| Файл | Тема |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Слоеве, пътища, игрова доктрина |
| [DEPLOY.md](DEPLOY.md) | PC, Docker, Windows, fly.io, Render |
| [docs/ENV.md](docs/ENV.md) | Променливи на средата |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Как да допринасяш |
| [SECURITY.md](SECURITY.md) | Как се докладват проблеми |
| [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) | Правила за участие |
| [CHANGELOG.md](CHANGELOG.md) | История на версиите |
| [AGENTS.md](AGENTS.md) | Кратък бриф за coding агенти в това репо |
| [packaging/windows/README.md](packaging/windows/README.md) | Windows приложение |
| [LICENSE](LICENSE) | MIT |

---

## Сигурност и принос

Уязвимости: виж [SECURITY.md](SECURITY.md).  
Merge request: виж [CONTRIBUTING.md](CONTRIBUTING.md).  
Проблеми: [Issues](https://gitlab.com/Blaue-Ente/deadalus-agent-0/-/issues).

---

## Лиценз

[MIT](LICENSE) © 2026 Daedalus Contributors.

Опционалният MiroFish sidecar (ако го пуснеш сам) е **AGPL-3.0**. Дедал говори с него само по HTTP — изходният му код **не** влиза в това репо.
