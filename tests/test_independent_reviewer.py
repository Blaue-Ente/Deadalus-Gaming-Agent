"""IndependentReviewer leaf — second-pass sign-off. No live LLM."""
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
    DodVerdict,
    ReviewFinding,
    ReviewMode,
    ReviewReport,
    ReviewSubjectKind,
    ReviewVerdict,
    Run,
    RunCheckpoint,
    StageRow,
)
from daedalus.independent_reviewer import IndependentReviewer, ReviewRequest, review
from daedalus.paths import data_root, playground_projects_dir

ROOT = Path(__file__).resolve().parents[1]
LEAF = ROOT / "daedalus" / "independent_reviewer.py"
FORBIDDEN_IMPORTS = frozenset({
    "daedalus.agent",
    "daedalus.native_tools",
    "daedalus.tool_loop",
    "daedalus.run_finalizer",
    "daedalus.mentor",
    "daedalus.mentor_helm",
    "daedalus.mentor_graph",
    "daedalus.workers",
    "daedalus.improvements",
    "daedalus.model_gateway",
    "daedalus.llm_client",
    "daedalus.cheap_llm",
    "daedalus.subagents",
    "daedalus.kernel",
    "daedalus.kernel.full_autopilot",
    "daedalus.kernel.orchestrator",
    "daedalus.verification_controller",
    "daedalus.otel",
    "daedalus.context_budget",
    "daedalus.memory_provenance",
})
UNTOUCHED = (
    ROOT / "daedalus" / "agent.py",
    ROOT / "daedalus" / "native_tools.py",
    ROOT / "daedalus" / "tool_loop.py",
    ROOT / "daedalus" / "run_finalizer.py",
    ROOT / "daedalus" / "mentor.py",
    ROOT / "daedalus" / "workers.py",
    ROOT / "app" / "main.py",
    ROOT / "daedalus" / "standalone_boot.py",
    ROOT / "daedalus" / "verification_controller.py",
    ROOT / "daedalus" / "otel.py",
    ROOT / "daedalus" / "context_budget.py",
    ROOT / "daedalus" / "memory_provenance.py",
)


def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_INDEPENDENT_REVIEW", raising=False)
    monkeypatch.delenv("DAEDALUS_INDEPENDENT_REVIEW_LLM", raising=False)
    monkeypatch.delenv("DAEDALUS_OFFLINE", raising=False)
    monkeypatch.delenv("DAEDALUS_BENCH_LIVE", raising=False)
    data_root.cache_clear()
    playground_projects_dir.cache_clear()


def _honest(**overrides: object) -> ReviewRequest:
    body: dict[str, object] = {
        "run_id": "run-1",
        "session_id": "sess-1",
        "subject": ReviewSubjectKind.RUN.value,
        "stages": [StageRow(stage="static_verify", ok=True)],
        "dod": DodVerdict(coding_task=True, complete=True),
    }
    body.update(overrides)
    return ReviewRequest(**body)  # type: ignore[arg-type]


def test_contract_skip_cannot_be_ok():
    with pytest.raises(ValidationError):
        ReviewFinding(severity="high", code="x", skipped=True, ok=True)
    with pytest.raises(ValidationError):
        ReviewReport(ok=True, skipped=True)
    with pytest.raises(ValidationError):
        ReviewReport(ok=True, skipped=False, verdict=ReviewVerdict.APPROVED.value, blockers=["x"])
    with pytest.raises(ValidationError):
        ReviewReport(ok=False, skipped=False, verdict=ReviewVerdict.SKIPPED.value)
    with pytest.raises(ValidationError):
        ReviewFinding.model_validate({"severity": "low", "code": "x", "nope": 1})
    row = ReviewFinding(severity="high", code="stage_skipped", skipped=True, ok=False)
    assert row.schema_version == 1
    assert ReviewFinding.model_validate(row.model_dump(mode="json")) == row
    report = ReviewReport(ok=True, skipped=False, mode=ReviewMode.OFF.value)
    assert report.schema_version == 1
    assert report.verdict == ReviewVerdict.NOT_RUN.value
    assert ReviewReport.model_validate(report.model_dump(mode="json")) == report


def test_run_schema_unchanged():
    assert "review" not in Run.model_fields
    assert "reviewer" not in Run.model_fields
    assert "sign_off" not in Run.model_fields
    assert "review" not in RunCheckpoint.model_fields
    assert "reviewer" not in RunCheckpoint.model_fields
    assert "sign_off" not in RunCheckpoint.model_fields
    with pytest.raises(ValidationError):
        Run.model_validate({"run_id": "r", "nope": 1})


def test_leaf_does_not_import_forbidden():
    tree = ast.parse(LEAF.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in FORBIDDEN_IMPORTS
                assert not any(
                    alias.name == bad or alias.name.startswith(bad + ".")
                    for bad in FORBIDDEN_IMPORTS
                )
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module not in FORBIDDEN_IMPORTS
            assert not any(
                node.module == bad or node.module.startswith(bad + ".")
                for bad in FORBIDDEN_IMPORTS
            )
    src = LEAF.read_text(encoding="utf-8")
    for bad in (
        "native_tools",
        "tool_loop",
        "run_finalizer",
        "mentor_helm",
        "mentor_graph",
        "improvements",
        "model_gateway",
        "llm_client",
        "cheap_llm",
        "subagents",
        "verification_controller",
        "context_budget",
        "memory_provenance",
    ):
        assert f"daedalus.{bad}" not in src
        assert f"from .{bad}" not in src
    assert "from daedalus.agent import" not in src
    assert "from daedalus.mentor import" not in src
    assert "from daedalus.workers import" not in src
    assert "from daedalus.otel import" not in src
    assert "from daedalus.kernel" not in src
    assert "review_gate_critique" not in src
    code = (
        "import daedalus.independent_reviewer as ir\n"
        "import sys\n"
        "bad = [n for n in sys.modules if n in "
        "('daedalus.agent', 'daedalus.native_tools', 'daedalus.tool_loop', "
        "'daedalus.run_finalizer', 'daedalus.mentor', 'daedalus.mentor_helm', "
        "'daedalus.mentor_graph', 'daedalus.workers', 'daedalus.improvements', "
        "'daedalus.model_gateway', 'daedalus.llm_client', 'daedalus.cheap_llm', "
        "'daedalus.subagents', 'daedalus.kernel', 'daedalus.kernel.full_autopilot', "
        "'daedalus.kernel.orchestrator', 'daedalus.verification_controller', "
        "'daedalus.otel', 'daedalus.context_budget', 'daedalus.memory_provenance') "
        "or n.startswith('daedalus.agent.') "
        "or n.startswith('daedalus.native_tools.') "
        "or n.startswith('daedalus.tool_loop.') "
        "or n.startswith('daedalus.mentor.') "
        "or n.startswith('daedalus.workers.') "
        "or n.startswith('daedalus.kernel.') "
        "or n.startswith('daedalus.verification_controller.') "
        "or n.startswith('daedalus.otel.') "
        "or n.startswith('daedalus.context_budget.') "
        "or n.startswith('daedalus.memory_provenance.')]\n"
        "assert not bad, bad\n"
        "from daedalus.independent_reviewer import ReviewRequest\n"
        "rep = ir.review(ReviewRequest(run_id='r'))\n"
        "assert rep.ok and (not rep.skipped) and rep.verdict == 'not_run'\n"
        "print('import-ok')\n"
    )
    env = os.environ.copy()
    env["DAEDALUS_STANDALONE"] = "1"
    env.pop("DAEDALUS_INDEPENDENT_REVIEW", None)
    env.pop("DAEDALUS_INDEPENDENT_REVIEW_LLM", None)
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
    assert "import-ok" in result.stdout


def test_agent_and_loops_untouched():
    agent = (ROOT / "daedalus" / "agent.py").read_text(encoding="utf-8")
    finalizer = (ROOT / "daedalus" / "run_finalizer.py").read_text(encoding="utf-8")
    for path in UNTOUCHED:
        blob = path.read_text(encoding="utf-8")
        assert "IndependentReviewer" not in blob
        assert "daedalus.independent_reviewer" not in blob
        assert "from daedalus.independent_reviewer" not in blob
    assert "NEED_PROPOSE_START" in agent
    assert "NEED_SANDBOX" in agent
    assert "NEED_VERIFY" in agent
    assert "review_gate_critique" in finalizer


def test_unset_env_is_noop_ok(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    report = review(ReviewRequest(run_id="run-off"))
    assert report.ok is True
    assert report.skipped is False
    assert report.mode == ReviewMode.OFF.value
    assert report.verdict == ReviewVerdict.NOT_RUN.value
    assert report.source == "none"
    assert report.persisted == ""
    assert not (data_root() / "review" / "run-off.json").exists()


def test_enabled_empty_subject_skip_not_ok(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_INDEPENDENT_REVIEW", "1")
    report = IndependentReviewer().review(ReviewRequest())
    assert report.skipped is True
    assert report.ok is False
    assert report.verdict == ReviewVerdict.SKIPPED.value
    assert "empty_subject" in report.blockers


def test_enabled_honest_stages_and_dod_approved(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_INDEPENDENT_REVIEW", "1")
    report = IndependentReviewer().review(_honest())
    assert report.ok is True
    assert report.skipped is False
    assert report.verdict == ReviewVerdict.APPROVED.value
    assert report.mode == ReviewMode.CHECKLIST.value
    assert report.source == "checklist"
    assert report.reviewer_model == "checklist"
    assert report.blockers == []


def test_enabled_skipped_stage_not_approved(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_INDEPENDENT_REVIEW", "1")
    report = IndependentReviewer().review(
        _honest(stages=[StageRow(stage="visual_verify", ok=False, skipped=True)])
    )
    assert report.ok is False
    assert report.skipped is False
    assert report.verdict == ReviewVerdict.BLOCKED.value
    assert "stage_skipped" in report.blockers


def test_enabled_dod_incomplete_not_approved(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_INDEPENDENT_REVIEW", "1")
    report = IndependentReviewer().review(
        _honest(dod=DodVerdict(coding_task=True, complete=False, blockers=["open_slices"]))
    )
    assert report.ok is False
    assert report.verdict == ReviewVerdict.BLOCKED.value
    assert "dod_incomplete" in report.blockers


def test_llm_flag_without_fn_skip_not_ok(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_INDEPENDENT_REVIEW", "1")
    monkeypatch.setenv("DAEDALUS_INDEPENDENT_REVIEW_LLM", "1")
    report = IndependentReviewer().review(_honest())
    assert report.skipped is True
    assert report.ok is False
    assert report.mode == ReviewMode.LLM.value
    assert "missing_reviewer" in report.blockers


def test_llm_same_writer_not_approved(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_INDEPENDENT_REVIEW", "1")
    monkeypatch.setenv("DAEDALUS_INDEPENDENT_REVIEW_LLM", "1")
    report = IndependentReviewer().review(
        _honest(
            writer_model="gpt-x",
            reviewer_model="gpt-x",
            llm_fn=lambda _s, _u: '{"verdict":"approved","findings":[]}',
        )
    )
    assert report.ok is False
    assert report.verdict == ReviewVerdict.BLOCKED.value
    assert "same_writer" in report.blockers


def test_llm_approved_cannot_override_checklist(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_INDEPENDENT_REVIEW", "1")
    monkeypatch.setenv("DAEDALUS_INDEPENDENT_REVIEW_LLM", "1")
    report = IndependentReviewer().review(
        _honest(
            stages=[StageRow(stage="build", ok=False, skipped=True)],
            writer_model="writer-a",
            reviewer_model="reviewer-b",
            llm_fn=lambda _s, _u: '{"verdict":"approved","findings":[]}',
        )
    )
    assert report.ok is False
    assert report.skipped is False
    assert report.verdict == ReviewVerdict.BLOCKED.value
    assert "stage_skipped" in report.blockers
    assert report.mode == ReviewMode.LLM.value


def test_llm_parse_failure_skip_not_ok(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_INDEPENDENT_REVIEW", "1")
    monkeypatch.setenv("DAEDALUS_INDEPENDENT_REVIEW_LLM", "1")
    report = IndependentReviewer().review(
        _honest(
            writer_model="writer-a",
            reviewer_model="reviewer-b",
            llm_fn=lambda _s, _u: "not json",
        )
    )
    assert report.skipped is True
    assert report.ok is False
    assert "llm_parse" in report.blockers


def test_write_tools_not_approved(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_INDEPENDENT_REVIEW", "1")
    report = IndependentReviewer().review(_honest(tools=["read_file", "write_file"]))
    assert report.ok is False
    assert report.verdict == ReviewVerdict.BLOCKED.value
    assert "write_tools" in report.blockers


def test_finding_block_not_approved(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_INDEPENDENT_REVIEW", "1")
    report = IndependentReviewer().review(
        _honest(findings=[{"severity": "critical", "code": "secret", "detail": "key"}])
    )
    assert report.ok is False
    assert "finding_block" in report.blockers


def test_self_source_not_approved(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_INDEPENDENT_REVIEW", "1")
    report = IndependentReviewer().review(_honest(source="mentor"))
    assert report.ok is False
    assert "self_source" in report.blockers


def test_llm_offline_skip_not_ok(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_INDEPENDENT_REVIEW", "1")
    monkeypatch.setenv("DAEDALUS_INDEPENDENT_REVIEW_LLM", "1")
    monkeypatch.setenv("DAEDALUS_OFFLINE", "1")
    report = IndependentReviewer().review(
        _honest(llm_fn=lambda _s, _u: '{"verdict":"approved","findings":[]}')
    )
    assert report.skipped is True
    assert report.ok is False
    assert "offline" in report.blockers


def test_persist_under_data_review(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    report = review(ReviewRequest(run_id="run-rev-1", persist=True))
    path = Path(report.persisted)
    assert path.is_file()
    assert path == data_root() / "review" / "run-rev-1.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run-rev-1"
    assert payload["ok"] is True
    assert payload["verdict"] == ReviewVerdict.NOT_RUN.value
    assert payload["schema_version"] == 1


def test_persist_false_writes_nothing(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    report = review(ReviewRequest(run_id="run-rev-off", persist=False))
    assert report.persisted == ""
    assert not (data_root() / "review" / "run-rev-off.json").exists()
