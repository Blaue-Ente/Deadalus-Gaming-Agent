"""Optional SmolVM adapter — guest Chromium with a live viewer.

SmolVM is not imported at module load and is not a required dependency.
Tests inject ``_FACTORY``. Production uses ``smolvm.SmolVM.browser``.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

# lab_id → live session (sandbox objects stay here, never in JSON)
_SESSIONS: dict[str, dict[str, Any]] = {}

# Tests set this to a callable(**kwargs) -> sandbox-like object.
_FACTORY: Callable[..., Any] | None = None

_GUEST_PORT = 8090
_GUEST_MOUNT = "/workspace"


def probe() -> dict[str, Any]:
    imported = False
    version = ""
    err = ""
    if _FACTORY is not None:
        imported = True
        version = "test-factory"
    else:
        try:
            import smolvm  # type: ignore
            imported = True
            version = str(getattr(smolvm, "__version__", "") or "")
        except Exception as extra:
            err = str(extra)[:200]
    bin_path = shutil.which("smolvm") or ""
    kvm = Path("/dev/kvm").exists()
    ready = imported and not _disabled()
    note = "SmolVM import OK — a start will boot a guest browser."
    if not imported:
        note = (
            "SmolVM is not installed. Optional: pip install smolvm && smolvm setup. "
            "Play Lab still serves HTML on this machine."
        )
    elif _disabled():
        note = "DAEDALUS_SMOLVM=0 — guest computer disabled."
        ready = False
    return {
        "ok": ready,
        "import": imported,
        "bin": bin_path,
        "version": version,
        "kvm": kvm,
        "error": err,
        "note": note,
    }


def _disabled() -> bool:
    from .computer import env_disabled
    return env_disabled("DAEDALUS_SMOLVM")


def available() -> bool:
    if _disabled():
        return False
    return bool(probe().get("ok"))


def list_sessions() -> list[dict[str, Any]]:
    return [_public(lab_id, row) for lab_id, row in _SESSIONS.items()]


def session_for(lab_id: str) -> dict[str, Any] | None:
    row = _SESSIONS.get(lab_id)
    if not row:
        return None
    return _public(lab_id, row)


def _public(lab_id: str, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "lab_id": lab_id,
        "backend": "smolvm",
        "status": "running",
        "cdp_url": str(row.get("cdp_url") or ""),
        "viewer_url": str(row.get("viewer_url") or ""),
        "display_url": str(row.get("display_url") or ""),
        "guest_url": str(row.get("guest_url") or ""),
        "url": str(row.get("url") or ""),
        "warning": str(row.get("warning") or ""),
    }


def stop_lab(lab_id: str) -> None:
    row = _SESSIONS.pop(lab_id, None)
    if not row:
        return
    _teardown(row)


def stop_all() -> None:
    for lab_id in list(_SESSIONS):
        stop_lab(lab_id)


def _teardown(row: dict[str, Any]) -> None:
    sandbox = row.get("sandbox")
    for name in ("stop", "close", "delete"):
        fn = getattr(sandbox, name, None)
        if callable(fn):
            try:
                fn()
            except Exception as extra:
                log.debug("smolvm %s: %s", name, extra)
    cm = row.get("cm")
    if cm is not None and hasattr(cm, "__exit__"):
        try:
            cm.__exit__(None, None, None)
        except Exception as extra:
            log.debug("smolvm context exit: %s", extra)


def _open_browser(**kwargs: Any) -> tuple[Any, Any | None]:
    factory = _FACTORY
    if factory is None:
        from smolvm import SmolVM  # type: ignore
        factory = SmolVM.browser
    last_err: Exception | None = None
    attempts: list[dict[str, Any]] = [dict(kwargs)]
    if "mounts" in kwargs:
        slim = dict(kwargs)
        slim.pop("mounts", None)
        slim.pop("writable_mounts", None)
        attempts.append(slim)
    attempts.append({"headless": kwargs.get("headless", False)})
    for attempt in attempts:
        try:
            raw = factory(**attempt)
        except TypeError as extra:
            last_err = extra
            continue
        except Exception as extra:
            last_err = extra
            continue
        if hasattr(raw, "cdp_url") or hasattr(raw, "viewer_url") or hasattr(raw, "connect_playwright"):
            return raw, None
        if hasattr(raw, "__enter__"):
            sandbox = raw.__enter__()
            return sandbox, raw
        return raw, None
    raise RuntimeError(f"SmolVM.browser failed: {last_err}")


def start_lab_browser(
    lab_id: str,
    *,
    serve_url: str = "",
    mount: Path | None = None,
    playable: str = "",
    live: bool = True,
) -> dict[str, Any]:
    """Boot a guest Chromium. Reuses a running session for this lab."""
    if not available():
        raise RuntimeError("SmolVM is not available")
    existing = _SESSIONS.get(lab_id)
    if existing:
        pub = _public(lab_id, existing)
        pub["reused"] = True
        return pub

    kwargs: dict[str, Any] = {
        "headless": not live,
        "viewport": {"width": 1280, "height": 720},
    }
    if mount and mount.is_dir():
        kwargs["mounts"] = [str(mount.resolve())]
        kwargs["writable_mounts"] = False

    sandbox, cm = _open_browser(**kwargs)
    warnings: list[str] = []
    guest_url = ""
    if mount and mount.is_dir() and hasattr(sandbox, "run"):
        guest_url = _serve_in_guest(sandbox, playable, warnings)
    elif mount and mount.is_dir():
        warnings.append("SmolVM sandbox has no run() — guest HTTP not started.")

    if guest_url:
        _goto(sandbox, guest_url, warnings)
    elif serve_url and not _is_loopback(serve_url):
        _goto(sandbox, serve_url, warnings)
    else:
        warnings.append(
            "Guest browser is up. The game bytes are on this machine "
            f"({serve_url or 'local HTTP'}). Guest 127.0.0.1 is not the host — "
            "open the viewer; host Playwright still uses the local URL."
        )

    row = {
        "sandbox": sandbox,
        "cm": cm,
        "cdp_url": str(getattr(sandbox, "cdp_url", "") or ""),
        "viewer_url": str(getattr(sandbox, "viewer_url", "") or ""),
        "display_url": str(getattr(sandbox, "display_url", "") or ""),
        "guest_url": guest_url,
        "url": serve_url,
        "warning": " ".join(warnings),
    }
    _SESSIONS[lab_id] = row
    pub = _public(lab_id, row)
    pub["ok"] = True
    return pub


def _is_loopback(url: str) -> bool:
    u = (url or "").lower()
    return "127.0.0.1" in u or "localhost" in u or u.startswith("http://[::1]")


def _serve_in_guest(sandbox: Any, playable: str, warnings: list[str]) -> str:
    cmd = (
        f"python3 -m http.server {_GUEST_PORT} --directory {_GUEST_MOUNT} "
        f"--bind 127.0.0.1 >/tmp/daedalus-lab-http.log 2>&1 & echo $!"
    )
    try:
        result = sandbox.run(cmd)
    except TypeError:
        try:
            result = sandbox.run(cmd, timeout=8)
        except Exception as extra:
            warnings.append(f"guest http.server: {extra}"[:240])
            return ""
    except Exception as extra:
        warnings.append(f"guest http.server: {extra}"[:240])
        return ""
    code = getattr(result, "exit_code", None)
    if isinstance(result, dict):
        code = result.get("exit_code", code)
    if code not in (None, 0):
        err = getattr(result, "stderr", "") or (result.get("stderr") if isinstance(result, dict) else "")
        warnings.append(f"guest http.server exit {code}: {err}"[:240])
    path = ""
    if playable and str(playable).endswith((".html", ".htm")):
        path = "/" + str(playable).lstrip("/")
    return f"http://127.0.0.1:{_GUEST_PORT}{path or '/'}"


def _goto(sandbox: Any, url: str, warnings: list[str]) -> None:
    connect = getattr(sandbox, "connect_playwright", None)
    if not callable(connect):
        warnings.append("No connect_playwright — open viewer_url and navigate yourself.")
        return
    try:
        browser = connect()
        page = browser.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
    except TypeError:
        try:
            browser = connect()
            page = browser.new_page()
            page.goto(url)
        except Exception as extra:
            warnings.append(f"guest goto failed: {extra}"[:240])
    except Exception as extra:
        warnings.append(f"guest goto failed: {extra}"[:240])
