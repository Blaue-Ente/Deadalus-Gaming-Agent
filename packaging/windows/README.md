# Daedalus on Windows

Two extra install options on top of ZIP / `git clone`. **Neither is Electron
or a PyInstaller one-file freeze.** Studio is still FastAPI in a venv. The
window is Edge WebView2 (`pywebview`), else Tk, else the browser. The server
binds `127.0.0.1:8787` only. No Administrator.

| Option | What you get | Need Python on PATH? |
|---|---|---|
| A. `install.ps1` | Start Menu + **Daedalus.exe** | No — a private CPython is downloaded into the app folder if missing |
| B. `Daedalus-Setup.exe` | Same, via an installer wizard | No — Setup bundles the CPython nupkg when built with `build-setup.ps1` |
| C. Developer | `pip install -e .` in a venv | Yes (3.11+) |

Ollama, Godot, Blender, and Node are still separate apps if you use those features.
The private CPython build has no Tcl/Tk: the window is WebView2 or the browser.

## A. Double-click (ZIP, no terminal)

1. Download the repo ZIP from GitLab (**Code → Download source code**) or `git clone`.
2. Unzip the folder. Windows will **not** auto-run anything on extract.
3. Double-click **`Install-Daedalus.bat`** at the repo root (calls this folder’s `install.ps1` with `-ExecutionPolicy Bypass`).

## B. PowerShell (ZIP or git)

1. Same download/unzip as above.
2. Optional: install [Python 3.11+](https://www.python.org/downloads/windows/) yourself. If you skip this, the script fetches a private 64-bit CPython (official NuGet `python` package) and does **not** add it to PATH.
3. In the repo folder:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\packaging\windows\install.ps1 -DesktopShortcut
# always use the private CPython (what Setup.exe does):
# .\packaging\windows\install.ps1 -BundlePython -DesktopShortcut
# never download CPython (fail if PATH has no 3.11+):
# .\packaging\windows\install.ps1 -UseSystemPython
```

4. Start → **Daedalus**. Studio: http://127.0.0.1:8787

| What | Where |
|---|---|
| `Daedalus.exe` + venv + private CPython | `%LOCALAPPDATA%\Programs\Daedalus\` |
| `.env`, memory, games | `%LOCALAPPDATA%\Daedalus\` |
| Console if the window fails | `%LOCALAPPDATA%\Programs\Daedalus\Daedalus.cmd` |

## C. `Daedalus-Setup.exe`

GitLab CI on Linux cannot compile this binary. On a Windows machine with
[Inno Setup 6](https://jrsoftware.org/isinfo.php):

```powershell
.\packaging\windows\build-setup.ps1
```

Output: `dist\Daedalus-Setup.exe`. The wizard is per-user. It runs
`install.ps1 -InPlace -BundlePython` (private CPython + `Daedalus.exe`).
First install still needs internet for `pip` (FastAPI, Chroma, …).

Manual compile if Inno is already installed: `iscc packaging\windows\Daedalus.iss`
(run `build-setup.ps1` once so `redist\python.3.12.10.nupkg` is present).

## Uninstall

Settings → Apps → Daedalus, or:

```powershell
%LOCALAPPDATA%\Programs\Daedalus\uninstall.ps1
# also wipe memory/games:
# ...\uninstall.ps1 -RemoveData
```

## What this is not

- Not a single frozen `.exe` that contains FastAPI + Chroma with no other files.
- Not a store listing. You (or a maintainer) build Setup.exe on Windows and
  attach it to a GitLab Release if you want a downloadable installer.
