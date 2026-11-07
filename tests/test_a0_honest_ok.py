"""A0-h: failure, skip and stub results never report ok:true."""
from __future__ import annotations

import asyncio

import pytest
from a0_support import a0_tmp  # noqa: F401
from fastapi.testclient import TestClient


# ---- radar -----------------------------------------------------------------

def _radar(monkeypatch, search_result):
    async def fake_web(**kwargs):
        if isinstance(search_result, Exception):
            raise search_result
        return search_result

    monkeypatch.setattr("daedalus.tools.web_search_tool", fake_web)
    monkeypatch.setattr("daedalus.tools.github_search_tool", fake_web)


def test_radar_all_sources_failed_is_not_ok(a0_tmp, monkeypatch):
    from daedalus.radar import run_radar_scan

    _radar(monkeypatch, RuntimeError("network down"))
    out = asyncio.run(run_radar_scan(object(), source_ids=None))
    assert out["ok"] is False and out["error"] == "all_sources_failed"
    assert out["sources_ok"] == 0 and len(out["failed_sources"]) == out["sources_scanned"] > 0


def test_radar_partial_success_stays_ok_and_lists_failures(a0_tmp, monkeypatch):
    from daedalus.radar import run_radar_scan

    calls = {"n": 0}

    async def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("one down")
        return {"ok": True, "result_count": 2, "results": []}

    monkeypatch.setattr("daedalus.tools.web_search_tool", flaky)
    monkeypatch.setattr("daedalus.tools.github_search_tool", flaky)
    out = asyncio.run(run_radar_scan(object(), source_ids=None))
    assert out["ok"] is True and len(out["failed_sources"]) == 1 and out["sources_ok"] >= 1


# ---- approve-all-pending ---------------------------------------------------

def test_approve_all_pending_reports_failures(a0_tmp, monkeypatch):
    from app.main import app

    monkeypatch.setattr("daedalus.self_improve.list_proposals",
                        lambda status=None: [{"proposal_id": "a"}, {"proposal_id": "b"}])
    results = {"a": {"ok": True}, "b": {"ok": False, "error": "boom"}}
    monkeypatch.setattr("daedalus.self_improve.approve_proposal", lambda pid, approved_by="": results[pid])
    body = TestClient(app).post("/admin/daedalus/proposals/approve-all-pending").json()
    assert body["ok"] is False and body["partial"] is True and body["applied"] == 1
    assert body["failed"] == [{"id": "b", "error": "boom"}]

    results["b"] = {"ok": True}
    body = TestClient(app).post("/admin/daedalus/proposals/approve-all-pending").json()
    assert body["ok"] is True and body["partial"] is False and body["failed"] == []

    monkeypatch.setattr("daedalus.self_improve.list_proposals", lambda status=None: [])
    assert TestClient(app).post("/admin/daedalus/proposals/approve-all-pending").json()["ok"] is True


# ---- blockers ----------------------------------------------------------------

def test_auto_fix_all_not_ok_when_nothing_fixed(a0_tmp, monkeypatch):
    from daedalus import blockers

    monkeypatch.setattr(blockers, "list_blockers", lambda status=None, enrich=False: [{"id": "b1"}])
    monkeypatch.setattr(blockers, "auto_fix_blocker", lambda bid, hours=8.0: {"ok": False, "error": "no hint"})
    out = blockers.auto_fix_all_open()
    assert out["ok"] is False and out["fixed_count"] == 0 and out["skipped_count"] == 1
    assert out["error"] == "no_fix_available"
    monkeypatch.setattr(blockers, "auto_fix_blocker", lambda bid, hours=8.0: {"ok": True, "granted": ["x"]})
    assert blockers.auto_fix_all_open()["ok"] is True
    monkeypatch.setattr(blockers, "list_blockers", lambda status=None, enrich=False: [])
    assert blockers.auto_fix_all_open()["ok"] is True  # nothing to fix is fine


# ---- evolution -------------------------------------------------------------

def test_evolution_cycle_not_ok_when_radar_failed_and_nothing_studied(a0_tmp, monkeypatch):
    from daedalus.evolution import run_cycle, set_enabled

    set_enabled(True)

    async def bad_radar(agent, source_ids=None, for_self=False):
        return {"ok": False, "error": "all_sources_failed", "sources": []}

    class Dummy:
        memory = None

    out = asyncio.run(run_cycle(force=True, daedalus=Dummy(), radar_fn=bad_radar,
                                study_fn=lambda url, **kw: {"ok": True}))
    assert out["skipped"] is False and out["ok"] is False and out["error"] == "all_sources_failed"


# ---- reference scout ---------------------------------------------------------

def test_reference_scout_degraded_is_failure_without_brief(a0_tmp, monkeypatch):
    from daedalus import reference_scout as rs

    async def boom(**kwargs):
        raise RuntimeError("mesh down")

    async def none(*a, **k):
        return []

    monkeypatch.setattr("daedalus.daedalus_mesh.route", boom, raising=False)
    monkeypatch.setattr(rs, "_collect_sources", none)
    saved = []
    monkeypatch.setattr(rs, "save_reference", lambda slug, res: saved.append(slug))
    out = asyncio.run(rs.scout_reference("Pong", memory=object(), save_slug="demo"))
    assert out["ok"] is False and out["degraded"] is True and out["error"] == "synthesis_failed"
    assert out["brief_block"] == "" and out["summary"] == "" and out["fallback"]["title"] == "Pong"
    assert saved == []


# ---- workers -------------------------------------------------------------------

@pytest.mark.parametrize("mesh_res,expected_ok,err", [
    ({"content": [{"type": "text", "text": "stop: budget"}], "budget_stopped": True, "ok": False}, False, "budget_stopped"),
    ({"content": [{"type": "text", "text": ""}]}, False, "empty_or_failed_reply"),
    ({"content": [{"type": "text", "text": "real answer"}], "model": "m"}, True, None),
])
def test_worker_mesh_result_ok_is_honest(a0_tmp, monkeypatch, mesh_res, expected_ok, err):
    from daedalus import daedalus_mesh as dm, workers

    async def fake_worker_complete(**kwargs):
        return mesh_res

    monkeypatch.setattr(dm, "should_route_worker_via_mesh", lambda provider, model: True)
    monkeypatch.setattr(dm, "worker_complete", fake_worker_complete)
    out = asyncio.run(workers._call_worker_single("deep", "do it", {"model": "m", "provider": "openrouter"}))
    assert out["ok"] is expected_ok
    if err:
        assert out["error"] == err


# ---- blender -------------------------------------------------------------------

def test_blender_execute_without_binary_is_a_skip(a0_tmp, monkeypatch, tmp_path):
    from daedalus import blender_jobs as b

    monkeypatch.setattr(b, "blender_bin", lambda: "")
    job = b.JOB_IDS[0]
    out = b.run_job(tmp_path, job, {"execute": True})
    assert out["ok"] is False and out["skipped"] is True and out["ran"] is False
    emitted = b.run_job(tmp_path, job, {})  # emit-only stays ok
    assert emitted["ok"] is True


# ---- skill creator ----------------------------------------------------------------

def test_skill_creator_start_stub_is_not_ok(a0_tmp, monkeypatch):
    from app.main import app

    async def boom(**kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr("daedalus.llm_client.complete", boom)
    res = TestClient(app).post("/admin/daedalus/playground/api/skill-creator/start", json={"idea": "remember exits"})
    body = res.json()
    assert res.status_code == 200 and body["ok"] is False and body["error"] == "llm_failed" and body["stub"] is True
    assert body["research_notes"] == "remember exits"  # the stub session is still visible
