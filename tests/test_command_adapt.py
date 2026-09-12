"""Structured command adapter. No live LLM."""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

from daedalus.command_adapt import request_from_legacy, result_from_legacy, result_to_legacy
from daedalus.contracts.commands import (
    CommandRequest,
    CommandResult,
    EnvPolicy,
    FilesystemPolicy,
    NetworkPolicy,
)

ROOT = Path(__file__).resolve().parents[1]


def test_request_from_legacy_splits_argv(monkeypatch):
    monkeypatch.delenv("DAEDALUS_SHELL_LEGACY", raising=False)
    monkeypatch.delenv("DAEDALUS_SHELL_PASS_SECRETS", raising=False)
    req = request_from_legacy(
        'python3 -c "print(1+1)"', cwd=".", slug="snake", timeout=12,
    )
    assert req.executable == "python3"
    assert req.arguments == ("-c", "print(1+1)")
    assert req.cwd == "."
    assert req.slug == "snake"
    assert req.timeout_s == 12
    assert req.raw_command == 'python3 -c "print(1+1)"'
    assert req.use_legacy_shell is False
    assert req.env_policy is EnvPolicy.STRIP_SECRETS
    assert req.network_policy is NetworkPolicy.UNRESTRICTED
    assert req.filesystem_policy is FilesystemPolicy.JAIL


def test_request_from_legacy_peels_npm_pipe(monkeypatch):
    monkeypatch.delenv("DAEDALUS_SHELL_LEGACY", raising=False)
    req = request_from_legacy("npm install 2>&1 | tail")
    assert req.raw_command == "npm install 2>&1 | tail"
    assert req.executable == "npm"
    assert req.arguments == ("install",)
    assert req.use_legacy_shell is False


def test_request_from_legacy_does_not_peel_echo_pipe(monkeypatch):
    monkeypatch.delenv("DAEDALUS_SHELL_LEGACY", raising=False)
    raw = "echo hello | wc"
    req = request_from_legacy(raw)
    assert req.raw_command == raw
    assert req.executable == "echo"
    assert "wc" in req.arguments or "|" in req.arguments


def test_request_from_legacy_omitted_timeout_stays_none(monkeypatch):
    monkeypatch.delenv("DAEDALUS_SHELL_LEGACY", raising=False)
    req = request_from_legacy("npm install")
    assert req.timeout_s is None
    assert req.executable == "npm"


def test_request_from_legacy_legacy_shell_flag(monkeypatch):
    monkeypatch.setenv("DAEDALUS_SHELL_LEGACY", "1")
    req = request_from_legacy("echo hello-legacy")
    assert req.use_legacy_shell is True
    assert req.executable == ""
    assert req.arguments == ()
    assert req.raw_command == "echo hello-legacy"


def test_request_from_legacy_pass_secrets_policy(monkeypatch):
    monkeypatch.delenv("DAEDALUS_SHELL_PASS_SECRETS", raising=False)
    assert request_from_legacy("ls").env_policy is EnvPolicy.STRIP_SECRETS
    monkeypatch.setenv("DAEDALUS_SHELL_PASS_SECRETS", "1")
    req = request_from_legacy("ls")
    assert req.env_policy is EnvPolicy.PASS_SECRETS
    dumped = req.model_dump()
    assert "OPENROUTER_API_KEY" not in dumped
    assert not any(isinstance(v, str) and "sk-" in v for v in dumped.values())


def test_result_to_legacy_success_includes_ran():
    res = CommandResult(
        ok=True,
        command="ls",
        ran="ls",
        stdout="ok",
        exit_code=0,
        cwd="/tmp",
        elapsed_ms=4,
    )
    row = result_to_legacy(res)
    assert row["ran"] == "ls"
    assert row["ok"] is True
    assert set(row) >= {
        "ok", "blocked", "disabled", "error", "command", "ran",
        "stdout", "stderr", "exit_code", "cwd", "elapsed_ms",
    }


def test_result_to_legacy_timeout_omits_ran():
    res = CommandResult(
        ok=False,
        error="timeout after 1s",
        command="sleep 9",
        ran="",
        exit_code=124,
        cwd="/tmp",
    )
    row = result_to_legacy(res)
    assert "ran" not in row
    assert row["exit_code"] == 124
    again = result_from_legacy(row)
    assert again.ran == ""
    assert "ran" not in result_to_legacy(again)


def test_result_from_legacy_preserves_missing_ran():
    row = {
        "ok": False,
        "blocked": False,
        "disabled": False,
        "error": "timeout after 30s",
        "command": "sleep 99",
        "stdout": "",
        "stderr": "",
        "exit_code": 124,
        "cwd": "/data",
        "elapsed_ms": 30000,
    }
    assert "ran" not in row
    res = result_from_legacy(row)
    assert res.ran == ""
    assert "ran" not in result_to_legacy(res)


def test_run_command_public_dict_still_omits_ran_on_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_BIND_HOST", raising=False)
    from daedalus.paths import data_root, playground_projects_dir, product_root, repo_root

    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    product_root.cache_clear()
    repo_root.cache_clear()

    def boom(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="sleep 9", timeout=1)

    monkeypatch.setattr("daedalus.shell_center.subprocess.run", boom)
    from daedalus.shell_center import run_command

    row = run_command("sleep 9", timeout=1)
    assert row["ok"] is False
    assert row["exit_code"] == 124
    assert "timeout" in row["error"]
    assert "ran" not in row


def test_execute_command_from_structured_request(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    from daedalus.paths import data_root, playground_projects_dir, product_root, repo_root

    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    product_root.cache_clear()
    repo_root.cache_clear()
    from daedalus.shell_center import execute_command

    res = execute_command(CommandRequest(
        executable="python3",
        arguments=("-c", "print(2+2)"),
    ))
    assert res.ok is True
    assert "4" in res.stdout


def test_import_command_adapt_does_not_load_agent():
    env = os.environ.copy()
    env["DAEDALUS_STANDALONE"] = "1"
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    code = (
        "import daedalus.command_adapt as a\n"
        "import daedalus.contracts.commands as c\n"
        "import sys\n"
        "bad = [n for n in sys.modules if n == 'daedalus.agent' "
        "or n.startswith('daedalus.agent.')]\n"
        "assert not bad, bad\n"
        "print('import-ok', c.CommandRequest, a.request_from_legacy)\n"
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


def test_agent_does_not_import_command_adapt():
    src = (ROOT / "daedalus" / "agent.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "command_adapt" not in alias.name
                assert alias.name != "daedalus.contracts.commands"
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module != "daedalus.command_adapt"
            assert "command_adapt" not in (node.module or "")
    assert "CommandRequest" not in src


def test_leftover_playground_run_and_sandbox_untouched():
    agent = (ROOT / "daedalus" / "agent.py").read_text(encoding="utf-8")
    scan = (ROOT / "daedalus" / "need_scan.py").read_text(encoding="utf-8")
    protocol = (ROOT / "daedalus" / "tool_protocol.py").read_text(encoding="utf-8")
    assert "pg_run_q" in scan
    assert "NEED_PLAYGROUND_RUN" in scan
    assert "playground_runner" in agent
    assert "install_deps=True" in agent
    assert "NEED_SANDBOX" in agent
    assert "code_sandbox_tool" in agent
    assert 'args.setdefault("command", "true")' in protocol
    from daedalus.native_tools import NATIVE_TOOL_SPECS

    spec = next(s for s in NATIVE_TOOL_SPECS if s["name"] == "run_command")
    props = spec["input_schema"]["properties"]
    assert "command" in props
    assert "executable" not in props
    assert spec["input_schema"]["required"] == ["command"]
    assert (ROOT / "daedalus" / "kernel" / "command_runner.py").is_file()
