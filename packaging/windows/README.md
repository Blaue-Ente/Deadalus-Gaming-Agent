# Дедал на Windows

Инсталация **без Administrator**. Двоен клик / Start Menu отваря **прозорец** с Studio
(Edge WebView2, ако `pywebview` се инсталира; иначе Tk панел; иначе браузър).
Сървърът слуша само `127.0.0.1:8787`.

## Бърз път (PowerShell)

1. Инсталирай [Python 3.11+](https://www.python.org/downloads/windows/) и маркирай **Add python.exe to PATH**.
2. Свали zip-а на репото от GitLab (или `git clone`) и го разархивирай.
3. PowerShell в папката на репото:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\packaging\windows\install.ps1
# по желание:
# .\packaging\windows\install.ps1 -DesktopShortcut
# .\packaging\windows\install.ps1 -Startup
```

4. Старт → **Дедал**. Първият старт вдига прозорец към http://127.0.0.1:8787

По желание (ако инсталаторът не успя): `%LOCALAPPDATA%\Programs\Daedalus\.venv\Scripts\pip install -e ".[gui]"` — тогава Studio е в собствен прозорец, не в Chrome.

Ключове (облак) или Ollama: `%LOCALAPPDATA%\Daedalus\.env`  
Игри / памет: `%LOCALAPPDATA%\Daedalus\`  
Код + venv: `%LOCALAPPDATA%\Programs\Daedalus\`

Ако нещо не стартира, пусни `%LOCALAPPDATA%\Programs\Daedalus\Daedalus.cmd` — там се вижда конзолата.

## Setup.exe (Inno Setup)

На Windows с [Inno Setup 6](https://jrsoftware.org/isinfo.php):

```text
iscc packaging\windows\Daedalus.iss
```

Изход: `dist\Daedalus-Setup.exe`. Инсталаторът пак вика `install.ps1 -InPlace` (трябва Python).

## Премахване

Settings → Apps → Дедал, или:

```powershell
%LOCALAPPDATA%\Programs\Daedalus\uninstall.ps1
# заедно с памет/игри:
# ...\uninstall.ps1 -RemoveData
```

## Какво не е

Това **не** е компилиран `.exe` без Python и **не** е Electron. Прозорецът е Edge WebView2
(`pywebview`) или стандартен Tk панел. Chroma + FastAPI се държат в venv. Ollama си остава
отделно приложение, ако искаш локален модел.
