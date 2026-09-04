"""Play Lab computers — local HTTP plus optional SmolVM guest browser."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


class FakePage:
    def __init__(self) -> None:
        self.url = ""

    def goto(self, url: str, **_kwargs) -> None:
        self.url = url


class FakePWBrowser:
    def __init__(self) -> None:
        self.pages: list[FakePage] = []

    def new_page(self) -> FakePage:
        page = FakePage()
        self.pages.append(page)
        return page

    def close(self) -> None:
        return None


class FakeSandbox:
    cdp_url = "http://127.0.0.1:9333"
    viewer_url = "http://127.0.0.1:36080/vnc.html"
    display_url = "vnc://127.0.0.1:35900"

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.cmds: list[str] = []
        self.stopped = False
        self.pw = FakePWBrowser()

    def run(self, cmd: str, **_kwargs):
        self.cmds.append(cmd)
        return type("R", (), {"exit_code": 0, "stdout": "1", "stderr": ""})()

    def connect_playwright(self) -> FakePWBrowser:
        return self.pw

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.stopped = True


@pytest.fixture
def harness_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_LAB_BACKEND", raising=False)
    monkeypatch.delenv("DAEDALUS_SMOLVM", raising=False)
    monkeypatch.delenv("DAEDALUS_LAB_PULL", raising=False)
    monkeypatch.setenv("DAEDALUS_LAB_DOCKER", "0")
    from daedalus.paths import data_root, playground_projects_dir, product_root, repo_root
    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    product_root.cache_clear()
    repo_root.cache_clear()
    from daedalus.store import reset_sqlite_pool
    reset_sqlite_pool()
    import daedalus.computer_smolvm as smol
    smol._FACTORY = None
    smol.stop_all()
    yield tmp_path
    smol.stop_all()
    smol._FACTORY = None
    from daedalus.play_lab import stop_all_http
    stop_all_http()
    reset_sqlite_pool()


def test_probe_without_smolvm(harness_tmp):
    from daedalus.computer import requested_backend
    from daedalus.play_lab import probe_host
    assert requested_backend() == "auto"
    host = probe_host()
    assert host["local_http"] is True
    assert host["backend_requested"] == "auto"
    assert "smolvm" in host
    smol = host.get("smolvm") or {}
    assert smol.get("import") is False or smol.get("ok") is False
    if not smol.get("import"):
        assert smol.get("ok") is False
        note = (smol.get("note") or "").lower()
        assert "not installed" in note or "optional" in note


def test_smolvm_explicit_without_package_fails(harness_tmp, monkeypatch):
    import daedalus.computer_smolvm as smol
    from daedalus.play_lab import create_lab, serve_url, start_vm

    monkeypatch.setenv("DAEDALUS_LAB_BACKEND", "smolvm")
    monkeypatch.setattr(smol, "available", lambda: False)
    lab = create_lab(title="Need Smol")
    with pytest.raises(ValueError, match="SmolVM"):
        start_vm(lab["id"])
    assert serve_url(lab["id"]) is None


def test_smolvm_factory_attaches_viewer(harness_tmp, monkeypatch):
    import daedalus.computer_smolvm as smol
    from daedalus.play_lab import create_lab, start_vm, stop_vm, write_install_file

    boxes: list[FakeSandbox] = []

    def factory(**kwargs):
        box = FakeSandbox(**kwargs)
        boxes.append(box)
        return box

    smol._FACTORY = factory
    monkeypatch.setenv("DAEDALUS_LAB_BACKEND", "auto")
    lab = create_lab(title="Watch Me")
    write_install_file(lab["id"], "index.html", "<html><body>hi</body></html>")
    started = start_vm(lab["id"])
    assert started["ok"] is True
    assert started["url"].startswith("http://127.0.0.1:")
    computer = started.get("computer") or {}
    assert computer.get("backend") == "smolvm"
    assert computer.get("viewer_url") == FakeSandbox.viewer_url
    assert computer.get("cdp_url") == FakeSandbox.cdp_url
    assert boxes and boxes[0].cmds
    assert boxes[0].pw.pages and boxes[0].pw.pages[0].url.startswith("http://127.0.0.1:8090")
    stopped = stop_vm(lab["id"])
    assert stopped["ok"] is True
    assert boxes[0].stopped is True
    assert smol.session_for(lab["id"]) is None


def test_open_project_skips_node_modules(harness_tmp, monkeypatch):
    monkeypatch.setenv("DAEDALUS_LAB_BACKEND", "local")
    from daedalus.computer import open_project_computer
    from daedalus.paths import playground_projects_dir
    from daedalus.play_lab import install_dir, stop_vm

    slug = "toygame"
    root = playground_projects_dir() / slug
    root.mkdir(parents=True)
    (root / "index.html").write_text("<html><canvas></canvas></html>", encoding="utf-8")
    (root / "node_modules").mkdir()
    (root / "node_modules" / "heavy.js").write_text("nope", encoding="utf-8")
    started = open_project_computer(slug)
    assert started["ok"] is True
    lab_id = started["lab_id"]
    assert not (install_dir(lab_id) / "node_modules").exists()
    assert (install_dir(lab_id) / "index.html").is_file()
    assert "index.html" in (started.get("url") or "")
    stop_vm(lab_id)


def test_computer_http(harness_tmp, monkeypatch):
    import daedalus.computer_smolvm as smol
    from app.main import app
    from daedalus.paths import playground_projects_dir

    smol._FACTORY = lambda **kwargs: FakeSandbox(**kwargs)
    slug = "see-me"
    root = playground_projects_dir() / slug
    root.mkdir(parents=True)
    (root / "index.html").write_text("<html>ok</html>", encoding="utf-8")

    client = TestClient(app)
    status = client.get("/api/v1/daedalus/computer")
    assert status.status_code == 200
    assert status.json()["ok"] is True
    assert "smolvm" in status.json()["host"]

    caps = client.get("/capabilities")
    assert "smolvm-computer" in caps.json()["capabilities"]
    assert "Watch build" in client.get("/").text

    opened = client.post("/api/v1/daedalus/computer", json={"slug": slug})
    assert opened.status_code == 200
    body = opened.json()
    assert body["ok"] is True
    assert body.get("computer", {}).get("viewer_url")
    live = client.get("/api/v1/daedalus/computer").json()["live"]
    assert live and live[0]["viewer_url"]
    assert "index.html" in (live[0].get("url") or "")

    stopped = client.post("/api/v1/daedalus/computer/stop", json={})
    assert stopped.status_code == 200
    assert stopped.json()["ok"] is True


def test_kill_switch(harness_tmp, monkeypatch):
    import daedalus.computer_smolvm as smol
    from daedalus.computer_smolvm import available
    from daedalus.play_lab import create_lab, start_vm, stop_vm, write_install_file

    smol._FACTORY = lambda **kwargs: FakeSandbox(**kwargs)
    assert available() is True
    monkeypatch.setenv("DAEDALUS_SMOLVM", "0")
    assert available() is False
    lab = create_lab(title="No Guest")
    write_install_file(lab["id"], "index.html", "<html>ok</html>")
    started = start_vm(lab["id"])
    assert started["ok"] is True
    assert not started.get("computer")
    stop_vm(lab["id"])


def test_local_backend_skips_smolvm(harness_tmp, monkeypatch):
    import daedalus.computer_smolvm as smol
    from daedalus.play_lab import create_lab, start_vm, stop_vm, write_install_file

    smol._FACTORY = lambda **kwargs: FakeSandbox(**kwargs)
    monkeypatch.setenv("DAEDALUS_LAB_BACKEND", "local")
    lab = create_lab(title="Local Only")
    write_install_file(lab["id"], "index.html", "<html>ok</html>")
    started = start_vm(lab["id"])
    assert started["ok"] is True
    assert started["backend"] == "local-http"
    assert started.get("lab_id") == lab["id"]
    assert not started.get("computer")
    assert started["url"].endswith("index.html")
    stop_vm(lab["id"])
