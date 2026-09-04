"""Economy / combat Monte Carlo — `playtest/balance.json` (optional `.mjs`).

A resource that only grows, a win condition that never fires, or a fight
that always wins when a lose condition exists, fails the gate. No file
means the old keyword heuristic still warns and does not block.
"""
from __future__ import annotations

import json
import logging
import random
import shutil
import subprocess
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

BALANCE_JSON = "playtest/balance.json"
BALANCE_MJS = "playtest/balance.mjs"
DEFAULT_ROUNDS = 1000
DEFAULT_MAX_STEPS = 40


def _meets_win(state: dict[str, float], win_when: dict[str, Any] | None) -> bool:
    if not win_when:
        return False
    for key, threshold in win_when.items():
        try:
            if float(state.get(key, 0)) < float(threshold):
                return False
        except (TypeError, ValueError):
            return False
    return True


def _meets_lose(state: dict[str, float], lose_when: dict[str, Any] | None) -> bool:
    if not lose_when:
        return False
    for key, threshold in lose_when.items():
        try:
            if float(state.get(key, 0)) <= float(threshold):
                return True
        except (TypeError, ValueError):
            continue
    return False


def _can_afford(state: dict[str, float], cost: dict[str, Any] | None) -> bool:
    if not cost:
        return True
    for key, amount in cost.items():
        try:
            if float(state.get(key, 0)) < float(amount):
                return False
        except (TypeError, ValueError):
            return False
    return True


def _apply(state: dict[str, float], delta: dict[str, Any] | None, *, sign: int) -> None:
    if not delta:
        return
    for key, amount in delta.items():
        try:
            state[key] = float(state.get(key, 0)) + sign * float(amount)
        except (TypeError, ValueError):
            continue


def _as_float_map(raw: Any) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for key, val in raw.items():
        try:
            out[str(key)] = float(val)
        except (TypeError, ValueError):
            continue
    return out


def simulate(spec: dict[str, Any], *, seed: int = 1) -> dict[str, Any]:
    starting = _as_float_map(spec.get("starting") or {"gold": 10, "hp": 100})
    actions = spec.get("actions") if isinstance(spec.get("actions"), list) else []
    actions = [a for a in actions if isinstance(a, dict)]
    rounds = int(spec.get("rounds") or DEFAULT_ROUNDS)
    max_steps = int(spec.get("max_steps") or DEFAULT_MAX_STEPS)
    win_when = spec.get("win_when") if isinstance(spec.get("win_when"), dict) else None
    lose_when = spec.get("lose_when") if isinstance(spec.get("lose_when"), dict) else None
    rng = random.Random(int(spec.get("seed") or seed))

    degenerate: list[str] = []
    sinks: set[str] = set()
    sources: set[str] = set()
    for act in actions:
        sinks.update(_as_float_map(act.get("cost")))
        sources.update(_as_float_map(act.get("reward")))
    for key in list(starting) + list(sources):
        if key in sources and key not in sinks:
            degenerate.append(f"{key} only grows (no sink)")

    if win_when and _meets_win(starting, win_when):
        degenerate.append("already winning at start")

    if not actions:
        degenerate.append("no actions")

    wins = loses = timeouts = 0
    totals = {k: 0.0 for k in starting}
    for _ in range(max(1, rounds)):
        state = dict(starting)
        outcome = "timeout"
        for _step in range(max(1, max_steps)):
            if _meets_lose(state, lose_when):
                outcome = "lose"
                break
            if _meets_win(state, win_when):
                outcome = "win"
                break
            affordable = [a for a in actions if _can_afford(state, a.get("cost"))]
            if not affordable:
                outcome = "lose" if lose_when else "timeout"
                break
            act = rng.choice(affordable)
            _apply(state, act.get("cost"), sign=-1)
            p_win = act.get("p_win")
            try:
                chance = 1.0 if p_win is None else float(p_win)
            except (TypeError, ValueError):
                chance = 1.0
            if rng.random() < chance:
                _apply(state, act.get("reward"), sign=1)
            for key in list(state):
                if key in ("hp", "integrity", "lives"):
                    state[key] = max(0.0, state[key])
            if _meets_lose(state, lose_when):
                outcome = "lose"
                break
            if _meets_win(state, win_when):
                outcome = "win"
                break
        if outcome == "win":
            wins += 1
        elif outcome == "lose":
            loses += 1
        else:
            timeouts += 1
        for key, val in state.items():
            totals[key] = totals.get(key, 0.0) + float(val)

    n = max(1, rounds)
    win_rate = wins / n
    lose_rate = loses / n
    if win_when and wins == 0:
        degenerate.append("never win")
    if lose_when and wins == n:
        degenerate.append("always win (never lose)")

    ok = not degenerate
    mean_final = {k: v / n for k, v in totals.items()}
    return {
        "ok": ok,
        "skipped": False,
        "rounds": rounds,
        "wins": wins,
        "loses": loses,
        "timeouts": timeouts,
        "win_rate": win_rate,
        "lose_rate": lose_rate,
        "timeout_rate": timeouts / n,
        "degenerate": degenerate,
        "mean_final": mean_final,
        "detail": "; ".join(degenerate) if degenerate else (
            f"win_rate={win_rate:.2f} lose_rate={lose_rate:.2f} over {rounds} rounds"
        ),
    }


def _run_node_mjs(path: Path, timeout: float = 15.0) -> dict[str, Any] | None:
    node = shutil.which("node")
    if not node:
        return None
    try:
        proc = subprocess.run(
            [node, str(path)],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(path.parent.parent),
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("balance.mjs: %s", exc)
        return {
            "ok": False,
            "skipped": False,
            "degenerate": [f"balance.mjs failed: {exc}"[:160]],
            "detail": str(exc)[:160],
        }
    try:
        raw = json.loads(proc.stdout.strip() or "{}")
    except json.JSONDecodeError:
        return {
            "ok": False,
            "skipped": False,
            "degenerate": ["balance.mjs did not print JSON"],
            "detail": (proc.stderr or proc.stdout or "no json")[:160],
        }
    if not isinstance(raw, dict):
        return {"ok": False, "skipped": False, "degenerate": ["balance.mjs JSON must be an object"]}
    degenerate = list(raw.get("degenerate") or [])
    ok = bool(raw.get("ok")) and not degenerate
    raw["ok"] = ok
    raw["skipped"] = False
    raw.setdefault("detail", "; ".join(degenerate) if degenerate else "node balance.mjs")
    return raw


def run_for_project(pdir: Path | str) -> dict[str, Any] | None:
    """Return a sim result, or None when the project has no balance spec."""
    root = Path(pdir)
    json_path = root / BALANCE_JSON
    mjs_path = root / BALANCE_MJS
    if json_path.is_file():
        try:
            spec = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {
                "ok": False,
                "skipped": False,
                "degenerate": [f"unreadable balance.json: {exc}"[:160]],
                "detail": str(exc)[:160],
            }
        if not isinstance(spec, dict):
            return {
                "ok": False,
                "skipped": False,
                "degenerate": ["balance.json must be an object"],
                "detail": "balance.json must be an object",
            }
        return simulate(spec)
    if mjs_path.is_file():
        ran = _run_node_mjs(mjs_path)
        if ran is not None:
            return ran
        return {
            "ok": True,
            "skipped": True,
            "degenerate": [],
            "detail": "playtest/balance.mjs needs node on PATH — skipped",
        }
    return None
