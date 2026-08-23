# Daedalus extraction — migration

## Done

- Product lives in `Daedalus/` (`daedalus/` package + `app/` FastAPI + Studio)
- ADN import path kept via shim (`argos_data_nexus.modules.daedalus`)
- Standalone: SQLite registry, direct LLM (AllToken/OpenRouter/…), Studio UI
- Docker / compose for PC and cloud
- Kernel full-autopilot when `DAEDALUS_STANDALONE=1`
- **ADN admin.py, templates, and Postgres schema were not moved or deleted**

## ADN safety (must hold)

| Keep | Why |
|---|---|
| Shim `argos_data_nexus/modules/daedalus/__init__.py` | ADN `from argos_data_nexus.modules.daedalus import get_daedalus` |
| `/admin/daedalus/*` routes in `admin.py` | Existing Command Center |
| `templates/daedalus*.html` + `static/daedalus-ui.*` | ADN UI |
| Unset `DAEDALUS_STANDALONE` in the ADN process | Postgres + MeshRouter stay on |

Standalone copies UI into `Daedalus/ui/` (templates + `daedalus-ui.js/css`) and
serves it at `/admin/daedalus/*`. ADN originals stay in place.

## How ADN keeps working

```python
from argos_data_nexus.modules.daedalus import get_daedalus
from argos_data_nexus.modules.daedalus.playground import list_projects
```

## How to run only Daedalus

```bash
export DAEDALUS_STANDALONE=1
cd Daedalus && python -m app.cli serve --port 8787
```

After export (no `argos_data_nexus/` sibling) the product is the repo root:
self-improve may patch `daedalus/`, `app/`, `ui/`; SQL tools talk to SQLite;
`NEED_MODULES` / code index scan this tree; upgrade-pool lives at
`.agents/upgrade-pool/pool.json`.

## Dual package name

Do not put `Daedalus-Agent/` on `sys.path` in the same process as this product
(`import daedalus` would clash). Use `daedalus.kernel` here.

## Separate git repo

`scripts/publish-standalone-repo.sh` copies **this directory only** into a new
git history and force-pushes it (`--force` for GitLab starter READMEs).

Published remote: https://gitlab.com/Blaue-Ente/deadalus-agent-0  
(Dockerfile, `daedalus/`, `app/`, `ui/` — no `argos_data_nexus/`).

ADN (`Data-Asset-Hub`) is untouched.
