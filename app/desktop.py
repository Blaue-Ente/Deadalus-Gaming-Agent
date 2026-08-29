"""Desktop mode — start Studio and open a Windows window (or the browser).

This is the process behind ``daedalus desktop`` / ``Daedalus.exe``. It binds
loopback only, reuses an already-running instance, and never requires a host app.

Preferred surface: pywebview + Edge WebView2. Fallback: tkinter panel.
Last resort: the default browser. ``--no-browser`` / ``ui=none`` is headless.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any

from app.windows_gui import UI_BROWSER, UI_NONE, choose_ui, present_ui

log = logging.getLogger("app.desktop")


def health_url(host: str, port: int) -> str:
    return f"http://{host}:{port}/health"


def studio_url(host: str, port: int) -> str:
    return f"http://{host}:{port}/"


def instance_healthy(host: str, port: int, timeout: float = 0.8) -> bool:
    try:
        import httpx
        response = httpx.get(health_url(host, port), timeout=timeout)
        return response.status_code == 200
    except Exception:
        return False


def wait_until_up(host: str, port: int, *, seconds: float = 20.0) -> bool:
    import time

    deadline = time.time() + seconds
    while time.time() < deadline:
        if instance_healthy(host, port, timeout=0.6):
            return True
        time.sleep(0.25)
    return False


def prepare_desktop_env() -> dict[str, Any]:
    """Windows-app paths + .env, safe to call on Linux (uses HOME fallback)."""
    from app.envload import load_env_file
    from daedalus.windows_layout import apply_layout

    loc = apply_layout()
    load_env_file()
    return loc


def run_desktop(
    *,
    host: str | None = None,
    port: int | None = None,
    open_browser: bool = True,
    ui: str = "auto",
    serve: bool = True,
) -> int:
    loc = prepare_desktop_env()
    host = host or str(os.environ.get("HOST") or loc["host"])
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    try:
        port = int(port or os.environ.get("PORT") or loc["port"])
    except (TypeError, ValueError):
        port = int(loc["port"])
    url = studio_url(host, port)
    mode = choose_ui(ui, open_browser=open_browser)

    def _surface(*, on_close=None) -> str:
        if mode == UI_NONE:
            return UI_NONE
        return present_ui(
            url,
            mode=mode,
            on_close=on_close,
            data_dir=Path(loc["data"]),
            env_file=Path(loc["env_file"]),
        )

    if instance_healthy(host, port):
        log.info("Дедал вече работи на %s", url)
        shown = _surface()
        if shown != UI_NONE:
            print(f"Дедал · Studio {url}  (вече работи)")
        return 0

    if not serve:
        return 2

    os.environ["DAEDALUS_STANDALONE"] = "1"
    os.environ["HOST"] = host
    os.environ["PORT"] = str(port)
    os.environ["DAEDALUS_BIND_HOST"] = host

    import uvicorn

    config = uvicorn.Config(
        "app.main:app",
        host=host,
        port=port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="daedalus-studio", daemon=True)
    thread.start()
    if not wait_until_up(host, port):
        print(f"Дедал не стартира на {url}", file=sys.stderr)
        server.should_exit = True
        return 1

    def _stop() -> None:
        server.should_exit = True

    print(f"Дедал · Studio {url}")
    shown = _surface(on_close=_stop)
    if shown in {UI_BROWSER, UI_NONE}:
        print("  (Ctrl+C спира)")
        try:
            while thread.is_alive():
                thread.join(0.4)
        except KeyboardInterrupt:
            _stop()
            thread.join(4)
        return 0
    _stop()
    thread.join(4)
    return 0
