# Daedalus — coding agent

Games first. Apps second. Verify is part of building.

- Standalone: `DAEDALUS_STANDALONE=1`, SQLite, direct LLM.
- Next to ADN: leave that flag unset. Do not edit ADN `admin.py` playground
  routes or templates unless the task is explicitly ADN UI.
- Package: `daedalus.*`. Kernel: `daedalus.kernel`.
- Playground files: `playground/projects/{slug}/`.
- LLM: AllToken / OpenRouter / Novita / NVIDIA via `daedalus.llm_config`.
- Effort: `daedalus.task_effort` — whisper / craft / epic. Studio segmented control.
- Epic games: `world/`, `characters/`, `levels/` (see `daedalus/world_kit.py`).
- Studio: `app/static/studio.html` + `studio.css` + `studio.js`.
