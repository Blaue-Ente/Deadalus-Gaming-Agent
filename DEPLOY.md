# Deploy Daedalus (PC + cloud)

Daedalus is a single FastAPI process on port **8787**. No Postgres required
in standalone mode (SQLite under `DAEDALUS_DATA`). ADN is not started.

## Live URL (Наживо)

Studio → **Наживо** (or `POST /api/v1/playground/projects/{slug}/live`):

1. Builds static (vite/expo) and serves `/apps/{slug}/` on this process.
2. Picks the first public backend that works:
   - `NETLIFY_AUTH_TOKEN` → durable `*.netlify.app` (seconds; needs Node/`npx`)
   - `GITLAB_TOKEN` → public GitLab Pages project `daedalus-{slug}` (`*.gitlab.io`, CI 1–2 min)
   - Netlify `--allow-anonymous` → instant URL, **claim within 1 hour**
3. Offline kit → local `/apps/` only.

Free custom `.com` is not included. You get a real https subdomain.

## Windows (Start Menu app)

```powershell
# Python 3.11+ with "Add python.exe to PATH"
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\packaging\windows\install.ps1 -DesktopShortcut
```

This copies the product to `%LOCALAPPDATA%\Programs\Daedalus`, creates a venv,
tries `pip install -e ".[gui]"` (pywebview / Edge WebView2), and a Start Menu
shortcut **Дедал**. The shortcut opens a window, not only a browser tab.
Studio binds **127.0.0.1:8787** only.
Put cloud keys in `%LOCALAPPDATA%\Daedalus\.env` (copied from `.env.example`
on first install). Ollama still works as a local provider.

Uninstall: Settings → Apps → Дедал, or `uninstall.ps1` (add `-RemoveData` to
wipe memory/games). Inno Setup script: `packaging/windows/Daedalus.iss`.

## Local PC

```bash
cd Daedalus
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[playwright]"   # playwright optional, for browser verify
python -m playwright install chromium   # if you installed the extra
export DAEDALUS_STANDALONE=1
export ALLTOKEN_API_KEY=...      # or OPENROUTER_API_KEY
python -m app.cli serve --host 127.0.0.1 --port 8787
```

Studio: http://127.0.0.1:8787  
Command Center: http://127.0.0.1:8787/admin/daedalus  
Playground: http://127.0.0.1:8787/admin/daedalus/playground  
API docs: http://127.0.0.1:8787/docs  
Health: http://127.0.0.1:8787/health

## Docker (PC or VPS)

```bash
export ALLTOKEN_API_KEY=...
docker compose up --build -d
```

Data (projects, SQLite, Chroma memory) lives in the `daedalus-data` volume
mounted at `/var/lib/daedalus`. The image sets `DAEDALUS_DATA` there even
without compose, so a plain `docker run -v daedalus-data:/var/lib/daedalus`
keeps memory across restarts.

## Cloud notes

- **One replica is enough** for the agent loop (jobs are in-process).
- Persist the volume. Back it up; it holds Playground workspaces + SQLite.
- Put a reverse proxy (Caddy/nginx) in front if you want HTTPS.
- Set `IYVERIS_MESH_KEY` to a long secret before exposing `/api/iyveris`.
- Do **not** set `DAEDALUS_STANDALONE=1` on the ADN Replit/service — that
  process must keep using Postgres + MeshRouter.

### fly.io

```bash
fly launch --copy-config --yes   # uses fly.toml; create the daedalus_data volume
fly secrets set ALLTOKEN_API_KEY=...
fly deploy
```

### Render

Connect the standalone GitLab/GitHub repo and use `render.yaml` (Docker + disk
at `/var/lib/daedalus`). Set `ALLTOKEN_API_KEY` in the dashboard.

### GitLab Container Registry

On `main`, `.gitlab-ci.yml` builds the image and, when `CI_REGISTRY` is set,
pushes `$CI_REGISTRY_IMAGE:latest`. Deploy that image anywhere Docker runs.

## Environment

See `.env.example`. Minimum: one of `ALLTOKEN_API_KEY`, `OPENROUTER_API_KEY`,
`NOVITA_API_KEY`, `NVIDIA_API_KEY`. Optional `NOVITA_API_KEY` or
`OPENROUTER_API_KEY` for Playground sprite generation.
