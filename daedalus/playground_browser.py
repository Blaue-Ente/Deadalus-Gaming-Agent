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
import os
import shutil
from pathlib import Path
from typing import Any

from . import playground as pg
from .playground_runtime import VISUAL_KINDS
from .visual_integrity import entrypoint_integrity_failures, png_color_stats, screenshot_is_suspect_blank

log = logging.getLogger("argos.daedalus.playground_browser")

# Headless WebGL: --disable-gpu makes Phaser.AUTO paint a black canvas.
# Swiftshader keeps screenshots honest without a host GPU.
CHROME_ARGS = (
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--enable-webgl",
    "--ignore-gpu-blocklist",
)

def paint_summary(
    *,
    reachable: bool,
    dark_frame: bool,
    blank_screen: bool,
    has_fatal: bool,
    has_canvas: bool,
    page_errors: list[str],
    console_errors: list[str],
    failed_requests: list[str],
    dom_text_len: int,
    integrity_warnings: list[str] | None = None,
) -> str:
    """Owner-facing proof line. Canvas HUD lives in the frame, not DOM text."""
    if not reachable:
        return "❌ App did not load in the browser"
    if dark_frame:
        return "❌ Dark frame — canvas/WebGL did not paint a scene (not a boutique title)"
    if blank_screen:
        return "❌ Blank screen — nothing rendered (empty DOM)"
    if has_fatal:
        bits = []
        if page_errors:
            bits.append(f"{len(page_errors)} uncaught")
        if console_errors:
            bits.append(f"{len(console_errors)} console errors")
        if failed_requests:
            bits.append(f"{len(failed_requests)} broken requests")
        return "❌ Renders, but runtime problems: " + ", ".join(bits)
    iw = [w for w in (integrity_warnings or []) if w]
    if iw:
        return "⚠️ Renders cleanly; integrity note: " + "; ".join(iw[:4])
    if has_canvas:
        return "✅ Canvas/WebGL painted (HUD is in the screenshot, not DOM text)"
    return f"✅ Renders cleanly ({dom_text_len} visible DOM characters)"


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
_CRITICAL_RESOURCE_TYPES = ("script", "stylesheet", "fetch", "xhr", "document", "image")


def _playwright_chromium_exe() -> Path | None:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    try:
        with sync_playwright() as p:
            pe = p.chromium.executable_path
            if pe and Path(pe).is_file():
                return Path(pe)
    except Exception:
        return None
    return None


def chromium_executable() -> str | None:
    """Resolve a Chromium or Chrome-based browser for Playwright.

    Order: ``DAEDALUS_CHROMIUM``, common PATH names, Edge under Program Files*,
    Playwright's Chromium download.
    """
    override = (os.environ.get("DAEDALUS_CHROMIUM") or "").strip()
    if override:
        cand = Path(override).expanduser()
        if cand.is_file():
            return str(cand.resolve())

    for name in ("chromium", "chromium-browser", "google-chrome-stable", "google-chrome", "chrome"):
        p = shutil.which(name)
        if p:
            return p

    if os.name == "nt":
        roots = []
        for key in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData"):
            v = os.environ.get(key, "").strip()
            if v:
                roots.append(Path(v))
        for base in roots:
            for tail in (
                "Microsoft/Edge/Application/msedge.exe",
                "Google/Chrome/Application/chrome.exe",
            ):
                exe = (base / tail)
                try:
                    if exe.is_file():
                        return str(exe.resolve())
                except OSError:
                    continue

    pw = _playwright_chromium_exe()
    if pw and pw.is_file():
        return str(pw)
    return None


def _find_chrome() -> str | None:
    return chromium_executable()


def integrity_fatal_and_warnings(codes: list[str]) -> tuple[list[str], list[str]]:
    """Split integrity codes: fatal blockers vs scaffold-only warnings."""
    fatal = [c for c in codes if c != "scaffold_left_in_place"]
    warnings: list[str] = []
    if "scaffold_left_in_place" in codes:
        warnings.append(
            "kernel Phaser seed still present in Play.js (replace gameplay — scaffold only)"
        )
    return fatal, warnings


def screenshot_looks_blank(path: Path | str | None) -> bool:
    """True when the PNG is a near-solid raster (decoder + heuristics)."""
    if not path:
        return False
    stats = png_color_stats(path)
    if stats is None:
        try:
            n = Path(path).stat().st_size
        except OSError:
            return False
        return n > 0 and n < 12_000
    return screenshot_is_suspect_blank(stats)


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
        doctor = (
            "Doctor: no Chromium/Edge found for visual proof. Install Playwright browsers: "
            "`pip install -e \".[playwright]\"` then `python -m playwright install chromium`, "
            "or set DAEDALUS_CHROMIUM to a chrome/chromium/msedge executable."
        )
        return {
            "ok": False, "reachable": False, "blank_screen": False,
            "console_errors": [], "page_errors": [], "failed_requests": [],
            "dom_text_len": 0, "has_canvas": False, "screenshot_path": None,
            "summary": "Chromium not found — visual proof skipped. " + doctor,
            "error": "chromium_not_found", "skipped": True,
            "doctor": doctor,
        }

    try:
        from playwright.async_api import async_playwright
    except Exception as exc:  # pragma: no cover
        return {
            "ok": False, "reachable": False, "blank_screen": False,
            "console_errors": [], "page_errors": [], "failed_requests": [],
            "dom_text_len": 0, "has_canvas": False, "screenshot_path": None,
            "summary": f"Playwright unavailable — {exc}",
            "error": "playwright_unavailable", "skipped": True,
            "doctor": "Install optional deps: pip install -e \".[playwright]\"",
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
    dom_text_full = ""

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True, executable_path=chrome,
                args=list(CHROME_ARGS),
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
                if screenshot_looks_blank(shot_file):
                    await page.wait_for_timeout(2000)
                    await page.screenshot(path=str(shot_file), full_page=False)
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

    shot_abs = None
    if screenshot_path:
        try:
            shot_abs = pg.project_dir(slug) / screenshot_path
        except Exception:
            shot_abs = None
    dark_frame = screenshot_looks_blank(shot_abs)
    root = pg.project_dir(slug)
    try:
        idx = (root / "index.html").read_text(encoding="utf-8", errors="replace")
    except OSError:
        idx = ""
    integrity_codes = entrypoint_integrity_failures(
        index_html=idx, root=root, writer_assist_seeded=False,
    )
    fatal_integrity, integrity_warnings = integrity_fatal_and_warnings(integrity_codes)
    for code in fatal_integrity:
        page_errors.append(f"integrity:{code}")
    # Dark WebGL without HUD text fails; a dark game with visible UI text passes.
    blank_screen = reachable and (
        (dom_text_len < 8 and not has_canvas)
        or (dark_frame and dom_text_len < 8)
    )
    has_fatal = (
        bool(page_errors)
        or bool(failed_requests)
        or bool(console_errors)
        or bool(fatal_integrity)
    )
    ok = reachable and (not blank_screen) and (not has_fatal)

    summary = paint_summary(
        reachable=reachable,
        dark_frame=dark_frame,
        blank_screen=blank_screen,
        has_fatal=has_fatal,
        has_canvas=has_canvas,
        page_errors=page_errors,
        console_errors=console_errors,
        failed_requests=failed_requests,
        dom_text_len=dom_text_len,
        integrity_warnings=integrity_warnings,
    )

    return {
        "ok": ok, "reachable": reachable, "blank_screen": blank_screen,
        "console_errors": console_errors, "page_errors": page_errors,
        "failed_requests": failed_requests, "dom_text_len": dom_text_len,
        "has_canvas": has_canvas, "screenshot_path": screenshot_path,
        "dom_text": (dom_text_full or "").strip()[:2500],
        "summary": summary,
        "integrity_codes": integrity_codes,
        "integrity_warnings": integrity_warnings,
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

    class _Quiet(SimpleHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:   # one line per asset flooded the server log
            pass

    handler = functools.partial(_Quiet, directory=str(directory))
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


def _stage_dist_as_published(dist: Path, slug: str) -> tuple[Path, Any]:
    """Materialize ``dist/`` under ``<tmp>/apps/<slug>/`` like production ADN.

    Absolute ``/assets/...`` then misses (site root has no ``assets/``), matching
    ``/apps/<slug>/`` hosting.
    """
    import shutil
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="daedalus_appproof_"))
    dest = root / "apps" / slug
    dest.parent.mkdir(parents=True, exist_ok=True)
    # A static project is served from its own folder: its .git and node_modules are not part of the site.
    shutil.copytree(dist, dest, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git", "node_modules"))

    def cleanup() -> None:
        shutil.rmtree(root, ignore_errors=True)

    return root, cleanup


async def verify_app(slug: str, kind: str) -> dict[str, Any]:
    """High-level Render-and-See: pick the right way to serve ``slug`` for its
    ``kind``, load it in Chromium, and return a browser_verify result.

    • static            → serve the project dir as-is.
    • vite / vite-react → serve the built ``dist/`` (the real published artifact;
      already produced by the smoke-test compile gate) — falls back to project
      dir if dist is missing.
    • phaser          → same path as vite (Phaser seed, Vite compile gate).
    • streamlit         → start the dev runner, hit its port (longer settle for
      the websocket-driven first paint).
    • expo              → serve built ``dist/`` when present; otherwise start
      Metro web and load the local port directly (bypasses iframe proxy).
    Non-visual kinds return a skipped result.
    """
    kind = pg.normalize_engine_kind(kind)
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

    if kind in ("vite", "vite-react", "phaser"):
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
        serve_from = dist if (dist.exists() and (dist / "index.html").exists()) else root
        stage_root, cleanup_stage = _stage_dist_as_published(serve_from, slug)
        base, stop = _serve_static(stage_root)
        try:
            return await browser_verify(
                slug, base + f"/apps/{slug}/", kind=kind, settle_ms=4000,
            )
        finally:
            stop()
            cleanup_stage()

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
    for w in (result.get("integrity_warnings") or [])[:6]:
        lines.append(f"INTEGRITY (non-fatal): {w}")
    return "\n".join(lines) if lines else "Няма открити runtime грешки."
