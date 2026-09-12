"""Transitions and dual-path mapping for daedalus.stage_machine."""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from daedalus.contracts import EventKind, RunEvent, RunStage, RunStatus
from daedalus.stage_machine import (
    OPTIONAL_SKIP_STAGES,
    SPINE,
    TERMINAL_STAGES,
    WORK_STAGES,
    StageMachineError,
    apply_stage,
    from_legacy_stage,
    from_phase_event,
    peek_stage,
    skip_stage,
    status_for_stage,
)

ROOT = Path(__file__).resolve().parents[1]
STAGE_MACHINE_PATH = ROOT / "daedalus" / "stage_machine.py"
FORBIDDEN_IMPORTS = frozenset({
    "daedalus.agent",
    "daedalus.native_tools",
    "daedalus.job_ledger",
    "daedalus.run_engine",
    "daedalus.build_loop",
    "daedalus.kernel.full_autopilot",
})


def test_run_stage_values_are_lowercase():
    expected = (
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
    )
    assert tuple(stage.value for stage in RunStage) == expected
    assert RunStage("plan") is RunStage.PLAN
    assert len(WORK_STAGES) == 11
    assert TERMINAL_STAGES == frozenset({RunStage.ACCEPTED, RunStage.CANCELLED})
    assert RunStage.REPAIR in WORK_STAGES
    assert RunStage.BLOCKED not in WORK_STAGES
    assert RunStage.FAILED not in WORK_STAGES


def test_happy_path_adjacent_hops():
    current = RunStage.INTAKE
    for nxt in SPINE[1:]:
        current = apply_stage(current, nxt)
        assert current is nxt
    assert current is RunStage.ACCEPTED


def test_backward_hop_raises():
    with pytest.raises(StageMachineError, match="illegal transition"):
        apply_stage(RunStage.REVIEW, RunStage.PLAN)


def test_apply_stage_idempotent():
    stage = RunStage.IMPLEMENT
    assert apply_stage(stage, RunStage.IMPLEMENT) is stage
    assert apply_stage(stage, "implement") is stage


def test_implement_exits_and_rejects_accepted():
    for nxt in (RunStage.REPAIR, RunStage.BLOCKED, RunStage.FAILED, RunStage.CANCELLED):
        assert apply_stage(RunStage.IMPLEMENT, nxt) is nxt
    with pytest.raises(StageMachineError, match="illegal transition"):
        apply_stage(RunStage.IMPLEMENT, RunStage.ACCEPTED)


def test_repair_returns_to_build_not_accepted():
    assert apply_stage(RunStage.REPAIR, RunStage.BUILD) is RunStage.BUILD
    with pytest.raises(StageMachineError, match="illegal transition"):
        apply_stage(RunStage.REPAIR, RunStage.ACCEPTED)


def test_accepted_and_cancelled_are_absorbing():
    others = [stage for stage in RunStage if stage not in TERMINAL_STAGES]
    for terminal in TERMINAL_STAGES:
        assert apply_stage(terminal, terminal) is terminal
        for nxt in others:
            with pytest.raises(StageMachineError, match="illegal transition"):
                apply_stage(terminal, nxt)


def test_blocked_and_failed_recover():
    assert apply_stage(RunStage.BLOCKED, RunStage.REPAIR) is RunStage.REPAIR
    assert apply_stage(RunStage.FAILED, RunStage.IMPLEMENT) is RunStage.IMPLEMENT
    assert apply_stage(RunStage.FAILED, RunStage.INTAKE) is RunStage.INTAKE


def test_skip_stage_next_optional_neighbor_only():
    assert skip_stage(RunStage.BUILD, RunStage.ENGINE_TEST) is RunStage.ENGINE_TEST
    assert OPTIONAL_SKIP_STAGES == frozenset({
        RunStage.ENGINE_TEST,
        RunStage.PLAYTEST,
        RunStage.VISUAL_VERIFY,
    })
    with pytest.raises(StageMachineError, match="illegal skip"):
        skip_stage(RunStage.BUILD, RunStage.REVIEW)
    with pytest.raises(StageMachineError, match="illegal skip"):
        skip_stage(RunStage.BUILD, RunStage.PLAYTEST)
    with pytest.raises(StageMachineError, match="illegal skip"):
        skip_stage(RunStage.INTAKE, RunStage.DISCOVER)


def test_from_legacy_stage_table():
    assert from_legacy_stage("plan") is RunStage.PLAN
    assert from_legacy_stage("code") is RunStage.IMPLEMENT
    assert from_legacy_stage("runtime") is RunStage.ENGINE_TEST
    assert from_legacy_stage("visual") is RunStage.VISUAL_VERIFY
    assert from_legacy_stage("publish") is RunStage.ACCEPTED
    assert from_legacy_stage("cleared") is RunStage.CANCELLED
    assert from_legacy_stage("idea") is RunStage.INTAKE
    assert from_legacy_stage("slice") is RunStage.IMPLEMENT
    assert from_legacy_stage("running") is None
    assert from_legacy_stage(3) is None
    assert from_legacy_stage("3") is None


def test_from_phase_event_full_autopilot_n():
    assert from_phase_event(n=0) is RunStage.INTAKE
    assert from_phase_event(n=1) is RunStage.PLAN
    assert from_phase_event(n=2) is RunStage.BUILD
    assert from_phase_event(n=3) is RunStage.ENGINE_TEST
    assert from_phase_event(n=4) is RunStage.VISUAL_VERIFY
    assert from_phase_event(n=4, label="Playtest personas") is RunStage.PLAYTEST
    assert from_phase_event(n=4, label="Gameplay") is RunStage.PLAYTEST
    assert from_phase_event(n=4, label="Engine verify") is RunStage.ENGINE_TEST
    assert from_phase_event(n=5) is RunStage.REVIEW
    assert from_phase_event(stage="plan") is RunStage.PLAN
    assert from_phase_event(n=9) is None


def test_status_for_stage_mapping():
    assert status_for_stage(RunStage.INTAKE) is RunStatus.IN_PROGRESS
    assert status_for_stage(RunStage.REPAIR) is RunStatus.IN_PROGRESS
    assert status_for_stage(RunStage.REVIEW) is RunStatus.IN_PROGRESS
    assert status_for_stage(RunStage.ACCEPTED) is RunStatus.DONE
    assert status_for_stage(RunStage.BLOCKED) is RunStatus.BLOCKED
    assert status_for_stage(RunStage.FAILED) is RunStatus.FAILED
    assert status_for_stage(RunStage.CANCELLED) is RunStatus.INTERRUPTED


def test_peek_stage_uses_last_phase_ignores_plan_index():
    events = (
        RunEvent(ts=1.0, event=EventKind.PHASE, data={"n": 1, "label": "Планиране"}),
        RunEvent(ts=2.0, event=EventKind.STAGE, data={"i": 3}),
        RunEvent(ts=3.0, event="phase", data={"n": 2, "label": "Строене"}),
        RunEvent(ts=4.0, event="stage", data={"i": 4}),
    )
    assert peek_stage(events) is RunStage.BUILD
    assert peek_stage(events[:1]) is RunStage.PLAN
    assert peek_stage(events[1:2]) is None
    assert peek_stage(()) is None


def test_unknown_stage_string_on_apply():
    with pytest.raises(StageMachineError, match="unknown run stage"):
        apply_stage(RunStage.PLAN, "running")


def test_stage_machine_does_not_import_runtime():
    tree = ast.parse(STAGE_MACHINE_PATH.read_text(encoding="utf-8"))
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

    code = (
        "import daedalus.stage_machine\n"
        "import sys\n"
        "bad = [n for n in sys.modules if n in "
        "('daedalus.agent', 'daedalus.native_tools', 'daedalus.job_ledger', "
        "'daedalus.run_engine', 'daedalus.build_loop') "
        "or n.startswith('daedalus.agent.') "
        "or n.startswith('daedalus.native_tools.') "
        "or n.startswith('daedalus.job_ledger.') "
        "or n.startswith('daedalus.run_engine.') "
        "or n.startswith('daedalus.build_loop.') "
        "or n.startswith('daedalus.kernel.full_autopilot')]\n"
        "assert not bad, bad\n"
        "assert daedalus.stage_machine.TERMINAL_STAGES\n"
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
