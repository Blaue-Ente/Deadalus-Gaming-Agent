"""Selectable command backends behind ``execute_command``.

LocalBackend wraps ``shell_center.execute_host`` (host ``subprocess.run``).
FakeBackend is test injection only. DockerBackend runs ``docker_sandbox``.
Do not import this module from agent.py.
"""
from __future__ import annotations

import os

from daedalus.contracts.commands import CommandRequest, CommandResult, ExecutionBackend

_override: ExecutionBackend | None = None


def _request_command(request: CommandRequest) -> str:
    return request.raw_command or ""


class LocalBackend:
    """Today's host path. ``subprocess.run`` stays on ``shell_center``."""

    def run(self, request: CommandRequest) -> CommandResult:
        from daedalus.shell_center import execute_host

        return execute_host(request)


class FakeBackend:
    """Programmed results for tests. Never calls subprocess or writes history."""

    def __init__(self) -> None:
        self.calls: list[CommandRequest] = []
        self._queue: list[CommandResult] = []
        self._default: CommandResult | None = None

    def queue(self, result: CommandResult) -> None:
        self._queue.append(result)

    def always(self, result: CommandResult) -> None:
        self._default = result

    def run(self, request: CommandRequest) -> CommandResult:
        self.calls.append(request)
        if self._queue:
            return self._queue.pop(0)
        if self._default is not None:
            return self._default
        command = _request_command(request)
        return CommandResult(
            ok=False,
            blocked=True,
            error="fake backend: no programmed result",
            command=command,
            exit_code=126,
        )


class DockerBackend:
    """Command sandbox. Fail-closed without docker / daemon / image. No Local fall-through."""

    def run(self, request: CommandRequest) -> CommandResult:
        from daedalus.docker_sandbox import run_in_docker

        return run_in_docker(request)


class _UnknownBackend:
    def __init__(self, name: str) -> None:
        self.name = name

    def run(self, request: CommandRequest) -> CommandResult:
        return CommandResult(
            ok=False,
            blocked=True,
            error=f"unknown execution backend: {self.name}",
            command=_request_command(request),
            exit_code=126,
        )


def set_execution_backend(backend: ExecutionBackend | None) -> None:
    global _override
    _override = backend


def reset_execution_backend() -> None:
    set_execution_backend(None)


def get_execution_backend() -> ExecutionBackend:
    if _override is not None:
        return _override
    name = (os.environ.get("DAEDALUS_EXEC_BACKEND") or "local").strip().lower()
    if name in ("", "local", "host"):
        return LocalBackend()
    if name == "docker":
        return DockerBackend()
    return _UnknownBackend(name)


__all__ = [
    "DockerBackend",
    "FakeBackend",
    "LocalBackend",
    "get_execution_backend",
    "reset_execution_backend",
    "set_execution_backend",
]
