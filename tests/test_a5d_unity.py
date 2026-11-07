"""A5d: Unity play harness. Pure tests run everywhere; the live test builds and drives a real Windows player and
runs only where Unity and a desktop exist (DAEDALUS_REQUIRE_PLAY=1 turns a missing prerequisite into a failure)."""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest
from a0_support import a0_tmp  # noqa: F401

from daedalus.harness.play_worker import unity
from daedalus.harness.play_worker.errors import PlayError

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "unity_play"


def test_unity_editor_env_wins_and_a_bad_pin_is_an_honest_error(tmp_path, monkeypatch):
    exe = tmp_path / "Unity.exe"
    exe.write_text("x")
    monkeypatch.setenv("UNITY_PATH", str(exe))
    assert unity.find_unity() == {"ok": True, "path": str(exe), "source": "env"}
    monkeypatch.setenv("UNITY_PATH", str(tmp_path / "nope.exe"))
    r = unity.find_unity()
    assert r["ok"] is False and r["error"] == "engine_missing" and r["source"] == "env"


def test_a_hub_launcher_shim_on_path_is_not_taken_for_the_editor(tmp_path, monkeypatch):
    shim = tmp_path / "Unity" / "bin" / "Unity.exe"
    shim.parent.mkdir(parents=True)
    shim.write_text("x")
    monkeypatch.delenv("UNITY_PATH", raising=False)
    monkeypatch.delenv("UNITY_EDITOR", raising=False)
    monkeypatch.setattr(unity.shutil, "which", lambda name: str(shim))
    monkeypatch.setattr(unity.Path, "is_dir", lambda self: False)
    assert unity._is_shim(str(shim)) is True
    assert unity.find_unity()["ok"] is False


def test_is_unity_project(tmp_path):
    assert not unity.is_unity_project(tmp_path)
    (tmp_path / "Assets").mkdir()
    (tmp_path / "ProjectSettings").mkdir()
    assert unity.is_unity_project(tmp_path)


def test_content_hash_follows_project_content_not_library(tmp_path):
    proj = tmp_path / "p"
    shutil.copytree(FIXTURE, proj)
    h1 = unity.content_hash(proj, "ed1")
    assert h1 == unity.content_hash(proj, "ed1")
    assert h1 != unity.content_hash(proj, "ed2")                     # another editor build is another player
    (proj / "Library").mkdir()
    (proj / "Library" / "junk").write_text("x")
    assert unity.content_hash(proj, "ed1") == h1                     # caches never invalidate the key
    (proj / "Assets" / "FixtureGame.cs").write_text("// changed")
    assert unity.content_hash(proj, "ed1") != h1


def test_the_bridge_never_pretends_to_inject_legacy_input():
    src = (unity._ASSETS / "DaedalusPlayBridge.cs").read_text(encoding="utf-8")
    assert 'Fail(req, "unsupported_input_route")' in src
    assert "--daedalus-bridge" in src and "IPAddress.Loopback" in src      # loopback only, only when launched by us


def test_build_refuses_off_windows_with_a_typed_error(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("Windows builds for real")
    proj = tmp_path / "p"
    shutil.copytree(FIXTURE, proj)
    with pytest.raises(PlayError) as exc:
        unity.build_player(proj, tmp_path / "cache", "unity")
    assert exc.value.code == "unsupported_platform"


def test_manager_accepts_unity_and_still_refuses_unknown_engines(tmp_path, monkeypatch):
    from daedalus.harness.play_worker import manager as mgr_mod
    m = mgr_mod.PlayManager(limit=1)
    with pytest.raises(PlayError) as exc:
        m.start("x", tmp_path, engine="unreal")
    assert exc.value.code == "unsupported_engine"
    monkeypatch.setattr(mgr_mod.PlaySession, "start", lambda self, **k: {"ok": True, "engine": self.engine})
    assert m.start("x", tmp_path, engine="unity")["engine"] == "unity"


def test_a_godot_project_is_not_started_as_unity(tmp_path):
    from daedalus.harness.play_worker.session import PlaySession
    (tmp_path / "project.godot").write_text("x")
    s = PlaySession(tmp_path, slug="g", engine="unity")
    with pytest.raises(PlayError) as exc:
        s.start()
    assert exc.value.code in {"engine_missing", "start_failed"}      # never a started session


# ------------------------------------------------------------------------------------------ live (Windows only)
def _prereq() -> tuple[bool, str]:
    if sys.platform != "win32":
        return False, "not Windows"
    eng = unity.find_unity()
    return bool(eng.get("ok")), str(eng.get("message", ""))


_OK, _WHY = _prereq()
REQUIRE = os.environ.get("DAEDALUS_REQUIRE_PLAY") == "1" and sys.platform == "win32"
live = pytest.mark.skipif(not _OK and not REQUIRE, reason=f"Unity live prerequisites missing: {_WHY}")


@pytest.fixture
def unity_project(a0_tmp):  # noqa: F811
    projects = a0_tmp / "projects"
    projects.mkdir(parents=True, exist_ok=True)
    shutil.copytree(FIXTURE, projects / "ufx")
    return projects / "ufx"


@live
def test_unity_player_is_driven_by_real_input_and_the_picture_changes(unity_project):
    from daedalus.harness.play_worker.capture import sha256_file
    from daedalus.harness.play_worker.session import PlaySession

    before = {str(p.relative_to(unity_project)): sha256_file(p) for p in sorted(unity_project.rglob("*")) if p.is_file()}
    s = PlaySession(unity_project, slug="ufx", engine="unity")
    try:
        st = s.start(timeout=60)
        assert st["backend"] == "windows" and st["level"] == 1
        assert s.fps()["ok"] is True
        tree = s.tree()
        assert any(n.get("name") == "FixtureGame" for n in tree["tree"])
        info = s.frame("window")
        assert info["blank"] is False and s.level == 3
        eng = s.frame("engine")                                  # the player's own render, via the bridge
        assert eng["blank"] is False
        key = s.probe_input(keys=["space"], via="os")
        assert key["conclusive"] and key["observed_change"] and s.level == 4
        assert s.game_state()["ok"] is True
        click = s.probe_input(mouse={"kind": "click", "x": 300, "y": 240}, via="os")
        assert click["observed_change"]
        with pytest.raises(PlayError):
            s.send(keys=["space"], via="bridge")                 # no pretend injection for the legacy input system
    finally:
        s.stop()
    after = {str(p.relative_to(unity_project)): sha256_file(p) for p in sorted(unity_project.rglob("*")) if p.is_file()}
    assert after == before                                       # the owner's project was never touched
    assert not (unity_project / "Library").exists()
