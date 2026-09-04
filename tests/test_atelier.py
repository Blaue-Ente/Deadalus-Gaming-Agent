"""Atelier: machine profile, wardrobe, offline kit, addons, capsules."""
from __future__ import annotations

import zipfile
from io import BytesIO

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def harness_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_PROVIDER", raising=False)
    monkeypatch.delenv("DAEDALUS_MODEL", raising=False)
    monkeypatch.delenv("DAEDALUS_LOCAL_LLM", raising=False)
    monkeypatch.delenv("DAEDALUS_OFFLINE", raising=False)
    monkeypatch.delenv("DAEDALUS_USE_ADN_DB", raising=False)
    from daedalus.paths import data_root, playground_projects_dir, product_root, repo_root
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    product_root.cache_clear()
    repo_root.cache_clear()
    from daedalus.store import reset_sqlite_pool
    reset_sqlite_pool()
    yield tmp_path
    reset_sqlite_pool()


def test_classify_tiers():
    from daedalus.machine_profile import classify
    assert classify(ram_gb=4, vram_gb=0) == "tiny"
    assert classify(ram_gb=16, vram_gb=0) == "laptop"
    assert classify(ram_gb=32, vram_gb=8) == "studio"
    assert classify(ram_gb=64, vram_gb=24) == "workstation"


def test_recommend_tiny_skips_32b():
    from daedalus.llm_wardrobe import recommend
    rec = recommend({"ram_gb": 8, "gpu": None})
    names = [r["ollama"] for r in rec]
    assert "qwen2.5-coder:7b" in names
    assert "qwen2.5-coder:32b" not in names
    rec_tiny = recommend({"ram_gb": 4, "gpu": None})
    assert all(r["ram_gb"] <= 4.5 for r in rec_tiny)


def test_wardrobe_activate_and_local_requested(harness_tmp, monkeypatch):
    monkeypatch.delenv("DAEDALUS_PROVIDER", raising=False)
    monkeypatch.delenv("DAEDALUS_LOCAL_LLM", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-should-not-win")
    monkeypatch.setenv("DAEDALUS_MODEL", "poolside/laguna-s-2.1:free")
    from daedalus.llm_wardrobe import activate, load, use_cloud
    from daedalus.local_llm import local_requested
    from daedalus.llm_config import get_config
    from daedalus.workers import get_worker_config
    r = activate("code-7b")
    assert r["active"] == "qwen2.5-coder:7b"
    assert load()["backend"] == "ollama"
    assert local_requested() is True
    cfg = get_config()
    assert cfg["provider"] == "ollama"
    assert cfg["model"] == "qwen2.5-coder:7b"
    assert cfg.get("local") is True
    wc = get_worker_config()
    assert wc, "worker slots should exist"
    for slot, sc in wc.items():
        assert sc["provider"] == "ollama", slot
        assert sc["model"] == "qwen2.5-coder:7b"
        assert sc.get("source") == "master_local"
    use_cloud()
    assert local_requested() is False
    cloud = get_config()
    assert cloud["provider"] == "openrouter"
    wc2 = get_worker_config()
    assert all(sc.get("source") != "master_local" for sc in wc2.values())
    assert wc2["fast"]["provider"] == "openrouter"


def test_offline_denies_web(harness_tmp):
    from daedalus.llm_wardrobe import set_offline
    from daedalus.offline import deny, enabled
    set_offline(True)
    assert enabled() is True
    msg = deny("web_read")
    assert msg and "OFFLINE" in msg
    assert deny("run_command") is None
    set_offline(False)
    assert deny("web_read") is None


def test_install_plan_does_not_pull():
    from daedalus.llm_wardrobe import install_plan
    plan = install_plan("code-7b")
    assert plan["ok"] is True
    assert "ollama pull" in plan["command"]
    assert plan["item"]["ollama"] == "qwen2.5-coder:7b"


def test_addons_enable_mcp_json(harness_tmp):
    from daedalus.addons import enable, catalog
    from daedalus.mcp_client import load_servers
    r = enable("memory")
    assert r["ok"] is True
    servers = load_servers()
    assert "memory" in servers
    assert servers["memory"]["command"] == "npx"
    rows = {a["id"]: a for a in catalog()}
    assert rows["memory"]["enabled"] is True
    assert rows["fetch"]["offline_ok"] is False
    assert "github-mcp" in rows and "gitlab-mcp" in rows
    assert rows["godot-headless"]["kind"] == "native"


def test_github_mcp_copies_token(harness_tmp, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_atelier_token")
    from daedalus.addons import enable
    from daedalus.mcp_client import load_servers
    r = enable("github-mcp")
    assert r["ok"] is True
    env = (load_servers().get("github-mcp") or {}).get("env") or {}
    assert env.get("GITHUB_PERSONAL_ACCESS_TOKEN") == "ghp_atelier_token"


def test_capsule_zip(harness_tmp):
    from daedalus.capsule import capsule_bytes, capsule_meta
    from daedalus.paths import playground_projects_dir
    root = playground_projects_dir() / "zipgame"
    root.mkdir(parents=True)
    (root / "index.html").write_text("<html><body>hi</body></html>", encoding="utf-8")
    meta = capsule_meta("zipgame")
    assert meta["has_index"] is True
    raw = capsule_bytes("zipgame")
    zf = zipfile.ZipFile(BytesIO(raw))
    assert "index.html" in zf.namelist()


def test_atelier_http(harness_tmp):
    from app.main import app
    client = TestClient(app)
    w = client.get("/api/v1/llm/wardrobe")
    assert w.status_code == 200
    body = w.json()
    assert "catalog" in body and "profile" in body
    assert any(x["id"] == "code-7b" for x in body["catalog"])

    act = client.post("/api/v1/llm/wardrobe/activate", json={"id": "talk-3b"})
    assert act.json()["active"] == "qwen2.5:3b"

    plan = client.post("/api/v1/llm/wardrobe/pull", json={"id": "code-7b", "confirm": False})
    assert plan.json().get("needs_confirm") is True

    off = client.post("/api/v1/daedalus/offline", json={"offline": True})
    assert off.json()["offline"] is True

    home = client.get("/")
    assert "Мозък" in home.text
    assert "Свързаност" in home.text
    assert "Капсула" in home.text

    loc = client.post("/api/v1/llm/wardrobe/local", json={"backend": "ollama"})
    assert loc.json().get("ok") is True
    assert loc.json().get("backend") == "ollama"

    addons = client.get("/api/v1/daedalus/addons")
    assert addons.status_code == 200
    assert len(addons.json()["addons"]) >= 4

    caps = client.get("/capabilities")
    assert "llm-wardrobe" in caps.json()["capabilities"]
    assert "offline-kit" in caps.json()["capabilities"]
    assert "capsule" in caps.json()["capabilities"]
