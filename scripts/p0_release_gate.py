#!/usr/bin/env python3
"""P0.22 release-gate aggregator.

Hermetic evidence only: P0.00 pack, honesty 15 vs snapshot, game_v1 20 vs
sibling snapshot, skip≠ok, Docker refuse, RunState/checkpoint, typed overlay
+ six-tool registry + leftover ladder, MCP enforce, CHAT_RETURN_KEYS.

Does not re-run the full pytest suite (that is the ``pytest`` CI job).
Does not start P1. Does not delete leftover NEED_* markers.
Human merge of the P0 MR stack and GitLab branch protection are informational.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import record_baseline as baseline  # noqa: E402

EXPECTED_CHAT_RETURN_KEYS = (
    "agent",
    "session_id",
    "for_self",
    "reply",
    "context",
    "pending_proposal",
    "task_events",
    "model",
    "effort",
    "elapsed_seconds",
    "timestamp",
    "complete",
    "dod",
    "run_budget",
)
REGISTERED_SIX = frozenset(
    {"read_file", "list_dir", "code_search", "write_file", "edit_file", "run_command"}
)
OVERLAY_COUNT = 34
HUMAN_MERGE_DETAIL = (
    "P0 on main still needs a human to merge !75–!92 plus this MR, then the "
    "stack tip → main. Drafts !83 (P0.12) and !90 (P0.19) must be marked ready. "
    "P0.06 leftover-tool removal was never opened and stays forbidden."
)
BRANCH_PROTECTION_DETAIL = (
    "GitLab settings cannot be set from this repo. Owner should require "
    "ruff, mypy, uv-lock, pytest, secret-scan, pip-audit, trivy-fs, and "
    "p0-release-gate. Do not require build-image, pytest-docker, or bench-live. "
    "A blanket 'pipelines must succeed' on merge requests would also wait on "
    "DinD build-image — prefer a per-job list."
)


@dataclass
class Check:
    id: str
    criterion: str
    ok: bool
    detail: str
    blocking: bool = True
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "id": self.id,
            "criterion": self.criterion,
            "ok": self.ok,
            "detail": self.detail,
            "blocking": self.blocking,
        }
        if self.extra:
            row.update(self.extra)
        return row


def _hooks() -> dict[str, Any]:
    from daedalus.game_agent_bench import P0_22_HOOKS

    return dict(P0_22_HOOKS)


def _case_ids(report: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for row in report.get("cases") or []:
        if isinstance(row, dict) and row.get("id"):
            ids.append(str(row["id"]))
    return ids


def _assigned_tuple_strs(path: Path, name: str) -> list[str] | None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    value = baseline._assigned_value(tree, name)
    if not isinstance(value, ast.Tuple):
        return None
    out: list[str] = []
    for elt in value.elts:
        text = baseline._const_str(elt)
        if text is None:
            return None
        out.append(text)
    return out


@contextmanager
def _isolated_product(root: Path) -> Iterator[Path]:
    """Keep run_engine / honesty side effects off the repo tree."""
    tmp = tempfile.TemporaryDirectory(prefix="p0-gate-")
    data = Path(tmp.name) / "data"
    play = Path(tmp.name) / "projects"
    data.mkdir()
    play.mkdir()
    keys = (
        "DAEDALUS_STANDALONE",
        "DAEDALUS_DATA",
        "DAEDALUS_PLAYGROUND",
        "DAEDALUS_BENCH_LIVE",
        "DAEDALUS_EXEC_BACKEND",
        "DAEDALUS_EXEC_PULL",
    )
    old = {key: os.environ.get(key) for key in keys}
    os.environ["DAEDALUS_STANDALONE"] = "1"
    os.environ["DAEDALUS_DATA"] = str(data)
    os.environ["DAEDALUS_PLAYGROUND"] = str(play)
    os.environ.pop("DAEDALUS_BENCH_LIVE", None)
    os.environ.pop("DAEDALUS_EXEC_BACKEND", None)
    os.environ.pop("DAEDALUS_EXEC_PULL", None)
    try:
        from daedalus.paths import data_root, playground_projects_dir

        data_root.cache_clear()
        playground_projects_dir.cache_clear()
        yield root
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        try:
            from daedalus.paths import data_root, playground_projects_dir

            data_root.cache_clear()
            playground_projects_dir.cache_clear()
        except Exception:
            pass
        tmp.cleanup()


def check_pack(root: Path) -> Check:
    errors = baseline.check_pack(root)
    return Check(
        id="p0_00_pack",
        criterion="benchmark_no_worse",
        ok=not errors,
        detail="pack ok" if not errors else "; ".join(errors)[:400],
    )


def check_honesty(root: Path, hooks: dict[str, Any]) -> Check:
    from daedalus.game_agent_bench import evaluate

    live = evaluate(persist=False, suite="honesty")
    snap_path = root / str(hooks["honesty_artifact"])
    if not snap_path.is_file():
        return Check(
            id="honesty_15",
            criterion="benchmark_no_worse",
            ok=False,
            detail=f"missing honesty snapshot {hooks['honesty_artifact']}",
        )
    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    want = int(hooks["honesty_case_count"])
    errors: list[str] = []
    if live.get("ok") is not True:
        errors.append("live honesty not ok")
    if live.get("bake_off") is not False:
        errors.append("bake_off must be false")
    if int(live.get("total") or 0) != want:
        errors.append(f"total {live.get('total')} != {want}")
    if list(live.get("failed") or []):
        errors.append("failed=" + ",".join(str(x) for x in live["failed"]))
    if int(live.get("passed") or 0) < int(snap.get("passed") or 0):
        errors.append(f"passed {live.get('passed')} < snapshot {snap.get('passed')}")
    if _case_ids(live) != _case_ids(snap):
        errors.append("case ids differ from snapshot")
    if "path" in live:
        errors.append("honesty persist must stay off")
    return Check(
        id="honesty_15",
        criterion="benchmark_no_worse",
        ok=not errors,
        detail="15/15 vs snapshot" if not errors else "; ".join(errors),
        extra={"passed": live.get("passed"), "total": live.get("total")},
    )


def check_game_v1(root: Path, hooks: dict[str, Any], live: dict[str, Any] | None = None) -> Check:
    from daedalus.game_agent_bench import GAME_TASKS, evaluate

    snap_path = root / str(hooks["game_artifact"])
    if not snap_path.is_file():
        return Check(
            id="game_v1_20",
            criterion="benchmark_no_worse",
            ok=False,
            detail=f"missing game snapshot {hooks['game_artifact']}",
        )
    if live is None:
        live = evaluate(persist=False, suite="game")
    snap = json.loads(snap_path.read_text(encoding="utf-8"))
    want = int(hooks["game_task_count"])
    errors: list[str] = []
    if live.get("ok") is not True:
        errors.append("live game_v1 not ok")
    if live.get("bake_off") is not False:
        errors.append("bake_off must be false")
    if live.get("live") is not False:
        errors.append("live engines must be off")
    if int(live.get("total") or 0) != want:
        errors.append(f"total {live.get('total')} != {want}")
    if list(live.get("failed") or []):
        errors.append("failed=" + ",".join(str(x) for x in live["failed"]))
    if _case_ids(live) != list(GAME_TASKS):
        errors.append("live ids != GAME_TASKS")
    if _case_ids(live) != _case_ids(snap):
        errors.append("case ids differ from snapshot")
    hooks_live = live.get("p0_22_hooks") or {}
    if hooks_live.get("live_required_for_release") is not False:
        errors.append("live_required_for_release must be false")
    if hooks.get("live_required_for_release") is not False:
        errors.append("hooks.live_required_for_release must be false")
    if "path" in live:
        errors.append("game persist must stay off")
    return Check(
        id="game_v1_20",
        criterion="benchmark_no_worse",
        ok=not errors,
        detail="20/20 hermetic vs snapshot" if not errors else "; ".join(errors),
        extra={"passed": live.get("passed"), "total": live.get("total")},
    )


def write_game_snapshot(root: Path, hooks: dict[str, Any]) -> Path:
    from daedalus.game_agent_bench import evaluate

    report = evaluate(persist=False, suite="game")
    report.pop("path", None)
    dest = root / str(hooks["game_artifact"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return dest


def check_skip_is_not_ok() -> Check:
    from daedalus.game_build_contract import skip_is_not_ok, verified

    skipped = skip_is_not_ok({"ok": True, "skipped": True})
    passed = (
        skipped.get("ok") is False
        and verified({"ok": True, "skipped": True}) is False
        and verified({"ok": True, "skipped": False}) is True
        and verified({"ok": True, "na": True, "skipped": False}) is True
    )
    return Check(
        id="skip_is_not_ok",
        criterion="false_success_reduced",
        ok=passed,
        detail="skipped is not success" if passed else "skip_is_not_ok drifted",
    )


def check_docker_refuse() -> Check:
    from daedalus.contracts.commands import CommandRequest
    from daedalus.docker_sandbox import run_in_docker
    from daedalus.execution_backends import (
        DockerBackend,
        LocalBackend,
        get_execution_backend,
        reset_execution_backend,
    )
    from daedalus.shell_center import history
    import daedalus.docker_sandbox as sandbox
    import daedalus.shell_center as shell

    reset_execution_backend()
    errors: list[str] = []
    default = get_execution_backend()
    if not isinstance(default, LocalBackend):
        errors.append(f"default backend is {type(default).__name__}, not Local")
    os.environ["DAEDALUS_EXEC_BACKEND"] = "docker"
    reset_execution_backend()
    docker_be = get_execution_backend()
    if not isinstance(docker_be, DockerBackend):
        errors.append(f"docker backend is {type(docker_be).__name__}")
    os.environ.pop("DAEDALUS_EXEC_BACKEND", None)
    reset_execution_backend()

    seen: list[int] = []

    def boom(*_a: object, **_k: object) -> None:
        seen.append(1)
        raise AssertionError("missing docker must not call subprocess")

    orig_bin = sandbox.docker_bin
    orig_ds_run = sandbox.subprocess.run
    orig_sh_run = shell.subprocess.run
    sandbox.docker_bin = lambda: None  # type: ignore[method-assign]
    sandbox.subprocess.run = boom  # type: ignore[method-assign]
    shell.subprocess.run = boom  # type: ignore[method-assign]
    try:
        res = run_in_docker(CommandRequest(raw_command="echo should-not-run"))
    finally:
        sandbox.docker_bin = orig_bin  # type: ignore[method-assign]
        sandbox.subprocess.run = orig_ds_run  # type: ignore[method-assign]
        shell.subprocess.run = orig_sh_run  # type: ignore[method-assign]
        reset_execution_backend()

    if res.ok is not False or res.blocked is not True or res.exit_code != 126:
        errors.append("refuse shape drifted")
    if "docker not on PATH" not in (res.error or ""):
        errors.append(res.error or "missing refuse reason")
    if seen:
        errors.append("subprocess ran")
    if history():
        errors.append("host history was written")
    return Check(
        id="docker_refuse",
        criterion="command_isolated",
        ok=not errors,
        detail="Docker refuse, default local" if not errors else "; ".join(errors),
    )


def check_resume() -> Check:
    from daedalus.run_engine import (
        append_event,
        checkpoint_for_slug,
        load_checkpoint,
        load_events,
        save_checkpoint,
    )
    from daedalus.run_state import dumps, from_job, loads

    job = {
        "job_id": "gate-run",
        "slug": "gate-slug",
        "session_id": "gate-sess",
        "original_ask": "resume check",
        "acceptance": ["checkpoint"],
        "tasks": [
            {
                "id": "t1",
                "title": "resume",
                "detail": "resume",
                "role": "coder",
                "status": "open",
                "verify": True,
            }
        ],
        "status": "in_progress",
        "created_at": 1.0,
        "updated_at": 2.0,
    }
    state = from_job(job)
    again = loads(dumps(state))
    errors: list[str] = []
    if again.run.run_id != state.run.run_id or again.run.slug != "gate-slug":
        errors.append("RunState dumps/loads drifted")
    append_event("gate-run", "phase", {"n": 2, "label": "gate"})
    save_checkpoint("gate-run", {"stage": 3, "plan": {"title": "x"}}, slug="gate-slug")
    ckpt = load_checkpoint("gate-run")
    pointed = checkpoint_for_slug("gate-slug")
    events = load_events("gate-run")
    if not ckpt or ckpt.get("stage") != 3 or ckpt.get("run_id") != "gate-run":
        errors.append("checkpoint roundtrip failed")
    if not pointed or pointed.get("stage") != 3:
        errors.append("slug pointer failed")
    if not events or events[-1].get("event") != "phase":
        errors.append("event log empty")
    return Check(
        id="run_engine_resume",
        criterion="run_engine_resume",
        ok=not errors,
        detail="RunState + checkpoint" if not errors else "; ".join(errors),
    )


def check_typed_protocol(root: Path) -> Check:
    from daedalus.contracts.tools import TOOL_SPEC_OVERLAY
    from daedalus.tool_registry import REGISTERED_TOOLS

    names = baseline.native_tool_names(root)
    overlay = set(TOOL_SPEC_OVERLAY)
    errors: list[str] = []
    if len(names) != OVERLAY_COUNT:
        errors.append(f"native tools {len(names)} != {OVERLAY_COUNT}")
    if overlay != set(names):
        missing = sorted(set(names) - overlay)
        extra = sorted(overlay - set(names))
        errors.append(f"overlay mismatch missing={missing} extra={extra}")
    if set(REGISTERED_TOOLS) != set(REGISTERED_SIX):
        errors.append(f"registry {sorted(REGISTERED_TOOLS)} != six dual-path tools")
    loop = (root / "daedalus" / "tool_loop.py").read_text(encoding="utf-8")
    if "execute_leftover_fn" not in loop or "NEED_PROPOSE_START" not in loop:
        errors.append("leftover ladder missing")
    return Check(
        id="typed_protocol",
        criterion="typed_protocol",
        ok=not errors,
        detail="overlay=34 registry=6 leftover present" if not errors else "; ".join(errors),
    )


def check_mcp_enforce() -> Check:
    from daedalus.mcp_policy import policy_is_open

    open_policy = policy_is_open()
    return Check(
        id="mcp_enforce",
        criterion="command_isolated",
        ok=not open_policy,
        detail="MCP policy not open" if not open_policy else "DAEDALUS_MCP_POLICY is open",
    )


def check_public_api(root: Path) -> Check:
    keys = _assigned_tuple_strs(root / "tests" / "characterization_fakes.py", "CHAT_RETURN_KEYS")
    errors: list[str] = []
    if keys != list(EXPECTED_CHAT_RETURN_KEYS):
        errors.append("CHAT_RETURN_KEYS drifted")
    api = (root / "app" / "harness_api.py").read_text(encoding="utf-8")
    if 'suite: str = "honesty"' not in api:
        errors.append("GET bench default suite drifted")
    if "run_suite(persist=True)" not in api:
        errors.append("POST bench persist shape drifted")
    return Check(
        id="public_api",
        criterion="old_public_api",
        ok=not errors,
        detail="CHAT_RETURN_KEYS + bench POST honesty" if not errors else "; ".join(errors),
    )


def check_human_merge() -> Check:
    return Check(
        id="human_merge",
        criterion="critical_tests_green",
        ok=True,
        detail=HUMAN_MERGE_DETAIL,
        blocking=False,
        extra={"status": "pending"},
    )


def check_branch_protection() -> Check:
    return Check(
        id="branch_protection",
        criterion="critical_tests_green",
        ok=True,
        detail=BRANCH_PROTECTION_DETAIL,
        blocking=False,
        extra={"status": "pending"},
    )


def evaluate(*, root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    hooks = _hooks()
    with _isolated_product(root):
        checks = [
            check_pack(root),
            check_honesty(root, hooks),
            check_game_v1(root, hooks),
            check_skip_is_not_ok(),
            check_docker_refuse(),
            check_resume(),
            check_typed_protocol(root),
            check_mcp_enforce(),
            check_public_api(root),
            check_human_merge(),
            check_branch_protection(),
        ]
    blocking_fail = [c for c in checks if c.blocking and not c.ok]
    by_id = {c.id: c.ok for c in checks}

    def _and(*ids: str) -> bool:
        return all(by_id.get(i) is True for i in ids)

    criteria = {
        "benchmark_no_worse": _and("p0_00_pack", "honesty_15", "game_v1_20"),
        "false_success_reduced": _and("skip_is_not_ok"),
        "command_isolated": _and("docker_refuse", "mcp_enforce"),
        "run_engine_resume": _and("run_engine_resume"),
        "typed_protocol": _and("typed_protocol"),
        "critical_tests_green": _and("p0_00_pack", "honesty_15", "game_v1_20"),
        "old_public_api": _and("public_api"),
    }
    return {
        "ok": not blocking_fail,
        "gate": "p0_release_gate",
        "bake_off": False,
        "live_required_for_release": False,
        "p0_22_hooks": hooks,
        "criteria": criteria,
        "checks": [c.as_dict() for c in checks],
        "failed": [c.id for c in blocking_fail],
        "human_merge": {
            "status": "pending",
            "detail": HUMAN_MERGE_DETAIL,
        },
        "branch_protection": {
            "status": "pending",
            "detail": BRANCH_PROTECTION_DETAIL,
        },
        "note": (
            "Aggregator of hermetic P0 evidence. Full pytest is the pytest job. "
            "Live engines and build-image are not release requirements. "
            "Human merge and GitLab settings are not CI."
        ),
    }


def _print_human(report: dict[str, Any]) -> None:
    mark = "ok" if report["ok"] else "FAIL"
    print(f"p0_release_gate {mark}  bake_off=false  live_required=false")
    for row in report["checks"]:
        flag = "pass" if row["ok"] else ("info" if not row.get("blocking", True) else "FAIL")
        if row["id"] in {"human_merge", "branch_protection"}:
            flag = "info"
        print(f"  {flag}  {row['id']}  {row['detail'][:160]}")
    print("human_merge=pending  branch_protection=pending")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P0.22 hermetic release-gate aggregator.")
    parser.add_argument("--json", action="store_true", help="Print the full report JSON")
    parser.add_argument(
        "--write-game-snapshot",
        action="store_true",
        help="Write artifacts/baseline/game-benchmark-results.json (first record).",
    )
    args = parser.parse_args(argv)
    if args.write_game_snapshot:
        dest = write_game_snapshot(ROOT, _hooks())
        print(f"wrote {dest}")
        return 0
    report = evaluate()
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        _print_human(report)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
