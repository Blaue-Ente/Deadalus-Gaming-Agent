"""Tests that stay with each game: its own logic tests and a baseline of what already worked.

Owner, 2026-11-12: Daedalus should make games a game-development team can keep building on. A team's game (The-
Labirinth) has a pure simulation with tests and a verify step that must pass before every push; a Daedalus game was
checked once, when it was built, and never again. So:

- Logic tests. The writer keeps the rules (score, lives, collisions, win and lose, restart) in a pure module and writes
  ``tests/*.test.mjs`` with ``node:test``. They run with ``node --test``: no install, no browser.
- A baseline. When a game is accepted (lead and audience), the checks that pass are recorded in
  ``studio/baseline.json``. On a later change, a check that passed then and fails now is a regression, named.

``run`` never raises; a missing Node or a timeout is a named result, never a pass.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import time
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

BASELINE_REL = "studio/baseline.json"
TEST_GLOBS = ("tests/*.test.mjs", "tests/*.test.js", "test/*.test.mjs", "test/*.test.js")
LOGIC_TIMEOUT_S = 60

# The writer's part, said once in the build prompt for games (see guidance()).
GUIDANCE = (
    "TESTS (this game is kept, so it gets tests): put the rules in a pure ES module logic.mjs with no DOM or canvas "
    "access: state in, state out (score, lives, collisions, win and lose, restart). game.js imports it. Write "
    "tests/logic.test.mjs with node:test and node:assert covering those rules (import ../logic.mjs). Daedalus runs "
    "them with `node --test`; keep them fast and deterministic (no timers, no randomness without a seed)."
)

_TAP_PASS = re.compile(r"^# pass (\d+)", re.M)
_TAP_FAIL = re.compile(r"^# fail (\d+)", re.M)
_NOT_OK = re.compile(r"^\s*not ok \d+ - (.+)$", re.M)


def _root(slug: str) -> Path:
    from . import playground as pg
    return pg.project_dir(slug)


def test_files(root: Path) -> list[Path]:
    seen: list[Path] = []
    for pattern in TEST_GLOBS:
        for p in sorted(root.glob(pattern)):
            if p.is_file() and p not in seen:
                seen.append(p)
    return seen


async def run_logic_tests(slug: str) -> dict[str, Any]:
    """``node --test`` over the project's test files. {ran, ok, passed, failed, failures, note}."""
    root = _root(slug)
    files = test_files(root)
    if not files:
        return {"ran": False, "ok": None, "passed": 0, "failed": 0, "failures": [], "note": "no tests in tests/"}
    node = shutil.which("node")
    if not node:
        return {"ran": False, "ok": False, "passed": 0, "failed": 0, "failures": [],
                "note": "Node.js is not installed here: the logic tests could not run"}
    from .playground_runtime import PlaygroundRuntime

    env = PlaygroundRuntime.child_env(0)
    proc = await asyncio.create_subprocess_exec(
        node, "--test", "--test-reporter=tap", *[p.relative_to(root).as_posix() for p in files],
        cwd=str(root), env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        start_new_session=True)
    try:
        raw, _ = await asyncio.wait_for(proc.communicate(), timeout=LOGIC_TIMEOUT_S)
    except asyncio.TimeoutError:
        from .proc import terminate_process_tree
        await terminate_process_tree(proc, timeout=8.0)
        return {"ran": True, "ok": False, "passed": 0, "failed": 0, "failures": [],
                "note": f"the logic tests did not finish in {LOGIC_TIMEOUT_S}s (a loop or a timer that never ends?)"}
    out = (raw or b"").decode("utf-8", errors="replace")
    passed = int(m.group(1)) if (m := _TAP_PASS.search(out)) else 0
    failed = int(m.group(1)) if (m := _TAP_FAIL.search(out)) else (0 if proc.returncode == 0 else 1)
    failures = [f.strip()[:160] for f in _NOT_OK.findall(out)][:8]
    ok = proc.returncode == 0 and failed == 0 and passed > 0
    note = "" if ok else ("no test ran (a syntax error or a bad import?)" if passed == 0 and failed == 0 else "")
    if not ok and not failures:
        from .static_build import digest
        note = note or digest(out, 400)
    return {"ran": True, "ok": ok, "passed": passed, "failed": failed, "failures": failures, "note": note,
            "files": [p.relative_to(root).as_posix() for p in files]}


def _passing_play(play: dict[str, Any] | None) -> list[str]:
    checks = (play or {}).get("checks") or []
    return sorted({str(c.get("id")) for c in checks if isinstance(c, dict) and c.get("ok") and c.get("id")})


def load_baseline(slug: str) -> dict[str, Any] | None:
    try:
        data = json.loads((_root(slug) / BASELINE_REL).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def record_baseline(slug: str, logic: dict[str, Any], play: dict[str, Any] | None) -> dict[str, Any]:
    """What an accepted game passes: kept with the project, so later changes are measured against it."""
    data = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "play_passing": _passing_play(play),
            "logic_passed": int(logic.get("passed") or 0) if logic.get("ok") else 0,
            "logic_files": list(logic.get("files") or [])}
    path = _root(slug) / BASELINE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data


def regressions(baseline: dict[str, Any] | None, logic: dict[str, Any], play: dict[str, Any] | None) -> list[str]:
    """What worked when the game was accepted and does not now."""
    if not baseline:
        return []
    out: list[str] = []
    # A play result counts only when the play really ran (a skipped check or {} is not a failure of the game).
    real_play = bool(play) and not play.get("skipped") and bool(play.get("checks"))
    now = set(_passing_play(play)) if real_play else None
    if now is not None:
        out += [f"play check {cid} passed at acceptance and fails now" for cid in baseline.get("play_passing") or []
                if cid not in now]
    was = int(baseline.get("logic_passed") or 0)
    if not logic.get("ran"):
        return out                               # no Node here: not something the writer can fix
    if was and not logic.get("ok"):
        out.append(f"the logic tests passed at acceptance ({was}) and fail now")
    elif was and int(logic.get("passed") or 0) < was:
        out.append(f"fewer logic tests than at acceptance ({logic.get('passed')} < {was}): were tests removed?")
    return out


async def run(slug: str, *, play: dict[str, Any] | None = None) -> dict[str, Any]:
    """The project's tests now: logic tests, and against its baseline. Never raises."""
    try:
        logic = await run_logic_tests(slug)
    except Exception as exc:
        log.warning("logic tests for %s failed to start", slug, exc_info=True)
        logic = {"ran": False, "ok": False, "passed": 0, "failed": 0, "failures": [], "note": str(exc)[:200]}
    baseline = load_baseline(slug)
    regs = regressions(baseline, logic, play)
    ok = logic.get("ok") is not False and not regs
    parts = []
    if logic.get("ran"):
        parts.append(f"logic {logic.get('passed', 0)} passed, {logic.get('failed', 0)} failed")
    else:
        parts.append(str(logic.get("note") or "no logic tests"))
    parts.append(f"{len(regs)} regression(s)" if baseline else "no baseline yet")
    report = {"ok": ok, "logic": logic, "regressions": regs, "baseline": bool(baseline), "summary": "; ".join(parts)}
    _ledger(slug, report)
    return report


def _ledger(slug: str, report: dict[str, Any]) -> None:
    try:
        from .harness import runs
        rid = runs.current_run_id()
        if rid:
            lg = report["logic"]
            runs.safe_event(rid, "project_tests", {
                "slug": slug, "ok": report["ok"], "ran": bool(lg.get("ran")), "passed": int(lg.get("passed") or 0),
                "failed": int(lg.get("failed") or 0), "failures": list(lg.get("failures") or [])[:4],
                "regressions": report["regressions"][:4], "baseline": report["baseline"],
                "note": str(lg.get("note") or "")[:200]})
    except Exception:
        log.debug("project_tests event skipped", exc_info=True)


def upstream_repo(slug: str) -> bool:
    """A cloned or revived repository: its own tests and structure are the upstream's, not Daedalus's to impose."""
    return (_root(slug) / ".daedalus" / "upstream_manifest.json").is_file()
