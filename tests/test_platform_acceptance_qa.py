"""Platform Acceptance QA — 15 fail-closed assertions. No live Chromium/Godot/Xcode."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from daedalus.capability_registry import (
    GODOT_GAMEPLAY_PROOFS,
    REGISTRY,
    RUNNER_VERSION,
    WEB_GAMEPLAY_PROOFS,
    Capability,
    DEVICE_SMOKE_PROOFS,
    evidence_record,
)
from daedalus.learned_skills import MAX_SKILL_CHARS, list_skills, load_skill
from daedalus.platform_acceptance import (
    ASSERTIONS,
    CHECK_ORDER,
    EXAMPLE_PROMPTS,
    PROFILES,
    SELECT_KEYWORDS,
    acceptance_result,
    candidate_id,
    claims_approved,
    contradictory_approval,
    looks_like_qa,
    profile_for,
    required_check_ids,
    run_qa,
    scenario_shape,
    ship_language_blocked,
)


def _pass(
    capability: str,
    *,
    target: str = "godot",
    revision: str = "r2",
    artifact_hash: str = "sha256:aaa",
    project_id: str = "dungeon-001",
    details: str = "ok",
    checks: dict[str, bool] | None = None,
):
    if checks is None:
        if capability == Capability.GAMEPLAY_PROOF.value:
            keys = GODOT_GAMEPLAY_PROOFS if target == "godot" else WEB_GAMEPLAY_PROOFS
            checks = {k: True for k in keys}
        elif capability == Capability.DEVICE_SMOKE.value:
            checks = {k: True for k in DEVICE_SMOKE_PROOFS}
        else:
            checks = {}
    return evidence_record(
        capability,
        status="pass",
        project_id=project_id,
        revision=revision,
        artifact_hash=artifact_hash,
        details=details,
        runner_version=RUNNER_VERSION,
        checks=checks,
    )


def _all_pass(platform: str):
    return [
        _pass(c.value, target=platform)
        for c in REGISTRY.release_capabilities(platform)
    ]


def test_a1_candidate_identity_formula():
    cid = candidate_id("dungeon-001", "godot", "r2", "sha256:aaa")
    assert cid == "dungeon-001+godot+r2+sha256:aaa"
    assert "project_id + target + revision + artifact_hash" in " ".join(
        s for _, s in ASSERTIONS
    )


def test_a2_statuses_are_closed_set():
    bogus = acceptance_result(
        project_id="p",
        target="web",
        revision="r1",
        artifact_hash="sha256:aaa",
        evidence=[
            evidence_record(
                "run",
                status="maybe",
                project_id="p",
                revision="r1",
                artifact_hash="sha256:aaa",
            )
        ],
    )
    run_row = next(r for r in bogus["checks"] if r["id"] == "run")
    assert run_row["status"] == "blocked"
    assert bogus["release"]["approved"] is False
    assert {row["status"] for row in bogus["checks"]} <= {"pass", "fail", "skipped", "blocked"}


def test_a3_skipped_blocked_fail_are_not_success():
    for status in ("skipped", "blocked", "fail"):
        ev = [replace(row, status=status) if row.capability == "run" else row for row in _all_pass("web")]
        result = acceptance_result(
            project_id="dungeon-001",
            target="web",
            revision="r2",
            artifact_hash="sha256:aaa",
            evidence=ev,
        )
        assert result["release"]["approved"] is False
        assert result["ok"] is False


def test_a4_unknown_platform_fail_closed():
    result = acceptance_result(
        project_id="u",
        target="unity",
        revision="r1",
        artifact_hash="sha256:aaa",
        evidence=[],
    )
    assert result["release"]["approved"] is False
    assert "unsupported platform" in " ".join(result["release"]["blockers"]).lower()
    with pytest.raises(Exception, match="unsupported"):
        profile_for("unity")


def test_a5_web_canvas_is_not_gameplay():
    fake = _pass(
        "gameplay_proof",
        target="web",
        details="canvas and index.html exist",
        checks={},
    )
    ev = [fake if row.capability == "gameplay_proof" else row for row in _all_pass("web")]
    result = acceptance_result(
        project_id="dungeon-001",
        target="web",
        revision="r2",
        artifact_hash="sha256:aaa",
        evidence=ev,
    )
    assert result["release"]["approved"] is False
    gp = next(r for r in result["checks"] if r["id"] == "gameplay_proof")
    assert gp["status"] != "pass"


def test_a6_godot_files_are_not_play():
    fake = _pass(
        "gameplay_proof",
        target="godot",
        details=".tscn and .gd files exist",
        checks={},
    )
    ev = [fake if row.capability == "gameplay_proof" else row for row in _all_pass("godot")]
    result = acceptance_result(
        project_id="dungeon-001",
        target="godot",
        revision="r2",
        artifact_hash="sha256:aaa",
        evidence=ev,
    )
    assert result["release"]["approved"] is False
    assert "device_smoke" not in required_check_ids("godot")
    assert "scene" in scenario_shape("godot")


def test_a7_mobile_web_preview_is_not_device_smoke():
    fake = _pass(
        "device_smoke",
        target="expo",
        details="expo web preview",
        checks={},
    )
    ev = [fake if row.capability == "device_smoke" else row for row in _all_pass("expo")]
    result = acceptance_result(
        project_id="dungeon-001",
        target="expo",
        revision="r2",
        artifact_hash="sha256:aaa",
        evidence=ev,
    )
    assert result["release"]["approved"] is False
    assert "device_smoke" in required_check_ids("expo")
    assert profile_for("capacitor")["id"] == "mobile"


def test_a8_missing_runner_is_blocked_not_pass():
    result = acceptance_result(
        project_id="p",
        target="web",
        revision="r2",
        artifact_hash="sha256:aaa",
        evidence=[],
    )
    assert result["release"]["approved"] is False
    run_row = next(r for r in result["checks"] if r["id"] == "run")
    assert run_row["status"] == "blocked"
    assert "chromium" in PROFILES["web"]["runner"]
    assert "godot" in PROFILES["godot"]["runner"]
    assert "device" in PROFILES["mobile"]["runner"]


def test_a9_stale_revision_and_hash_mismatch():
    ev = _all_pass("godot")
    r1 = acceptance_result(
        project_id="dungeon-001",
        target="godot",
        revision="r2",
        artifact_hash="sha256:aaa",
        evidence=[replace(row, revision="r1") if row.capability == "run" else row for row in ev],
    )
    assert r1["release"]["approved"] is False
    r2 = acceptance_result(
        project_id="dungeon-001",
        target="godot",
        revision="r2",
        artifact_hash="sha256:aaa",
        evidence=[
            replace(row, artifact_hash="sha256:bbb") if row.capability == "export" else row
            for row in ev
        ],
    )
    assert r2["release"]["approved"] is False
    r3 = acceptance_result(
        project_id="dungeon-001",
        target="godot",
        revision="r2",
        artifact_hash="sha256:aaa",
        evidence=ev,
        stale=True,
    )
    assert r3["release"]["approved"] is False
    assert any("stale" in b for b in r3["release"]["blockers"])


def test_a10_publish_requires_matching_hashed_artifact():
    ok = acceptance_result(
        project_id="dungeon-001",
        target="godot",
        revision="r2",
        artifact_hash="sha256:aaa",
        evidence=_all_pass("godot"),
        rollback_ready=True,
    )
    assert ok["release"]["approved"] is True
    assert ok["release"]["rollback_ready"] is True
    missing = acceptance_result(
        project_id="dungeon-001",
        target="godot",
        revision="r2",
        artifact_hash="",
        evidence=_all_pass("godot"),
    )
    assert missing["release"]["approved"] is False


def test_a11_forbidden_ship_words_when_not_approved():
    hits = ship_language_blocked(
        "This build is ready, done, shipped, publishable, готово", approved=False,
    )
    assert set(hits) >= {"ready", "done", "shipped", "publishable", "готово"}
    result = acceptance_result(
        project_id="p",
        target="web",
        revision="r1",
        artifact_hash="sha256:aaa",
        evidence=[],
    )
    assert result["release"]["approved"] is False
    assert "not publishable" in result["summary"].lower()
    assert "do not claim" in result["summary"].lower()


def test_a12_approved_inside_not_approved():
    assert claims_approved("NOT APPROVED") is False
    assert claims_approved("APPROVED") is True
    assert claims_approved("Verdict: NOT APPROVED (was APPROVED by mistake)") is False
    assert contradictory_approval("NOT APPROVED then APPROVED") is True
    assert contradictory_approval("NOT APPROVED") is False


def test_a13_compile_is_not_visual_or_gameplay_gate():
    ev = [replace(row, status="fail") if row.capability == "render_proof" else row for row in _all_pass("web")]
    result = acceptance_result(
        project_id="dungeon-001",
        target="web",
        revision="r2",
        artifact_hash="sha256:aaa",
        evidence=ev,
    )
    assert any(r["id"] == "run" and r["status"] == "pass" for r in result["checks"])
    assert result["release"]["approved"] is False
    assert any("render_proof" in b for b in result["release"]["blockers"])


def test_a14_model_score_cannot_override_missing_visual():
    ev = [row for row in _all_pass("web") if row.capability != "render_proof"]
    result = acceptance_result(
        project_id="dungeon-001",
        target="web",
        revision="r2",
        artifact_hash="sha256:aaa",
        evidence=ev,
        quality_score={"overall": 96},
    )
    assert result["release"]["approved"] is False
    assert any("overall score" in b for b in result["release"]["blockers"])


def test_a15_promised_features_are_not_silently_dropped():
    result = acceptance_result(
        project_id="dungeon-001",
        target="web",
        revision="r2",
        artifact_hash="sha256:aaa",
        evidence=_all_pass("web"),
        extra_promises=["save/load", "gamepad"],
    )
    assert result["release"]["approved"] is False
    joined = " ".join(result["release"]["blockers"])
    assert "save/load" in joined
    assert "gamepad" in joined


def test_skill_and_prompts_and_injected_core_contract():
    assert len(ASSERTIONS) == 15
    ids = {s["id"] for s in list_skills()}
    assert "platform_acceptance_qa" in ids
    loaded = load_skill("platform_acceptance_qa")
    assert loaded
    raw = loaded["raw"]
    body = loaded["body"]
    assert loaded["meta"].get("name") == "platform-acceptance-qa"
    for aid, _stmt in ASSERTIONS:
        assert aid in raw
    assert len(EXAMPLE_PROMPTS) >= 6
    assert "Phaser dungeon" in raw
    assert "scenes/main.tscn" in raw
    assert "web ZIP" in raw
    core = body[:MAX_SKILL_CHARS]
    assert "skipped" in core
    assert "artifact_hash" in core or "artifact hash" in core
    assert "pass" in core
    assert CHECK_ORDER[0] == "create"
    assert "device_smoke" in CHECK_ORDER


def test_select_skills_and_prefer_qa():
    from daedalus import improvements
    from daedalus.game_engineer import extra_skill_ids, prefer_skill_id

    improvements._SKILLS_LOADED = False
    improvements._SKILLS_CACHE.clear()
    names = improvements.select_skills(
        "Run platform acceptance QA with fail-closed device_smoke",
        "verify",
    )
    assert "platform_acceptance_qa" in names
    assert SELECT_KEYWORDS
    extra = extra_skill_ids("Направи змия в playground", "build")
    assert "platform_acceptance_qa" in extra
    assert prefer_skill_id("platform acceptance QA for this slug") == "platform_acceptance_qa"
    assert prefer_skill_id("Направи 3D игра с octree physics", intent="build") == "senior_tools"
    assert looks_like_qa("release gate + artifact hash")


def test_production_qa_action_fail_closed(tmp_path: Path, monkeypatch):
    from daedalus.production_layer import run_action

    monkeypatch.setattr("daedalus.playground.project_dir", lambda slug: tmp_path / slug)
    missing = run_action("qa", "nope")
    assert missing.get("ok") is False
    (tmp_path / "snake").mkdir()
    (tmp_path / "snake" / "index.html").write_text("<canvas></canvas>", encoding="utf-8")
    qa = run_action("qa", "snake", kind="static")
    assert qa["ok"] is False
    assert qa["release"]["approved"] is False
    assert qa["release"]["blockers"]
    direct = run_qa("snake", kind="static")
    assert direct["ok"] is False
