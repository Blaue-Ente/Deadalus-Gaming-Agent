"""Characterization of propose/approve/reject and _run_propose_loop — current behavior."""
from __future__ import annotations

import asyncio

import pytest

from tests.characterization_fakes import isolate_paths, rebind_self_improve_roots


@pytest.fixture
def char_env(tmp_path, monkeypatch):
    isolate_paths(tmp_path, monkeypatch)
    rebind_self_improve_roots(tmp_path, monkeypatch)
    yield tmp_path


def _enable_skill(name: str, enabled: bool = True) -> None:
    from daedalus.self_improve import load_skills_state, save_skills_state

    state = load_skills_state()
    state[name] = enabled
    save_skills_state(state)


def test_propose_patch_pending_on_disk(char_env):
    from daedalus.self_improve import PROPOSALS_DIR, get_proposal, propose_patch

    out = propose_patch(
        "tests/char_probe_mod.py",
        "x = 1\n",
        "characterization",
        proposed_by="char-test",
    )
    assert out["ok"] is True
    pid = out["proposal_id"]
    assert pid.startswith("prop_")
    stored = get_proposal(pid)
    assert stored is not None
    assert stored["status"] == "pending"
    assert stored["file_path"] == "tests/char_probe_mod.py"
    assert (PROPOSALS_DIR / f"{pid}.json").is_file()


def test_propose_identical_content_rejected(char_env):
    from daedalus.self_improve import PROJECT_ROOT, propose_patch

    target = PROJECT_ROOT / "tests" / "char_probe_mod.py"
    target.write_text("same\n", encoding="utf-8")
    out = propose_patch("tests/char_probe_mod.py", "same\n", "noop")
    assert out["ok"] is False
    assert "identical" in out["error"]


def test_propose_hard_blocked_path(char_env):
    from daedalus.self_improve import propose_patch

    out = propose_patch(".env", "SECRET=1\n", "nope")
    assert out["ok"] is False
    assert "hard-blocked" in out["error"]


def test_propose_disabled_skill(char_env):
    from daedalus.self_improve import propose_patch

    _enable_skill("edit_modules", False)
    out = propose_patch("daedalus/foo.py", "x = 1\n", "edit harness")
    assert out["ok"] is False
    assert out.get("required_skill") == "edit_modules"
    assert "disabled" in out["error"]


def test_approve_writes_file_and_backup(char_env):
    from daedalus.self_improve import (
        BACKUPS_DIR,
        PROJECT_ROOT,
        approve_proposal,
        get_proposal,
        propose_patch,
    )

    rel = "tests/char_probe_mod.py"
    target = PROJECT_ROOT / rel
    target.write_text("old\n", encoding="utf-8")
    proposed = propose_patch(rel, "new\n", "swap")
    assert proposed["ok"] is True
    applied = approve_proposal(proposed["proposal_id"], approved_by="owner")
    assert applied["ok"] is True
    assert target.read_text(encoding="utf-8") == "new\n"
    backup = BACKUPS_DIR / proposed["proposal_id"] / "char_probe_mod.py"
    assert backup.is_file()
    assert backup.read_text(encoding="utf-8") == "old\n"
    row = get_proposal(proposed["proposal_id"])
    assert row["status"] == "applied"


def test_approve_non_pending_fails(char_env):
    from daedalus.self_improve import approve_proposal, propose_patch, reject_proposal

    proposed = propose_patch("tests/char_probe_mod.py", "x = 1\n", "once")
    assert proposed["ok"] is True
    rejected = reject_proposal(proposed["proposal_id"], rejected_by="owner")
    assert rejected["ok"] is True
    again = approve_proposal(proposed["proposal_id"])
    assert again["ok"] is False
    assert "pending" in again["error"]


def test_reject_sets_status(char_env):
    from daedalus.self_improve import PROJECT_ROOT, get_proposal, propose_patch, reject_proposal

    proposed = propose_patch("tests/char_probe_mod.py", "x = 1\n", "reject me")
    out = reject_proposal(proposed["proposal_id"], rejected_by="owner", note="no")
    assert out["ok"] is True
    row = get_proposal(proposed["proposal_id"])
    assert row["status"] == "rejected"
    assert not (PROJECT_ROOT / "tests" / "char_probe_mod.py").exists()


def test_run_propose_loop_permission_denied(char_env, monkeypatch):
    from daedalus.agent import _run_propose_loop

    monkeypatch.setattr("daedalus.outbox.check_permission", lambda *_a, **_k: False)
    text, cleaned = asyncio.run(
        _run_propose_loop(
            initial_raw=(
                "intro\n<NEED_PROPOSE_START>\n"
                "FILE: tests/char_probe_mod.py\n"
                "REASON: add x\n"
                "CODE_START\nx = 1\nCODE_END\n"
                "</NEED_PROPOSE_START>\n"
            ),
            initial_block={
                "file_path": "tests/char_probe_mod.py",
                "reason": "add x",
                "code": "x = 1\n",
                "confidence": 0.9,
                "evidence_count": 2,
            },
            user_content="task",
            persona="p",
        )
    )
    assert "PERMISSION DENIED" in text
    assert "NEED_PROPOSE_START" not in cleaned


def test_run_propose_loop_low_confidence_blocked(char_env, monkeypatch):
    from daedalus.agent import _run_propose_loop

    monkeypatch.setattr("daedalus.outbox.check_permission", lambda *_a, **_k: True)
    text, cleaned = asyncio.run(
        _run_propose_loop(
            initial_raw=(
                "<NEED_PROPOSE_START>\n"
                "FILE: tests/char_probe_mod.py\n"
                "REASON: add x\n"
                "CONFIDENCE: 0.40\n"
                "CODE_START\nx = 1\nCODE_END\n"
                "</NEED_PROPOSE_START>"
            ),
            initial_block={
                "file_path": "tests/char_probe_mod.py",
                "reason": "add x",
                "code": "x = 1\n",
                "confidence": 0.40,
                "evidence_count": 0,
            },
            user_content="task",
            persona="p",
        )
    )
    assert "CONFIDENCE GATE BLOCKED" in text
    assert "0.40" in text
    assert "NEED_PROPOSE_START" not in cleaned


def test_run_propose_loop_standalone_argos_import_failure(char_env, monkeypatch):
    """Standalone has no argos_data_nexus; success-path import raises today."""
    from daedalus.agent import _run_propose_loop

    monkeypatch.setattr("daedalus.outbox.check_permission", lambda *_a, **_k: True)
    monkeypatch.setattr(
        "daedalus.sandbox.validate_patch",
        lambda **_k: {"ok": True, "layers": [], "elapsed_ms": 1, "errors": []},
    )
    with pytest.raises(ModuleNotFoundError, match="argos_data_nexus"):
        asyncio.run(
            _run_propose_loop(
                initial_raw=(
                    "<NEED_PROPOSE_START>\n"
                    "FILE: tests/char_probe_mod.py\n"
                    "REASON: add x\n"
                    "CODE_START\nx = 1\nCODE_END\n"
                    "</NEED_PROPOSE_START>"
                ),
                initial_block={
                    "file_path": "tests/char_probe_mod.py",
                    "reason": "add x",
                    "code": "x = 1\n",
                    "confidence": None,
                    "evidence_count": 0,
                },
                user_content="task",
                persona="p",
                max_retries=0,
            )
        )
