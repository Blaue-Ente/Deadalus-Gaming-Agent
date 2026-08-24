# Changelog

Формат: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).  
Версията в `pyproject.toml` е източникът на истина за пакета.

## [0.3.4] — 2026-08-24

Текущото състояние на продукта в това GitLab репо.

### Агент

- FastAPI Studio на `/` и Command Center на `/admin/daedalus/*`.
- Нативни инструменти (файлове, shell, търсене, браузър, MCP, подагенти, playtest).
- SQLite + Chroma памет; предпочитания и научени умения.
- Автопилот с Definition of Done; предложения с Approve gate.
- Опционална еволюция (idle gardener) и офлайн кит.

### Игри

- Playground kinds: static, vite, vite-react, fastapi, streamlit, node, expo, unity, godot.
- Arcade craft: Vite + Three.js WebGPU racers (`src/racer/`).
- Godot 4: 2D scaffold и 3D greybox стая (плоча → врата), headless probe ако има бинарен Godot.
- Blender jobs: emit-only (без `bpy` в процеса на Дедал).
- Капсули и Наживо (Netlify / GitLab Pages / 1h preview).

### Доставка

- CLI: `daedalus serve | ping | models | desktop`.
- Docker Compose + GitLab CI образ.
- Windows Start Menu инсталатор (WebView2 / Tk).
- LLM: AllToken, OpenRouter, Novita, NVIDIA, Ollama и други локални бекенди.

### Документация

- README и спътниците описват само Дедал като самостоятелен продукт.
