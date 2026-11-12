"""A longer, goal-directed play session: a player that sees the screen and chooses its next inputs.

Owner, 2026-11-12 (gap 5): the audience judged a game from a fixed five-second script (Enter, arrows, a few clicks),
the same for every game. A team's playtest has a goal: start, score, lose, play again. Here a vision model plays in
turns: it sees the current frame, says which goals it has reached, and chooses up to ``MAX_ACTIONS`` inputs. The
frames of that session, its inputs and the goals it reached or missed go to the simulated players (playtest).

Each turn is one model call (intent critic, through the gateway and its budget gate). Used for epic builds; craft
keeps the fixed script, which costs no extra calls.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

GOALS = ("start the game", "score at least once", "lose a life or reach game over", "restart and play again")
STEPS = 8
MAX_ACTIONS = 5
MAX_HOLD_MS = 1500
VIEW_W, VIEW_H = 960, 640
FRAMES_KEPT = 6
KEYS = ("ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Space", "Enter", "Escape",
        "KeyW", "KeyA", "KeyS", "KeyD", "KeyR", "KeyP", "KeyZ", "KeyX")

SeeFn = Callable[[str, str, list[dict[str, Any]]], Awaitable[str]]

SYSTEM = (
    "You are playing a small browser game to reach goals, one turn at a time. The image is the screen now "
    f"({VIEW_W}x{VIEW_H} px). Reply JSON only: "
    '{"reached": ["<goal, exactly as listed>"], "actions": [{"key": "ArrowRight", "hold_ms": 600} | '
    '{"click": [x, y]}], "note": "one sentence: what you see and why these inputs"}. '
    f"At most {MAX_ACTIONS} actions; hold_ms 0-{MAX_HOLD_MS} (0 = a tap). Keys: {', '.join(KEYS)}. "
    "A goal is reached only when the screen shows it (a score that went up, a lost life or a game-over screen, a new "
    "game after a restart), never because you pressed something. Play like a person trying to win."
)


def _parse(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`").split("\n", 1)[-1]
    start, end = raw.find("{"), raw.rfind("}")
    try:
        data = json.loads(raw[start:end + 1]) if start >= 0 < end else {}
    except ValueError:
        data = {}
    return data if isinstance(data, dict) else {}


def actions_from(data: dict[str, Any]) -> list[tuple[str, Any]]:
    """The model's inputs, validated: known keys, holds capped, clicks inside the screen. Anything else is dropped."""
    out: list[tuple[str, Any]] = []
    for act in (data.get("actions") or [])[:20] if isinstance(data.get("actions"), list) else []:
        if len(out) >= MAX_ACTIONS:
            break
        if not isinstance(act, dict):
            continue
        if act.get("key") in KEYS:
            try:
                hold = max(0, min(int(act.get("hold_ms") or 0), MAX_HOLD_MS))
            except (TypeError, ValueError):
                hold = 0
            out.append(("key", (act["key"], hold)))
        elif isinstance(act.get("click"), list) and len(act["click"]) == 2:
            try:
                x, y = int(act["click"][0]), int(act["click"][1])
            except (TypeError, ValueError):
                continue
            if 0 <= x < VIEW_W and 0 <= y < VIEW_H:
                out.append(("click", (x, y)))
    return out


def _said(actions: list[tuple[str, Any]]) -> str:
    bits = []
    for kind, val in actions:
        if kind == "click":
            bits.append(f"click at {val[0]},{val[1]}")
        else:
            bits.append(f"{val[0]} held {val[1] / 1000:.1f}s" if val[1] else val[0])
    return ", ".join(bits) or "waited"


def _kept(n: int) -> list[int]:
    """Which of n frames the players see: the first, the last, evenly between."""
    if n <= FRAMES_KEPT:
        return list(range(n))
    step = (n - 1) / (FRAMES_KEPT - 1)
    return sorted({round(i * step) for i in range(FRAMES_KEPT)})


def _user(goals: tuple[str, ...], reached: list[str], history: list[str], turn: int, steps: int) -> str:
    lines = [f"Turn {turn} of {steps}.", "Goals:"]
    lines += [f"- {g}{' (reached)' if g in reached else ''}" for g in goals]
    if history:
        lines.append("Your last turns:")
        lines += history[-4:]
    return "\n".join(lines)


async def goal_play(slug: str, kind: str = "static", *, see: SeeFn, goals: tuple[str, ...] = GOALS,
                    steps: int = STEPS) -> dict[str, Any]:
    """Play the game in turns toward ``goals``. Never raises: {ran, reached, missed, frames, shas, inputs, notes,
    errors, turns, stopped}."""
    from .mentor_lead import playable_dir

    result: dict[str, Any] = {"ran": False, "goals": list(goals), "reached": [], "missed": list(goals),
                              "frames": [], "shas": [], "inputs": "", "notes": [], "errors": [], "turns": 0,
                              "stopped": ""}
    serve_dir = playable_dir(slug, kind)
    if serve_dir is None:
        result["stopped"] = "no playable build"
        return result
    try:
        from playwright.async_api import async_playwright

        from .harness import gateway as gw
        from .playground_browser import CHROME_ARGS, _serve_static, chromium_executable
    except Exception:
        result["stopped"] = "no browser"
        return result
    base, stop = _serve_static(serve_dir)
    shots: list[bytes] = []
    steps_said: list[str] = []
    reached: list[str] = []
    try:
        async with async_playwright() as p:
            exe = chromium_executable()
            browser = await p.chromium.launch(args=list(CHROME_ARGS), **({"executable_path": exe} if exe else {}))
            try:
                page = await browser.new_page(viewport={"width": VIEW_W, "height": VIEW_H})
                page.on("pageerror", lambda e: result["errors"].append(str(e)[:200])
                        if len(result["errors"]) < 5 else None)
                await page.goto(base + "/", wait_until="load", timeout=20_000)
                await page.wait_for_timeout(1000)
                for turn in range(1, max(1, steps) + 1):
                    shot = await page.screenshot(type="png")
                    shots.append(shot)
                    part = gw.image_part(media_type="image/png", data=shot)
                    try:
                        data = _parse(await see(SYSTEM, _user(goals, reached, steps_said, turn, steps), [part]))
                    except Exception as exc:          # budget stop or provider error: keep what was played
                        result["stopped"] = str(exc)[:160] or "model call failed"
                        break
                    result["turns"] = turn
                    for g in data.get("reached") or []:
                        if g in goals and g not in reached:
                            reached.append(g)
                    if data.get("note"):
                        result["notes"].append(str(data["note"])[:200])
                    if len(reached) == len(goals):
                        steps_said.append(f"turn {turn}: all goals reached")
                        break
                    acts = actions_from(data)
                    steps_said.append(f"turn {turn}: {_said(acts)}")
                    for kind_, val in acts:
                        if kind_ == "click":
                            await page.mouse.click(*val)
                        elif val[1]:
                            await page.keyboard.down(val[0])
                            await page.wait_for_timeout(val[1])
                            await page.keyboard.up(val[0])
                        else:
                            await page.keyboard.press(val[0])
                        await page.wait_for_timeout(120)
                    await page.wait_for_timeout(500)
                shots.append(await page.screenshot(type="png"))
            finally:
                await browser.close()
    except Exception as exc:
        log.info("goal play failed for %s", slug, exc_info=True)
        result["stopped"] = result["stopped"] or f"browser: {str(exc)[:120]}"
    finally:
        stop()
    keep = _kept(len(shots))
    parts = [gw.image_part(media_type="image/png", data=shots[i]) for i in keep if len(shots[i]) > 64]
    marks = {i: n + 1 for n, i in enumerate(keep)}
    said = []
    for i, line in enumerate(steps_said):
        said.append((f"[frame {marks[i]}] " if i in marks else "") + line)
    if len(shots) - 1 in marks:
        said.append(f"[frame {marks[len(shots) - 1]}] the end")
    result.update(ran=bool(result["turns"]), reached=reached, missed=[g for g in goals if g not in reached],
                  frames=parts, shas=[str(x.get("sha256") or "") for x in parts], inputs="; ".join(said))
    return result
