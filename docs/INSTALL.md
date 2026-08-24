# Как да сложиш Дедал на компютъра

Кодът в GitLab репото **е** Дедал. Сваляш го веднъж на диска, инсталираш зависимостите, пускаш процеса. Няма магазинно приложение и **няма** готов `.exe`, който работи без Python.

## Три въпроса

**Има ли `.exe` като игра — двоен клик и готово?**  
Не. Дедал е Python (FastAPI + Studio). На Windows *има* запис в Start Menu и прозорец, но първо трябва Python 3.11+ на машината.

**Трябва ли да копирам репото?**  
Да. Или `git clone`, или ZIP от GitLab. Git не е задължителен, за да работи. Git е по-удобен само за обновяване (`git pull`).

**Къде отиват игрите и паметта?**  
Отделно от кода: `data/daedalus/` (Linux/mac), `%LOCALAPPDATA%\Daedalus` (Windows installer), `/var/lib/daedalus` (Docker).

---

## Сваляне на кода

### С git (удобно за обновяване)

```bash
git clone https://gitlab.com/Blaue-Ente/deadalus-agent-0.git
cd deadalus-agent-0
```

### Без git (ZIP)

1. Отвори https://gitlab.com/Blaue-Ente/deadalus-agent-0
2. Бутон **Code** → **Download source code** → zip  
   или директният файл:  
   https://gitlab.com/Blaue-Ente/deadalus-agent-0/-/archive/main/deadalus-agent-0-main.zip
3. Разархивирай. Папката обикновено се казва `deadalus-agent-0-main`. Вътре трябва да се виждат `daedalus/`, `app/`, `packaging/`, `README.md`.

Не копирай само един файл. Цялата папка е продуктът.

---

## Windows

### 1. Python

Свали [Python 3.11 или по-нов](https://www.python.org/downloads/windows/).  
На първия екран на инсталатора маркирай **Add python.exe to PATH**, после Install.

Проверка в PowerShell:

```powershell
py -3 --version
```

Трябва да пише 3.11, 3.12 или по-ново.

### 2. Инсталатор към Start Menu

В разархивираната папка на репото:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\packaging\windows\install.ps1 -DesktopShortcut
```

Старт → **Дедал**. Прозорец към http://127.0.0.1:8787

| Какво | Къде |
|---|---|
| Програмата (venv + код) | `%LOCALAPPDATA%\Programs\Daedalus\` |
| `.env`, памет, игри | `%LOCALAPPDATA%\Daedalus\` |
| Конзола ако не тръгва | `%LOCALAPPDATA%\Programs\Daedalus\Daedalus.cmd` |

По желание прозорец вместо браузър: след инсталацията

```powershell
%LOCALAPPDATA%\Programs\Daedalus\.venv\Scripts\pip install -e ".[gui]"
```

### 3. `Daedalus-Setup.exe`

Това **не** се сваля готово от GitLab Releases (освен ако някой го е качил ръчно). Компилира се на Windows с [Inno Setup 6](https://jrsoftware.org/isinfo.php):

```text
iscc packaging\windows\Daedalus.iss
```

Изход: `dist\Daedalus-Setup.exe`. Инсталаторът пак вика `install.ps1` и **пак иска Python**.

Не е Electron и не е замразен PyInstaller `.exe` без Python.

Пълно: [packaging/windows/README.md](../packaging/windows/README.md).

---

## Linux и macOS

В папката на репото (клон или разархивиран ZIP):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
export DAEDALUS_STANDALONE=1
python -m app.cli serve --host 127.0.0.1 --port 8787
```

Отвори http://127.0.0.1:8787

Проверка на LLM без UI: `python -m app.cli ping` и `python -m app.cli models`.

Нужен е Python 3.11+. На macOS: `brew install python@3.12` ако системният е стар.

---

## Docker

Ако имаш Docker и не искаш локален venv:

```bash
cp .env.example .env
# попълни ключ
docker compose up --build
```

Studio пак е http://127.0.0.1:8787. Данните са в volume `daedalus-data`.

---

## След като тръгне

1. Отвори Studio: http://127.0.0.1:8787
2. Сложи LLM ключ в `.env` **или** пусни Ollama и избери модел в Studio → LLM.
3. Command Center: http://127.0.0.1:8787/admin/daedalus  
   Playground: http://127.0.0.1:8787/admin/daedalus/playground

Променливи: [ENV.md](ENV.md). Облак / VPS: [DEPLOY.md](../DEPLOY.md).

---

## Обновяване

| Как си го свалил | Как се обновява |
|---|---|
| `git clone` | `git pull` после `pip install -e .` (във venv). На Windows installer: пак `install.ps1` от новия checkout. |
| ZIP | Свали нов zip. Замени папката с кода. Не трий `data/daedalus/` / `%LOCALAPPDATA%\Daedalus`. |

---

## Ако не тръгва

- `python` не се намира → Python не е в PATH; затвори и отвори терминала след инсталацията.
- Порт 8787 зает → `python -m app.cli serve --port 8788` и отвори този порт.
- Няма чат / грешка за ключ → `.env` с поне един `*_API_KEY`, или локален Ollama.
- Windows прозорец черен → пусни `Daedalus.cmd` и прочети конзолата.
