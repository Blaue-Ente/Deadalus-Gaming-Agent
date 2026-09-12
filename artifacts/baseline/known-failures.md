# Known failures — P0.00 golden baseline

This file records current behavior. It is not permission to delete, skip, or weaken tests.

## Environment

- Generated at: 2026-09-12T00:20:55.988365+00:00
- Git commit: `96c3917c5b13f0a2f0b9178a38c816d2f0622c49`
- Git branch: `p0/19-mcp-security`
- Python: 3.12.3
- Extras installed: `dev` only (no playwright / computer / gui)
- `DAEDALUS_STANDALONE=1`
- Honesty suite: `python -m daedalus.game_agent_bench --no-persist --json`

## Pytest summary

- Collected / reported tests: 935
- Passed: 931
- Failed: 0
- Errors: 0
- Skipped: 4
- Duration (s): 52.664
- Pytest exit code: 0
- Coverage line-rate (`daedalus`, observational): 49.59%

- Pytest last line: `931 passed, 4 skipped, 1 warning in 52.68s`

## Failed or errored tests

No product test failures or errors were recorded.
## Skipped tests

- `tests/test_docker_sandbox.py::test_live_python_add` — docker exec image not available
- `tests/test_docker_sandbox.py::test_live_readonly_rootfs` — docker exec image not available
- `tests/test_docker_sandbox.py::test_live_no_docker_socket` — docker exec image not available
- `tests/test_gameplay_bot.py::test_chromium_tiny_canvas_bot` — could not import 'playwright': No module named 'playwright'

## Honesty suite (`game_agent_bench`)

- ok: True
- passed/total: 15/15
- bake_off: False
- ms: 26

No honesty-suite case failures.
