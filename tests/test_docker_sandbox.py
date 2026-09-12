"""Docker command sandbox. No live LLM. Default path does not need a daemon."""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

from daedalus.contracts.commands import CommandRequest
from daedalus.docker_sandbox import (
    DEFAULT_IMAGE,
    MAX_COPY_BYTES,
    build_docker_run_argv,
    exec_image,
    extract_artifacts,
    forbidden_argv_reason,
    forbidden_mount_reason,
    guest_env,
    probe_docker,
    run_in_docker,
    snapshot_workspace,
)
from daedalus.execution_backends import reset_execution_backend

ROOT = Path(__file__).resolve().parents[1]


def _standalone(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("DAEDALUS_STANDALONE", "1")
    monkeypatch.setenv("DAEDALUS_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("DAEDALUS_PLAYGROUND", str(tmp_path / "projects"))
    monkeypatch.delenv("DAEDALUS_BIND_HOST", raising=False)
    monkeypatch.delenv("DAEDALUS_EXEC_BACKEND", raising=False)
    monkeypatch.delenv("DAEDALUS_EXEC_PULL", raising=False)
    monkeypatch.delenv("DAEDALUS_EXEC_NETWORK", raising=False)
    monkeypatch.delenv("DAEDALUS_SHELL_LEGACY", raising=False)
    from daedalus.paths import data_root, playground_projects_dir, product_root, repo_root

    data_root.cache_clear()
    playground_projects_dir.cache_clear()
    product_root.cache_clear()
    repo_root.cache_clear()
    reset_execution_backend()
    return tmp_path


def _hide_docker(monkeypatch) -> None:
    monkeypatch.setattr("daedalus.docker_sandbox.docker_bin", lambda: None)


def _project(tmp_path: Path) -> Path:
    from daedalus.paths import playground_projects_dir

    root = playground_projects_dir() / "toy"
    root.mkdir(parents=True)
    (root / "index.js").write_text("ok\n", encoding="utf-8")
    return root


def test_probe_without_docker(monkeypatch):
    _hide_docker(monkeypatch)
    assert probe_docker() == "docker not on PATH"


def test_fail_closed_without_docker_no_history(tmp_path, monkeypatch):
    _standalone(tmp_path, monkeypatch)
    _hide_docker(monkeypatch)
    seen: list[object] = []

    def boom(*_a, **_k):
        seen.append(1)
        raise AssertionError("missing docker must not call subprocess")

    monkeypatch.setattr("daedalus.docker_sandbox.subprocess.run", boom)
    monkeypatch.setattr("daedalus.shell_center.subprocess.run", boom)
    res = run_in_docker(CommandRequest(raw_command="echo should-not-run"))
    assert res.ok is False
    assert res.blocked is True
    assert res.exit_code == 126
    assert "docker not on PATH" in res.error
    assert "P0.18" not in res.error
    from daedalus.shell_center import history

    assert history() == []
    assert seen == []


def test_daemon_down_fail_closed(tmp_path, monkeypatch):
    _standalone(tmp_path, monkeypatch)
    monkeypatch.setattr("daedalus.docker_sandbox.docker_bin", lambda: "/usr/bin/docker")

    def fake_run(argv, **_k):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="Cannot connect")

    monkeypatch.setattr("daedalus.docker_sandbox.subprocess.run", fake_run)
    res = run_in_docker(CommandRequest(raw_command="echo x"))
    assert res.blocked is True
    assert res.exit_code == 126
    assert "docker daemon not reachable" in res.error
    from daedalus.shell_center import history

    assert history() == []


def test_image_missing_no_pull(tmp_path, monkeypatch):
    _standalone(tmp_path, monkeypatch)
    monkeypatch.setattr("daedalus.docker_sandbox.docker_bin", lambda: "/usr/bin/docker")
    monkeypatch.setattr("daedalus.docker_sandbox.probe_docker", lambda: None)
    monkeypatch.setattr("daedalus.docker_sandbox.image_present", lambda _image: False)
    calls: list[list[str]] = []

    def fake_run(argv, **_k):
        calls.append(list(argv))
        raise AssertionError("missing image must not docker run")

    monkeypatch.setattr("daedalus.docker_sandbox.subprocess.run", fake_run)
    res = run_in_docker(CommandRequest(raw_command="echo x"))
    assert res.blocked is True
    assert DEFAULT_IMAGE in res.error or exec_image() in res.error
    assert "DAEDALUS_EXEC_PULL=1" in res.error
    assert calls == []


def test_hardening_argv_defaults():
    argv = build_docker_run_argv(
        image=DEFAULT_IMAGE,
        name="daedalus-exec-test",
        work_dir="/tmp/daedalus-exec-work",
        command=["python3", "-c", "print(1)"],
        network=False,
        cpus="1",
        memory="512m",
        pids=128,
    )
    assert argv[:3] == ["docker", "run", "--rm"]
    assert "--read-only" in argv
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert "--security-opt" in argv and "no-new-privileges" in argv
    assert argv[argv.index("--user") + 1] == "10000:10000"
    assert argv[argv.index("--network") + 1] == "none"
    assert argv[argv.index("--pids-limit") + 1] == "128"
    assert argv[argv.index("--memory") + 1] == "512m"
    assert argv[argv.index("--cpus") + 1] == "1"
    assert argv[argv.index("--workdir") + 1] == "/work"
    assert "--privileged" not in argv
    assert "-p" not in argv and "--publish" not in argv
    assert "docker.sock" not in " ".join(argv)


def test_network_opt_in_drops_none_keeps_no_host():
    off = build_docker_run_argv(
        image="img", name="n", work_dir="/tmp/w", command=["true"], network=False,
    )
    on = build_docker_run_argv(
        image="img", name="n", work_dir="/tmp/w", command=["true"], network=True,
    )
    assert "--network" in off and "none" in off
    assert "none" not in on
    assert "--net=host" not in on and "--network=host" not in on
    assert "-p" not in on


def test_socket_mount_refused():
    assert forbidden_mount_reason("/var/run/docker.sock")
    assert forbidden_argv_reason(["docker", "run", "-v", "/var/run/docker.sock:/var/run/docker.sock"])
    with pytest.raises(ValueError, match="socket"):
        build_docker_run_argv(
            image="img",
            name="n",
            work_dir="/var/run/docker.sock",
            command=["true"],
        )
    assert forbidden_argv_reason(["docker", "run", "--privileged", "img"])
    assert forbidden_argv_reason(["docker", "run", "-p", "8080:80", "img"])
    assert forbidden_argv_reason(["docker", "run", "--network", "host", "img"])


def test_snapshot_skips_node_modules_and_honors_cap(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "index.js").write_text("ok", encoding="utf-8")
    mods = src / "node_modules"
    mods.mkdir()
    (mods / "heavy.js").write_text("nope", encoding="utf-8")
    dest = tmp_path / "work"
    assert snapshot_workspace(src, dest) is None
    assert (dest / "index.js").is_file()
    assert not (dest / "node_modules").exists()

    fat = tmp_path / "fat"
    fat.mkdir()
    (fat / "big.bin").write_bytes(b"x" * 40)
    monkeypatch.setattr("daedalus.docker_sandbox.MAX_COPY_BYTES", 10)
    assert snapshot_workspace(fat, tmp_path / "work2") == (
        "workspace too large for docker sandbox (64 MiB cap)"
    )
    assert MAX_COPY_BYTES == 64 * 1024 * 1024


def test_extract_artifacts_allowlist(tmp_path):
    work = tmp_path / "work"
    jail = tmp_path / "jail"
    jail.mkdir()
    (work / "out").mkdir(parents=True)
    (work / "out" / "a.txt").write_text("keep", encoding="utf-8")
    (work / "out" / ".env").write_text("SECRET=1", encoding="utf-8")
    (work / ".env").write_text("nope", encoding="utf-8")
    (work / "secret.pem").write_text("nope", encoding="utf-8")
    (work / "scratch.txt").write_text("ignore", encoding="utf-8")
    extracted = extract_artifacts(work, jail)
    assert "out/a.txt" in extracted
    assert (jail / "out" / "a.txt").read_text(encoding="utf-8") == "keep"
    assert not (jail / "out" / ".env").exists()
    assert not (jail / ".env").exists()
    assert not (jail / "scratch.txt").exists()
    assert not (jail / "secret.pem").exists()


def test_preflight_blocks_before_docker(tmp_path, monkeypatch):
    _standalone(tmp_path, monkeypatch)
    monkeypatch.setattr("daedalus.docker_sandbox.docker_bin", lambda: "/usr/bin/docker")
    called: list[object] = []

    def boom(*_a, **_k):
        called.append(1)
        raise AssertionError("preflight must not reach docker")

    monkeypatch.setattr("daedalus.docker_sandbox.subprocess.run", boom)
    sudo = run_in_docker(CommandRequest(raw_command="sudo ls"))
    assert sudo.blocked is True
    assert sudo.exit_code == 126
    pipe = run_in_docker(CommandRequest(raw_command="echo hello | wc"))
    assert pipe.blocked is True
    outside = run_in_docker(CommandRequest(raw_command="pwd", cwd="/etc"))
    assert outside.blocked is True
    assert "cwd outside jail" in outside.error
    assert called == []


def test_mocked_run_extracts_and_writes_history(tmp_path, monkeypatch):
    _standalone(tmp_path, monkeypatch)
    project = _project(tmp_path)
    import daedalus.docker_sandbox as ds

    monkeypatch.setattr(ds, "docker_bin", lambda: "/usr/bin/docker")
    monkeypatch.setattr(ds, "probe_docker", lambda: None)
    monkeypatch.setattr(ds, "image_present", lambda _image: True)
    calls: list[list[str]] = []

    def fake_run(argv, **_kwargs):
        argv = list(argv)
        calls.append(argv)
        if len(argv) > 1 and argv[1] == "run":
            vol = next(a for a in argv if a.endswith(":/work:rw"))
            work = Path(vol.split(":", 1)[0])
            (work / "out").mkdir(exist_ok=True)
            (work / "out" / "a.txt").write_text("artifact", encoding="utf-8")
            (work / "out" / ".env").write_text("SECRET=1", encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, stdout="hello\n", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(ds.subprocess, "run", fake_run)
    res = run_in_docker(CommandRequest(raw_command="echo hello", cwd=str(project), slug="toy"))
    assert res.ok is True
    assert "hello" in res.stdout
    assert res.ran == "echo hello"
    assert (project / "out" / "a.txt").read_text(encoding="utf-8") == "artifact"
    assert not (project / "out" / ".env").exists()
    run_calls = [c for c in calls if len(c) > 1 and c[1] == "run"]
    assert run_calls and "--read-only" in run_calls[0] and "none" in run_calls[0]
    assert any(c[1:] == ["rm", "-f", c[2]] or (len(c) > 1 and c[1] == "rm") for c in calls)
    from daedalus.shell_center import history

    rows = history()
    assert rows and rows[-1]["ok"] is True
    assert rows[-1]["backend"] == "docker"


def test_mocked_timeout_omits_ran_and_rms(tmp_path, monkeypatch):
    _standalone(tmp_path, monkeypatch)
    _project(tmp_path)
    import daedalus.docker_sandbox as ds

    monkeypatch.setattr(ds, "docker_bin", lambda: "/usr/bin/docker")
    monkeypatch.setattr(ds, "probe_docker", lambda: None)
    monkeypatch.setattr(ds, "image_present", lambda _image: True)
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        argv = list(argv)
        calls.append(argv)
        if len(argv) > 1 and argv[1] == "run":
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout") or 1)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(ds.subprocess, "run", fake_run)
    res = run_in_docker(CommandRequest(raw_command="sleep 9", slug="toy", timeout_s=1))
    assert res.ok is False
    assert res.blocked is False
    assert res.exit_code == 124
    assert res.ran == ""
    assert "timeout after 1s" in res.error
    assert any(len(c) > 1 and c[1] == "rm" for c in calls)
    from daedalus.command_adapt import result_to_legacy

    assert "ran" not in result_to_legacy(res)


def test_guest_env_drops_host_python_paths():
    env = guest_env({
        "PATH": "/host/bin",
        "VIRTUAL_ENV": "/host/venv",
        "PYTHONPATH": "/host/lib",
        "HOME": "/home/me",
        "DAEDALUS_DATA": "/data",
    })
    assert env["PATH"].startswith("/usr/local")
    assert env["HOME"] == "/work"
    assert env["USER"] == "sandbox"
    assert "VIRTUAL_ENV" not in env
    assert "PYTHONPATH" not in env
    assert env["DAEDALUS_DATA"] == "/data"


def test_import_docker_sandbox_does_not_load_agent_or_play_lab():
    env = os.environ.copy()
    env["DAEDALUS_STANDALONE"] = "1"
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    code = (
        "import daedalus.docker_sandbox as d\n"
        "import sys\n"
        "bad = [n for n in sys.modules if n == 'daedalus.agent' "
        "or n.startswith('daedalus.agent.') or n == 'daedalus.play_lab']\n"
        "assert not bad, bad\n"
        "print('import-ok', d.build_docker_run_argv, d.DEFAULT_IMAGE)\n"
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


def test_agent_and_play_lab_do_not_import_docker_sandbox():
    for rel in ("daedalus/agent.py", "daedalus/play_lab.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "docker_sandbox" not in alias.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module != "daedalus.docker_sandbox"
                assert "docker_sandbox" not in (node.module or "")
        assert "docker_sandbox" not in src


def test_leftover_sandbox_semantics_untouched():
    agent = (ROOT / "daedalus" / "agent.py").read_text(encoding="utf-8")
    sandbox = (ROOT / "daedalus" / "sandbox.py").read_text(encoding="utf-8")
    coding = (ROOT / "daedalus" / "coding_tools.py").read_text(encoding="utf-8")
    assert "NEED_SANDBOX" in agent
    assert "code_sandbox_tool" in agent
    assert "validate_patch" in sandbox
    assert "def code_sandbox" in coding
    assert "from daedalus.play_lab" not in (ROOT / "daedalus" / "docker_sandbox.py").read_text(
        encoding="utf-8",
    )


def _docker_ready() -> bool:
    from daedalus.docker_sandbox import image_present

    return probe_docker() is None and image_present(exec_image())


@pytest.mark.docker
def test_live_python_add(tmp_path, monkeypatch):
    if not _docker_ready():
        pytest.skip("docker exec image not available")
    _standalone(tmp_path, monkeypatch)
    _project(tmp_path)
    res = run_in_docker(CommandRequest(
        executable="python3",
        arguments=("-c", "print(2+2)"),
        slug="toy",
    ))
    assert res.ok is True
    assert "4" in res.stdout


@pytest.mark.docker
def test_live_readonly_rootfs(tmp_path, monkeypatch):
    if not _docker_ready():
        pytest.skip("docker exec image not available")
    _standalone(tmp_path, monkeypatch)
    _project(tmp_path)
    res = run_in_docker(CommandRequest(
        raw_command="python3 -c \"open('/etc/passwd','a')\"",
        slug="toy",
    ))
    assert res.ok is False


@pytest.mark.docker
def test_live_no_docker_socket(tmp_path, monkeypatch):
    if not _docker_ready():
        pytest.skip("docker exec image not available")
    _standalone(tmp_path, monkeypatch)
    _project(tmp_path)
    res = run_in_docker(CommandRequest(
        raw_command="python3 -c \"import os; print(os.path.exists('/var/run/docker.sock'))\"",
        slug="toy",
    ))
    assert res.ok is True
    assert "False" in res.stdout
