---
name: windows
description: Install and run Daedalus as a per-user Windows Start Menu app with Daedalus.exe.
---

# SKILL: Windows desktop

**When**: owner says "install on Windows", "exe", "Start Menu", "double-click", "GUI".

**How**:
1. Extra option (no Python on PATH): `packaging\windows\install.ps1` downloads a private CPython (NuGet `python` 3.12.10, SHA-512 checked) into `%LOCALAPPDATA%\Programs\Daedalus\runtime`.
2. From the repo: `packaging\windows\install.ps1 -DesktopShortcut` (optional `-BundlePython` / `-UseSystemPython` / `-Startup`).
3. Wizard installer: on Windows, `packaging\windows\build-setup.ps1` → `dist\Daedalus-Setup.exe` (Inno Setup 6). Setup runs `-InPlace -BundlePython`.
4. Launch: `Daedalus.exe` (compiled at install from `DaedalusLauncher.cs`) → `pythonw -m app.cli desktop`. Fallback: `Daedalus.vbs` / `Daedalus.cmd`.
5. Data is `%LOCALAPPDATA%\Daedalus`. Code + venv + runtime are `%LOCALAPPDATA%\Programs\Daedalus`.
6. Bind loopback only (`127.0.0.1:8787`).
7. Window: pywebview + Edge WebView2 when `pip install -e ".[gui]"` succeeded; otherwise Tk; last resort the browser.
8. A second click reuses the running server. Closing the owned window stops the local server.
9. Headless: `daedalus desktop --no-gui`. Force browser: `daedalus desktop --browser`. Force Tk: `daedalus desktop --ui tk`.

**Anti-patterns**:
- Do not ask for Administrator.
- Do not store API keys in Program Files.
- Do not bind `0.0.0.0` on a home PC.
- Do not delete `%LOCALAPPDATA%\Daedalus` on uninstall unless the owner asked (`-RemoveData`).
- Do not rebuild Studio in Tk — wrap the existing HTML Studio.
- Do not add Electron or a bundled Chromium; WebView2 is already on Windows 10/11.
- Do not promise a PyInstaller one-file freeze of FastAPI+Chroma; the extra exe is a launcher + private CPython.
