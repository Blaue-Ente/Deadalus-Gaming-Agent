"""F5 skill memory: progressive disclosure + owner Approve."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def harness_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_FULL_PERSONA", raising=False)
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


def test_pending_draft_does_not_inject(harness_tmp):
    from daedalus.learned_skills import (
        draft_from_failure,
        format_block,
        list_pending,
        list_skills,
    )

    path = draft_from_failure(
        kind="static",
        errors=["SyntaxError: unexpected EOF"],
        context="snake",
    )
    assert path is not None and path.is_file()
    raw = path.read_text(encoding="utf-8")
    assert "SyntaxError" in raw
    assert not any("repair" in s["id"] or s.get("origin") == "failure" for s in list_skills())
    block = format_block("static snake syntax")
    assert "SyntaxError" not in block
    assert "Do this next time" not in block
    assert list_pending()
    pending_names = {p.get("name") for p in list_pending()}
    live_ids = {s["id"] for s in list_skills()}
    assert pending_names.isdisjoint(live_ids)


def test_approve_then_disclose_includes_body(harness_tmp):
    from daedalus.learned_skills import approve, disclose, draft_from_failure, list_pending, list_skills

    draft_from_failure(kind="static", errors=["SyntaxError: unexpected EOF"], context="snake")
    rec = list_pending()[0]
    result = approve(rec["id"])
    assert result.get("ok") is True
    assert any("repair" in s["id"] for s in list_skills())
    live = disclose("static snake syntax")
    assert "SyntaxError" in live
    assert live.count("━━━ SKILL BODY:") <= 1


def test_reject_never_goes_live(harness_tmp):
    from daedalus.learned_skills import (
        disclose,
        draft_from_failure,
        list_pending,
        list_skills,
        reject,
    )

    draft_from_failure(kind="static", errors=["NameError: x"], context="fix")
    rec = list_pending()[0]
    out = reject(rec["id"], reason="nope")
    assert out.get("ok") is True
    assert list_pending() == []
    assert not any(s["id"] == rec.get("name") for s in list_skills())
    assert "NameError" not in disclose("static NameError")


def test_catalog_is_name_and_description(harness_tmp):
    from daedalus.improvements import render_skills_block
    from daedalus.learned_skills import disclose

    empty = disclose("")
    assert empty == ""
    block = disclose("godot characterbody", extra_ids=["godot", "asset_pipeline"])
    assert "SKILL CATALOG" in block
    assert "skill action=load" in block
    assert block.count("━━━ SKILL BODY:") <= 1
    dumped = render_skills_block(["godot", "asset_pipeline", "gameplay_bot"])
    assert dumped.count("━━━ SKILL BODY:") <= 1
    assert dumped.count("━━━ SKILL:") == 0


def test_native_skill_cannot_approve(harness_tmp):
    from daedalus.learned_skills import draft_from_failure, list_pending, run_action

    draft_from_failure(kind="static", errors=["boom"], context="x")
    rec = list_pending()[0]
    denied = run_action("approve", id=rec["id"])
    assert denied.get("ok") is False
    assert "cannot approve" in (denied.get("error") or "").lower()
    catalog = run_action("catalog", query="godot")
    assert catalog.get("ok") is True
    assert catalog.get("catalog")
    loaded = run_action("load", id="godot")
    assert loaded.get("ok") is True
    assert loaded.get("body")
    pending = run_action("pending")
    assert pending.get("ok") is True
    assert pending["pending"]


def test_explorer_has_skill_not_write():
    from daedalus.native_tools import NATIVE_TOOL_SPECS
    from daedalus.subagents import tools_for_role

    spec = next(t for t in NATIVE_TOOL_SPECS if t["name"] == "skill")
    assert "catalog" in spec["description"]
    assert "skill" in tools_for_role("explorer")
    assert "skill" in tools_for_role("verify")
    assert "skill" in tools_for_role("coder")
    assert "write_file" not in tools_for_role("explorer")
    assert "write_file" not in tools_for_role("verify")
    assert "assets" not in tools_for_role("explorer")


def test_native_skill_execute_catalog(harness_tmp):
    from daedalus.native_tools import execute_native_tool

    text = asyncio.run(
        execute_native_tool(
            "skill",
            {"action": "catalog", "query": "godot"},
            memory=None,
            session_id="t",
            read_file_fn=lambda *a, **k: {"ok": False},
        )
    )
    assert "FAILED" not in text
    assert "godot" in text.lower() or "catalog" in text.lower()
    denied = asyncio.run(
        execute_native_tool(
            "skill",
            {"action": "approve", "id": "nope"},
            memory=None,
            session_id="t",
            read_file_fn=lambda *a, **k: {"ok": False},
        )
    )
    assert "FAILED" in denied
    assert "cannot approve" in denied.lower()


def test_success_draft_also_pending(harness_tmp):
    from daedalus.learned_skills import disclose, draft_from_success, list_pending, list_skills

    path = draft_from_success(kind="vite", summary="keep the racer on WebGPU")
    assert path is not None and path.is_file()
    assert not any(s.get("origin") == "success" for s in list_skills())
    assert "WebGPU" not in disclose("vite racer")
    assert list_pending()


def test_http_approve_reject_and_studio_copy(harness_tmp):
    from app.main import app
    from daedalus.learned_skills import draft_from_failure, list_skills

    client = TestClient(app)
    home = client.get("/")
    assert home.status_code == 200
    assert "Lessons" in home.text
    assert "Approve" in home.text
    i18n = client.get("/static/i18n.js")
    assert "Уроци" in i18n.text
    assert "Одобри" in i18n.text

    listed = client.get("/api/v1/daedalus/learned-skills")
    assert listed.status_code == 200
    assert "pending" in listed.json()

    path = draft_from_failure(kind="static", errors=["IndentationError: bad"], context="http")
    assert path is not None
    draft_id = path.stem
    pending = client.get("/api/v1/daedalus/learned-skills").json()["pending"]
    assert any(p["id"] == draft_id for p in pending)

    denied_native = client.post(f"/api/v1/daedalus/learned-skills/{draft_id}/approve")
    assert denied_native.status_code == 200
    assert denied_native.json().get("ok") is True
    assert any("repair" in s["id"] for s in list_skills())

    other = draft_from_failure(kind="fastapi", errors=["ImportError: missing"], context="rej")
    other_id = other.stem  # type: ignore[union-attr]
    rej = client.post(f"/api/v1/daedalus/learned-skills/{other_id}/reject")
    assert rej.status_code == 200
    leftover = client.get("/api/v1/daedalus/learned-skills").json()["pending"]
    assert not any(p["id"] == other_id for p in leftover)

    caps = client.get("/capabilities")
    assert "skill-memory" in caps.json()["capabilities"]

    cc = client.get("/admin/daedalus/skills")
    assert cc.status_code == 200
    assert "Lessons" in cc.text or "data-i18n=\"skills.lessons\"" in cc.text


def test_persona_still_compact_with_skill_tool(monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.delenv("DAEDALUS_FULL_PERSONA", raising=False)
    from daedalus.identity import build_persona

    text = build_persona()
    assert "skill" in text
    assert "Approve" in text
    assert len(text) < 12_000
    assert text.count("\n") < 200


def test_agent_discloses_once_and_success_draft_wired():
    agent = (ROOT / "daedalus" / "agent.py").read_text(encoding="utf-8")
    assembler = (ROOT / "daedalus" / "context_assembler.py").read_text(encoding="utf-8")
    assert "from .learned_skills import format_block" not in agent
    assert "from .learned_skills import format_block" not in assembler
    assert "disclose" in assembler
    assert "draft_from_success" in agent
    auto = (ROOT / "daedalus" / "kernel" / "full_autopilot.py").read_text(encoding="utf-8")
    assert "draft_from_success" in auto
    assert "disclose(" in auto


def test_knowledge_skips_pending_drafts():
    src = (ROOT / "daedalus" / "knowledge.py").read_text(encoding="utf-8")
    assert 'if "learned_skills" in path.parts and "pending" in path.parts:' in src


def test_bench_skill_memory_gate():
    from daedalus.game_agent_bench import status

    st = status()
    assert st["gates"]["skill_memory_approve"] is True
    assert st["missing"] == []

