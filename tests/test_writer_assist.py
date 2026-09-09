"""Writer assist — Phaser WEBGL seed, Auto/Off/Kernel, Studio toggle."""
from __future__ import annotations

from daedalus.playground import _write_scaffold


def _read(root, rel: str) -> str:
    return (root / rel).read_text(encoding="utf-8")


def test_default_auto_todo_is_vite_hello(tmp_path, monkeypatch):
    from daedalus import writer_assist as wa

    monkeypatch.delenv("DAEDALUS_WRITER_ASSIST", raising=False)
    dest = tmp_path / "todo"
    _write_scaffold(dest, "vite", "Todo", "todo", "track your tasks")
    main = _read(dest, "src/main.js")
    assert "Vanilla JS работи" in main
    assert "Phaser.WEBGL" not in main
    assert wa.mode() == "auto"


def test_kernel_scaffold_writes_webgl_game_mount(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_WRITER_ASSIST", "kernel-scaffold")
    dest = tmp_path / "ember"
    _write_scaffold(dest, "vite", "Ember Walk", "ember-walk", "WASD collect orbs")
    html = _read(dest, "index.html")
    main = _read(dest, "src/main.js")
    menu = _read(dest, "src/scenes/Menu.js")
    play = _read(dest, "src/scenes/Play.js")
    boot = _read(dest, "src/scenes/Boot.js")
    pkg = _read(dest, "package.json")
    scenario = _read(dest, "playtest/scenario.json")
    assert 'id="game"' in html
    assert 'id="app"' not in html
    assert "Phaser.WEBGL" in main
    assert "Phaser.AUTO" not in main
    assert "parent: 'game'" in main
    assert "physics" in main
    assert "from 'phaser'" in main
    assert "from 'phaser'" in boot
    assert "from 'phaser'" in menu
    assert "from 'phaser'" in play
    assert "addKeys" in play or "WASD" in play
    assert "fill this scene" not in play
    assert "Enter" in scenario
    assert "canvas_changed" in scenario
    assert "Ember Walk" in menu
    assert "querySelector('#app')" not in main
    assert '"phaser"' in pkg
    assert '"vite"' in pkg
    assert (dest / ".daedalus-writer-assist.json").is_file()


def test_auto_seeds_only_when_brief_names_phaser(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_WRITER_ASSIST", "auto")
    hello = tmp_path / "todo"
    _write_scaffold(hello, "vite", "Todo", "todo", "track your tasks")
    assert "Vanilla JS работи" in _read(hello, "src/main.js")

    phaser = tmp_path / "ember"
    _write_scaffold(phaser, "vite", "Ember", "ember", "Phaser 3 Boot Menu Play")
    assert "Phaser.WEBGL" in _read(phaser, "src/main.js")


def test_auto_uses_native_ask_when_description_empty(tmp_path, monkeypatch):
    from daedalus import writer_assist as wa

    monkeypatch.setenv("DAEDALUS_WRITER_ASSIST", "auto")
    wa.set_ask("kind=vite Phaser 3 src/main.js new Phaser.Game")
    try:
        dest = tmp_path / "ember"
        _write_scaffold(dest, "vite", "Ember", "ember", "")
        assert "Phaser.WEBGL" in _read(dest, "src/main.js")
    finally:
        wa.set_ask("")


def test_racer_still_wins_over_kernel_scaffold(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_WRITER_ASSIST", "kernel-scaffold")
    dest = tmp_path / "mist"
    _write_scaffold(dest, "vite", "Mist", "mist", "F-Zero hover racer")
    main = _read(dest, "src/main.js")
    assert (dest / "src" / "racer" / "track.js").is_file()
    assert "Phaser.WEBGL" not in main
    assert "from 'phaser'" not in main.lower()


def test_env_lock_beats_kv(tmp_path, monkeypatch):
    from daedalus import writer_assist as wa
    from daedalus.paths import data_root

    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path))
    monkeypatch.setenv("DAEDALUS_WRITER_ASSIST", "off")
    data_root.cache_clear()
    wa.set_mode("kernel-scaffold")
    assert wa.mode() == "off"
    st = wa.status()
    assert st["env_locked"] is True
    assert st["persisted"] == "kernel-scaffold"


def test_seed_note_only_when_marker_present(tmp_path, monkeypatch):
    from daedalus import writer_assist as wa

    monkeypatch.setenv("DAEDALUS_WRITER_ASSIST", "kernel-scaffold")
    monkeypatch.setattr("daedalus.playground.project_dir", lambda slug: tmp_path / slug)
    dest = tmp_path / "ember-walk"
    wa.seed_phaser_vite(dest, name="Ember Walk", slug="ember-walk")
    note = wa.seed_note("ember-walk")
    assert "Phaser.WEBGL" in note
    assert "Play.js" in note
    assert not wa.seed_note("missing")


def test_writer_assist_http_toggle(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from daedalus.paths import data_root

    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path))
    monkeypatch.delenv("DAEDALUS_WRITER_ASSIST", raising=False)
    data_root.cache_clear()
    client = TestClient(app)
    got = client.get("/api/v1/daedalus/writer-assist")
    assert got.status_code == 200
    assert got.json()["mode"] == "auto"
    kernel = client.post("/api/v1/daedalus/writer-assist", json={"mode": "kernel-scaffold"})
    assert kernel.status_code == 200
    assert kernel.json()["mode"] == "kernel-scaffold"
    assert kernel.json()["ok"] is True
    auto = client.post("/api/v1/daedalus/writer-assist", json={"mode": "auto"})
    assert auto.json()["mode"] == "auto"
    off = client.post("/api/v1/daedalus/writer-assist", json={"enabled": False})
    assert off.json()["mode"] == "off"
