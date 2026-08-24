"""Windows (and portable) desktop shell around the local Studio.

Preferred path: pywebview + Edge WebView2 — a real window, not a browser tab.
Fallback: stdlib tkinter control panel (no extra install).
Last resort: the default browser.
"""

from __future__ import annotations

import os
import webbrowser
from pathlib import Path
from typing import Any, Callable

UI_AUTO = "auto"
UI_WEBVIEW = "webview"
UI_TK = "tk"
UI_BROWSER = "browser"
UI_NONE = "none"
_VALID_UI = {UI_AUTO, UI_WEBVIEW, UI_TK, UI_BROWSER, UI_NONE}

_GOLD = "#c9a227"
_INK = "#14110d"
_PAPER = "#f4efe4"
_MUTED = "#8a7d68"


def probe() -> dict[str, Any]:
    """Report which GUI backends this process can actually import."""
    webview_ok = False
    webview_err = ""
    try:
        import webview  # noqa: F401

        webview_ok = True
    except Exception as exc:
        webview_err = type(exc).__name__

    tk_ok = False
    tk_err = ""
    try:
        import tkinter  # noqa: F401

        tk_ok = True
    except Exception as exc:
        tk_err = type(exc).__name__

    if webview_ok:
        preferred = UI_WEBVIEW
    elif tk_ok:
        preferred = UI_TK
    else:
        preferred = UI_BROWSER
    return {
        "webview": webview_ok,
        "tkinter": tk_ok,
        "preferred": preferred,
        "webview_error": webview_err,
        "tkinter_error": tk_err,
    }


def choose_ui(requested: str | None, *, open_browser: bool = True) -> str:
    """Map CLI flags to a requested surface. Does not probe backends."""
    mode = (requested or UI_AUTO).strip().lower() or UI_AUTO
    if mode not in _VALID_UI:
        mode = UI_AUTO
    if mode == UI_AUTO and not open_browser:
        return UI_NONE
    return mode


def resolve_ui(requested: str, *, available: dict[str, Any] | None = None) -> str:
    mode = choose_ui(requested, open_browser=True)
    if mode in {UI_NONE, UI_BROWSER}:
        return mode
    info = available if available is not None else probe()
    if mode == UI_WEBVIEW:
        return UI_WEBVIEW if info.get("webview") else (
            UI_TK if info.get("tkinter") else UI_BROWSER
        )
    if mode == UI_TK:
        return UI_TK if info.get("tkinter") else UI_BROWSER
    preferred = str(info.get("preferred") or UI_BROWSER)
    return preferred if preferred in {UI_WEBVIEW, UI_TK, UI_BROWSER} else UI_BROWSER


def present_ui(
    url: str,
    *,
    mode: str = UI_AUTO,
    on_close: Callable[[], None] | None = None,
    data_dir: Path | None = None,
    env_file: Path | None = None,
    available: dict[str, Any] | None = None,
) -> str:
    """Open a desktop surface for *url*. Returns the mode that actually ran.

    Blocks for webview/tk until the window closes. Browser/none return immediately.
    """
    chosen = resolve_ui(mode, available=available)
    if chosen == UI_NONE:
        return chosen
    if chosen == UI_WEBVIEW:
        return _run_webview(url, on_close=on_close)
    if chosen == UI_TK:
        return _run_tk(
            url,
            on_close=on_close,
            data_dir=data_dir,
            env_file=env_file,
        )
    webbrowser.open(url)
    return UI_BROWSER


def _run_webview(url: str, *, on_close: Callable[[], None] | None) -> str:
    try:
        import webview
    except Exception:
        webbrowser.open(url)
        return UI_BROWSER

    window = webview.create_window(
        "Daedalus",
        url,
        width=1280,
        height=800,
        min_size=(880, 560),
        background_color=_INK,
    )

    def _closed() -> None:
        if on_close:
            on_close()

    try:
        window.events.closed += _closed
    except Exception:
        pass

    webview.start()
    if on_close:
        on_close()
    return UI_WEBVIEW


def _run_tk(
    url: str,
    *,
    on_close: Callable[[], None] | None,
    data_dir: Path | None,
    env_file: Path | None,
) -> str:
    try:
        import tkinter as tk
        from tkinter import font as tkfont
        from tkinter import messagebox
    except Exception:
        webbrowser.open(url)
        return UI_BROWSER

    root = tk.Tk()
    root.title("Daedalus")
    root.configure(bg=_INK)
    root.minsize(420, 360)
    root.geometry("480x420")

    heading = tkfont.Font(family="Georgia", size=20, weight="bold")
    body = tkfont.Font(family="Segoe UI", size=10)
    small = tkfont.Font(family="Segoe UI", size=9)

    tk.Label(
        root,
        text="Daedalus",
        fg=_GOLD,
        bg=_INK,
        font=heading,
    ).pack(anchor="w", padx=24, pady=(22, 4))
    tk.Label(
        root,
        text="Локално ателие. Studio слуша на loopback.",
        fg=_MUTED,
        bg=_INK,
        font=small,
    ).pack(anchor="w", padx=24)
    tk.Label(
        root,
        text=url,
        fg=_PAPER,
        bg=_INK,
        font=body,
    ).pack(anchor="w", padx=24, pady=(8, 16))

    def _open(path: str | None = None) -> None:
        webbrowser.open(path or url)

    def _reveal(target: Path | None) -> None:
        if target is None:
            return
        target = Path(target)
        folder = target if target.is_dir() else target.parent
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        if os.name == "nt":
            os.startfile(str(folder))  # type: ignore[attr-defined]
        else:
            webbrowser.open(folder.as_uri())

    def _edit_env() -> None:
        if env_file is None:
            return
        env_file.parent.mkdir(parents=True, exist_ok=True)
        if not env_file.exists():
            env_file.write_text(
                "# Daedalus — сложи ключ тук\n"
                "ALLTOKEN_API_KEY=\n"
                "DAEDALUS_LLM_MODEL=alltoken:deepseek-v3.1-terminus\n",
                encoding="utf-8",
            )
        if os.name == "nt":
            os.startfile(str(env_file))  # type: ignore[attr-defined]
        else:
            webbrowser.open(env_file.as_uri())

    def _stop() -> None:
        if on_close:
            on_close()
        root.destroy()

    btn_kw = {
        "bg": _GOLD,
        "fg": _INK,
        "activebackground": "#e4c56a",
        "activeforeground": _INK,
        "relief": "flat",
        "font": body,
        "padx": 14,
        "pady": 8,
        "cursor": "hand2",
    }
    ghost_kw = {
        **btn_kw,
        "bg": "#2a241c",
        "fg": _PAPER,
        "activebackground": "#3a3228",
        "activeforeground": _PAPER,
    }

    tk.Button(root, text="Отвори Studio", command=lambda: _open(), **btn_kw).pack(
        fill="x", padx=24, pady=4
    )
    tk.Button(root, text="Отвори /apps/", command=lambda: _open(url.rstrip("/") + "/apps/"), **ghost_kw).pack(
        fill="x", padx=24, pady=4
    )
    tk.Button(root, text="Папка с данни", command=lambda: _reveal(data_dir), **ghost_kw).pack(
        fill="x", padx=24, pady=4
    )
    tk.Button(root, text="Отвори .env", command=_edit_env, **ghost_kw).pack(
        fill="x", padx=24, pady=4
    )
    tk.Button(root, text="Спри Daedalus", command=_stop, **ghost_kw).pack(
        fill="x", padx=24, pady=(16, 8)
    )

    def _on_wm_close() -> None:
        if on_close and not messagebox.askokcancel("Daedalus", "Да спра локалния сървър?"):
            return
        _stop()

    root.protocol("WM_DELETE_WINDOW", _on_wm_close)
    root.mainloop()
    if on_close:
        on_close()
    return UI_TK
