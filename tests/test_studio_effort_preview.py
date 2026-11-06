"""Studio effort note uses POST /api/v1/daedalus/effort (same body as classify)."""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_USE_ADN_DB", raising=False)
    from daedalus.paths import data_root, playground_projects_dir

    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    from daedalus.store import reset_sqlite_pool

    reset_sqlite_pool()
    yield tmp_path
    reset_sqlite_pool()


def test_effort_post_owner_tier_forced(isolated, monkeypatch):
    from app.main import app

    client = TestClient(app)
    got = client.post("/api/v1/daedalus/effort", json={"text": "", "effort": "epic"})
    assert got.status_code == 200
    body = got.json()
    assert body.get("tier") == "epic"
    assert body.get("source") == "owner"


def test_studio_js_uses_effort_post_not_query_only():
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "app" / "static" / "studio.js").read_text(encoding="utf-8")
    assert 'api("/api/v1/daedalus/effort"' in js
    assert "previewEffort" in js
    assert re.search(r'body:\s*JSON\.stringify\(\s*\{[^}]*effort:\s*state\.effort', js, re.S)
