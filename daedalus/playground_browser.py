"""Дедал Playground — Render-and-See verification (headless Chromium).

Loads a built Playground app in a real browser engine, executes its
JavaScript, and observes what the smoke test cannot: runtime console
errors, uncaught exceptions, failed network requests, blank/white screen,
plus a screenshot. This is the signal that turns "the server started" into
"the app actually works".

Why a real browser (not NEED_WEB / curl_cffi): web search/fetch read static
HTML text only — they never execute JS, so a React app that compiles cleanly
but crashes at runtime (white screen) looks "fine" to them. Chromium runs the
app exactly like the user's browser does.

Chromium comes from the Nix store (carries its own shared libs); the
Playwright-downloaded build fails on NixOS (missing libnspr4.so etc.), so we
launch via ``executable_path`` pointing at the Nix ``chromium`` binary.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from . import playground as pg
from .playground_runtime import VISUAL_KINDS

log = logging.getLogger("argos.daedalus.playground_browser")

# Console warnings that are noise, not real failures — never fail a build on these.
_BENIGN_CONSOLE = (
    "Download the React DevTools",
    "react-refresh",
    "[vite] connected",
    "[vite] connecting",
    "[HMR]",
    "Lighthouse",
    "favicon.ico",
    "DevTools failed to load source map",
    "was preloaded using link preload but not used",
    "Tailwind CSS",  # cdn.tailwindcss.com prod-warning
    "cdn.tailwindcss.com should not be used in production",
)

# Resource types whose failed requests actually matter (broken app assets / APIs).
_CRITICAL_RESOURCE_TYPES = ("script", "stylesheet", "fetch", "xhr", "document")


def _find_chrome() -> str | None:
    """Resolve a runnable Chromium binary (Nix store first — it ships its libs)."""
    for name in ("chromium", "chromium-browser", "google-chrome", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    return None


def _is_benign(text: str) -> bool:
    return any(b.lower() in text.lower() for b in _BENIGN_CONSOLE)


async def browser_verify(
    slug: str,
    preview_url: str,
    *,
    kind: str = "static",
    settle_ms: int = 2500,
    nav_timeout_ms: int = 25000,
) -> dict[str, Any]:
    """Load ``preview_url`` in headless Chromium and report what really happened.

    Returns dict:
      ok               — bool: rendered AND no fatal runtime errors
      reachable        — bool: page loaded at all
      blank_screen     — bool: rendered but DOM is effectively empty (white screen)
      console_errors   — list[str] (benign filtered out)
      page_errors      — list[str] (uncaught exceptions / unhandled rejections)
      failed_requests  — list[str] (critical asset/API 4xx/5xx + net failures)
      dom_text_len     — int: length of visible <body> text
      has_canvas       — bool: canvas/svg present (games render here, not as text)
      screenshot_path  — str | None: project-relative path to saved PNG
      summary          — str: one-line human/LLM-friendly verdict
      error            — str: present only on infra failure (chromium missing etc.)
    """
    chrome = _find_chrome()
    if not chrome:
        return {
            "ok": False, "reachable": False, "blank_screen": False,
            "console_errors": [], "page_errors": [], "failed_requests": [],
            "dom_text_len": 0, "has_canvas": False, "screenshot_path": None,
            "summary": "Chromium binary не е намерен — Render-and-See пропуснат",
            "error": "chromium_not_found", "skipped": True,
        }

    try:
        from playwright.async_api import async_playwright
    except Exception as exc:  # pragma: no cover
        return {
            "ok": False, "reachable": False, "blank_screen": False,
            "console_errors": [], "page_errors": [], "failed_requests": [],
            "dom_text_len": 0, "has_canvas": False, "screenshot_path": None,
            "summary": f"Playwright недостъпен — {exc}",
            "error": "playwright_unavailable", "skipped": True,
        }

    console_errors: list[str] = []
    page_errors: list[str] = []
    failed_requests: list[str] = []

    def _on_console(msg) -> None:
        try:
            if msg.type != "error":
                return
            t = msg.text or ""
            # Chromium puts the failing URL in msg.location, not msg.text — a
            # favicon.ico 404 shows up as a bare "Failed to load resource: …404".
            # Check the location URL too so benign assets don't fail the build.
            loc_url = ""
            try:
                loc = msg.location or {}
                loc_url = loc.get("url", "") if isinstance(loc, dict) else ""
            except Exception:
                loc_url = ""
            if _is_benign(t) or (loc_url and _is_benign(loc_url)):
                return
            # "Failed to load resource" console lines mirror the network layer.
            # _on_response / _on_requestfailed already record the ones that matter
            # (filtered to _CRITICAL_RESOURCE_TYPES), so counting them here too would
            # double-fault on benign assets (favicon/image/font). Drop them — the
            # network handlers are the single source of truth for failed requests.
            if "failed to load resource" in t.lower():
                return
            console_errors.append(f"{msg.type}: {t[:300]}")
        except Exception:
            pass

    def _on_pageerror(err) -> None:
        try:
            page_errors.append(str(err)[:400])
        except Exception:
            pass

    def _on_response(resp) -> None:
        try:
            st = resp.status
            if st >= 400:
                rt = (resp.request.resource_type or "").lower()
                if rt in _CRITICAL_RESOURCE_TYPES:
                    failed_requests.append(f"{st} {rt}: {resp.url[:200]}")
        except Exception:
            pass

    def _on_requestfailed(req) -> None:
        try:
            rt = (req.resource_type or "").lower()
            if rt in _CRITICAL_RESOURCE_TYPES:
                failed_requests.append(f"NET_FAIL {rt}: {req.url[:200]}")
        except Exception:
            pass

    screenshot_path: str | None = None
    reachable = False
    dom_text_len = 0
    has_canvas = False

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True, executable_path=chrome,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
            page = await browser.new_page(viewport={"width": 1280, "height": 800})
            page.on("console", _on_console)
            page.on("pageerror", _on_pageerror)
            page.on("response", _on_response)
            page.on("requestfailed", _on_requestfailed)
            try:
                await page.goto(preview_url, wait_until="domcontentloaded",
                                timeout=nav_timeout_ms)
                reachable = True
            except Exception as nav_exc:
                # Last-ditch: maybe it loaded enough to inspect anyway.
                page_errors.append(f"navigation: {str(nav_exc)[:200]}")
            # Let SPA frameworks mount + async data settle.
            try:
                await page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass
            await page.wait_for_timeout(settle_ms)

            dom_text_full = ""
            try:
                dom_text_full = await page.inner_text("body")
                dom_text_len = len((dom_text_full or "").strip())
            except Exception:
                dom_text_len = 0
            try:
                has_canvas = await page.evaluate(
                    "() => !!document.querySelector('canvas, svg, video, img')"
                )
            except Exception:
                has_canvas = False

            # Vite error overlay: shown instead of the real app when there is a
            # runtime import/module error (e.g. "No matching export in …").
            # It has DOM text so blank-screen detection misses it — check explicitly.
            vite_error_text = ""
            try:
                vite_error_text = await page.evaluate("""() => {
                    const ov = document.querySelector('vite-error-overlay');
                    if (ov && ov.shadowRoot) {
                        return ov.shadowRoot.textContent || '';
                    }
                    return ov ? (ov.textContent || '') : '';
                }""")
            except Exception:
                vite_error_text = ""
            # Also detect error text patterns in the main DOM that indicate a Vite
            # HMR / runtime error overlay even without the custom element.
            _DOM_ERROR_PATTERNS = (
                "no matching export",
                "plugin vite:import-analysis",
                "failed to resolve import",
                "cannot find module",
                "[plugin:vite",
                "esbuild error",
            )
            dom_has_vite_error = bool(vite_error_text) or any(
                p in (dom_text_full or "").lower() for p in _DOM_ERROR_PATTERNS
            )
            if dom_has_vite_error and vite_error_text:
                # Vite error text is a cleaner summary — prepend it to page_errors
                # so it surfaces in the LLM fix prompt.
                page_errors.append(f"ViteError: {vite_error_text.strip()[:400]}")
            elif dom_has_vite_error:
                page_errors.append("ViteError: Vite runtime error overlay detected in DOM")

            try:
                shot_dir = pg.project_dir(slug) / "assets"
                shot_dir.mkdir(parents=True, exist_ok=True)
                shot_file = shot_dir / "_verify_screenshot.png"
                await page.screenshot(path=str(shot_file), full_page=False)
                screenshot_path = "assets/_verify_screenshot.png"
            except Exception as shot_exc:
                log.warning("browser_verify screenshot failed for %s: %s", slug, shot_exc)

            await browser.close()
    except Exception as exc:
        log.warning("browser_verify failed for %s: %s", slug, exc)
        return {
            "ok": False, "reachable": reachable, "blank_screen": False,
            "console_errors": console_errors, "page_errors": page_errors,
            "failed_requests": failed_requests, "dom_text_len": dom_text_len,
            "has_canvas": has_canvas, "screenshot_path": screenshot_path,
            "summary": f"Browser verify грешка: {str(exc)[:160]}",
            "error": str(exc)[:200],
        }

    # ── Verdict ──────────────────────────────────────────────────────────
    blank_screen = reachable and dom_text_len < 8 and not has_canvas
    has_fatal = bool(page_errors) or bool(failed_requests) or bool(console_errors)
    ok = reachable and (not blank_screen) and (not has_fatal)

    if not reachable:
        summary = "❌ Приложението не се зареди в браузъра"
    elif blank_screen:
        summary = "❌ Бял екран — нищо не се рендерира (DOM празен)"
    elif has_fatal:
        bits = []
        if page_errors:
            bits.append(f"{len(page_errors)} uncaught грешки")
        if console_errors:
            bits.append(f"{len(console_errors)} console грешки")
        if failed_requests:
            bits.append(f"{len(failed_requests)} счупени заявки")
        summary = "❌ Рендерира се, но има runtime проблеми: " + ", ".join(bits)
    else:
        summary = f"✅ Рендерира се чисто ({dom_text_len} символа видим текст)"

    return {
        "ok": ok, "reachable": reachable, "blank_screen": blank_screen,
        "console_errors": console_errors, "page_errors": page_errors,
        "failed_requests": failed_requests, "dom_text_len": dom_text_len,
        "has_canvas": has_canvas, "screenshot_path": screenshot_path,
        "dom_text": (dom_text_full or "").strip()[:2500],
        "summary": summary,
    }


def _serve_static(directory: Path):
    """Serve ``directory`` over a throwaway localhost HTTP server.

    Returns (base_url, stop). Render-and-See hits this directly (127.0.0.1),
    bypassing the admin-auth'd preview proxy entirely — the headless browser
    has no session cookie, so direct serving is both simpler and more robust.
    """
    import functools
    import threading
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(directory))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()

    def stop() -> None:
        try:
            httpd.shutdown()
            httpd.server_close()
        except Exception:
            pass

    return f"http://127.0.0.1:{port}", stop


async def verify_app(slug: str, kind: str) -> dict[str, Any]:
    """High-level Render-and-See: pick the right way to serve ``slug`` for its
    ``kind``, load it in Chromium, and return a browser_verify result.

    • static            → serve the project dir as-is.
    • vite / vite-react → serve the built ``dist/`` (the real published artifact;
      already produced by the smoke-test compile gate) — falls back to project
      dir if dist is missing.
    • streamlit         → start the dev runner, hit its port (longer settle for
      the websocket-driven first paint).
    • expo              → serve built ``dist/`` when present; otherwise start
      Metro web and load the local port directly (bypasses iframe proxy).
    Non-visual kinds return a skipped result.
    """
    if kind not in VISUAL_KINDS:
        return {"ok": True, "skipped": True, "summary": f"Render-and-See пропуснат за {kind}",
                "console_errors": [], "page_errors": [], "failed_requests": [],
                "blank_screen": False, "reachable": True, "dom_text_len": 0,
                "has_canvas": False, "screenshot_path": None}

    root = pg.project_dir(slug)

    if kind == "static":
        base, stop = _serve_static(root)
        try:
            return await browser_verify(slug, base + "/", kind=kind)
        finally:
            stop()

    if kind in ("vite", "vite-react"):
        from . import playground_runner as runner
        gate = await runner.vite_compile_gate(root, slug=slug, kind=kind)
        if not gate.get("ok") and not gate.get("skipped"):
            err = str(gate.get("error") or "vite compile gate failed")[:400]
            logs = "\n".join((gate.get("logs") or [])[-12:])
            return {
                "ok": False, "reachable": False, "blank_screen": False,
                "console_errors": [],
                "page_errors": [f"vite compile gate: {err}"],
                "failed_requests": [], "dom_text_len": 0, "has_canvas": False,
                "screenshot_path": None,
                "summary": f"❌ Vite install/build failed — Chromium not opened. {err}"[:240],
                "error": err,
                "compile_logs": logs,
            }
        dist = root / "dist"
        serve_dir = dist if (dist.exists() and (dist / "index.html").exists()) else root
        base, stop = _serve_static(serve_dir)
        try:
            return await browser_verify(slug, base + "/", kind=kind)
        finally:
            stop()

    if kind == "streamlit":
        from . import playground_runner as runner
        try:
            rec = await runner.start(slug, install_deps=True)
            port = rec.get("port")
            if not port:
                return {"ok": True, "skipped": True, "summary": "Streamlit не стартира — verify пропуснат",
                        "console_errors": [], "page_errors": [], "failed_requests": [],
                        "blank_screen": False, "reachable": False, "dom_text_len": 0,
                        "has_canvas": False, "screenshot_path": None}
            return await browser_verify(slug, f"http://127.0.0.1:{port}/",
                                        kind=kind, settle_ms=4500, nav_timeout_ms=30000)
        finally:
            try:
                await runner.stop(slug)
            except Exception:
                pass

    if kind == "expo":
        dist = root / "dist"
        if dist.exists() and (dist / "index.html").exists():
            base, stop = _serve_static(dist)
            try:
                return await browser_verify(slug, base + "/", kind=kind)
            finally:
                stop()
        from . import playground_runner as runner
        try:
            rec = await runner.start(slug, install_deps=True)
            port = rec.get("port")
            if not port:
                return {"ok": True, "skipped": True, "summary": "Expo Metro не стартира — verify пропуснат",
                        "console_errors": [], "page_errors": [], "failed_requests": [],
                        "blank_screen": False, "reachable": False, "dom_text_len": 0,
                        "has_canvas": False, "screenshot_path": None}
            return await browser_verify(slug, f"http://127.0.0.1:{port}/",
                                        kind=kind, settle_ms=5000, nav_timeout_ms=35000)
        finally:
            try:
                await runner.stop(slug)
            except Exception:
                pass

    return {"ok": True, "skipped": True, "summary": "skip",
            "console_errors": [], "page_errors": [], "failed_requests": [],
            "blank_screen": False, "reachable": True, "dom_text_len": 0,
            "has_canvas": False, "screenshot_path": None}


def format_errors_for_fix(result: dict[str, Any]) -> str:
    """Render a browser_verify result into a compact block for an LLM fix prompt."""
    lines: list[str] = []
    if not result.get("reachable"):
        lines.append("ПРОБЛЕМ: приложението изобщо не се зареди в браузъра.")
    if result.get("blank_screen"):
        lines.append("ПРОБЛЕМ: БЯЛ ЕКРАН — DOM е празен след зареждане. "
                     "Най-често: JS грешка спира mount-ването, грешен import, "
                     "или root елементът не се намира.")
    for e in result.get("page_errors", [])[:8]:
        lines.append(f"UNCAUGHT JS: {e}")
    for e in result.get("console_errors", [])[:8]:
        lines.append(f"CONSOLE: {e}")
    for e in result.get("failed_requests", [])[:8]:
        lines.append(f"FAILED REQUEST: {e}")
    return "\n".join(lines) if lines else "Няма открити runtime грешки."
