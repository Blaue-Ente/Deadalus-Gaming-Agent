"""ExecutionBackend selection. No live LLM."""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

from daedalus.contracts.commands import CommandRequest, CommandResult
from daedalus.execution_backends import (
    DockerBackend,
    FakeBackend,
    LocalBackend,
    get_execution_backend,
    reset_execution_backend,
    set_execution_backend,
)

ROOT = Path(__file__).resolve().parents[1]


def _standalone(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_BIND_HOST", raising=False)
    monkeypatch.delenv("DAEDALUS_EXEC_BACKEND", raising=False)
    from daedalus.paths import data_root, playground_projects_dir, product_root, repo_root

    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    product_root.cache_clear()
    repo_root.cache_clear()
    reset_execution_backend()


def test_default_backend_is_local(monkeypatch):
    monkeypatch.delenv("DAEDALUS_EXEC_BACKEND", raising=False)
    reset_execution_backend()
    backend = get_execution_backend()
    assert isinstance(backend, LocalBackend)
    assert callable(backend.run)


def test_execute_command_default_is_local_host(tmp_path, monkeypatch):
    _standalone(tmp_path, monkeypatch)
    from daedalus.shell_center import execute_command

    res = execute_command(CommandRequest(
        executable="python3",
        arguments=("-c", "print(2+2)"),
    ))
    assert res.ok is True
    assert "4" in res.stdout


def test_execute_command_accepts_fake_backend(tmp_path, monkeypatch):
    _standalone(tmp_path, monkeypatch)
    seen: list[object] = []

    def boom(*_a, **_k):
        seen.append(1)
        raise AssertionError("FakeBackend must not call subprocess")

    monkeypatch.setattr("daedalus.shell_center.subprocess.run", boom)
    fake = FakeBackend()
    programmed = CommandResult(ok=True, command="ls", ran="ls", stdout="ok", exit_code=0)
    fake.queue(programmed)
    from daedalus.shell_center import execute_command

    res = execute_command(CommandRequest(raw_command="ls"), backend=fake)
    assert res == programmed
    assert len(fake.calls) == 1
    assert fake.calls[0].raw_command == "ls"
    assert seen == []


def test_set_execution_backend_affects_run_command(tmp_path, monkeypatch):
    _standalone(tmp_path, monkeypatch)
    fake = FakeBackend()
    fake.queue(CommandResult(
        ok=False,
        error="timeout after 1s",
        command="sleep 9",
        ran="",
        exit_code=124,
        cwd="/tmp",
    ))
    set_execution_backend(fake)
    from daedalus.command_adapt import result_to_legacy
    from daedalus.shell_center import run_command

    row = run_command("sleep 9", timeout=1)
    assert row["ok"] is False
    assert row["exit_code"] == 124
    assert "ran" not in row
    assert row == result_to_legacy(CommandResult(
        ok=False,
        error="timeout after 1s",
        command="sleep 9",
        ran="",
        exit_code=124,
        cwd="/tmp",
    ))
    reset_execution_backend()
    assert isinstance(get_execution_backend(), LocalBackend)


def test_fake_backend_always_after_queue_empty():
    fake = FakeBackend()
    once = CommandResult(ok=True, command="a", ran="a", stdout="1")
    fallback = CommandResult(ok=True, command="b", ran="b", stdout="2")
    fake.queue(once)
    fake.always(fallback)
    assert fake.run(CommandRequest(raw_command="a")).stdout == "1"
    assert fake.run(CommandRequest(raw_command="b")).stdout == "2"


def test_fake_backend_fail_closed_when_empty():
    fake = FakeBackend()
    res = fake.run(CommandRequest(raw_command="echo x"))
    assert res.ok is False
    assert res.blocked is True
    assert res.exit_code == 126
    assert "no programmed result" in res.error
    assert res.command == "echo x"


def test_docker_backend_fail_closed_without_docker(tmp_path, monkeypatch):
    _standalone(tmp_path, monkeypatch)
    import daedalus.docker_sandbox as ds

    monkeypatch.setattr(ds, "docker_bin", lambda: None)
    seen: list[object] = []

    def boom(*_a, **_k):
        seen.append(1)
        raise AssertionError("DockerBackend must not call host subprocess")

    monkeypatch.setattr("daedalus.shell_center.subprocess.run", boom)
    req = CommandRequest(raw_command="echo should-not-run")
    res = DockerBackend().run(req)
    assert res.ok is False
    assert res.blocked is True
    assert res.exit_code == 126
    assert "docker not on PATH" in res.error
    assert "P0.18" not in res.error
    assert "not implemented" not in res.error.lower()
    assert res.command == "echo should-not-run"
    assert "should-not-run" not in (res.stdout or "")
    assert seen == []


def test_env_docker_fail_closed_not_host(tmp_path, monkeypatch):
    _standalone(tmp_path, monkeypatch)
    import daedalus.docker_sandbox as ds

    monkeypatch.setattr(ds, "docker_bin", lambda: None)
    monkeypatch.setenv("DAEDALUS_EXEC_BACKEND", "docker")
    reset_execution_backend()
    assert isinstance(get_execution_backend(), DockerBackend)
    from daedalus.shell_center import run_command

    row = run_command("echo should-not-run")
    assert row["ok"] is False
    assert row["blocked"] is True
    assert row["exit_code"] == 126
    assert "docker not on PATH" in row["error"]
    assert "P0.18" not in row["error"]
    assert "not implemented" not in row["error"].lower()
    assert "should-not-run" not in (row.get("stdout") or "")


def test_env_local_is_host_path(tmp_path, monkeypatch):
    _standalone(tmp_path, monkeypatch)
    monkeypatch.setenv("DAEDALUS_EXEC_BACKEND", "local")
    reset_execution_backend()
    from daedalus.shell_center import run_command

    row = run_command("echo host-ok")
    assert row["ok"] is True
    assert "host-ok" in row["stdout"]


def test_env_unknown_and_fake_fail_closed(tmp_path, monkeypatch):
    _standalone(tmp_path, monkeypatch)
    from daedalus.shell_center import run_command

    monkeypatch.setenv("DAEDALUS_EXEC_BACKEND", "fake")
    reset_execution_backend()
    fake_row = run_command("echo should-not-run")
    assert fake_row["ok"] is False
    assert fake_row["blocked"] is True
    assert fake_row["exit_code"] == 126
    assert "unknown execution backend: fake" in fake_row["error"]
    assert "should-not-run" not in (fake_row.get("stdout") or "")

    monkeypatch.setenv("DAEDALUS_EXEC_BACKEND", "bogus")
    reset_execution_backend()
    bad = run_command("echo should-not-run")
    assert bad["blocked"] is True
    assert "unknown execution backend: bogus" in bad["error"]
    assert "exception" not in bad["error"].lower()


def test_host_alias_selects_local(monkeypatch):
    monkeypatch.setenv("DAEDALUS_EXEC_BACKEND", "host")
    reset_execution_backend()
    assert isinstance(get_execution_backend(), LocalBackend)


def test_import_execution_backends_does_not_load_agent_or_shell():
    env = os.environ.copy()
    env["DAEDALUS_STANDALONE"] = "1"
    env.pop("DAEDALUS_EXEC_BACKEND", None)
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    code = (
        "import daedalus.execution_backends as b\n"
        "import daedalus.contracts.commands as c\n"
        "import sys\n"
        "bad = [n for n in sys.modules if n == 'daedalus.agent' "
        "or n.startswith('daedalus.agent.')]\n"
        "assert not bad, bad\n"
        "assert 'daedalus.shell_center' not in sys.modules\n"
        "backend = b.get_execution_backend()\n"
        "assert isinstance(backend, b.LocalBackend)\n"
        "assert 'daedalus.shell_center' not in sys.modules\n"
        "assert 'daedalus.docker_sandbox' not in sys.modules\n"
        "print('import-ok', c.ExecutionBackend, b.FakeBackend, b.DockerBackend)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "import-ok" in result.stdout


def test_agent_does_not_import_execution_backends():
    src = (ROOT / "daedalus" / "agent.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "execution_backends" not in alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module != "daedalus.execution_backends"
            assert node.module != "daedalus.docker_sandbox"
            assert "execution_backends" not in (node.module or "")
            assert "docker_sandbox" not in (node.module or "")
    assert "ExecutionBackend" not in src
    assert "FakeBackend" not in src
    assert "docker_sandbox" not in src


def test_leftover_ladder_untouched():
    agent = (ROOT / "daedalus" / "agent.py").read_text(encoding="utf-8")
    scan = (ROOT / "daedalus" / "need_scan.py").read_text(encoding="utf-8")
    assert "NEED_PLAYGROUND_RUN" in scan
    assert "pg_run_q" in scan
    assert "playground_runner" in agent
    assert "install_deps=True" in agent
    assert "NEED_SANDBOX" in agent
    assert "code_sandbox_tool" in agent
    assert (ROOT / "daedalus" / "kernel" / "command_runner.py").is_file()
    assert (ROOT / "daedalus" / "sandbox.py").is_file()
    assert "docker_sandbox" not in agent
    sandbox_src = (ROOT / "daedalus" / "docker_sandbox.py").read_text(encoding="utf-8")
    tree = ast.parse(sandbox_src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "play_lab" not in node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert "play_lab" not in alias.name


def test_contracts_still_do_not_import_shell_center():
    tree = ast.parse((ROOT / "daedalus" / "contracts" / "commands.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "shell_center" not in alias.name
                assert "execution_backends" not in alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert "shell_center" not in node.module
            assert "execution_backends" not in node.module
