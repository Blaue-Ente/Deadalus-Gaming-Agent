# Environment variables

Copy `.env.example` → `.env`. Daedalus reads `.env` when the CLI starts.

## Required for a cloud LLM

At least **one** of:

| Variable | Provider |
|---|---|
| `ALLTOKEN_API_KEY` | AllToken |
| `OPENROUTER_API_KEY` | OpenRouter |
| `NOVITA_API_KEY` | Novita (and sprites) |
| `NVIDIA_API_KEY` | NVIDIA NIM |

Without them you need a local provider (Ollama and so on).

## Process

| Variable | Default | Meaning |
|---|---|---|
| `DAEDALUS_STANDALONE` | `1` in Docker/CLI | SQLite + direct LLM. Leave it on in this repo. |
| `HOST` | `127.0.0.1` (CLI default; Docker `0.0.0.0`) | Bind address. Public bind requires `DAEDALUS_TOKEN`. |
| `PORT` | `8787` | HTTP port |
| `DAEDALUS_TOKEN` | (generated on loopback) | Owner secret for Studio/API. File: `$DAEDALUS_DATA/owner.token` |
| `DAEDALUS_SHELL_ISOLATION` | — | `1` allows `run_command` when not on loopback |
| `DAEDALUS_SHELL_LEGACY` | — | `1` opts in to shell metacharacters (still not a container) |
| `DAEDALUS_DATA` | `data/daedalus/` | SQLite, Chroma, proposals, evolution |
| `DAEDALUS_PLAYGROUND` | `playground/projects` | Working projects |
| `DAEDALUS_WINDOWS_APP` | — | Set by the Windows installer; data goes to `%LOCALAPPDATA%\Daedalus` |

## Local LLM

| Variable | Example |
|---|---|
| `DAEDALUS_PROVIDER` | `ollama` |
| `DAEDALUS_MODEL` | `qwen2.5-coder:7b` |
| `DAEDALUS_LOCAL_LLM` | `1` |
| `OLLAMA_HOST` | `127.0.0.1:11434` |
| `DAEDALUS_OFFLINE` | `1` — refuse search / Jina / images |

Studio → LLM (wardrobe) can switch models without editing `.env` (`wardrobe.json` under the data dir).

## Agent behaviour

| Variable | Meaning |
|---|---|
| `DAEDALUS_NATIVE_TOOLS` | `0` = NEED_* markers only; native tools are on by default |
| `DAEDALUS_EVOLUTION` | `1` turns the gardener on at boot; `0` is a kill switch |
| `DAEDALUS_EVOLUTION_LLM` | `1` allows a short chat during a cycle |

## Publishing

| Variable | Meaning |
|---|---|
| `NETLIFY_AUTH_TOKEN` | Durable `*.netlify.app` |
| `GITLAB_TOKEN` | GitLab Pages `daedalus-{slug}` + git push |
| Node.js (`npx`) | 1-hour Netlify preview with no account |

## MCP and sidecar

- MCP servers: `$DAEDALUS_DATA/mcp.json` (the Studio shelf writes it).
- `MIROFISH_URL` / `MIROFISH_UI` — only if you run the AGPL sidecar yourself. It is not vendored here.

## Peer HTTP

`.env.example` includes a shared secret for the optional HTTP peer ping. Change it before a public bind. Do not commit it. A public bind also needs `DAEDALUS_TOKEN` — the peer secret is not a substitute.
