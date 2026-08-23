---
name: windows
description: Install and run Daedalus as a per-user Windows Start Menu app.
---

# SKILL: Windows desktop

**When**: owner says "инсталирай на Windows", "приложение", "Start Menu", "двоен клик".

**How**:
1. Need Python 3.11+ on PATH (`Add python.exe to PATH` at install).
2. From the repo: `packaging\windows\install.ps1` (optionally `-DesktopShortcut` / `-Startup`).
3. Or compile `packaging\windows\Daedalus.iss` with Inno Setup → `Daedalus-Setup.exe`.
4. Launch is `daedalus desktop` via `Daedalus.vbs` (no console) or `Daedalus.cmd` (logs).
5. Data is `%LOCALAPPDATA%\Daedalus`. Code is `%LOCALAPPDATA%\Programs\Daedalus`.
6. Bind loopback only (`127.0.0.1:8787`). A second click just opens the browser.

**Anti-patterns**:
- Do not ask for Administrator.
- Do not store API keys in Program Files.
- Do not bind `0.0.0.0` on a home PC.
- Do not delete `%LOCALAPPDATA%\Daedalus` on uninstall unless the owner asked (`-RemoveData`).
