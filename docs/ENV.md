# Променливи на средата

Копирай `.env.example` → `.env`. Дедал чете `.env` при старт на CLI.

## Задължителни за облачен LLM

Поне **една** от:

| Променлива | Провайдър |
|---|---|
| `ALLTOKEN_API_KEY` | AllToken |
| `OPENROUTER_API_KEY` | OpenRouter |
| `NOVITA_API_KEY` | Novita (и спрайтове) |
| `NVIDIA_API_KEY` | NVIDIA NIM |

Без тях трябва локален провайдър (Ollama и т.н.).

## Процес

| Променлива | По подразбиране | Значение |
|---|---|---|
| `DAEDALUS_STANDALONE` | `1` в Docker/CLI | SQLite + директен LLM. Оставяй включено в това репо. |
| `HOST` | `127.0.0.1` (desktop) / `0.0.0.0` (Docker) | Bind адрес |
| `PORT` | `8787` | HTTP порт |
| `DAEDALUS_DATA` | `data/daedalus/` | SQLite, Chroma, предложения, evolution |
| `DAEDALUS_PLAYGROUND` | `playground/projects` | Работни проекти |
| `DAEDALUS_WINDOWS_APP` | — | Слага го Windows инсталаторът; данните отиват в `%LOCALAPPDATA%\Daedalus` |

## Локален LLM

| Променлива | Пример |
|---|---|
| `DAEDALUS_PROVIDER` | `ollama` |
| `DAEDALUS_MODEL` | `qwen2.5-coder:7b` |
| `DAEDALUS_LOCAL_LLM` | `1` |
| `OLLAMA_HOST` | `127.0.0.1:11434` |
| `DAEDALUS_OFFLINE` | `1` — отказ на търсене / Jina / картинки |

Studio → LLM (гардероб) може да сменя модела без редакция на `.env` (`wardrobe.json` под данните).

## Поведение на агента

| Променлива | Значение |
|---|---|
| `DAEDALUS_NATIVE_TOOLS` | `0` = само NEED_* маркери; по подразбиране нативните tools са включени |
| `DAEDALUS_EVOLUTION` | `1` включва gardener при boot; `0` е kill switch |
| `DAEDALUS_EVOLUTION_LLM` | `1` позволява кратък chat по време на цикъл |

## Публикуване

| Променлива | Значение |
|---|---|
| `NETLIFY_AUTH_TOKEN` | Траен `*.netlify.app` |
| `GITLAB_TOKEN` | GitLab Pages `daedalus-{slug}` + git push |
| Node.js (`npx`) | 1-часов Netlify preview без акаунт |

## MCP и sidecar

- MCP сървъри: файл `$DAEDALUS_DATA/mcp.json` (Studio рафтът го пише).
- `MIROFISH_URL` / `MIROFISH_UI` — само ако сам пускаш AGPL sidecar. Не се вендорира тук.

## Peer HTTP

`.env.example` съдържа споделен секрет за опционалния HTTP peer ping. Смени го преди публичен bind. Не го commi-твай.
