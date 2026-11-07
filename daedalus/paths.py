"""Filesystem roots for the Daedalus product.

Monorepo layout::

    <repo>/
      argos_data_nexus/     ADN host (optional mesh/LLM/DB)
      Daedalus/             this product
        daedalus/           canonical Python package
      Daedalus-Agent/       portable TDD/sandbox pip slice
      playground/projects/  Playground workspaces
      data/daedalus/        memory, proposals, skills
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def product_root() -> Path:
    """``Daedalus/`` directory (parent of this package)."""
    env = os.environ.get("DAEDALUS_PRODUCT_ROOT")
    if env:
        return Path(env).resolve()
    return Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def package_root() -> Path:
    """``Daedalus/daedalus/`` — the Python package directory."""
    return Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def repo_root() -> Path:
    """Monorepo root when present; otherwise the product directory."""
    env = os.environ.get("DAEDALUS_REPO_ROOT")
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve()
    for p in here.parents:
        if (p / "argos_data_nexus").is_dir() and (p / "Daedalus").is_dir():
            return p
        if (p / ".git").is_dir() and (p / "Daedalus").is_dir():
            return p
    return product_root()


@lru_cache(maxsize=1)
def adn_package_root() -> Path | None:
    """ADN Python package when this is a monorepo checkout; ``None`` after export."""
    env = os.environ.get("DAEDALUS_ADN_ROOT")
    if env:
        path = Path(env).resolve()
        return path if path.is_dir() else None
    candidate = repo_root() / "argos_data_nexus"
    if candidate.is_dir():
        return candidate
    return None


@lru_cache(maxsize=1)
def data_root() -> Path:
    env = os.environ.get("DAEDALUS_DATA") or os.environ.get("DAEDALUS_DATA_DIR")
    if env:
        path = Path(env).resolve()
    else:
        path = repo_root() / "data" / "daedalus"
    path.mkdir(parents=True, exist_ok=True)
    return path


def chroma_dir() -> Path:
    """Persistent Chroma store — lives under DAEDALUS_DATA (Docker volume)."""
    path = data_root() / "chroma"
    path.mkdir(parents=True, exist_ok=True)
    return path


@lru_cache(maxsize=1)
def playground_projects_dir() -> Path:
    env = os.environ.get("DAEDALUS_PLAYGROUND")
    if env:
        path = Path(env).resolve()
    else:
        from .host import standalone_mode
        if standalone_mode():
            path = data_root() / "playground"
        else:
            path = repo_root() / "playground" / "projects"
    path.mkdir(parents=True, exist_ok=True)
    return path


def kernel_root() -> Path:
    """In-package kernel (absorbed Daedalus-Agent modules)."""
    return package_root() / "kernel"
