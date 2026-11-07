"""Gameplay interaction verify — scenario bot, pixel-diff, FPS/memory (F2)."""
from __future__ import annotations

import logging
from typing import Any

from . import playground as pg
from .playtest_scenario import (
    is_racer_tree,
    load_scenario,
    run_scenario_on_page,
    scenario_checks,
    write_default_if_missing,
)
from .playground_browser import VISUAL_KINDS, _find_chrome, _serve_static
from .playground_runtime import verify_unity_scaffold

log = logging.getLogger(__name__)

_START_TEXT_RE = (
    "start", "play", "игра", "старт", "begin", "new game", "continue", "продължи",
)

_GAMEPLAY_JS = """
() => {
  const checks = [];
  const bodyText = (document.body && document.body.innerText) || '';
  const canvas = document.querySelector('canvas');
  const hasCanvas = !!canvas;
  checks.push({ id: 'canvas_or_visual', ok: hasCanvas || !!document.querySelector('svg, video'),
    detail: hasCanvas ? 'canvas present' : (document.querySelector('svg') ? 'svg' : 'no canvas') });
  const gameHandle = !!(window.Phaser || window.game || window.__GAME__ || window.Game);
  checks.push({ id: 'game_handle', ok: gameHandle || hasCanvas,
    detail: gameHandle ? 'game global detected' : 'no Phaser/game global' });
  const buttons = [...document.querySelectorAll('button, [role="button"], a, .btn, #start, .start')];
  let startBtn = null;
  for (const el of buttons) {
    const t = ((el.innerText || el.textContent || '') + ' ' + (el.getAttribute('aria-label') || '')).toLowerCase();
    if (%s.some(k => t.includes(k))) { startBtn = el; break; }
  }
  checks.push({ id: 'start_control', ok: !!startBtn || hasCanvas,
    detail: startBtn ? ('found: ' + (startBtn.innerText || startBtn.textContent || '').trim().slice(0, 40)) : 'no start button' });
  let clickOk = false;
  let afterCanvas = hasCanvas;
  if (startBtn) {
    try { startBtn.click(); clickOk = true; } catch (e) { clickOk = false; }
  }
  return { checks, clickOk, hasCanvas, bodyLen: bodyText.trim().length,
    afterClick: { clickOk, canvas: !!document.querySelector('canvas') } };
}
""".replace(
    "%s",
    str(list(_START_TEXT_RE)).replace("'", '"'),
)


def _gameplay_js_after_wait() -> str:
    return """
() => {
  const canvas = document.querySelector('canvas');
  const errors = (window.__gameplayErrors || []);
  return {
    hasCanvas: !!canvas,
    canvasW: canvas ? canvas.width : 0,
    canvasH: canvas ? canvas.height : 0,
    gameHandle: !!(window.Phaser || window.game || window.__GAME__),
    bodyLen: ((document.body && document.body.innerText) || '').trim().length,
    errors,
  };
}
"""


def _keyboard_probe_js() -> str:
    return """
() => {
  window.__gsKeyProbe = window.__gsKeyProbe || { count: 0, bound: false };
  if (!window.__gsKeyProbe.bound) {
    const bump = () => { window.__gsKeyProbe.count += 1; };
    window.addEventListener('keydown', bump, true);
    document.addEventListener('keydown', bump, true);
    window.__gsKeyProbe.bound = true;
  }
  const canvas = document.querySelector('canvas');
  return {
    before: window.__gsKeyProbe.count,
    hasCanvas: !!canvas,
    activeTag: (document.activeElement && document.activeElement.tagName) || '',
  };
}
"""


def _keyboard_probe_after_js() -> str:
    return """
() => ({
  count: (window.__gsKeyProbe && window.__gsKeyProbe.count) || 0,
  hasCanvas: !!document.querySelector('canvas'),
  gameHandle: !!(window.Phaser || window.game || window.__GAME__),
  bodyLen: ((document.body && document.body.innerText) || '').trim().length,
})
"""


async def _simulate_keyboard(page) -> dict[str, Any]:
    """Press common game keys and report whether the page reacted."""
    probe_before = await page.evaluate(_keyboard_probe_js())
    canvas = await page.query_selector("canvas")
    if canvas:
        try:
            await canvas.click(timeout=2000)
        except Exception:
            pass
    keys = ("ArrowRight", "ArrowLeft", "ArrowUp", "ArrowDown", " ", "w", "a", "s", "d")
    for key in keys:
        try:
            await page.keyboard.press(key)
            await page.wait_for_timeout(60)
        except Exception:
            pass
    await page.wait_for_timeout(400)
    probe_after = await page.evaluate(_keyboard_probe_after_js())
    events = max(0, int(probe_after.get("count", 0)) - int(probe_before.get("before", 0)))
    return {
        "events": events,
        "has_canvas": bool(probe_after.get("hasCanvas")),
        "game_handle": bool(probe_after.get("gameHandle")),
        "body_len": int(probe_after.get("bodyLen") or 0),
    }


def _aggregate_checks(
    before: dict,
    after: dict,
    milestone_id: str,
    *,
    keyboard: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for c in before.get("checks") or []:
        checks.append({**c, "phase": "before"})
    if before.get("clickOk"):
        checks.append({
            "id": "start_click",
            "ok": True,
            "detail": "Start/Play clicked",
            "phase": "action",
        })
    else:
        checks.append({
            "id": "start_click",
            "ok": bool(before.get("hasCanvas")),
            "detail": "no click (canvas-only game?)" if before.get("hasCanvas") else "no start control",
            "phase": "action",
        })
    after_ok = bool(after.get("hasCanvas") or after.get("gameHandle") or after.get("bodyLen", 0) > 20)
    checks.append({
        "id": "post_start_state",
        "ok": after_ok,
        "detail": f"canvas={after.get('hasCanvas')} game={after.get('gameHandle')} body={after.get('bodyLen')}",
        "phase": "after",
    })
    if milestone_id in ("M1", "M2", "M3"):
        checks.append({
            "id": "milestone_loop",
            "ok": after.get("hasCanvas") or after.get("gameHandle"),
            "detail": f"expected playable state for {milestone_id}",
            "phase": "milestone",
        })
    kb = keyboard or {}
    kb_events = int(kb.get("events") or 0)
    kb_ok = kb_events > 0 or (kb.get("has_canvas") and kb.get("game_handle"))
    checks.append({
        "id": "keyboard_input",
        "ok": kb_ok,
        "detail": f"key_events={kb_events} canvas={kb.get('has_canvas')} game={kb.get('game_handle')}",
        "phase": "keyboard",
    })
    return checks


def gameplay_passed(checks: list[dict[str, Any]], milestone_id: str = "M0") -> bool:
    if not checks:
        return False
    required = ["canvas_or_visual", "post_start_state"]
    for c in checks:
        cid = c.get("id")
        if c.get("required") and cid and cid not in required:
            required.append(str(cid))
    seen: set[str] = set()
    for rid in required:
        if rid in seen:
            continue
        seen.add(rid)
        row = next((c for c in checks if c.get("id") == rid), None)
        if not row or not row.get("ok"):
            return False
    if milestone_id in ("M1", "M2", "M3"):
        kb = next((c for c in checks if c.get("id") == "keyboard_input"), None)
        if kb and not kb.get("ok"):
            return False
    return True


async def _run_gameplay_on_url(
    slug: str,
    preview_url: str,
    *,
    kind: str,
    milestone_id: str,
) -> dict[str, Any]:
    chrome = _find_chrome()
    if not chrome:
        return {
            "ok": False,
            "skipped": True,
            "error": "chromium_not_found",
            "summary": "Gameplay verify изисква Chromium — не е намерен",
            "checks": [],
            "proof_incomplete": True,
        }
    try:
        from playwright.async_api import async_playwright
    except Exception as exc:
        return {
            "ok": False,
            "skipped": True,
            "error": "playwright_unavailable",
            "summary": str(exc)[:200],
            "checks": [],
            "proof_incomplete": True,
        }

    root = pg.project_dir(slug)
    scenario, scenario_err = load_scenario(root)
    checks: list[dict[str, Any]] = []
    screenshot_path = None
    bot: dict[str, Any] = {}
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                executable_path=chrome,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
            page = await browser.new_page(viewport={"width": 1280, "height": 800})
            await page.goto(preview_url, wait_until="domcontentloaded", timeout=25000)
            try:
                await page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass
            await page.wait_for_timeout(2000)
            before = await page.evaluate(_GAMEPLAY_JS)
            await page.wait_for_timeout(1200)
            after = await page.evaluate(_gameplay_js_after_wait())
            keyboard = await _simulate_keyboard(page)
            checks = _aggregate_checks(before, after, milestone_id, keyboard=keyboard)
            if scenario is None and scenario_err not in ("", "missing"):
                checks.append({
                    "id": "scenario_file",
                    "ok": False,
                    "detail": scenario_err,
                    "required": True,
                    "phase": "scenario",
                })
            elif scenario is not None:
                bot = await run_scenario_on_page(page, scenario)
                checks.extend(scenario_checks(bot, scenario))
            try:
                shot_dir = root / "assets"
                shot_dir.mkdir(parents=True, exist_ok=True)
                shot_file = shot_dir / "_gameplay_screenshot.png"
                await page.screenshot(path=str(shot_file), full_page=False)
                screenshot_path = "assets/_gameplay_screenshot.png"
            except Exception as shot_exc:
                log.warning("gameplay screenshot: %s", shot_exc)
            await browser.close()
    except Exception as exc:
        log.warning("gameplay_verify failed for %s: %s", slug, exc)
        return {
            "ok": False,
            "skipped": False,
            "summary": str(exc)[:300],
            "checks": checks,
            "screenshot_path": screenshot_path,
            "proof_incomplete": False,
        }

    ok = gameplay_passed(checks, milestone_id)
    return {
        "ok": ok,
        "skipped": False,
        "summary": "gameplay checks passed" if ok else "gameplay checks failed",
        "checks": checks,
        "screenshot_path": screenshot_path,
        "kind": kind,
        "milestone_id": milestone_id,
        "proof_incomplete": False,
        "bot": {
            "saw_lose": bot.get("saw_lose"),
            "saw_win": bot.get("saw_win"),
            "saw_canvas_changed": bot.get("saw_canvas_changed"),
            "fps": bot.get("fps"),
            "frames": bot.get("frames"),
            "memory_mb": bot.get("memory_mb"),
        } if bot else {},
    }


async def gameplay_verify_app(
    slug: str,
    kind: str,
    *,
    milestone_id: str = "M0",
) -> dict[str, Any]:
    """Serve app (or Unity web preview) and run interaction probes."""
    if (kind or "").strip().lower() != "unity":
        kind = pg.normalize_engine_kind(kind)
    if kind == "unity":
        root = pg.project_dir(slug)
        preview = root / "studio" / "preview"
        if preview.is_dir() and (preview / "index.html").is_file():
            base, stop = _serve_static(preview)
            try:
                return await _run_gameplay_on_url(
                    slug, base + "/", kind="static", milestone_id=milestone_id,
                )
            finally:
                stop()
        uv = verify_unity_scaffold(slug)
        return {
            "ok": bool(uv.get("ok")),
            "skipped": True,
            "summary": (
                "Unity: няма studio/preview — scaffold "
                + ("OK" if uv.get("ok") else "FAIL")
                + ". Отвори проекта в Unity Hub за пълен тест."
            ),
            "checks": [{"id": "unity_scaffold", "ok": bool(uv.get("ok")), "detail": uv.get("error") or "ok"}],
            "unity_local_required": True,
            "proof_incomplete": not uv.get("ok"),
        }

    if kind not in VISUAL_KINDS:
        return {"ok": True, "skipped": True, "summary": f"gameplay skip ({kind})", "checks": []}

    root = pg.project_dir(slug)
    write_default_if_missing(root, kind, racer=is_racer_tree(root))

    if kind == "static":
        base, stop = _serve_static(root)
        try:
            return await _run_gameplay_on_url(slug, base + "/", kind=kind, milestone_id=milestone_id)
        finally:
            stop()

    if kind in ("vite", "vite-react"):
        dist = root / "dist"
        serve_dir = dist if (dist.exists() and (dist / "index.html").exists()) else root
        base, stop = _serve_static(serve_dir)
        try:
            return await _run_gameplay_on_url(slug, base + "/", kind=kind, milestone_id=milestone_id)
        finally:
            stop()

    if kind == "expo":
        dist = root / "dist"
        if dist.exists() and (dist / "index.html").exists():
            base, stop = _serve_static(dist)
            try:
                return await _run_gameplay_on_url(slug, base + "/", kind=kind, milestone_id=milestone_id)
            finally:
                stop()
        from . import playground_runner as runner
        try:
            rec = await runner.start(slug, install_deps=True)
            port = rec.get("port")
            if not port:
                return {"ok": False, "skipped": True, "summary": "Expo не стартира", "checks": []}
            return await _run_gameplay_on_url(
                slug, f"http://127.0.0.1:{port}/", kind=kind, milestone_id=milestone_id,
            )
        finally:
            try:
                await runner.stop(slug)
            except Exception:
                pass

    return {"ok": True, "skipped": True, "summary": "gameplay skip", "checks": []}


def format_gameplay_for_fix(result: dict[str, Any]) -> str:
    lines = ["GAMEPLAY VERIFY FAILED:"]
    for c in result.get("checks") or []:
        if not c.get("ok"):
            lines.append(f"- {c.get('id')}: {c.get('detail', '')}")
    lines.append(result.get("summary") or "")
    return "\n".join(lines)
