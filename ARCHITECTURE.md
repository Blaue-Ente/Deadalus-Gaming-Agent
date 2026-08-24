# Архитектура на Дедал

Дедал е **един продукт**: coding агент + Studio + Playground. Един FastAPI процес, порт **8787**, SQLite, директен OpenAI-съвместим LLM.

```
  Собственик (BG / EN)
           │
           ▼
  ┌─────────────────────────────────────┐
  │  Дедал                               │
  │  памет · персона · playground        │
  │  автопилот · самоподобрение · kernel │
  └──────────────┬──────────────────────┘
                 │
                 ▼
        OpenAI-compatible LLM
        AllToken · OpenRouter · Novita · NVIDIA
        или Ollama / LM Studio / llama.cpp / vLLM
```

## Слоеве

| Слой | Къде | Бележки |
|------|------|---------|
| Идентичност | `daedalus/identity.py` + `identity_standalone.py` | Компактен tool contract в този продукт |
| Памет | `memory.py`, `knowledge.py` | Chroma колекции под `$DAEDALUS_DATA` |
| Агентски цикъл | `agent.py`, `native_tools.py`, `tools.py`, `workers.py`, `subagents.py` | Нативни инструменти по подразбиране; jail-нати подагенти |
| Shell | `shell_center.py` | Jail + махане на тайни от env |
| Локален LLM | `local_llm.py` + `llm_wardrobe.py` + `machine_profile.py` | Probe + хардуерен каталог |
| Офлайн | `offline.py` | Блокира мрежови native tools |
| Добавки | `addons.py` | MCP рафт → `mcp.json` |
| Капсула | `capsule.py` | Zip на playground проект |
| Наживо | `live_publish.py` | `/apps/` + Netlify / GitLab Pages / 1h preview |
| Предпочитания | `preferences.py` | Реч на собственика → трайна памет |
| Научени умения | `learned_skills.py` | Провал/успех → SKILL.md |
| DoD | `job_contract.py` | Код без доказателство не е готов |
| Reach | `reach.py` | Jina Reader + опционални CLI |
| Playtest | `playtest.py` | Персони-съдии; без външен 1M dataset |
| MiroFish | `mirofish_client.py` | Само HTTP към sidecar — никога vendor |
| MCP | `mcp_client.py` | JSON-RPC stdio/HTTP от `mcp.json` |
| Браузър | `playground_browser.py`, `browser_session.py` | Smoke + интерактивна сесия |
| Playground | `playground*.py`, `game_*.py` | static, vite, phaser-via-vite, expo, unity, godot |
| Автопилот | `builder_runtime.py` + `kernel/full_autopilot.py` | Kernel цикъл |
| Самоподобрение | `self_improve.py` | Gated skills; пачове чакат Approve |
| Еволюция | `evolution.py` | Opt-in idle gardener |
| Kernel | `daedalus/kernel/` | plan → code → test → verify |
| HTTP | `app/main.py` | Studio, `/admin/daedalus/*`, health, chat, playground API |
| Windows | `app/desktop.py`, `app/windows_gui.py`, `packaging/windows/` | Start Menu; WebView2 / Tk |
| UI | `ui/` | Шаблони и статични файлове на Command Center |
| Store | `daedalus/store.py` | SQLite |

## Пътища

`daedalus/paths.py` — корен на репото / продукт / данни / playground.

По подразбиране:

| Какво | Път |
|---|---|
| Код | коренът на git клонинга |
| SQLite + Chroma + предложения | `$DAEDALUS_DATA` → `data/daedalus/` |
| Проекти | `playground/projects/{slug}/` |
| Docker данни | `/var/lib/daedalus` |
| Windows данни | `%LOCALAPPDATA%\Daedalus` |

## Игрова доктрина

- Asset gate: генерираните `assets/*.png` трябва да се реферират (`load.image` / `<img>`).
- Verify: smoke + браузър render-and-see + playtest + quality critic.
- Unity е scaffold + Git, не in-cloud Editor.
- Arcade craft (`arcade_craft.py` + `studio_shelf.py` + `playground_racer_scaffold.py`): 3D аркада ползва Vite + Three.js WebGPU/TSL. Състезателен brief бутва playable M0 в `src/racer/` (не Phaser).
- Godot 4 (`playground_godot_scaffold.py`, `playground_godot3d_scaffold.py`, `godot_tools.py`): текст `.tscn`/GDScript, локален Editor. 3D M0 е greybox стая с плоча и врата.
- Blender jobs (`blender_jobs.py`, `python -m daedalus.blender_mcp`) **излъчват** bpy скрипт — не импортират `bpy` в процеса на Дедал.
- Не: собствен енджин, собствен 3D foundation model, пет успоредни Godot MCP сървъра, мълчаливо саморедактиране, „едно изречение → готова игра“.

## Лицензова граница (MiroFish)

MiroFish-Offline е AGPL-3.0. `daedalus/mirofish_client.py` е оригинален HTTP клиент. Не вкарвай техния backend в този процес — дръж го като отделен Docker stack, ако изобщо го ползваш.
