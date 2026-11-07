# Install Daedalus on your computer

The GitLab tree **is** the product. You copy it once (ZIP or `git clone`), then
pick an install path. Studio UI copy may still be Bulgarian; these docs are English.

## Choose an install path

| Path | Best for | Needs |
|---|---|---|
| **Windows `Install-Daedalus.bat`** | ZIP → unzip → double-click (no terminal) | 64-bit Windows 10/11, internet for `pip` the first time |
| **Windows Setup.exe** | Wizard installer + Start Menu `Daedalus.vbs` | Same |
| **Windows `install.ps1`** | Same app, from PowerShell / git | Same; Python on PATH is optional |
| **venv + `daedalus serve`** | Linux, macOS, Windows developers | Python 3.11+ |
| **Docker** | VPS / “I already use Compose” | Docker |

There is **no** PyInstaller one-file game-style `.exe`. The extra Windows
install is a **Start Menu shortcut** to **`Daedalus.vbs`** plus a **private CPython**
inside `%LOCALAPPDATA%\Programs\Daedalus` (not on PATH). The installer may still
build an optional unsigned **`Daedalus.exe`**; use Start Menu / `.vbs` / `.cmd` so
Defender does not block the default launch path.

Node, Godot, Blender, and Ollama stay separate if you use those features.

---

## Get the code

### git (easy updates)

```bash
git clone https://gitlab.com/Blaue-Ente/deadalus-agent-0.git
cd deadalus-agent-0
```

### ZIP (no git)

1. Open https://gitlab.com/Blaue-Ente/deadalus-agent-0
2. **Code → Download source code → zip**
   or https://gitlab.com/Blaue-Ente/deadalus-agent-0/-/archive/main/deadalus-agent-0-main.zip
3. Unzip. You should see `daedalus/`, `app/`, `packaging/`, `Install-Daedalus.bat`, `README.md`.

Copy the whole folder, not a single file.

Windows does **not** run anything automatically when you unzip (security). After extract, double-click **`Install-Daedalus.bat`** in the folder — no PowerShell commands needed.

---

## Windows — double-click from ZIP

In the extracted folder:

1. Double-click **`Install-Daedalus.bat`** (top level of the repo).
2. Wait for `Done.` (first run can take a few minutes).
3. Start → **Daedalus** or **Дедал** → http://127.0.0.1:8787
4. Add an LLM key in `%LOCALAPPDATA%\Daedalus\.env` or use Ollama (Studio → LLM).

The `.bat` file calls `packaging\windows\install.ps1` with `-ExecutionPolicy Bypass` and `-DesktopShortcut`.

---

## Windows — Start Menu app (ZIP / `install.ps1`)

Full detail: [packaging/windows/README.md](../packaging/windows/README.md).

### From ZIP / git (no Setup.exe needed)

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\packaging\windows\install.ps1 -DesktopShortcut
```

If Python 3.11+ is missing, the script downloads official CPython 3.12.10
(NuGet `python` package, SHA-512 checked) into the app folder. That runtime
has no Tcl/Tk — the window is WebView2 or the browser.

Start → **Daedalus** → http://127.0.0.1:8787

### Build `Daedalus-Setup.exe` (maintainers / a Windows PC)

```powershell
.\packaging\windows\build-setup.ps1
```

Produces `dist\Daedalus-Setup.exe` (needs [Inno Setup 6](https://jrsoftware.org/isinfo.php)).
That installer uses `-BundlePython` so PATH Python is ignored.

| What | Where |
|---|---|
| Launcher + venv + private CPython | `%LOCALAPPDATA%\Programs\Daedalus\` |
| `.env`, memory, games | `%LOCALAPPDATA%\Daedalus\` |

---

## Linux and macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
export DAEDALUS_STANDALONE=1
python -m app.cli serve --host 127.0.0.1 --port 8787
```

Open http://127.0.0.1:8787

Need Python 3.11+. On macOS: `brew install python@3.12` if the system Python is old.

Check the LLM without the UI: `python -m app.cli ping` and `python -m app.cli models`.

---

## Docker

```bash
cp .env.example .env
# fill an LLM key and, for Docker, DAEDALUS_TOKEN
docker compose up --build
```

Studio is still http://127.0.0.1:8787. Data lives in volume `daedalus-data`. Compose publishes `0.0.0.0` inside the container, so set `DAEDALUS_TOKEN` in `.env` first.

---

## After it starts

1. Studio: http://127.0.0.1:8787
2. Put an LLM key in `.env` **or** run Ollama and pick a model in Studio → LLM.
3. Command Center: http://127.0.0.1:8787/admin/daedalus  
   Playground: http://127.0.0.1:8787/admin/daedalus/playground

Env vars: [ENV.md](ENV.md). Cloud / VPS: [DEPLOY.md](../DEPLOY.md).

---

## Updates

| How you installed | How to update |
|---|---|
| `git clone` | `git pull` then `pip install -e .` (in the venv). Windows app: re-run `install.ps1` from the new checkout. |
| ZIP | Download a new zip. Replace the code folder. Do not delete `data/daedalus/` / `%LOCALAPPDATA%\Daedalus`. |
| Setup.exe | Build/install a newer Setup. Data stays under `%LOCALAPPDATA%\Daedalus`. |

---

## If it does not start

- `python` not found (venv path) → on Windows use `install.ps1` without `-UseSystemPython`.
- Port 8787 busy → `python -m app.cli serve --port 8788`.
- Chat errors about a key → `.env` with at least one `*_API_KEY`, or local Ollama.
- Windows window stays blank → run `Daedalus.cmd` and read the console.
