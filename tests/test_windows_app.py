"""Windows desktop layout, env loading, installer scripts — no Win32 required."""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def win_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.delenv("DAEDALUS_DATA", raising=False)
    monkeypatch.delenv("DAEDALUS_PLAYGROUND", raising=False)
    monkeypatch.delenv("DAEDALUS_WINDOWS_APP", raising=False)
    monkeypatch.delenv("DAEDALUS_USE_ADN_DB", raising=False)
    monkeypatch.delenv("HOST", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    from daedalus.paths import data_root, playground_projects_dir, product_root, repo_root
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    product_root.cache_clear()
    repo_root.cache_clear()
    yield tmp_path


def test_layout_uses_localappdata(win_tmp):
    from daedalus.windows_layout import DISPLAY_NAME, layout
    loc = layout()
    assert loc["data"] == win_tmp / "Local" / "Daedalus"
    assert loc["install"] == win_tmp / "Local" / "Programs" / "Daedalus"
    assert loc["host"] == "127.0.0.1"
    assert loc["studio_url"].startswith("http://127.0.0.1:")
    assert loc["display_name"] == DISPLAY_NAME


def test_apply_layout_setdefault(win_tmp, monkeypatch):
    from daedalus.windows_layout import apply_layout, is_windows_app
    keep = win_tmp / "keep-data"
    monkeypatch.setenv("DAEDALUS_DATA", str(keep))
    loc = apply_layout(create=True)
    assert loc["data"] == win_tmp / "Local" / "Daedalus"
    assert is_windows_app() is True
    import os
    assert os.environ["DAEDALUS_DATA"] == str(keep)


def test_apply_layout_creates_data(win_tmp):
    from daedalus.windows_layout import apply_layout
    loc = apply_layout(create=True)
    assert Path(loc["data"]).is_dir()
    assert Path(loc["playground"]).is_dir()
    import os
    assert os.environ["DAEDALUS_WINDOWS_APP"] == "1"
    assert os.environ["HOST"] == "127.0.0.1"


def test_envload_reads_user_data_env(win_tmp, monkeypatch):
    from app.envload import env_file_candidates, load_env_file
    data = win_tmp / "Local" / "Daedalus"
    data.mkdir(parents=True)
    (data / ".env").write_text("WINDOWS_SMOKE_KEY=from-appdata\n", encoding="utf-8")
    monkeypatch.setenv("DAEDALUS_DATA", str(data))
    monkeypatch.delenv("WINDOWS_SMOKE_KEY", raising=False)
    paths = env_file_candidates()
    assert any(p.name == ".env" and "Daedalus" in str(p) for p in paths)
    load_env_file()
    import os
    assert os.environ.get("WINDOWS_SMOKE_KEY") == "from-appdata"


def test_desktop_reuses_running_instance(win_tmp, monkeypatch):
    from app.desktop import run_desktop
    opened: list[str] = []
    monkeypatch.setattr("app.desktop.instance_healthy", lambda *a, **k: True)
    monkeypatch.setattr(
        "app.desktop.present_ui",
        lambda url, **kw: opened.append(url) or "browser",
    )
    code = run_desktop(host="127.0.0.1", port=8787, open_browser=True, ui="browser", serve=True)
    assert code == 0
    assert opened and opened[0].startswith("http://127.0.0.1:8787")


def test_desktop_passes_ui_mode(win_tmp, monkeypatch):
    from app.desktop import run_desktop
    seen: list[str] = []
    monkeypatch.setattr("app.desktop.instance_healthy", lambda *a, **k: True)
    monkeypatch.setattr(
        "app.desktop.present_ui",
        lambda url, **kw: seen.append(str(kw.get("mode"))) or "tk",
    )
    assert run_desktop(host="127.0.0.1", port=8787, ui="tk", serve=True) == 0
    assert seen == ["tk"]


def test_desktop_headless_skips_ui(win_tmp, monkeypatch):
    from app.desktop import run_desktop
    called: list[str] = []
    monkeypatch.setattr("app.desktop.instance_healthy", lambda *a, **k: True)
    monkeypatch.setattr("app.desktop.present_ui", lambda url, **kw: called.append(url) or "none")
    code = run_desktop(host="127.0.0.1", port=8787, open_browser=False, serve=True)
    assert code == 0
    assert called == []


def test_gui_probe_and_resolve():
    from app.windows_gui import choose_ui, probe, present_ui, resolve_ui

    info = probe()
    assert set(info) >= {"webview", "tkinter", "preferred"}
    assert info["preferred"] in {"webview", "tk", "browser"}
    assert choose_ui("auto", open_browser=False) == "none"
    assert choose_ui("auto", open_browser=True) == "auto"
    assert choose_ui("weird") == "auto"
    have = {"webview": True, "tkinter": True, "preferred": "webview"}
    assert resolve_ui("auto", available=have) == "webview"
    assert resolve_ui("none", available=have) == "none"
    assert resolve_ui("browser", available=have) == "browser"
    missing = {"webview": False, "tkinter": True, "preferred": "tk"}
    assert resolve_ui("webview", available=missing) == "tk"
    none = {"webview": False, "tkinter": False, "preferred": "browser"}
    assert resolve_ui("tk", available=none) == "browser"
    assert present_ui("http://127.0.0.1:9/", mode="none") == "none"


def test_present_ui_browser(monkeypatch):
    from app.windows_gui import present_ui
    opened: list[str] = []
    monkeypatch.setattr("app.windows_gui.webbrowser.open", lambda url: opened.append(url))
    assert present_ui("http://127.0.0.1:8787/", mode="browser") == "browser"
    assert opened == ["http://127.0.0.1:8787/"]


def test_cli_desktop_help(capsys):
    from app.cli import main
    with pytest.raises(SystemExit) as raised:
        main(["desktop", "--help"])
    assert raised.value.code == 0
    out = capsys.readouterr().out
    assert "--gui" in out
    assert "--browser" in out
    assert "--no-gui" in out


def test_cli_desktop_flag_resolution():
    from argparse import Namespace
    from app.cli import _desktop_open_browser, _desktop_ui

    auto = Namespace(ui="auto", gui=False, no_gui=False, browser=False, no_browser=False)
    assert _desktop_ui(auto) == "auto"
    assert _desktop_open_browser(auto) is True
    headless = Namespace(ui="auto", gui=False, no_gui=True, browser=False, no_browser=False)
    assert _desktop_ui(headless) == "none"
    gui_wins = Namespace(ui="auto", gui=True, no_gui=True, browser=False, no_browser=True)
    assert _desktop_ui(gui_wins) == "auto"
    browser = Namespace(ui="auto", gui=False, no_gui=False, browser=True, no_browser=False)
    assert _desktop_ui(browser) == "browser"
    forced = Namespace(ui="tk", gui=False, no_gui=False, browser=False, no_browser=False)
    assert _desktop_ui(forced) == "tk"


def test_stdio_utf8_safe_print_narrow_encoding():
    """Mimic Windows cp1252 TextIOWrapper: Unicode user content must not crash prints."""
    buf = io.BytesIO()
    wrapper = io.TextIOWrapper(buf, encoding="cp1252", errors="strict", line_buffering=True)
    from daedalus.stdio_utf8 import safe_print

    # Startup banner is ASCII English; safe_print still covers LLM/user text with non-ASCII.
    safe_print("Log: \u0414\u0435\u0434\u0430\u043b http://127.0.0.1:8787/", file=wrapper)
    wrapper.flush()
    out = buf.getvalue().decode("cp1252", errors="replace")
    assert "8787" in out


def test_desktop_banner_no_crash_absent_stdio(win_tmp, monkeypatch):
    """pythonw may leave sys.stdout/sys.stderr as None; startup must not raise."""
    from app.desktop import run_desktop
    from app.windows_gui import UI_BROWSER

    monkeypatch.setattr("app.desktop.instance_healthy", lambda *a, **k: False)
    monkeypatch.setattr("app.desktop.wait_until_up", lambda *a, **k: True)
    monkeypatch.setattr(
        "app.desktop.present_ui",
        lambda url, **kw: UI_BROWSER,
    )

    class _ThreadNoRun:
        def __init__(self, target=None, **kwargs):
            self._target = target

        def start(self) -> None:
            return

        def is_alive(self) -> bool:
            return False

        def join(self, *_a, **_k) -> None:
            return

    monkeypatch.setattr("app.desktop.threading.Thread", _ThreadNoRun)
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    assert run_desktop(host="127.0.0.1", port=8787, open_browser=True, ui="browser", serve=True) == 0


def test_installer_scripts_exist():
    root = Path(__file__).resolve().parents[1] / "packaging" / "windows"
    install = (root / "install.ps1").read_text(encoding="utf-8")
    uninstall = (root / "uninstall.ps1").read_text(encoding="utf-8")
    iss = (root / "Daedalus.iss").read_text(encoding="utf-8")
    bundled = (root / "Get-BundledPython.ps1").read_text(encoding="utf-8")
    launcher = (root / "DaedalusLauncher.cs").read_text(encoding="utf-8")
    build = (root / "build-setup.ps1").read_text(encoding="utf-8")
    assert "LOCALAPPDATA" in install
    assert "Start Menu" in install
    assert "pythonw.exe" in install
    assert "[gui]" in install or "pywebview" in install
    assert "BundlePython" in install
    assert "Daedalus.exe" in install
    assert "PrivilegesRequired=lowest" in iss
    assert "install.ps1" in iss
    assert "-BundlePython" in iss
    assert "-RemoveData" in uninstall
    assert "Programs\\Daedalus" in uninstall or "Programs\\$AppName" in uninstall
    assert "3.12.10" in bundled
    assert "globalcdn.nuget.org" in bundled or "nuget.org" in bundled
    assert "DaedalusPythonSha512" in bundled
    assert "-m app.cli desktop" in launcher
    assert "DAEDALUS_WINDOWS_APP" in launcher
    assert "PYTHONUTF8" in install
    assert "PYTHONIOENCODING" in install
    assert "PYTHONUTF8" in launcher
    assert "ISCC" in build or "iscc" in build
    assert (root / "redist" / "README.md").is_file()


def test_capabilities_windows_desktop(win_tmp, monkeypatch):
    monkeypatch.setenv("DAEDALUS_DATA", str(win_tmp / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(win_tmp / "projects"))
    from daedalus.paths import data_root, playground_projects_dir
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    from daedalus.store import reset_sqlite_pool
    reset_sqlite_pool()
    from app.main import app
    client = TestClient(app)
    caps = client.get("/capabilities")
    assert "windows-desktop" in caps.json()["capabilities"]
    assert "windows-gui" in caps.json()["capabilities"]
    reset_sqlite_pool()
