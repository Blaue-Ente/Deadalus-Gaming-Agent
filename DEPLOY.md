# Деплой на Дедал (PC + облак)

Дедал е един FastAPI процес на порт **8787**. Postgres не е нужен: SQLite живее под `$DAEDALUS_DATA`.

## Наживо

Studio → **Наживо** (или `POST /api/v1/playground/projects/{slug}/live`):

1. Строи static (vite/expo) и сервира `/apps/{slug}/` на същия процес.
2. Взема първия публичен backend, който работи:
   - `NETLIFY_AUTH_TOKEN` → траен `*.netlify.app` (секунди; иска Node/`npx`)
   - `GITLAB_TOKEN` → публичен GitLab Pages проект `daedalus-{slug}` (`*.gitlab.io`, CI 1–2 мин)
   - Netlify `--allow-anonymous` → моментален URL, **claim до 1 час**
3. Офлайн кит → само локално `/apps/`.

Безплатен custom `.com` не е включен. Получаваш реален https поддомейн.

## Windows (Start Menu)

Extra option: `Daedalus.exe` plus a private CPython (Python on PATH is optional). See [docs/INSTALL.md](docs/INSTALL.md).

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\packaging\windows\install.ps1 -DesktopShortcut
```

Copies the product to `%LOCALAPPDATA%\Programs\Daedalus`, creates a venv,
tries `pip install -e ".[gui]"` (pywebview / Edge WebView2), compiles
`Daedalus.exe`, and a Start Menu shortcut **Daedalus**. Studio binds
**127.0.0.1:8787** only.

Keys: `%LOCALAPPDATA%\Daedalus\.env` (copied from `.env.example` on first
install). Ollama stays a local provider.

Uninstall: Settings → Apps → Daedalus, or `uninstall.ps1` (add `-RemoveData`
to wipe memory/games). Wizard installer: `packaging/windows/build-setup.ps1`.

## Локален PC

```bash
cd deadalus-agent-0
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[playwright]"   # по желание, за браузърна проверка
python -m playwright install chromium
export DAEDALUS_STANDALONE=1
export ALLTOKEN_API_KEY=...      # или OPENROUTER_API_KEY / локален Ollama
python -m app.cli serve --host 127.0.0.1 --port 8787
```

| Какво | URL |
|---|---|
| Studio | http://127.0.0.1:8787 |
| Command Center | http://127.0.0.1:8787/admin/daedalus |
| Playground | http://127.0.0.1:8787/admin/daedalus/playground |
| API docs | http://127.0.0.1:8787/docs |
| Health | http://127.0.0.1:8787/health |

## Docker (PC или VPS)

```bash
cp .env.example .env
# попълни ключ
docker compose up --build -d
```

Данните (проекти, SQLite, Chroma) са в volume `daedalus-data` на `/var/lib/daedalus`. Образът задава `DAEDALUS_DATA` там и без compose, така че `docker run -v daedalus-data:/var/lib/daedalus` пази паметта между рестарти.

## Облак

- Една реплика стига (jobs са in-process).
- Persist-ни volume-а. Там са Playground работните копия + SQLite.
- Сложи reverse proxy (Caddy/nginx) отпред за HTTPS.
- Преди да отвориш порта към интернет: смени peer секрета в `.env` (виж [docs/ENV.md](docs/ENV.md)) и слушай през TLS, не директно `0.0.0.0` без защита.

### fly.io

```bash
fly launch --copy-config --yes   # ползва fly.toml; създай volume daedalus_data
fly secrets set ALLTOKEN_API_KEY=...
fly deploy
```

### Render

Свържи това GitLab репо и ползвай `render.yaml` (Docker + диск на `/var/lib/daedalus`). Задай `ALLTOKEN_API_KEY` (или друг ключ) в dashboard-а.

### GitLab Container Registry

На `main`, `.gitlab-ci.yml` строи образа и, когато `CI_REGISTRY` е сетнат, push-ва `$CI_REGISTRY_IMAGE:latest`. Деплойвай този образ където Docker върви.

## Среда

Виж `.env.example` и [docs/ENV.md](docs/ENV.md). Минимум: един от `ALLTOKEN_API_KEY`, `OPENROUTER_API_KEY`, `NOVITA_API_KEY`, `NVIDIA_API_KEY`, **или** локален провайдър. `NOVITA_API_KEY` / `OPENROUTER_API_KEY` включват и генериране на спрайтове в Playground.
