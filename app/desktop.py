"""Desktop mode — start Studio and open the browser (Windows Start Menu).

This is the process behind ``daedalus desktop`` / Daedalus.vbs. It binds
loopback only, reuses an already-running instance, and never requires ADN.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
import webbrowser
from typing import Any

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

    if instance_healthy(host, port):
        log.info("Дедал вече работи на %s", url)
        if open_browser:
            webbrowser.open(url)
        return 0

    if not serve:
        return 2

    os.environ["DAEDALUS_STANDALONE"] = "1"
    os.environ["HOST"] = host
    os.environ["PORT"] = str(port)

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
    if open_browser:
        webbrowser.open(url)
    print(f"Дедал · Studio {url}  (Ctrl+C спира)")
    try:
        while thread.is_alive():
            thread.join(0.4)
    except KeyboardInterrupt:
        server.should_exit = True
        thread.join(4)
    return 0
