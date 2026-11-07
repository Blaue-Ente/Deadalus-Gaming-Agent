---
name: shell
description: Use the sandboxed command center for pytest, npm, git, and project commands.
---

# SKILL: Shell command center

**When**: need to run tests, install deps, inspect files, or compile.

**How**:
- Native tool `run_command` or Studio Shell panel.
- Commands are argv (no shell by default). Pipes and `$(...)` are refused unless `DAEDALUS_SHELL_LEGACY=1`.
- cwd is jailed to product / playground / data. This is **not** a container: Python can still open host paths.
- On a public bind, the runner is off unless `DAEDALUS_SHELL_ISOLATION=1`.
- Secrets (`*API_KEY*`, tokens) are stripped from the child env.
- Blocked: sudo, rm -rf /, mkfs, shutdown.

Prefer `run_command` with a playground `slug` so builds happen in the project tree.
