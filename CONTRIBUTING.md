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
python3 -m pytest tests -q
```

CI runs the same in `.gitlab-ci.yml`. If you add a Godot/Blender path, cover the detector/scaffold with a unit test even when the Godot binary is missing in CI.

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
