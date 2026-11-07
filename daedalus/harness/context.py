"""ProjectContext and the path resolver: one place that says where Daedalus keeps things.

Module-level path constants used to be computed once at import time, so importing a module before
``DAEDALUS_DATA`` / ``DAEDALUS_PLAYGROUND`` was set (or before ``standalone_boot`` cleared the caches)
could create a second project root. These functions resolve the path on every call; the old names stay
readable through ``legacy_paths`` so existing readers and tests keep working.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

APP_KINDS = frozenset({"fastapi", "streamlit", "node", "expo"})


def data_dir() -> Path:
    from ..paths import data_root

    return data_root()


def projects_dir() -> Path:
    from ..paths import playground_projects_dir

    return playground_projects_dir()


def published_dir() -> Path:
    from ..playground_publish import published_dir as _published

    return _published()


def data_file(*parts: str, mkdir: bool = False) -> Path:
    """``data_dir()/parts``; with ``mkdir`` the directory (or the file's parent) is created."""
    p = data_dir().joinpath(*parts)
    if mkdir:
        (p if not p.suffix else p.parent).mkdir(parents=True, exist_ok=True)
    return p


def legacy_paths(module_globals: dict[str, Any], mapping: dict[str, Callable[[], Any]]) -> None:
    """Keep old module constants readable (PEP 562): ``module.OLD_NAME`` returns the live value.

    Nothing inside the package reads these names any more; they exist for external readers and tests.
    """
    def __getattr__(name: str) -> Any:
        if name in mapping:
            return mapping[name]()
        raise AttributeError(f"module {module_globals.get('__name__')!r} has no attribute {name!r}")

    module_globals["__getattr__"] = __getattr__


# ---- ProjectContext -------------------------------------------------------------------------------------
@dataclass
class ProjectContext:
    slug: str
    dir: str
    exists: bool
    kind: str = ""
    bucket: str = "games"          # games | apps (the unified Studio rule)
    design_files: list[str] = field(default_factory=list)
    active_run: dict[str, Any] | None = None
    last_proof_level: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def bucket_for(kind: str, *, has_game_studio_config: bool) -> str:
    """Games vs Apps, first match: a Game Studio project is a game; app kinds are apps; the rest are games."""
    if has_game_studio_config:
        return "games"
    return "apps" if (kind or "").strip().lower() in APP_KINDS else "games"


def project_context(slug: str, *, kind: str = "") -> ProjectContext:
    """What the harness knows about one project: its folder, kind/bucket, design files, live run, proof."""
    from .. import playground as pg
    from . import runs

    root = pg.project_dir(slug)
    exists = root.is_dir()
    design: list[str] = []
    if exists:
        for rel in ("design/game-studio.json", "design/gdd-lite.md"):
            if (root / rel).is_file():
                design.append(rel)
    safe = pg.slugify(slug)
    owner = runs.safe(runs.owner_of, safe)
    proof = None
    rows = runs.safe(runs.list_runs, slug=safe, limit=50, default=[]) or []
    levels = [r["proof_level"] for r in rows if r.get("proof_level") is not None]
    if levels:
        proof = max(levels)
    return ProjectContext(
        slug=safe, dir=str(root), exists=exists, kind=kind,
        bucket=bucket_for(kind, has_game_studio_config="design/game-studio.json" in design),
        design_files=design, active_run=owner, last_proof_level=proof,
    )
