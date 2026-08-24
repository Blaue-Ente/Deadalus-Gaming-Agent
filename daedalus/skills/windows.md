---
name: windows
description: Install and run Daedalus as a per-user Windows Start Menu app with a native window.
---

# SKILL: Windows desktop

**When**: owner says "инсталирай на Windows", "приложение", "Start Menu", "двоен клик", "графичен интерфейс".

**How**:
1. Need Python 3.11+ on PATH (`Add python.exe to PATH` at install).
2. From the repo: `packaging\windows\install.ps1` (optionally `-DesktopShortcut` / `-Startup`).
3. Or compile `packaging\windows\Daedalus.iss` with Inno Setup → `Daedalus-Setup.exe`.
4. Launch is `daedalus desktop` via `Daedalus.vbs` (no console) or `Daedalus.cmd` (logs).
5. Data is `%LOCALAPPDATA%\Daedalus`. Code is `%LOCALAPPDATA%\Programs\Daedalus`.
6. Bind loopback only (`127.0.0.1:8787`).
7. The Start Menu shortcut opens a **window**: pywebview + Edge WebView2 when `pip install -e ".[gui]"` succeeded; otherwise a Tk control panel; last resort the default browser.
8. A second click reuses the running server and opens another window (or the browser). Closing the owned window stops the local server.
9. Headless: `daedalus desktop --no-gui`. Force browser: `daedalus desktop --browser`. Force Tk: `daedalus desktop --ui tk`.

**Anti-patterns**:
- Do not ask for Administrator.
- Do not store API keys in Program Files.
- Do not bind `0.0.0.0` on a home PC.
- Do not delete `%LOCALAPPDATA%\Daedalus` on uninstall unless the owner asked (`-RemoveData`).
- Do not rebuild Studio in Tk — wrap or control the existing HTML Studio.
- Do not add Electron or a bundled Chromium; WebView2 is already on Windows 10/11.
