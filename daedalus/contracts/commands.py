"""Structured command request/result for the host runner and future backends.

P0.16 defines the shape. Defaults match today's ``shell_center.run_command``
(host argv, secret-stripped env, cwd jail, no network filter). Reserved
policies are not enforced here. Do not import agent, native_tools, or
shell_center.
"""
from __future__ import annotations

from enum import Enum
from typing import Protocol

from ._base import ContractModel


class EnvPolicy(str, Enum):
    STRIP_SECRETS = "strip_secrets"
    PASS_SECRETS = "pass_secrets"
    INHERIT = "inherit"


class NetworkPolicy(str, Enum):
    UNRESTRICTED = "unrestricted"
    DENY = "deny"
    ALLOWLIST = "allowlist"


class FilesystemPolicy(str, Enum):
    JAIL = "jail"
    PROJECT = "project"
    READONLY = "readonly"


class CommandRequest(ContractModel):
    executable: str = ""
    arguments: tuple[str, ...] = ()
    cwd: str = ""
    slug: str = ""
    timeout_s: int | None = None
    env_policy: EnvPolicy = EnvPolicy.STRIP_SECRETS
    network_policy: NetworkPolicy = NetworkPolicy.UNRESTRICTED
    filesystem_policy: FilesystemPolicy = FilesystemPolicy.JAIL
    raw_command: str = ""
    use_legacy_shell: bool = False


class CommandResult(ContractModel):
    ok: bool
    blocked: bool = False
    disabled: bool = False
    error: str = ""
    command: str = ""
    ran: str = ""
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    cwd: str = ""
    elapsed_ms: int = 0


class ExecutionBackend(Protocol):
    """Implemented by LocalBackend, FakeBackend, and DockerBackend."""

    def run(self, request: CommandRequest) -> CommandResult: ...


__all__ = [
    "CommandRequest",
    "CommandResult",
    "EnvPolicy",
    "ExecutionBackend",
    "FilesystemPolicy",
    "NetworkPolicy",
]
