# Daedalus — coding agent

Games first. Apps second. Verify is part of building.

- This repository **is** the product. Run with `DAEDALUS_STANDALONE=1` (SQLite, direct LLM).
- Package: `daedalus.*`. Kernel: `daedalus.kernel`.
- Playground files: `playground/projects/{slug}/`.
- LLM: AllToken / OpenRouter / Novita / NVIDIA via `daedalus.llm_config`. Local: Ollama / LM Studio / llama.cpp / vLLM.
- Effort: `daedalus.task_effort` — whisper / craft / epic. Studio segmented control.
- Native tool rounds: optional `DAEDALUS_NATIVE_ROUNDS` (1–64) caps writer rounds; otherwise whisper caps at 8 and epic floors at 20 when `task_effort` is bound.
- Epic games: `world/`, `characters/`, `levels/` (see `daedalus/world_kit.py`).
- Studio: `app/static/studio.html` + `studio.css` + `studio.js`.
- Do not silent-edit the harness. Self-improve patches wait for Approve.
- Public docs (README, ARCHITECTURE, DEPLOY, CONTRIBUTING) are English and describe only Daedalus.
- Mark: `assets/logo/` (labyrinth + compass-bird). UI copy is English by default; Bulgarian only when the owner picks **BG**.
