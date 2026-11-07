"""Interactive Playwright session — goto / click / type / extract / screenshot.

``playground_browser.browser_verify`` is Render-and-See (smoke). This module
is a reusable session the agent can drive like a human: open a URL, click,
type, read the DOM, snap a PNG into the Playground project.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

_pw: Any = None
_browser: Any = None
_page: Any = None


def _chrome() -> str | None:
    from .playground_browser import _find_chrome
    return _find_chrome()


async def _ensure_page() -> tuple[Any | None, str | None]:
    global _pw, _browser, _page
    chrome = _chrome()
    if not chrome:
        return None, "chromium_not_found"
    try:
        from playwright.async_api import async_playwright
    except Exception:
        return None, "playwright_unavailable"
    if _page is not None:
        return _page, None
    try:
        _pw = await async_playwright().start()
        _browser = await _pw.chromium.launch(
            headless=True,
            executable_path=chrome,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        _page = await _browser.new_page(viewport={"width": 1280, "height": 800})
        return _page, None
    except Exception as exc:
        log.warning("browser session launch failed: %s", exc)
        await close()
        return None, str(exc)[:200]


async def close() -> dict[str, Any]:
    global _pw, _browser, _page
    page, browser, pw = _page, _browser, _pw
    _page = _browser = _pw = None
    for obj, meth in ((page, "close"), (browser, "close"), (pw, "stop")):
        if obj is None:
            continue
        try:
            await getattr(obj, meth)()
        except Exception:
            pass
    return {"ok": True, "closed": True}


async def run_action(
    action: str,
    *,
    url: str = "",
    selector: str = "",
    text: str = "",
    slug: str = "",
    timeout_ms: int = 15000,
) -> dict[str, Any]:
    """Dispatch one browser action. ``close`` does not require Chromium."""
    act = (action or "extract").strip().lower()
    if act == "close":
        return await close()

    page, err = await _ensure_page()
    if page is None:
        return {"ok": False, "error": err or "no page", "skipped": True}

    try:
        if act == "goto":
            target = (url or "").strip()
            if not target:
                return {"ok": False, "error": "url required"}
            if not target.startswith(("http://", "https://")):
                target = "https://" + target
            await page.goto(target, wait_until="domcontentloaded", timeout=timeout_ms)
            return {"ok": True, "action": "goto", "url": page.url, "title": await page.title()}

        if act == "click":
            if not selector:
                return {"ok": False, "error": "selector required"}
            await page.click(selector, timeout=timeout_ms)
            return {"ok": True, "action": "click", "selector": selector, "url": page.url}

        if act in ("type", "fill"):
            if not selector:
                return {"ok": False, "error": "selector required"}
            await page.fill(selector, text or "", timeout=timeout_ms)
            return {"ok": True, "action": "type", "selector": selector}

        if act == "screenshot":
            from . import playground as pg
            from .paths import data_root
            dest_dir = (pg.project_dir(slug) / "assets") if slug else (data_root() / "generated")
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / "_session_screenshot.png"
            await page.screenshot(path=str(dest), full_page=False)
            rel = f"assets/_session_screenshot.png" if slug else str(dest)
            return {"ok": True, "action": "screenshot", "path": rel}

        # extract (default)
        body = ""
        try:
            body = await page.inner_text("body")
        except Exception:
            body = ""
        title = ""
        try:
            title = await page.title()
        except Exception:
            pass
        return {
            "ok": True,
            "action": "extract",
            "url": getattr(page, "url", ""),
            "title": title,
            "text": (body or "").strip()[:4000],
        }
    except Exception as exc:
        return {"ok": False, "action": act, "error": str(exc)[:240]}
