"""Construction and serialization for daedalus.contracts. No runtime wiring."""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from daedalus.contracts import (
    CONTRACT_SCHEMA_VERSION,
    KNOWN_PERMISSION_IDS,
    PERMISSION_CATALOG,
    TOOL_SPEC_OVERLAY,
    ArtifactRef,
    BuildArtifactRef,
    BuildVerdict,
    CapabilityEvidenceRecord,
    CommandRequest,
    CommandResult,
    ContractError,
    DodVerdict,
    EnvPolicy,
    ErrorClass,
    EventKind,
    ExecutionBackend,
    FilesystemPolicy,
    NamedArtifact,
    NetworkPolicy,
    Observation,
    PermissionDecision,
    PermissionGrant,
    PermissionRequirement,
    Plan,
    PlanScore,
    PlanStep,
    ReleaseManifest,
    Run,
    RunBudgetSpend,
    RunCheckpoint,
    RunEvent,
    RunStage,
    Slice,
    SliceRole,
    SideEffectClass,
    StageRow,
    TaskAction,
    TaskEvent,
    ToolError,
    ToolLogRow,
    ToolObservation,
    ToolRequest,
    ToolResult,
    ToolRisk,
    ToolSource,
    ToolSpec,
    overlay_for,
)
from daedalus.contracts.artifacts import NAMED_ARTIFACT_MAX_CHARS

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_DIR = ROOT / "daedalus" / "contracts"
FORBIDDEN_IMPORTS = frozenset({"daedalus.agent", "daedalus.native_tools"})
OUTBOX_PERMISSION_IDS = frozenset(
    {
        "insert_trading_concepts",
        "insert_signal_criteria",
        "insert_patterns",
        "insert_data",
        "execute_raw_sql",
        "modify_files",
        "create_files",
        "auto_approve",
        "web_search",
        "github_access",
        "send_outbox",
        "deep_worker",
        "analysis_worker",
        "claude_worker",
        "planner_auto_run",
        "mesh_call",
        "mcp_write",
        "postgres_query",
    }
)


def _run_payload(**overrides: object) -> dict:
    body: dict = {
        "run_id": "job_abc123",
        "job_id": "job_abc123",
        "slug": "snake",
        "session_id": "sess-1",
        "original_ask": "Add a pause key",
        "acceptance": ["P pauses", "P resumes"],
        "slices": [
            {
                "id": "t1",
                "title": "Add a pause key",
                "detail": "Add a pause key",
                "role": "coder",
                "status": "open",
                "verify": True,
            }
        ],
        "status": "in_progress",
        "created_at": 1.0,
        "updated_at": 2.0,
    }
    body.update(overrides)
    return body


def test_schema_version_defaults_to_1():
    run = Run.model_validate(_run_payload())
    assert CONTRACT_SCHEMA_VERSION == 1
    assert run.schema_version == 1


def test_run_matches_empty_job_keys():
    job = {
        "job_id": "job_ledger1",
        "slug": "arena",
        "session_id": "chat-1",
        "original_ask": "Fix collision",
        "acceptance": ["no overlap"],
        "tasks": [
            {
                "id": "t1",
                "title": "Fix collision",
                "detail": "Fix collision",
                "role": "coder",
                "status": "open",
                "verify": True,
            }
        ],
        "status": "in_progress",
        "created_at": 10.0,
        "updated_at": 11.0,
    }
    run = Run.model_validate(
        {
            "run_id": job["job_id"],
            "job_id": job["job_id"],
            "slug": job["slug"],
            "session_id": job["session_id"],
            "original_ask": job["original_ask"],
            "acceptance": job["acceptance"],
            "slices": job["tasks"],
            "status": job["status"],
            "created_at": job["created_at"],
            "updated_at": job["updated_at"],
        }
    )
    assert run.run_id == job["job_id"]
    assert run.job_id == job["job_id"]
    assert run.slug == job["slug"]
    assert run.session_id == job["session_id"]
    assert run.original_ask == job["original_ask"]
    assert run.acceptance == job["acceptance"]
    assert run.slices[0].id == "t1"
    assert run.status.value == job["status"]
    assert run.created_at == job["created_at"]
    assert run.updated_at == job["updated_at"]


def test_run_roundtrip_json():
    run = Run.model_validate(
        _run_payload(
            effort="craft",
            model="cohere/north-mini-code:free",
            complete=False,
            stop_reason="",
            spend=RunBudgetSpend(cap_usd=5, spent_usd=0.0, remaining_usd=5.0).model_dump(),
        )
    )
    dumped = run.model_dump(mode="json")
    again = Run.model_validate(json.loads(json.dumps(dumped)))
    assert again == run
    ckpt = RunCheckpoint(run_id="job_abc123", stage=3, plan={"title": "x"}, slug="snake", updated=3.0)
    assert RunCheckpoint.model_validate(ckpt.model_dump(mode="json")) == ckpt


def test_run_rejects_unknown_status():
    with pytest.raises(ValidationError):
        Run.model_validate(_run_payload(status="running"))


def test_run_stage_values_are_pipeline_positions():
    assert RunStage("intake") is RunStage.INTAKE
    assert {stage.value for stage in RunStage} == {
        "intake",
        "discover",
        "plan",
        "implement",
        "static_verify",
        "build",
        "engine_test",
        "playtest",
        "visual_verify",
        "review",
        "repair",
        "accepted",
        "blocked",
        "failed",
        "cancelled",
    }
    assert EventKind.STAGE.value == "stage"


def test_slice_roles_are_ledger_roles():
    assert {role.value for role in SliceRole} == {"explorer", "coder", "verify"}
    Slice.model_validate({"id": "t2", "title": "explore", "role": "explorer"})
    Slice.model_validate({"id": "t3", "title": "check", "role": "verify"})
    with pytest.raises(ValidationError):
        Slice.model_validate({"id": "t4", "title": "nope", "role": "planner"})


def test_plan_step_and_score_roundtrip():
    step = PlanStep(
        id="write",
        tool="write_file",
        required=True,
        docs=("daedalus/skills/arcade_craft.md",),
        depends_on=("new",),
        accept_any=("NEED_PLAYGROUND_WRITE",),
        why="write the slice",
    )
    plan = Plan(
        playbook_id="playground.static",
        title="Arcade",
        kind="static",
        tools=("write_file", "run_command"),
        steps=(step,),
        n=64,
        dim=2,
    )
    score = PlanScore(
        playbook_id="playground.static",
        complete=False,
        missing=["run_command"],
        used=["write_file"],
        blocker="missing smoke",
    )
    verdict = BuildVerdict(
        complete=False,
        blockers=["missing smoke"],
        missing_steps=["run_command"],
        next_step="run",
        failed_calls=["run_command"],
    )
    assert Plan.model_validate(plan.model_dump(mode="json")) == plan
    assert PlanScore.model_validate(score.model_dump(mode="json")) == score
    assert BuildVerdict.model_validate(verdict.model_dump(mode="json")) == verdict


def test_command_request_defaults_and_roundtrip():
    req = CommandRequest(executable="npm", arguments=("install",), slug="snake")
    assert req.env_policy is EnvPolicy.STRIP_SECRETS
    assert req.network_policy is NetworkPolicy.UNRESTRICTED
    assert req.filesystem_policy is FilesystemPolicy.JAIL
    assert req.timeout_s is None
    assert req.schema_version == 1
    again = CommandRequest.model_validate(req.model_dump(mode="json"))
    assert again == req
    with pytest.raises(ValidationError):
        CommandRequest.model_validate({"executable": "ls", "unknown_field": 1})
    res = CommandResult(ok=True, command="ls", ran="ls", exit_code=0, cwd="/tmp")
    assert CommandResult.model_validate(res.model_dump(mode="json")) == res
    assert ExecutionBackend is not None


def test_command_contracts_do_not_import_shell_center():
    tree = ast.parse((CONTRACTS_DIR / "commands.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "shell_center" not in alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert "shell_center" not in node.module


def test_tool_request_mirrors_protocol_fields():
    req = ToolRequest(name="web_search", args={"query": "tetris"}, source="marker", marker="NEED_WEB")
    assert set(req.model_dump()) >= {"name", "args", "source", "marker", "schema_version"}
    assert req.name == "web_search"
    assert req.args["query"] == "tetris"
    assert req.source is ToolSource.MARKER
    assert req.marker == "NEED_WEB"
    again = ToolRequest.model_validate(req.model_dump(mode="json"))
    assert again == req


def test_tool_log_row_blocked_may_be_ok():
    row = ToolLogRow(tool="code_search", input={"query": "x"}, ok=True, blocked=True)
    assert row.ok is True
    assert row.blocked is True
    assert ToolLogRow.model_validate(row.model_dump(mode="json")) == row


def test_tool_result_roundtrip():
    result = ToolResult(
        name="read_file",
        ok=True,
        text="── read_file OK ──",
        data={"path": "x.py"},
        source="native",
        blocked=False,
    )
    assert ToolResult.model_validate(result.model_dump(mode="json")) == result


def test_run_event_matches_run_engine_row():
    raw = {"ts": 12.5, "event": "phase", "data": {"n": 2, "label": "Строене"}}
    ev = RunEvent.model_validate(raw)
    assert ev.ts == 12.5
    assert ev.event == EventKind.PHASE or ev.event == "phase"
    assert ev.data["n"] == 2
    assert RunEvent.model_validate(ev.model_dump(mode="json")).data == raw["data"]


def test_task_event_actions():
    assert {a.value for a in TaskAction} == {"open", "done", "rework", "touch", "mode"}
    opened = TaskEvent(action="open", ok=True, task={"id": "task_ab", "title": "Pause"})
    done = TaskEvent(action="done", ok=False, error="task not found: pause")
    mode = TaskEvent(action="mode", ok=True, mode="build")
    assert opened.task and opened.task["id"] == "task_ab"
    assert done.error.startswith("task not found")
    assert mode.mode == "build"


def test_dod_verdict_matches_job_contract_as_dict():
    raw = {
        "coding_task": True,
        "complete": False,
        "checks": {"wrote_files": True, "verified": False},
        "blockers": ["Липсва smoke/verify — не казвай готово."],
        "summary": "Липсва smoke/verify — не казвай готово.",
    }
    verdict = DodVerdict.model_validate(raw)
    dumped = verdict.model_dump(mode="json")
    for key in ("coding_task", "complete", "checks", "blockers", "summary"):
        assert dumped[key] == raw[key]


def test_stage_row_skipped_implies_not_ok():
    StageRow(stage="visual", ok=False, skipped=True, blockers=["visual_skipped"])
    StageRow(stage="runtime", ok=True, skipped=False)
    with pytest.raises(ValidationError):
        StageRow(stage="visual", ok=True, skipped=True)


def test_capability_evidence_record_roundtrip():
    rec = CapabilityEvidenceRecord(
        capability="runtime",
        status="pass",
        project_id="snake",
        revision="abc123",
        artifact_hash="sha256:deadbeef",
        details="ok",
        runner_version="1",
        checks={"script_error": True},
    )
    assert CapabilityEvidenceRecord.model_validate(rec.model_dump(mode="json")) == rec


def test_named_artifact_max_content():
    NamedArtifact(id="game_loop", content="x" * NAMED_ARTIFACT_MAX_CHARS, source="t1", updated_at=1.0)
    with pytest.raises(ValidationError):
        NamedArtifact(id="game_loop", content="x" * (NAMED_ARTIFACT_MAX_CHARS + 1))


def test_build_artifact_ref_distinct_from_named():
    named = NamedArtifact(id="input_handler", content="keydown", source="explorer", updated_at=1.0)
    hashed = BuildArtifactRef(
        project_id="snake",
        revision="rev1",
        target="web",
        artifact_hash="sha256:aa",
        path="dist/index.html",
        immutable=True,
    )
    release = ReleaseManifest(
        slug="snake",
        version="1",
        target="web",
        artifact="index.html",
        sha256="aa",
        url="/releases/snake/current/index.html",
    )
    assert set(named.model_dump()) != set(hashed.model_dump())
    assert "content" in named.model_dump()
    assert "artifact_hash" in hashed.model_dump()
    assert "content" not in hashed.model_dump()
    assert ReleaseManifest.model_validate(release.model_dump(mode="json")) == release


def test_permission_catalog_snapshot():
    ids = set(KNOWN_PERMISSION_IDS)
    assert OUTBOX_PERMISSION_IDS <= ids
    assert "postgres_query" in ids
    catalog_ids = {row.id for row in PERMISSION_CATALOG}
    assert catalog_ids <= ids
    assert "postgres_query" not in catalog_ids


def test_permission_grant_shape():
    grant = PermissionGrant(
        perm_class="web_search",
        granted_at=100,
        expires_at=200,
        hours=24.0,
        granted_by="owner",
        active=True,
        remaining_min=15,
    )
    dumped = grant.model_dump(mode="json")
    for key in (
        "perm_class",
        "granted_at",
        "expires_at",
        "hours",
        "granted_by",
        "active",
        "remaining_min",
    ):
        assert key in dumped
    decision = PermissionDecision(allowed=True, perm_class="web_search", fail_open=True, reason="perms db down")
    assert decision.fail_open is True
    assert PermissionGrant.model_validate(dumped) == grant


def test_contract_error_classes():
    for cls in ErrorClass:
        err = ContractError(error_class=cls, message=cls.value, tool="web_search", perm_class="web_search")
        assert ContractError.model_validate(err.model_dump(mode="json")).error_class is cls


def test_extra_field_forbidden():
    with pytest.raises(ValidationError):
        Run.model_validate(_run_payload(nope=1))


def test_side_effect_and_risk_enums():
    assert {c.value for c in SideEffectClass} == {
        "read",
        "write",
        "shell",
        "network",
        "database",
        "spawn",
        "engine",
        "memory",
        "mcp",
    }
    assert {r.value for r in ToolRisk} == {"low", "medium", "high"}
    with pytest.raises(ValidationError):
        ToolSpec.model_validate({"name": "x", "side_effect": "explode"})
    with pytest.raises(ValidationError):
        ToolSpec.model_validate({"name": "x", "risk": "critical"})


def test_tool_spec_roundtrip():
    spec = ToolSpec(
        name="web_search",
        description="Search the public web",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
        side_effect="network",
        risk="medium",
        permission=PermissionRequirement(perm_class="web_search", fail_open=True),
        aliases=("NEED_WEB", "NEED_SEARCH"),
        need_marker="NEED_WEB",
        tags=("web",),
        module="daedalus.tools",
        when="Search the public web for docs or APIs.",
        network=True,
    )
    again = ToolSpec.model_validate(json.loads(json.dumps(spec.model_dump(mode="json"))))
    assert again == spec
    assert again.schema_version == 1
    with pytest.raises(ValidationError):
        ToolSpec.model_validate({"name": "web_search", "nope": 1})


def test_tool_error_uses_error_class():
    err = ToolError(error_class=ErrorClass.OFFLINE, message="offline", tool="web_search", retryable=True)
    assert isinstance(err, ContractError)
    again = ToolError.model_validate(err.model_dump(mode="json"))
    assert again.error_class is ErrorClass.OFFLINE
    assert again.retryable is True
    for cls in (
        ErrorClass.TOOL_FAILED,
        ErrorClass.TOOL_BLOCKED,
        ErrorClass.PERMISSION_DENIED,
        ErrorClass.OFFLINE,
        ErrorClass.UNKNOWN_TOOL,
    ):
        ToolError(error_class=cls, message=cls.value, tool="x")


def test_permission_requirement_rejects_unknown():
    PermissionRequirement(perm_class="web_search")
    PermissionRequirement(perm_class="postgres_query")
    with pytest.raises(ValidationError):
        PermissionRequirement(perm_class="not_a_perm")


def test_artifact_ref_distinct_from_build():
    ref = ArtifactRef(kind="file", path="index.html", slug="snake")
    hashed = BuildArtifactRef(
        project_id="snake",
        revision="rev1",
        target="web",
        artifact_hash="sha256:aa",
        path="dist/index.html",
        immutable=True,
    )
    dumped = ref.model_dump()
    assert "artifact_hash" not in dumped
    assert "content" not in dumped
    assert set(dumped) != set(hashed.model_dump())
    assert ArtifactRef.model_validate(ref.model_dump(mode="json")) == ref
    with pytest.raises(ValidationError):
        ArtifactRef(kind="blob", path="x")


def test_tool_result_additive_defaults():
    result = ToolResult(
        name="read_file",
        ok=True,
        text="── read_file OK ──",
        data={"path": "x.py"},
        source="native",
        blocked=False,
    )
    dumped = result.model_dump()
    assert dumped["error"] is None
    assert dumped["artifacts"] == ()
    assert dumped["risk"] is None
    assert ToolResult.model_validate(result.model_dump(mode="json")) == result


def test_tool_result_with_error_roundtrip():
    err = ToolError(error_class=ErrorClass.OFFLINE, message="offline", tool="web_search")
    result = ToolResult(
        name="web_search",
        ok=True,
        text="── OFFLINE KIT: web_search е изключен ──",
        blocked=False,
        error=err,
        artifacts=(ArtifactRef(kind="url", uri="https://example.com"),),
        risk=ToolRisk.MEDIUM,
    )
    again = ToolResult.model_validate(result.model_dump(mode="json"))
    assert again == result
    assert again.error is not None
    assert again.error.error_class is ErrorClass.OFFLINE


def test_observation_is_tool_observation():
    assert Observation is ToolObservation
    obs = Observation(tool="code_search", ok=True, blocked=True, args={"query": "x"}, head="BLOCKED")
    again = Observation.model_validate(obs.model_dump(mode="json"))
    assert again == obs
    assert again.error is None
    with_err = ToolObservation(
        tool="web_search",
        ok=True,
        blocked=False,
        error=ToolError(error_class=ErrorClass.OFFLINE, message="offline", tool="web_search"),
    )
    assert ToolObservation.model_validate(with_err.model_dump(mode="json")) == with_err


def test_tool_spec_overlay_covers_inventory():
    inv = json.loads((ROOT / "artifacts" / "baseline" / "architecture-inventory.json").read_text(encoding="utf-8"))
    names = set(inv["native_tools"])
    overlay_keys = set(TOOL_SPEC_OVERLAY)
    assert overlay_keys <= names
    assert names <= overlay_keys
    side, risk, perm = overlay_for("web_search")
    assert side is SideEffectClass.NETWORK
    assert risk is ToolRisk.MEDIUM
    assert perm == "web_search"
    unknown_side, unknown_risk, unknown_perm = overlay_for("not_a_real_tool")
    assert unknown_side is SideEffectClass.READ
    assert unknown_risk is ToolRisk.LOW
    assert unknown_perm == ""
    for name, (_side, _risk, perm_class) in TOOL_SPEC_OVERLAY.items():
        if perm_class:
            assert perm_class in KNOWN_PERMISSION_IDS, name


def test_contracts_do_not_import_runtime():
    for path in CONTRACTS_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in FORBIDDEN_IMPORTS
                    assert not alias.name.startswith("daedalus.agent")
                    assert not alias.name.startswith("daedalus.native_tools")
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module not in FORBIDDEN_IMPORTS
                assert not node.module.startswith("daedalus.agent")
                assert not node.module.startswith("daedalus.native_tools")

    code = (
        "import daedalus.contracts\n"
        "import sys\n"
        "bad = [n for n in sys.modules if n == 'daedalus.agent' "
        "or n == 'daedalus.native_tools' "
        "or n.startswith('daedalus.agent.') "
        "or n.startswith('daedalus.native_tools.')]\n"
        "assert not bad, bad\n"
    )
    env = os.environ.copy()
    env["DAEDALUS_STANDALONE"] = "1"
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
