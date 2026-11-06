"""Best-effort UTF-8 (or lossy) text output for Windows pythonw / narrow consoles.

``pythonw`` on Western European Windows often attaches stdout/stderr to a host
that uses cp1252. Printing Bulgarian log lines then raises UnicodeEncodeError.
This module re-wraps the underlying binary buffer as UTF-8 when possible and
provides a tiny ``safe_print`` fallback.
"""
from __future__ import annotations

import io
import os
import sys
from typing import Any, TextIO


def configure_stdio_utf8_best_effort() -> None:
    """Rebind ``sys.stdout`` / ``sys.stderr`` to UTF-8 TextIOWrapper when safe.

    No-op on non-Windows. If re-wrap fails, leaves streams unchanged; callers
    should use :func:`safe_print` for user-facing lines that may contain
    non-ASCII text.

    ``pythonw`` may leave both streams as ``None``; those are reopened as UTF-8
    text writes to ``os.devnull`` so logging and uvicorn can start.
    """
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="replace")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="replace")
    if os.name != "nt":
        return
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None or not hasattr(stream, "buffer"):
            continue
        buf = stream.buffer
        enc = getattr(stream, "encoding", None) or ""
        if enc.lower() in ("utf-8", "utf8"):
            continue
        try:
            line_buf = bool(getattr(stream, "line_buffering", False))
            replacement = io.TextIOWrapper(
                buf,
                encoding="utf-8",
                errors="replace",
                line_buffering=line_buf,
                write_through=bool(getattr(stream, "write_through", False)),
            )
        except Exception:
            continue
        setattr(sys, name, replacement)


def safe_print(*args: Any, sep: str = " ", end: str = "\n", file: TextIO | None = None, flush: bool = False) -> None:
    """Print ``*args`` to ``file``, never raising ``UnicodeEncodeError``.

    Uses normal ``print`` first; on encoding failure writes via the binary
    buffer with the stream's encoding and ``errors="replace"``, or UTF-8 as a
    last resort.
    """
    out = file if file is not None else sys.stdout
    if out is None:
        return
    try:
        print(*args, sep=sep, end=end, file=out, flush=flush)
        return
    except UnicodeEncodeError:
        pass
    text = sep.join(str(a) for a in args) + end
    buf = getattr(out, "buffer", None)
    if buf is not None:
        enc = getattr(out, "encoding", None) or "utf-8"
        try:
            buf.write(text.encode(enc, errors="replace"))
        except Exception:
            buf.write(text.encode("utf-8", errors="replace"))
        if flush:
            buf.flush()
        return
    # e.g. StringIO — drop non-ASCII rather than crash tests
    try:
        out.write(text.encode("ascii", errors="replace").decode("ascii"))
    except Exception:
        pass
    if flush:
        getattr(out, "flush", lambda: None)()
