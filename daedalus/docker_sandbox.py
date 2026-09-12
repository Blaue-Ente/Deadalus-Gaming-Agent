"""Docker command sandbox behind ``DockerBackend``.

Fail-closed without docker / daemon / image. Separate from the lab computer.
Do not import this module from agent.py.
"""
from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import subprocess
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from daedalus.contracts.commands import CommandRequest, CommandResult
from daedalus.paths import data_root

log = logging.getLogger(__name__)

DEFAULT_IMAGE = "daedalus-exec:p018"
SANDBOX_USER = "10000:10000"
MAX_COPY_BYTES = 64 * 1024 * 1024
SKIP_COPY = frozenset({".git", "node_modules", "__pycache__", ".venv", ".godot"})
ARTIFACT_DIRS = ("out", "dist", "build")
SECRET_NAMES = frozenset({".env", "secrets.env", "owner.token"})
SECRET_SUFFIXES = frozenset({".pem", ".key"})
SOCKET_NAMES = frozenset({"docker.sock"})
GUEST_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_DROP_HOST_ENV = frozenset({"VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH"})


def _env_flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def exec_image() -> str:
    return (os.environ.get("DAEDALUS_EXEC_IMAGE") or DEFAULT_IMAGE).strip() or DEFAULT_IMAGE


def network_enabled() -> bool:
    return _env_flag("DAEDALUS_EXEC_NETWORK")


def pull_opt_in() -> bool:
    return _env_flag("DAEDALUS_EXEC_PULL")


def exec_cpus() -> str:
    raw = (os.environ.get("DAEDALUS_EXEC_CPUS") or "1").strip()
    try:
        val = float(raw)
    except ValueError:
        return "1"
    if val <= 0 or val > 8:
        return "1"
    if val == int(val):
        return str(int(val))
    return str(val)


def exec_memory() -> str:
    raw = (os.environ.get("DAEDALUS_EXEC_MEMORY") or "512m").strip().lower()
    if raw.endswith(("k", "m", "g")) and raw[:-1].isdigit():
        return raw
    if raw.isdigit():
        return f"{raw}m"
    return "512m"


def exec_pids() -> int:
    raw = (os.environ.get("DAEDALUS_EXEC_PIDS") or "128").strip()
    try:
        return max(8, min(int(raw), 1024))
    except ValueError:
        return 128


def docker_bin() -> str | None:
    return shutil.which("docker")


def _run_docker(args: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    exe = docker_bin() or "docker"
    return subprocess.run(
        [exe, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def probe_docker() -> str | None:
    if not docker_bin():
        return "docker not on PATH"
    try:
        proc = _run_docker(["info"], timeout=8)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return "docker daemon not reachable"
    if proc.returncode != 0:
        return "docker daemon not reachable"
    return None


def image_present(image: str) -> bool:
    if not docker_bin():
        return False
    try:
        proc = _run_docker(["image", "inspect", image], timeout=8)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0


def pull_image(image: str) -> str | None:
    try:
        proc = _run_docker(["pull", image], timeout=120)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return f"docker pull failed: {exc}"[:300]
    if proc.returncode != 0:
        return f"docker pull failed: {(proc.stderr or proc.stdout or '')[:300]}"
    return None


def forbidden_mount_reason(work_dir: str | Path) -> str | None:
    resolved = Path(work_dir).expanduser()
    try:
        resolved = resolved.resolve()
    except OSError:
        resolved = Path(work_dir)
    if resolved.name in SOCKET_NAMES or str(resolved).endswith("docker.sock"):
        return "refusing to mount host Docker socket"
    for sock in (Path("/var/run/docker.sock"), Path("/run/docker.sock")):
        try:
            if resolved == (sock.resolve() if sock.exists() else sock):
                return "refusing to mount host Docker socket"
        except OSError:
            if resolved == sock:
                return "refusing to mount host Docker socket"
    return None


def forbidden_argv_reason(argv: list[str]) -> str | None:
    joined = " ".join(argv)
    if "docker.sock" in joined:
        return "refusing to mount host Docker socket"
    if "--privileged" in argv:
        return "refusing privileged docker run"
    for i, arg in enumerate(argv):
        if arg in ("-p", "--publish", "--publish-all", "-P"):
            return "refusing to publish ports"
        if arg in ("--net=host", "--network=host", "--pid=host", "--ipc=host"):
            return "refusing host namespace"
        if arg in ("--network", "--net") and i + 1 < len(argv) and argv[i + 1] == "host":
            return "refusing host namespace"
    return None


def guest_env(host_child: Mapping[str, str]) -> dict[str, str]:
    env = {key: val for key, val in host_child.items() if key not in _DROP_HOST_ENV}
    env["HOME"] = "/work"
    env["USER"] = "sandbox"
    env["PWD"] = "/work"
    env["TMPDIR"] = "/tmp"
    env["PATH"] = GUEST_PATH
    env.setdefault("PYTHONUNBUFFERED", "1")
    env["DAEDALUS_SHELL"] = "1"
    return env


def build_docker_run_argv(
    *,
    image: str,
    name: str,
    work_dir: str | Path,
    command: list[str],
    env: Mapping[str, str] | None = None,
    network: bool = False,
    cpus: str | None = None,
    memory: str | None = None,
    pids: int | str | None = None,
) -> list[str]:
    """Pure argv builder. Raises ValueError if a mount or flag is forbidden."""
    work = Path(work_dir)
    mount_err = forbidden_mount_reason(work)
    if mount_err:
        raise ValueError(mount_err)
    mem = memory or exec_memory()
    argv = [
        "docker", "run", "--rm",
        "--name", name,
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--user", SANDBOX_USER,
        "--workdir", "/work",
        "--cpus", cpus or exec_cpus(),
        "--memory", mem,
        "--memory-swap", mem,
        "--pids-limit", str(pids if pids is not None else exec_pids()),
        "--tmpfs", "/tmp:rw,nosuid,nodev,size=64m",
        "-v", f"{work.resolve()}:/work:rw",
    ]
    if not network:
        argv.extend(["--network", "none"])
    for key, val in (env or {}).items():
        argv.extend(["-e", f"{key}={val}"])
    argv.append(image)
    argv.extend(command)
    flag_err = forbidden_argv_reason(argv)
    if flag_err:
        raise ValueError(flag_err)
    return argv


def _tree_size(path: Path) -> int:
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for item in path.rglob("*"):
        if item.is_file() and not any(part in SKIP_COPY for part in item.parts):
            try:
                total += item.stat().st_size
            except OSError:
                continue
            if total > MAX_COPY_BYTES:
                return total
    return total


def _chmod_writable(root: Path) -> None:
    try:
        root.chmod(0o777)
    except OSError:
        pass
    for item in root.rglob("*"):
        try:
            item.chmod(0o777 if item.is_dir() else 0o666)
        except OSError:
            continue


def snapshot_workspace(src: Path, dest: Path) -> str | None:
    dest.mkdir(parents=True, exist_ok=True)
    _chmod_writable(dest)
    size = _tree_size(src)
    if size > MAX_COPY_BYTES:
        return "workspace too large for docker sandbox (64 MiB cap)"
    if src.is_file():
        shutil.copy2(src, dest / src.name)
        _chmod_writable(dest)
        return None

    def ignore(_directory: str, names: list[str]) -> list[str]:
        return [name for name in names if name in SKIP_COPY]

    for child in src.iterdir():
        if child.name in SKIP_COPY:
            continue
        target = dest / child.name
        if child.is_dir():
            shutil.copytree(child, target, ignore=ignore, dirs_exist_ok=True)
        elif child.is_file():
            shutil.copy2(child, target)
    _chmod_writable(dest)
    return None


def _is_secret(path: Path) -> bool:
    return path.name in SECRET_NAMES or path.suffix.lower() in SECRET_SUFFIXES


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def extract_artifacts(work: Path, jail_cwd: Path) -> list[str]:
    extracted: list[str] = []
    jail = jail_cwd.resolve()
    for name in ARTIFACT_DIRS:
        src = work / name
        if not src.exists():
            continue
        dest = (jail / name).resolve()
        if not _inside(dest, jail):
            continue
        if src.is_file():
            if _is_secret(src):
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            extracted.append(name)
            continue
        dest.mkdir(parents=True, exist_ok=True)
        for item in src.rglob("*"):
            if not item.is_file() or _is_secret(item):
                continue
            rel = item.relative_to(src)
            out = dest / rel
            if not _inside(out, jail):
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, out)
            extracted.append(f"{name}/{rel.as_posix()}")
    return extracted


def _write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_in_docker(request: CommandRequest) -> CommandResult:
    from daedalus.command_adapt import result_from_legacy
    from daedalus.shell_center import (
        DEFAULT_TIMEOUT_S,
        MAX_OUTPUT_CHARS,
        MAX_TIMEOUT_S,
        _PKG_HEAD,
        _append_history,
        _blocked,
        _child_env,
        _command_text,
        _fail_result,
        legacy_shell_enabled,
        peel_package_command,
        resolve_cwd,
        shell_enabled,
    )

    command = _command_text(request)
    cwd = request.cwd or None
    slug = request.slug
    timeout = request.timeout_s
    t0 = time.perf_counter()
    if not shell_enabled():
        return _fail_result(
            command,
            "shell disabled: public bind requires DAEDALUS_SHELL_ISOLATION=1",
            disabled=True,
        )
    peeled = peel_package_command(command)
    effective = command
    if peeled and peeled != command.strip():
        log.info("docker sandbox peeled package sugar %r → %r", command[:160], peeled)
        effective = peeled
    reason = _blocked(effective)
    if reason:
        return _fail_result(command, reason)
    try:
        workdir = resolve_cwd(cwd, slug=slug)
    except ValueError as exc:
        return _fail_result(command, str(exc), cwd=cwd or "")

    timeout_s = DEFAULT_TIMEOUT_S if timeout is None else int(timeout)
    if timeout is None and _PKG_HEAD.match(effective.strip()):
        timeout_s = 120
    timeout_s = max(1, min(timeout_s, MAX_TIMEOUT_S))
    use_shell = legacy_shell_enabled()
    argv: list[str] | None = None
    if not use_shell:
        try:
            argv = shlex.split(effective, posix=True)
        except ValueError as exc:
            return _fail_result(command, f"could not parse command: {exc}", cwd=str(workdir))
        if not argv:
            return _fail_result(command, "empty command", cwd=str(workdir))

    probe_err = probe_docker()
    if probe_err:
        return _fail_result(command, probe_err, cwd=str(workdir))
    image = exec_image()
    if not image_present(image):
        if pull_opt_in():
            pull_err = pull_image(image)
            if pull_err:
                return _fail_result(command, pull_err, cwd=str(workdir))
        else:
            return _fail_result(
                command,
                f"docker image {image} is not local — set DAEDALUS_EXEC_PULL=1 to pull",
                cwd=str(workdir),
            )

    run_id = uuid.uuid4().hex[:12]
    name = f"daedalus-exec-{run_id}"[:60]
    exec_root = data_root() / "exec" / run_id
    work = exec_root / "work"
    copy_err = snapshot_workspace(workdir, work)
    if copy_err:
        shutil.rmtree(exec_root, ignore_errors=True)
        return _fail_result(command, copy_err, cwd=str(workdir))
    mount_err = forbidden_mount_reason(work)
    if mount_err:
        shutil.rmtree(exec_root, ignore_errors=True)
        return _fail_result(command, mount_err, cwd=str(workdir))

    guest_cmd = ["sh", "-c", effective] if use_shell else list(argv or [])
    env = guest_env(_child_env())
    try:
        docker_argv = build_docker_run_argv(
            image=image,
            name=name,
            work_dir=work,
            command=guest_cmd,
            env=env,
            network=network_enabled(),
        )
    except ValueError as exc:
        shutil.rmtree(exec_root, ignore_errors=True)
        return _fail_result(command, str(exc), cwd=str(workdir))

    extracted: list[str] = []
    row: dict[str, Any]
    try:
        _run_docker(["rm", "-f", name], timeout=20)
        proc = subprocess.run(
            docker_argv,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        extracted = extract_artifacts(work, workdir)
        stdout = (proc.stdout or "")[:MAX_OUTPUT_CHARS]
        stderr = (proc.stderr or "")[:MAX_OUTPUT_CHARS]
        row = {
            "ok": proc.returncode == 0,
            "blocked": False,
            "disabled": False,
            "error": "" if proc.returncode == 0 else f"exit {proc.returncode}",
            "command": command,
            "ran": effective,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": proc.returncode,
            "cwd": str(workdir),
            "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        }
    except subprocess.TimeoutExpired:
        row = {
            "ok": False,
            "blocked": False,
            "disabled": False,
            "error": f"timeout after {timeout_s}s",
            "command": command,
            "stdout": "",
            "stderr": "",
            "exit_code": 124,
            "cwd": str(workdir),
            "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        }
    except Exception as exc:
        row = {
            "ok": False,
            "blocked": False,
            "disabled": False,
            "error": str(exc)[:300],
            "command": command,
            "stdout": "",
            "stderr": "",
            "exit_code": 1,
            "cwd": str(workdir),
            "elapsed_ms": int((time.perf_counter() - t0) * 1000),
        }
    finally:
        try:
            _run_docker(["rm", "-f", name], timeout=20)
        except Exception as exc:
            log.debug("docker rm %s: %s", name, exc)
        shutil.rmtree(work, ignore_errors=True)

    _write_manifest(exec_root / "manifest.json", {
        "image": image,
        "name": name,
        "exit_code": row.get("exit_code"),
        "extracted": extracted,
        "error": row.get("error") or "",
    })
    _append_history({
        "ts": time.time(),
        "command": command[:500],
        "cwd": row.get("cwd"),
        "ok": row.get("ok"),
        "exit_code": row.get("exit_code"),
        "elapsed_ms": row.get("elapsed_ms"),
        "backend": "docker",
    })
    return result_from_legacy(row)


__all__ = [
    "ARTIFACT_DIRS",
    "DEFAULT_IMAGE",
    "MAX_COPY_BYTES",
    "SKIP_COPY",
    "build_docker_run_argv",
    "docker_bin",
    "exec_image",
    "extract_artifacts",
    "forbidden_argv_reason",
    "forbidden_mount_reason",
    "guest_env",
    "image_present",
    "network_enabled",
    "probe_docker",
    "pull_image",
    "run_in_docker",
    "snapshot_workspace",
]
