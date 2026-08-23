"""Windows desktop layout, env loading, installer scripts — no Win32 required."""
from __future__ import annotations

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
    monkeypatch.setattr("app.desktop.webbrowser.open", lambda url: opened.append(url))
    code = run_desktop(host="127.0.0.1", port=8787, open_browser=True, serve=True)
    assert code == 0
    assert opened and opened[0].startswith("http://127.0.0.1:8787")


def test_cli_desktop_help():
    from app.cli import main
    with pytest.raises(SystemExit) as raised:
        main(["desktop", "--help"])
    assert raised.value.code == 0


def test_installer_scripts_exist():
    root = Path(__file__).resolve().parents[1] / "packaging" / "windows"
    install = (root / "install.ps1").read_text(encoding="utf-8")
    uninstall = (root / "uninstall.ps1").read_text(encoding="utf-8")
    iss = (root / "Daedalus.iss").read_text(encoding="utf-8")
    assert "LOCALAPPDATA" in install
    assert "Start Menu" in install
    assert "pythonw.exe" in install
    assert "PrivilegesRequired=lowest" in iss
    assert "install.ps1" in iss
    assert "-RemoveData" in uninstall
    assert "Programs\\Daedalus" in uninstall or "Programs\\$AppName" in uninstall


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
    reset_sqlite_pool()
