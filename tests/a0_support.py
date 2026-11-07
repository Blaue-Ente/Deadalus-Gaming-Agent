"""Shared fixture for the A0 safety/honesty regression tests (temp data root, standalone)."""
from __future__ import annotations

import pytest


@pytest.fixture
def a0_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    for name in (
        "DAEDALUS_PROVIDER", "DAEDALUS_MODEL", "DAEDALUS_LOCAL_LLM", "DAEDALUS_OFFLINE",
        "DAEDALUS_USE_ADN_DB", "DAEDALUS_USE_MESH_ROUTER", "DAEDALUS_MENTOR",
        "DAEDALUS_RUN_BUDGET", "DAEDALUS_WRITER_ASSIST", "DAEDALUS_MENTOR_MODEL",
        "DAEDALUS_MENTOR_PROVIDER",
    ):
        monkeypatch.delenv(name, raising=False)
    from daedalus.paths import data_root, playground_projects_dir, product_root, repo_root
    for fn in (data_root, playground_projects_dir, product_root, repo_root):
        fn.cache_clear()
    from daedalus.store import reset_sqlite_pool
    reset_sqlite_pool()
    yield tmp_path
    reset_sqlite_pool()
