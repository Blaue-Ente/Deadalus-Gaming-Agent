"""Web gameplay bot — `playtest/scenario.json`, pixel-diff, FPS/memory probes.

Chromium/Playwright is optional at import time. Schema + expect evaluation
are pure Python so CI can fail a missing win/lose without a browser.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCENARIO_REL = "playtest/scenario.json"
OPS = frozenset({"wait", "hold_keys", "observe", "click"})
FPS_MIN_FRAMES = 10
BUDGET_2D = {"fps_budget": 30, "memory_budget_mb": 256}
BUDGET_3D = {"fps_budget": 24, "memory_budget_mb": 512}

FPS_START_JS = """
() => {
  if (window.__daedalusFps) return true;
  const rec = { frames: 0, t0: performance.now() };
  window.__daedalusFps = rec;
  const tick = () => { rec.frames += 1; requestAnimationFrame(tick); };
  requestAnimationFrame(tick);
  return true;
}
"""

FPS_READ_JS = """
() => {
  const rec = window.__daedalusFps || { frames: 0, t0: performance.now() };
  const dt = (performance.now() - rec.t0) / 1000;
  const fps = dt > 0 ? rec.frames / dt : 0;
  const heap = (performance.memory && performance.memory.usedJSHeapSize) || null;
  return {
    fps: fps,
    frames: rec.frames,
    memory_mb: heap == null ? null : heap / (1024 * 1024),
  };
}
"""

FINGERPRINT_JS = """
() => {
  const canvas = document.querySelector('canvas');
  if (!canvas) return '';
  try {
    const off = document.createElement('canvas');
    off.width = 16;
    off.height = 16;
    const ctx = off.getContext('2d');
    if (!ctx) return '';
    ctx.drawImage(canvas, 0, 0, 16, 16);
    return Array.from(ctx.getImageData(0, 0, 16, 16).data).join(',');
  } catch (err) {
    try { return (canvas.toDataURL && canvas.toDataURL()) || ''; }
    catch (err2) { return ''; }
  }
}
"""

OBSERVE_JS = """
() => {
  const st = (window.__DAEDALUS_STATE__ && typeof window.__DAEDALUS_STATE__ === 'object')
    ? window.__DAEDALUS_STATE__
    : ((window.__GAME__ && typeof window.__GAME__ === 'object') ? window.__GAME__ : {});
  const failEl = document.querySelector('#fail');
  const winEl = document.querySelector('#win');
  const failVisible = !!(failEl && !failEl.hidden);
  const winVisible = !!(winEl && !winEl.hidden);
  const body = ((document.body && document.body.innerText) || '').toLowerCase();
  const lose = !!(st.failed || st.lose || st.gameOver || failVisible
    || body.includes('провал') || body.includes('game over'));
  const win = !!(st.win || st.won || st.victory || winVisible
    || body.includes('победа') || body.includes('you win'));
  return { lose, win, failed: !!st.failed, state: st };
}
"""


def is_racer_tree(pdir: Path | str) -> bool:
    return (Path(pdir) / "src" / "racer" / "vehicle.js").is_file()


def fingerprints_differ(before: str | bytes | None, after: str | bytes | None) -> bool:
    if before is None or after is None:
        return False
    if before == "" or after == "":
        return False
    return before != after


def default_scenario(kind: str = "static", *, racer: bool = False) -> dict[str, Any]:
    """Default bot script. Racers must prove a fail; generic canvas must move."""
    if racer:
        return {
            "version": 1,
            "fps_budget": BUDGET_3D["fps_budget"],
            "memory_budget_mb": BUDGET_3D["memory_budget_mb"],
            "require_win": False,
            "require_lose": True,
            "steps": [
                {"op": "wait", "ms": 800},
                {
                    "op": "hold_keys",
                    "keys": ["ArrowUp"],
                    "ms": 900,
                    "expect": {"canvas_changed": True},
                },
                {
                    "op": "hold_keys",
                    "keys": ["ArrowUp", "ArrowRight"],
                    "ms": 5000,
                    "expect": {"lose": True},
                },
            ],
        }
    return {
        "version": 1,
        "fps_budget": BUDGET_2D["fps_budget"],
        "memory_budget_mb": BUDGET_2D["memory_budget_mb"],
        "require_win": False,
        "require_lose": False,
        "steps": [
            {"op": "wait", "ms": 400},
            {
                "op": "hold_keys",
                "keys": ["ArrowRight", "ArrowUp"],
                "ms": 600,
                "expect": {"canvas_changed": True},
            },
        ],
    }


def dumps_scenario(scenario: dict[str, Any]) -> str:
    return json.dumps(scenario, ensure_ascii=False, indent=2) + "\n"


def validate_scenario(raw: Any) -> tuple[dict[str, Any] | None, str]:
    if not isinstance(raw, dict):
        return None, "scenario must be a JSON object"
    steps = raw.get("steps")
    if not isinstance(steps, list) or not steps:
        return None, "scenario.steps must be a non-empty list"
    cleaned: list[dict[str, Any]] = []
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            return None, f"steps[{i}] must be an object"
        op = str(step.get("op") or "observe")
        if op not in OPS:
            return None, f"steps[{i}].op must be one of {sorted(OPS)}"
        row = dict(step)
        row["op"] = op
        cleaned.append(row)
    out = dict(raw)
    out["steps"] = cleaned
    out["require_win"] = bool(raw.get("require_win"))
    out["require_lose"] = bool(raw.get("require_lose"))
    try:
        out["fps_budget"] = float(raw.get("fps_budget") or BUDGET_2D["fps_budget"])
        out["memory_budget_mb"] = float(raw.get("memory_budget_mb") or BUDGET_2D["memory_budget_mb"])
    except (TypeError, ValueError):
        return None, "fps_budget / memory_budget_mb must be numbers"
    return out, ""


def load_scenario(pdir: Path | str) -> tuple[dict[str, Any] | None, str]:
    path = Path(pdir) / SCENARIO_REL
    if not path.is_file():
        return None, "missing"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"unreadable: {exc}"[:160]
    return validate_scenario(raw)


def write_default_if_missing(
    pdir: Path | str,
    kind: str = "static",
    *,
    racer: bool = False,
) -> Path:
    root = Path(pdir)
    path = root / SCENARIO_REL
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_scenario(default_scenario(kind, racer=racer)), encoding="utf-8")
    return path


def eval_expect(observation: dict[str, Any], expect: dict[str, Any] | None) -> tuple[bool, str]:
    """Pure-Python expect. Used by the bot and by unit tests without Playwright."""
    if not expect:
        return True, "no expect"
    if expect.get("canvas_changed") and not observation.get("canvas_changed"):
        return False, "canvas did not change"
    if expect.get("lose") and not observation.get("lose"):
        return False, "lose not reached"
    if expect.get("win") and not observation.get("win"):
        return False, "win not reached"
    sel = expect.get("selector")
    if isinstance(sel, dict):
        texts = observation.get("selector_text") or {}
        for css, rule in sel.items():
            got = str(texts.get(css) or "")
            if not isinstance(rule, dict):
                continue
            if rule.get("contains") and rule["contains"] not in got:
                return False, f"{css} missing {rule['contains']!r}"
            if rule.get("visible") and not observation.get("selector_visible", {}).get(css):
                return False, f"{css} not visible"
    state_exp = expect.get("state")
    if isinstance(state_exp, dict):
        st = observation.get("state") or {}
        for key, want in state_exp.items():
            if st.get(key) != want:
                return False, f"state.{key}={st.get(key)!r} != {want!r}"
    return True, "ok"


def expects_canvas_changed(scenario: dict[str, Any]) -> bool:
    for step in scenario.get("steps") or []:
        exp = step.get("expect") if isinstance(step, dict) else None
        if isinstance(exp, dict) and exp.get("canvas_changed"):
            return True
    return False


def scenario_checks(bot: dict[str, Any], scenario: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn a bot run into gameplay_verify checks (required flags included)."""
    checks: list[dict[str, Any]] = []
    want_move = expects_canvas_changed(scenario)
    moved = bool(bot.get("saw_canvas_changed"))
    checks.append({
        "id": "scenario_canvas_changed",
        "ok": moved if want_move else True,
        "detail": "canvas fingerprint changed" if moved else "canvas fingerprint unchanged",
        "required": want_move,
        "phase": "scenario",
    })
    if scenario.get("require_lose"):
        lost = bool(bot.get("saw_lose"))
        checks.append({
            "id": "scenario_lose",
            "ok": lost,
            "detail": "lose reached" if lost else "require_lose but bot never saw fail/lose",
            "required": True,
            "phase": "scenario",
        })
    if scenario.get("require_win"):
        won = bool(bot.get("saw_win"))
        checks.append({
            "id": "scenario_win",
            "ok": won,
            "detail": "win reached" if won else "require_win but bot never saw win",
            "required": True,
            "phase": "scenario",
        })
    fps = bot.get("fps")
    frames = int(bot.get("frames") or 0)
    budget = float(scenario.get("fps_budget") or BUDGET_2D["fps_budget"])
    reliable = bool(bot.get("fps_reliable")) or frames >= FPS_MIN_FRAMES
    if not reliable:
        checks.append({
            "id": "fps_budget",
            "ok": True,
            "detail": f"headless FPS sample too thin (frames={frames}) — warn only",
            "required": False,
            "warn": True,
            "phase": "perf",
        })
    elif fps is not None and float(fps) < budget:
        checks.append({
            "id": "fps_budget",
            "ok": False,
            "detail": f"fps={float(fps):.1f} < budget {budget}",
            "required": True,
            "phase": "perf",
        })
    else:
        checks.append({
            "id": "fps_budget",
            "ok": True,
            "detail": f"fps={float(fps or 0):.1f} (budget {budget})",
            "required": False,
            "phase": "perf",
        })
    mem = bot.get("memory_mb")
    mem_budget = float(scenario.get("memory_budget_mb") or BUDGET_2D["memory_budget_mb"])
    if mem is None:
        checks.append({
            "id": "memory_budget",
            "ok": True,
            "detail": "performance.memory unavailable — skip",
            "required": False,
            "phase": "perf",
        })
    elif float(mem) > mem_budget:
        checks.append({
            "id": "memory_budget",
            "ok": False,
            "detail": f"heap {float(mem):.0f} MB > {mem_budget:.0f} MB",
            "required": True,
            "phase": "perf",
        })
    else:
        checks.append({
            "id": "memory_budget",
            "ok": True,
            "detail": f"heap {float(mem):.0f} MB (budget {mem_budget:.0f})",
            "required": False,
            "phase": "perf",
        })
    for row in bot.get("steps") or []:
        if row.get("ok") is False:
            checks.append({
                "id": f"scenario_step_{row.get('i')}",
                "ok": False,
                "detail": row.get("detail") or "step failed",
                "required": False,
                "phase": "scenario",
            })
    return checks


async def run_scenario_on_page(page: Any, scenario: dict[str, Any]) -> dict[str, Any]:
    """Drive Playwright keys and collect observations. Caller owns the page."""
    await page.evaluate(FPS_START_JS)
    canvas = await page.query_selector("canvas")
    if canvas:
        try:
            await canvas.click(timeout=2000)
        except Exception:
            pass

    saw_lose = False
    saw_win = False
    saw_change = False
    step_rows: list[dict[str, Any]] = []
    prev_fp = await page.evaluate(FINGERPRINT_JS)
    prev_shot: bytes | None = None

    for i, step in enumerate(scenario.get("steps") or []):
        op = step.get("op") or "observe"
        try:
            if op == "wait":
                await page.wait_for_timeout(int(step.get("ms") or 200))
            elif op == "hold_keys":
                keys = [str(k) for k in (step.get("keys") or [])]
                ms = int(step.get("ms") or 200)
                for key in keys:
                    await page.keyboard.down(key)
                await page.wait_for_timeout(ms)
                for key in reversed(keys):
                    try:
                        await page.keyboard.up(key)
                    except Exception:
                        pass
            elif op == "click":
                sel = str(step.get("selector") or "canvas")
                el = await page.query_selector(sel)
                if el:
                    await el.click(timeout=2000)
            elif op == "observe":
                await page.wait_for_timeout(int(step.get("ms") or 50))
        except Exception as exc:
            step_rows.append({
                "i": i, "op": op, "ok": False,
                "detail": f"{op} raised {exc}"[:160],
            })
            continue

        obs = await page.evaluate(OBSERVE_JS)
        fp = await page.evaluate(FINGERPRINT_JS)
        changed = fingerprints_differ(prev_fp, fp)
        if not changed:
            try:
                shot = await page.locator("canvas").first.screenshot()
                if prev_shot is not None:
                    changed = fingerprints_differ(prev_shot, shot)
                prev_shot = shot
            except Exception:
                pass
        prev_fp = fp
        obs["canvas_changed"] = changed
        if changed:
            saw_change = True
        if obs.get("lose"):
            saw_lose = True
        if obs.get("win"):
            saw_win = True
        ok, detail = eval_expect(obs, step.get("expect") if isinstance(step.get("expect"), dict) else None)
        step_rows.append({
            "i": i,
            "op": op,
            "ok": ok,
            "detail": detail,
            "observation": {
                "lose": bool(obs.get("lose")),
                "win": bool(obs.get("win")),
                "canvas_changed": changed,
            },
        })

    perf = await page.evaluate(FPS_READ_JS)
    frames = int(perf.get("frames") or 0)
    return {
        "steps": step_rows,
        "saw_lose": saw_lose,
        "saw_win": saw_win,
        "saw_canvas_changed": saw_change,
        "fps": perf.get("fps"),
        "frames": frames,
        "memory_mb": perf.get("memory_mb"),
        "fps_reliable": frames >= FPS_MIN_FRAMES,
    }
