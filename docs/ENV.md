# Environment variables

Copy `.env.example` → `.env`. Daedalus reads `.env` when the CLI starts.
Studio → **Connectors** writes the same keys to `$DAEDALUS_DATA/secrets.env`
(applied on boot, overwrites process env for those names). Do not commit that file.

## Required for a cloud LLM

At least **one** of:

| Variable | Provider |
|---|---|
| `ALLTOKEN_API_KEY` | AllToken |
| `OPENROUTER_API_KEY` | OpenRouter |
| `NOVITA_API_KEY` | Novita (and sprites) |
| `NVIDIA_API_KEY` | NVIDIA NIM |

Without them you need a local provider (Ollama and so on).

OpenRouter **without** an explicit model falls back to **paid** Claude Sonnet. For $0 use Studio preset `or_free` or:

```
DAEDALUS_PROVIDER=openrouter
DAEDALUS_MODEL=poolside/laguna-s-2.1:free
```

Optional: `OPENROUTER_HTTP_REFERER`, `OPENROUTER_APP_TITLE` (sent on OpenRouter requests).

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
| `DAEDALUS_LAB_BACKEND` | `auto` / `local` / `smolvm` / `docker` — computer for Play Lab |
| `DAEDALUS_SMOLVM` | `0` — never boot SmolVM even if installed |
| `DAEDALUS_LAB_IMAGE` | Docker image for Play Lab VM (default `python:3.12-alpine`) |
| `DAEDALUS_LAB_PULL` | `1` — allow `docker pull` for the lab image (off by default) |
| `DAEDALUS_LAB_DOCKER` | `0` — never start a Docker lab; local HTTP only |
| `DAEDALUS_LAB_INSTALL_ROOT` | extra allowed root for `play_lab` install paths |

Optional loopback sidecar (not vendored): `DAEDALUS_PROVIDER=omniroute` points at `http://127.0.0.1:20128/v1` with model `auto`. Studio preset **omniroute** pins workers to `auto` / `auto/fast` / `auto/coding`. The gateway process must already be running.

Studio → Connectors **Brain** (or Studio → Brain wardrobe) switches models without editing `.env` (`wardrobe.json` under the data dir). That choice is the process-wide brain: chat, workers, autopilot, and CLI all read it. `OLLAMA_HOST` is an advanced Connectors field if the daemon is not on `127.0.0.1:11434`.

## Agent behaviour

| Variable | Meaning |
|---|---|
| `DAEDALUS_NATIVE_TOOLS` | `0` = NEED_* markers only; native tools are on by default |
| `DAEDALUS_TOOL_REGISTRY` | `0` = leftover if-ladder for every native tool; default on routes `read_file` / `list_dir` / `code_search` through `ToolRegistry` (same strings) |
| `DAEDALUS_BUILD_FIX_ROUNDS` | Fix nudges when the plan is incomplete (default `3`; `0` = old passive stop) |
| `DAEDALUS_VISUAL_PROOF` | `0` turns off kernel-run browser/Godot proof after the last write (default on). Missing Chromium/Godot is not a skip-pass. |
| `DAEDALUS_WRITE_GATE` | `0` turns off the `write_file` syntax/arcade gate (default on). Duplicate `let` and missing canvas/loop become FAILED writes. |
| `DAEDALUS_QUALITY` | `0` turns the visual critic off (default on). Unparsed critic / skipped browser is not a pass. |
| `DAEDALUS_EVOLUTION` | `1` turns the gardener on at boot; `0` is a kill switch |
| `GODOT_BIN` | Path to Godot 4.3. Missing binary fails Godot kinds. When set, the TCP runtime bridge (player must move) is required — static files are not a playtest. |
| `GODOT_EXPORT_TEMPLATES` | Directory of Godot 4.3 export templates. Missing templates fail Stage 7 export (skip is not a pass). Default lookup: `~/.local/share/godot/export_templates/4.3.stable`. |
| `DAEDALUS_GODOT_PIN` | `0` allows other Godot 4.x binaries; default pins 4.3. Godot 3 always fails. |
| `DAEDALUS_GODOT_MCP` | Optional name of a Godot MCP server in `$DAEDALUS_DATA/mcp.json`. Headless bridge is the default; MCP is an adapter, not a second Editor. |
| `UNITY_PATH` | Path to the Unity Editor. Missing Editor fails Unity kinds (batchmode). Batchmode import is not Play mode. |
| `DAEDALUS_UNITY_MCP` | Optional name of a Unity MCP server in `$DAEDALUS_DATA/mcp.json`. Local Editor plugin — we do not vendor Unity. Without this or `UNITY_PATH`, `engine` on a Unity project refuses honestly. |

Web visual kinds need Chromium on PATH for the `playtest/scenario.json` bot (pixel-diff, FPS). Missing Chromium fails closed — it is not a skip-pass.

MobileAdapter (`production({action: pipeline})` on Expo or a Capacitor wrap) needs a device or emulator (`adb devices` and/or `xcrun simctl`). Missing runner is FAIL. Expo web export is not an iOS/Android artifact. Optional: `ANDROID_HOME` / `ANDROID_SDK_ROOT`, `EAS_TOKEN`.

## 3D mesh jobs (optional)

Atlas / tileset / SFX / bitmap font need **no** keys. Hero GLB submit fails closed unless one of these is set:

| Variable | Provider |
|---|---|
| `FAL_KEY` or `FAL_API_KEY` | fal.ai Hunyuan / Trellis queue |
| `TRIPO_API_KEY` | Tripo text-to-3D |
| `MESHY_API_KEY` | Meshy text-to-3D |
| `SUNO_API_KEY` | Recorded only — Daedalus does **not** submit Suno. Drop `assets/music.mp3`. |

Without a mesh key: `assets({action:mesh})` returns `need_key`. Greybox (`action:greybox`) is a labelled cube, not a hero. `DAEDALUS_OFFLINE=1` refuses mesh submit.

## Publishing

| Variable | Meaning |
|---|---|
| `NETLIFY_AUTH_TOKEN` | Durable `*.netlify.app` |
| `GITLAB_TOKEN` | GitLab Pages `daedalus-{slug}` + git clone/push + Merge Request |
| `GITHUB_TOKEN` | GitHub clone/push + Pull Request (classic PAT, scope `repo`) |
| Node.js (`npx`) | 1-hour Netlify preview with no account |

## MCP and sidecar

- MCP servers: `$DAEDALUS_DATA/mcp.json` (Studio → Connectors writes it).
- Optional GitHub/GitLab MCP addons reuse `GITHUB_TOKEN` / `GITLAB_TOKEN`. Native `git_forge` already opens PR/MR without those addons.
- `MIROFISH_URL` / `MIROFISH_UI` — only if you run the AGPL sidecar yourself. It is not vendored here.

## Peer HTTP

`.env.example` has an optional shared secret for the HTTP peer ping. Leave it empty to refuse ask/chat from peers. Set a long random value before a public bind if you use that endpoint. Do not commit it. A public bind also needs `DAEDALUS_TOKEN` — the peer secret is not a substitute.
