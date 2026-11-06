"""Process resolution and child env for Playground runners."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from daedalus.proc import WIN_EXTRA_ENV_KEYS, argv_for_command, child_env_from_allowlist, resolve_exe

_ALLOW = frozenset({"PATH", "HOME", "WINDIR"})


def test_child_env_preserves_systemroot_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("daedalus.proc.WINDOWS", True)
    base = {"PATH": "/x", "WINDIR": r"C:\Windows", "SYSTEMROOT": r"C:\Windows"}
    env = child_env_from_allowlist(_ALLOW, 0, base=base)
    assert env.get("SYSTEMROOT") == r"C:\Windows"
    assert env.get("WINDIR") == r"C:\Windows"
    assert "SYSTEMROOT" in WIN_EXTRA_ENV_KEYS


def test_resolve_exe_npm_cmd_with_pathext(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    npm_cmd = bindir / "npm.cmd"
    npm_cmd.write_text("@echo off\n", encoding="utf-8")
    ext_env = {"PATH": str(bindir), "PATHEXT": ".COM;.EXE;.BAT;.CMD"}
    got = resolve_exe("npm", env=ext_env)
    assert got is not None
    assert got.name.lower() == "npm.cmd"


def test_argv_for_command_resolves_npm(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "npm.cmd").write_text("rem\n", encoding="utf-8")
    env = {"PATH": str(bindir), "PATHEXT": ".CMD"}
    argv = argv_for_command("npm", "install", env=env)
    assert argv[0].lower().endswith("npm.cmd")
    assert argv[1:] == ["install"]
