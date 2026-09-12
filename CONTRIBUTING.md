# Contributing to Daedalus

Thanks. This repository **is** the product — Python package `daedalus`, Studio, Playground.

How to run it on a laptop (ZIP or git, Windows / Linux): [docs/INSTALL.md](docs/INSTALL.md).

## Dev setup

```bash
git clone https://gitlab.com/Blaue-Ente/deadalus-agent-0.git
cd deadalus-agent-0
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
export DAEDALUS_STANDALONE=1
python3 -m pytest tests -q
```

Optional browser verify: `pip install -e ".[playwright]"` and `python -m playwright install chromium`.  
Optional Windows GUI: `pip install -e ".[gui]"`.  
Optional Play Lab guest computer: `pip install -e ".[computer]"` then `smolvm setup`.

## Change rules

1. **Games first.** If you touch the builder, the vertical slice must play, not only compile.
2. **Verify is part of building.** New Playground path → smoke (and browser, if it is UI).
3. **No silent self-editing.** `edit_modules` and harness patches go through Approve.
4. **No second orchestrator.** Do not vendor CrewAI / five MCP bridges / an in-cloud Godot Editor.
5. **Public docs talk only about Daedalus.** README, ARCHITECTURE, DEPLOY, this file — English.

## Branches and merge requests

1. Clone, branch from `main`.
2. Keep the MR focused: one idea, green tests.
3. The description says *what* changes for the owner, not only a file list.
4. Do not commit `.env`, keys, `data/`, `playground/projects/`, `.venv/`.

GitLab: [Merge requests](https://gitlab.com/Blaue-Ente/deadalus-agent-0/-/merge_requests).

## Tests

```bash
export DAEDALUS_STANDALONE=1
export COVERAGE_FILE=/tmp/daedalus-ci.coverage
python3 -m pytest tests -q -m "not docker and not playwright"
python3 -m daedalus.game_agent_bench --no-persist
```

CI (`.gitlab-ci.yml`) is hermetic: standalone on, LLM keys unset, coverage floor 45%, no Docker-in-Docker on the default pytest job. Live docker tests are `@pytest.mark.docker` (optional job). Chromium extras are `@pytest.mark.playwright`. If you add a Godot/Blender path, cover the detector/scaffold with a unit test even when the Godot binary is missing in CI.

Without docker the required gates stay fail-closed: sandbox unit tests must refuse, not fall through to the host. Do not rewrite leftover `NEED_*` holes to silence ruff — `daedalus/tool_loop.py` F821 is a preserved ignore.

`main` should require a green pipeline (`ruff`, `mypy`, `uv-lock`, `pytest`, `secret-scan`, `pip-audit`, `trivy-fs`) before merge. Image build / live docker are not required. `trivy-fs` uses `--ignore-unfixed` (same as pip-audit) and documents Studio Dockerfile `USER` in `.trivyignore.yaml`.

## Style

- Python 3.11+, no new runtime npm dependencies in product hook/scripts.
- Names: `daedalus.*` for the package, `playground/projects/{slug}/` for user projects.
- Studio UI copy is Bulgarian (owner language). GitLab docs are English. Match the file you are in.

## Bug reports

Use a GitLab Issue. Include: OS, Python version, how you installed (venv / Docker / Windows installer), expected vs actual, console log (no keys).

Template: `.gitlab/issue_templates/Bug.md`.

## Security

Do not open a public issue with an exploit. See [SECURITY.md](SECURITY.md).

## License

By contributing you agree the patch is under [MIT](LICENSE), like the rest of the repo.
