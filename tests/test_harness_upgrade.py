"""Harness upgrades: local LLM, shell jail, preferences, DoD, native tool shapes."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def harness_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_PROVIDER", raising=False)
    monkeypatch.delenv("DAEDALUS_LOCAL_LLM", raising=False)
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


def test_local_provider_helpers():
    from daedalus.local_llm import is_local_provider, normalize_provider, _ensure_v1
    assert is_local_provider("ollama")
    assert is_local_provider("LM Studio")
    assert normalize_provider("llama.cpp") == "llamacpp"
    assert _ensure_v1("127.0.0.1:11434") == "http://127.0.0.1:11434/v1"
    assert _ensure_v1("http://localhost:11434") == "http://localhost:11434/v1"
    assert _ensure_v1("http://localhost:11434/v1") == "http://localhost:11434/v1"


def test_get_config_prefers_explicit_ollama(monkeypatch):
    monkeypatch.setenv("DAEDALUS_PROVIDER", "ollama")
    monkeypatch.setenv("DAEDALUS_MODEL", "qwen2.5-coder:7b")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("ALLTOKEN_API_KEY", raising=False)
    monkeypatch.delenv("NOVITA_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    from daedalus.llm_config import get_config
    cfg = get_config()
    assert cfg["provider"] == "ollama"
    assert cfg["local"] is True
    assert cfg["api_key"]
    assert cfg["base_url"].endswith("/v1")


def test_shell_echo_and_block_sudo(harness_tmp):
    from daedalus.shell_center import run_command
    ok = run_command("echo daedalus-shell")
    assert ok["ok"] is True
    assert "daedalus-shell" in ok["stdout"]
    bad = run_command("sudo ls")
    assert bad["ok"] is False
    assert bad["blocked"] is True


def test_preferences_explicit_speech(harness_tmp):
    from daedalus.preferences import observe_turn, format_block, load
    rows = observe_turn("Запомни, че предпочитам Phaser и български")
    assert rows
    items = load()
    assert any("Phaser" in i["text"] or "phaser" in i["text"].lower() or "предпочитам" in i["text"].lower() for i in items)
    block = format_block()
    assert "OWNER PREFERENCES" in block


def test_job_contract_coding_vs_chat():
    from daedalus.job_contract import evaluate
    chat = evaluate("как си днес?", "Добре, благодаря.")
    assert chat.coding_task is False
    assert chat.complete is True
    coding = evaluate(
        "напиши snake игра",
        "Готово.",
        tool_calls=[],
    )
    assert coding.coding_task is True
    assert coding.complete is False
    assert coding.blockers


def test_learned_skill_from_failure(harness_tmp):
    from daedalus.learned_skills import (
        approve,
        draft_from_failure,
        format_block,
        list_pending,
        list_skills,
    )
    path = draft_from_failure(kind="static", errors=["SyntaxError: unexpected EOF"], context="snake")
    assert path and path.is_file()
    assert path.suffix == ".json"
    assert "pending" in str(path)
    assert not any("repair" in s["id"] for s in list_skills())
    block = format_block("static snake syntax")
    assert "SyntaxError" not in block
    rec = list_pending()[0]
    assert rec["id"] in path.read_text(encoding="utf-8")
    ok = approve(rec["id"])
    assert ok.get("ok") is True
    live = format_block("static snake syntax")
    assert "SyntaxError" in live or "repair" in live.lower()


def test_openai_tool_message_roundtrip():
    from daedalus.native_tools import openai_tool_specs, to_openai_messages
    specs = openai_tool_specs()
    names = {s["function"]["name"] for s in specs}
    assert "run_command" in names
    assert "generate_image" in names
    assert "remember" in names
    assert "web_read" in names
    assert "spawn_subagent" in names
    oa = to_openai_messages("sys", [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "ok"},
            {"type": "tool_use", "id": "c1", "name": "run_command", "input": {"command": "ls"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "c1", "content": "ok"},
        ]},
    ])
    assert oa[0]["role"] == "system"
    assert any(m.get("tool_calls") for m in oa)
    assert any(m.get("role") == "tool" for m in oa)


def test_studio_and_harness_http(harness_tmp):
    from app.main import app
    client = TestClient(app)
    home = client.get("/")
    assert home.status_code == 200
    assert "Дедал" in home.text
    assert "daedalus-mark.png" in home.text
    assert "favicon.ico" in home.text
    assert "Свят" in home.text

    logo = client.get("/static/daedalus-mark.png")
    assert logo.status_code == 200

    sh = client.post("/api/v1/daedalus/shell", json={"command": "echo studio"})
    assert sh.status_code == 200
    body = sh.json()
    assert body["ok"] is True
    assert "studio" in body["stdout"]

    blocked = client.post("/api/v1/daedalus/shell", json={"command": "sudo reboot"})
    assert blocked.json().get("blocked") is True

    pref = client.post("/api/v1/daedalus/preferences", json={"text": "always reply in Bulgarian"})
    assert pref.status_code == 200
    listed = client.get("/api/v1/daedalus/preferences")
    assert listed.status_code == 200
    assert listed.json()["items"]

    local = client.get("/api/v1/llm/local")
    assert local.status_code == 200
    assert "backends" in local.json()

    caps = client.get("/capabilities")
    assert "local-llm" in caps.json()["capabilities"]
    assert "shell-center" in caps.json()["capabilities"]
    assert "playtest" in caps.json()["capabilities"]
    assert "dissection" in caps.json()["capabilities"]
