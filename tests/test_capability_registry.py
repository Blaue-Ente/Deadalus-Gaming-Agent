"""Capability Registry Contract — AT-01 … AT-14. No live Godot/Chromium/Xcode."""
from __future__ import annotations

from dataclasses import replace

import pytest

from daedalus.capability import resolve_target, run_pipeline
from daedalus.capability_registry import (
    DESKTOP_PROFILE,
    GODOT_GAMEPLAY_PROOFS,
    MOBILE_PROFILE,
    PLATFORMS,
    REGISTRY,
    RUNNER_VERSION,
    WEB_GAMEPLAY_PROOFS,
    AdapterSpec,
    BuildArtifact,
    Capability,
    CapabilityRegistry,
    DEVICE_SMOKE_PROOFS,
    UnsupportedPlatform,
    evidence_record,
    is_workspace_path,
    playground_kind,
)
from daedalus.capability_registry import CapabilityEvidence


def _artifact(**kwargs) -> BuildArtifact:
    base = dict(
        project_id="dungeon-001",
        revision="r2",
        target="godot",
        artifact_hash="sha256:aaa",
        path="build/dungeon-linux.zip",
        immutable=True,
    )
    base.update(kwargs)
    return BuildArtifact(**base)


def _pass(
    capability: str,
    *,
    target: str = "godot",
    revision: str = "r2",
    artifact_hash: str = "sha256:aaa",
    project_id: str = "dungeon-001",
    details: str = "ok",
    checks: dict[str, bool] | None = None,
) -> CapabilityEvidence:
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


def _required(platform: str):
    return REGISTRY.release_capabilities(platform)


def _all_pass(platform: str, **art_kw) -> tuple[BuildArtifact, list[CapabilityEvidence]]:
    art = _artifact(**{"target": platform, **art_kw})
    ev = [_pass(c.value, target=art.target) for c in _required(platform)]
    return art, ev


def test_at01_each_target_has_explicit_registry_entry():
    for name in PLATFORMS:
        spec = REGISTRY.adapter(name)
        assert spec.platform == name
        assert spec.name
        assert spec.module


def test_at02_unknown_platform_fail_closed():
    with pytest.raises(ValueError, match="unsupported platform"):
        REGISTRY.adapter("unity")
    with pytest.raises(UnsupportedPlatform, match="unsupported platform"):
        REGISTRY.adapter("unity")
    assert resolve_target("unity", "fps") == "unity"
    assert resolve_target("unity", "html canvas") != "web"
    assert resolve_target("godot", "html canvas phaser") == "godot"


def test_at03_missing_capability_is_concrete_blocker():
    spec = REGISTRY.adapter("expo")
    stripped = AdapterSpec(
        platform="expo",
        name=spec.name,
        module=spec.module,
        capabilities=frozenset(c for c in spec.capabilities if c != Capability.DEVICE_SMOKE),
        playground_kinds=spec.playground_kinds,
        visual=spec.visual,
        static_deploy=spec.static_deploy,
    )
    thin = REGISTRY.with_adapter(stripped)
    with pytest.raises(ValueError, match="expo lacks capabilities: device_smoke"):
        thin.assert_supported("expo", [Capability.EXPORT, Capability.DEVICE_SMOKE])
    assert thin.missing("expo", [Capability.EXPORT, Capability.DEVICE_SMOKE]) == ("device_smoke",)


def test_at04_stale_evidence_rejected():
    art, ev = _all_pass("godot")
    ev = [replace(row, revision="r1") if row.capability == "gameplay_proof" else row for row in ev]
    decision = REGISTRY.release_decision(art, _required("godot"), ev)
    assert decision.approved is False
    assert "invalid evidence: gameplay_proof" in decision.blockers


def test_at05_evidence_for_other_artifact_rejected():
    art, ev = _all_pass("godot")
    ev = [
        replace(row, artifact_hash="sha256:BBB") if row.capability == "gameplay_proof" else row
        for row in ev
    ]
    decision = REGISTRY.release_decision(art, _required("godot"), ev)
    assert decision.approved is False
    assert decision.blockers != ()


def test_at06_skipped_is_not_pass():
    art, ev = _all_pass("web", target="web", path="build/web")
    ev = [replace(row, status="skipped") if row.capability == "render_proof" else row for row in ev]
    decision = REGISTRY.release_decision(art, _required("web"), ev)
    assert decision.approved is False
    assert "invalid evidence: render_proof" in decision.blockers


def test_at07_blocked_is_not_pass():
    art, ev = _all_pass("expo", target="expo", path="build/app.apk")
    ev = [replace(row, status="blocked") if row.capability == "device_smoke" else row for row in ev]
    decision = REGISTRY.release_decision(art, _required("expo"), ev)
    assert decision.approved is False
    assert "invalid evidence: device_smoke" in decision.blockers


def test_at08_failed_is_not_pass():
    art, ev = _all_pass("godot")
    ev = [replace(row, status="fail") if row.capability == "gameplay_proof" else row for row in ev]
    decision = REGISTRY.release_decision(art, _required("godot"), ev)
    assert decision.approved is False
    assert "invalid evidence: gameplay_proof" in decision.blockers


def test_at09_artifact_without_hash_not_published():
    art, ev = _all_pass("godot")
    art = replace(art, artifact_hash="")
    decision = REGISTRY.release_decision(art, _required("godot"), ev)
    assert decision.approved is False
    assert "artifact hash is missing" in decision.blockers


def test_at10_mutable_artifact_not_published():
    art, ev = _all_pass("godot")
    art = replace(art, immutable=False)
    decision = REGISTRY.release_decision(art, _required("godot"), ev)
    assert decision.approved is False
    assert "artifact is not immutable" in decision.blockers


def test_at11_web_gameplay_proof_must_be_real():
    art, ev = _all_pass("web", target="web", path="build/web")
    fake = _pass(
        "gameplay_proof",
        target="web",
        details="canvas and index.html exist",
        checks={},
    )
    ev = [fake if row.capability == "gameplay_proof" else row for row in ev]
    decision = REGISTRY.release_decision(art, _required("web"), ev)
    assert decision.approved is False
    assert "invalid evidence: gameplay_proof" in decision.blockers


def test_at12_godot_gameplay_proof_must_be_engine_based():
    art, ev = _all_pass("godot")
    fake = _pass(
        "gameplay_proof",
        target="godot",
        details=".tscn and .gd files exist",
        checks={},
    )
    ev = [fake if row.capability == "gameplay_proof" else row for row in ev]
    decision = REGISTRY.release_decision(art, _required("godot"), ev)
    assert decision.approved is False
    assert "invalid evidence: gameplay_proof" in decision.blockers


def test_at13_mobile_export_requires_device_smoke():
    art, ev = _all_pass("expo", target="expo", path="build/app.apk")
    ev = [row for row in ev if row.capability != "device_smoke"]
    decision = REGISTRY.release_decision(art, _required("expo"), ev)
    assert decision.approved is False
    assert "invalid evidence: device_smoke" in decision.blockers
    preview = _pass(
        "device_smoke",
        target="expo",
        details="expo web preview",
        checks={},
    )
    decision = REGISTRY.release_decision(
        art, _required("expo"),
        [preview if row.capability == "device_smoke" else row for row in _all_pass("expo")[1]],
    )
    assert decision.approved is False


def test_at14_publish_uses_last_verified_artifact(tmp_path):
    art, ev = _all_pass("godot")
    decision = REGISTRY.release_decision(art, _required("godot"), ev)
    assert decision.approved is True
    assert decision.blockers == ()
    stale = replace(art, revision="r1")
    stale_decision = REGISTRY.release_decision(stale, _required("godot"), ev)
    assert stale_decision.approved is False
    (tmp_path / "index.html").write_text("x", encoding="utf-8")
    assert is_workspace_path(tmp_path, tmp_path) is True
    assert is_workspace_path(tmp_path, tmp_path / "build" / "out.zip") is False


def test_assert_supported_godot_happy_and_missing():
    REGISTRY.assert_supported(
        "godot",
        [
            Capability.RUN,
            Capability.RENDER_PROOF,
            Capability.GAMEPLAY_PROOF,
            Capability.EXPORT,
        ],
    )
    spec = REGISTRY.adapter("godot")
    thin = CapabilityRegistry(
        adapters={
            **{p: REGISTRY.adapter(p) for p in PLATFORMS},
            "godot": AdapterSpec(
                platform="godot",
                name=spec.name,
                module=spec.module,
                capabilities=frozenset({Capability.CREATE, Capability.WRITE, Capability.RUN, Capability.EXPORT}),
            ),
        }
    )
    with pytest.raises(ValueError, match="godot lacks capabilities: render_proof, gameplay_proof"):
        thin.assert_supported(
            "godot",
            [Capability.RUN, Capability.RENDER_PROOF, Capability.GAMEPLAY_PROOF, Capability.EXPORT],
        )


def test_builder_kind_godot_is_not_vite():
    assert playground_kind("godot") == "godot"
    assert playground_kind("vite-game") == "vite"
    with pytest.raises(ValueError, match="unsupported platform"):
        playground_kind("unreal")


def test_profiles_match_contract():
    assert tuple(REGISTRY.profile("web")) == DESKTOP_PROFILE
    assert tuple(REGISTRY.profile("godot")) == DESKTOP_PROFILE
    assert tuple(REGISTRY.profile("expo")) == MOBILE_PROFILE
    assert tuple(REGISTRY.profile("capacitor")) == MOBILE_PROFILE
    assert Capability.DEVICE_SMOKE not in REGISTRY.profile("web")
    assert Capability.DEVICE_SMOKE in REGISTRY.profile("expo")


def test_run_pipeline_unity_is_not_web(tmp_path, monkeypatch):
    monkeypatch.setattr("daedalus.playground.project_dir", lambda slug: tmp_path)
    pipe = run_pipeline("u", kind="unity", through="plan")
    assert pipe["ok"] is False
    assert pipe.get("honest_gap") == "unity_local"
    assert pipe["target"] != "web"
