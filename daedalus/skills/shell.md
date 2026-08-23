---
name: shell
description: Use the sandboxed command center for pytest, npm, git, and project commands.
---

# SKILL: Shell command center

**When**: need to run tests, install deps, inspect files, or compile.

**How**:
- Native tool `run_command` or Studio Shell panel.
- cwd is jailed to product / playground / data.
- Secrets (`*API_KEY*`, tokens) are stripped from the child env.
- Blocked: sudo, rm -rf /, mkfs, shutdown.

Prefer `run_command` with a playground `slug` so builds happen in the project tree.
