"""Adapters between free-form ``run_command`` strings and CommandRequest/Result.

Do not import this module from agent.py. Peel stays on shell_center; this
module lazy-imports it to avoid an import cycle.
"""
from __future__ import annotations

import os
import shlex
from collections.abc import Mapping
from typing import Any

from daedalus.contracts.commands import (
    CommandRequest,
    CommandResult,
    EnvPolicy,
    FilesystemPolicy,
    NetworkPolicy,
)


def _pass_secrets_enabled() -> bool:
    return (os.environ.get("DAEDALUS_SHELL_PASS_SECRETS") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def request_from_legacy(
    command: str,
    *,
    cwd: str | None = None,
    slug: str = "",
    timeout: int | None = None,
) -> CommandRequest:
    """Build a CommandRequest from the live ``run_command`` string API."""
    from daedalus.shell_center import legacy_shell_enabled, peel_package_command

    raw = command if command is not None else ""
    peeled = peel_package_command(raw)
    effective = raw
    if peeled and peeled != raw.strip():
        effective = peeled

    use_legacy = legacy_shell_enabled()
    executable = ""
    arguments: tuple[str, ...] = ()
    if not use_legacy:
        try:
            argv = shlex.split(effective, posix=True)
        except ValueError:
            argv = []
        if argv:
            executable = argv[0]
            arguments = tuple(argv[1:])

    timeout_s = None if timeout is None else int(timeout)
    return CommandRequest(
        executable=executable,
        arguments=arguments,
        cwd=cwd or "",
        slug=slug or "",
        timeout_s=timeout_s,
        env_policy=EnvPolicy.PASS_SECRETS if _pass_secrets_enabled() else EnvPolicy.STRIP_SECRETS,
        network_policy=NetworkPolicy.UNRESTRICTED,
        filesystem_policy=FilesystemPolicy.JAIL,
        raw_command=raw,
        use_legacy_shell=use_legacy,
    )


def result_from_legacy(row: Mapping[str, Any]) -> CommandResult:
    """Typed result. Missing ``ran`` (timeout / OS-error / block) stays empty."""
    return CommandResult(
        ok=bool(row.get("ok")),
        blocked=bool(row.get("blocked")),
        disabled=bool(row.get("disabled")),
        error=str(row.get("error") or ""),
        command=str(row.get("command") or ""),
        ran=str(row.get("ran") or "") if "ran" in row else "",
        stdout=str(row.get("stdout") or ""),
        stderr=str(row.get("stderr") or ""),
        exit_code=int(row.get("exit_code") or 0),
        cwd=str(row.get("cwd") or ""),
        elapsed_ms=int(row.get("elapsed_ms") or 0),
    )


def result_to_legacy(result: CommandResult) -> dict[str, Any]:
    """Live ``run_command`` dict. Omit ``ran`` when the live path omitted it."""
    row: dict[str, Any] = {
        "ok": result.ok,
        "blocked": result.blocked,
        "disabled": result.disabled,
        "error": result.error,
        "command": result.command,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
        "cwd": result.cwd,
        "elapsed_ms": result.elapsed_ms,
    }
    if result.ran:
        row["ran"] = result.ran
    return row


__all__ = [
    "request_from_legacy",
    "result_from_legacy",
    "result_to_legacy",
]
