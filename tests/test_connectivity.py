"""Studio → Свързаност: secrets.env, mask, recommendations, HTTP."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_LLM_KEYS = (
    "OPENROUTER_API_KEY", "ALLTOKEN_API_KEY", "NOVITA_API_KEY",
    "NVIDIA_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
    "DAEDALUS_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY",
    "DAEDALUS_PROVIDER", "DAEDALUS_MODEL",
)
_GIT_KEYS = ("GITHUB_TOKEN", "GITHUB_INTEGRATION_TOKEN", "GITLAB_TOKEN", "GitLabToken")
_PUBLISH = ("NETLIFY_AUTH_TOKEN",)


@pytest.fixture
def conn_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_USE_ADN_DB", raising=False)
    monkeypatch.delenv("DAEDALUS_LOCAL_LLM", raising=False)
    monkeypatch.delenv("DAEDALUS_OFFLINE", raising=False)
    for key in (*_LLM_KEYS, *_GIT_KEYS, *_PUBLISH):
        monkeypatch.delenv(key, raising=False)
    from daedalus.paths import data_root, playground_projects_dir, product_root, repo_root
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    product_root.cache_clear()
    repo_root.cache_clear()
    from daedalus.store import reset_sqlite_pool
    reset_sqlite_pool()
    yield tmp_path
    reset_sqlite_pool()


def test_mask_and_forbidden_keys(conn_tmp):
    from daedalus.connectivity import (
        FORBIDDEN_KEYS, mask_secret, set_secrets, load_secrets_file, secrets_path,
    )
    assert mask_secret("short") == "••••"
    assert mask_secret("ghp_abcdEFGH12") == "gh••••GH12"
    assert mask_secret("ollama", secret=False) == "ollama"
    assert "DAEDALUS_TOKEN" in FORBIDDEN_KEYS

    stored = set_secrets({
        "OPENROUTER_API_KEY": "sk-or-secret-value-zzzz",
        "DAEDALUS_TOKEN": "must-not-land",
        "PATH": "/tmp/evil",
        "NOT_A_REAL_KEY": "nope",
    })
    assert stored["OPENROUTER_API_KEY"] == "sk-or-secret-value-zzzz"
    assert "DAEDALUS_TOKEN" not in stored
    assert "PATH" not in stored
    assert "NOT_A_REAL_KEY" not in stored
    assert os.environ.get("OPENROUTER_API_KEY") == "sk-or-secret-value-zzzz"
    path = secrets_path()
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "sk-or-secret-value-zzzz" in text
    assert "must-not-land" not in text
    assert (path.stat().st_mode & 0o777) == 0o600

    empty = set_secrets({"OPENROUTER_API_KEY": ""})
    assert empty["OPENROUTER_API_KEY"] == "sk-or-secret-value-zzzz"

    cleared = set_secrets(clear=["OPENROUTER_API_KEY"])
    assert "OPENROUTER_API_KEY" not in cleared
    assert "OPENROUTER_API_KEY" not in load_secrets_file()
    assert not os.environ.get("OPENROUTER_API_KEY")


def test_recommendations_order(conn_tmp):
    from daedalus.connectivity import recommendations, set_secrets
    ids = [r["id"] for r in recommendations()]
    assert ids[0] == "need-llm"
    assert "need-git" in ids

    set_secrets({"OPENROUTER_API_KEY": "sk-or-x", "DAEDALUS_PROVIDER": "openrouter"})
    ids = [r["id"] for r in recommendations()]
    assert "need-llm" not in ids
    assert "or-model" in ids

    set_secrets({"DAEDALUS_MODEL": "poolside/laguna-s-2.1:free", "GITHUB_TOKEN": "ghp_testtokenxx"})
    ids = [r["id"] for r in recommendations()]
    assert "or-model" not in ids
    assert "need-git" not in ids
    assert "need-publish" in ids

    set_secrets({"NETLIFY_AUTH_TOKEN": "nfp_x"})
    ids = [r["id"] for r in recommendations()]
    assert ids == ["ready"]


def test_overview_never_returns_raw_secret(conn_tmp):
    from daedalus.connectivity import overview, set_secrets
    set_secrets({"OPENROUTER_API_KEY": "sk-or-raw-secret-SHOULD-NOT-LEAK"})
    data = overview()
    blob = str(data)
    assert "sk-or-raw-secret-SHOULD-NOT-LEAK" not in blob
    llm = next(g for g in data["groups"] if g["id"] == "llm")
    field = next(f for f in llm["fields"] if f["key"] == "OPENROUTER_API_KEY")
    assert field["set"] is True
    assert field["source"] == "ui"
    assert "sk-or" not in field["mask"] or "••••" in field["mask"]
    assert field["mask"] != "sk-or-raw-secret-SHOULD-NOT-LEAK"
    brain = data.get("brain") or {}
    assert "effective" in brain
    assert brain["effective"].get("has_key") is True
    assert "api_key" not in brain["effective"]
    blob_brain = str(brain)
    assert "sk-or-raw-secret-SHOULD-NOT-LEAK" not in blob_brain


def test_connectors_http_save_and_page(conn_tmp):
    from app.main import app
    client = TestClient(app)

    page = client.get("/admin/daedalus/connectors")
    assert page.status_code == 200
    assert "Свързаност" in page.text
    assert "secrets.env" in page.text
    assert 'id="brain"' in page.text
    assert "един за цял Дедал" in page.text
    assert 'id="brain"' in page.text
    assert "един за цял Дедал" in page.text

    overview = client.get("/admin/daedalus/connectors/overview")
    assert overview.status_code == 200
    body = overview.json()
    assert body.get("ok") is True
    assert "brain" in body
    assert "effective" in (body.get("brain") or {})
    groups = {g["id"] for g in body["groups"]}
    assert {"llm", "git", "publish", "mcp", "lab"} <= groups
    rec_ids = {r["id"] for r in body["recommendations"]}
    assert "need-llm" in rec_ids
    keys = {f["key"] for g in body["groups"] for f in g["fields"]}
    assert "GITHUB_TOKEN" in keys and "GITLAB_TOKEN" in keys and "OPENROUTER_API_KEY" in keys
    assert "DAEDALUS_TOKEN" not in keys

    saved = client.post(
        "/admin/daedalus/connectors/secrets",
        json={"secrets": {"OPENROUTER_API_KEY": "sk-or-http-secret-zzzz"}},
    )
    assert saved.status_code == 200
    assert saved.json()["ok"] is True
    assert "OPENROUTER_API_KEY" in saved.json()["set"]
    assert "sk-or-http-secret-zzzz" not in saved.text

    again = client.get("/admin/daedalus/connectors/overview")
    llm = next(g for g in again.json()["groups"] if g["id"] == "llm")
    field = next(f for f in llm["fields"] if f["key"] == "OPENROUTER_API_KEY")
    assert field["set"] is True
    assert "sk-or-http-secret-zzzz" not in again.text

    forbidden = client.post(
        "/admin/daedalus/connectors/secrets",
        json={"secrets": {"DAEDALUS_TOKEN": "nope", "PATH": "/tmp"}},
    )
    assert forbidden.json()["ok"] is True
    assert forbidden.json()["set"] == []
    secrets = (Path(os.environ["DAEDALUS_DATA"]) / "secrets.env")
    if secrets.is_file():
        assert "DAEDALUS_TOKEN" not in secrets.read_text(encoding="utf-8")
        assert "nope" not in secrets.read_text(encoding="utf-8")

    cleared = client.post(
        "/admin/daedalus/connectors/secrets",
        json={"clear": ["OPENROUTER_API_KEY"]},
    )
    assert cleared.json()["ok"] is True
    field2 = next(
        f for g in client.get("/admin/daedalus/connectors/overview").json()["groups"]
        if g["id"] == "llm"
        for f in g["fields"] if f["key"] == "OPENROUTER_API_KEY"
    )
    assert field2["set"] is False


def test_mcp_toggle_copies_github_token(conn_tmp, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_from_ui_xxxx")
    from app.main import app
    from daedalus.mcp_client import load_servers
    client = TestClient(app)
    r = client.post("/admin/daedalus/connectors/mcp", json={"id": "github-mcp", "enabled": True})
    assert r.status_code == 200
    assert r.json().get("ok") is True
    servers = load_servers()
    assert "github-mcp" in servers
    env = servers["github-mcp"].get("env") or {}
    assert env.get("GITHUB_PERSONAL_ACCESS_TOKEN") == "ghp_from_ui_xxxx"
    off = client.post("/admin/daedalus/connectors/mcp", json={"id": "github-mcp", "enabled": False})
    assert off.json().get("ok") is True
    assert "github-mcp" not in load_servers()


def test_secrets_provider_syncs_wardrobe(conn_tmp):
    from daedalus.connectivity import set_secrets
    from daedalus.llm_config import get_config
    from daedalus.llm_wardrobe import load
    from daedalus.local_llm import local_requested
    from daedalus.workers import get_worker_config

    set_secrets({"DAEDALUS_PROVIDER": "ollama", "DAEDALUS_MODEL": "qwen2.5:3b"})
    assert load()["backend"] == "ollama"
    assert local_requested() is True
    cfg = get_config()
    assert cfg["provider"] == "ollama"
    assert cfg.get("local") is True
    wc = get_worker_config()
    assert all(sc["provider"] == "ollama" for sc in wc.values())
    assert all(sc.get("source") == "master_local" for sc in wc.values())

    set_secrets({"DAEDALUS_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "sk-or-after-cloud"})
    assert load()["backend"] == "cloud"
    assert local_requested() is False
    cloud = get_config()
    assert cloud["provider"] == "openrouter"


def test_connectors_brain_http_activate(conn_tmp, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-http-must-not-leak")
    from app.main import app
    client = TestClient(app)
    act = client.post("/api/v1/llm/wardrobe/activate", json={"id": "talk-3b"})
    assert act.status_code == 200
    assert act.json()["active"] == "qwen2.5:3b"
    ov = client.get("/admin/daedalus/connectors/overview")
    assert ov.status_code == 200
    brain = ov.json()["brain"]
    assert brain["mode"] == "local"
    assert brain["effective"]["provider"] == "ollama"
    assert "sk-or-http-must-not-leak" not in ov.text
    workers = brain.get("workers") or {}
    assert workers
    assert all(s["provider"] == "ollama" for s in workers.values())
    loc = client.post("/api/v1/llm/wardrobe/local", json={"backend": "ollama"})
    assert loc.status_code == 200
    assert loc.json().get("ok") is True
    cloud = client.post("/api/v1/llm/wardrobe/cloud")
    assert cloud.json().get("backend") == "cloud"
