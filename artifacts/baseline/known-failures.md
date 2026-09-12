# Known failures — P0.00 golden baseline

This file records current behavior. It is not permission to delete, skip, or weaken tests.

## Environment

- Generated at: 2026-09-12T00:07:31.553892+00:00
- Git commit: `9aa65ad924bd062bf53ecfdb84679400341097ab`
- Git branch: `p0/18-docker-sandbox`
- Python: 3.12.3
- Extras installed: `dev` only (no playwright / computer / gui)
- `DAEDALUS_STANDALONE=1`
- Honesty suite: `python -m daedalus.game_agent_bench --no-persist --json`

## Pytest summary

- Collected / reported tests: 915
- Passed: 911
- Failed: 0
- Errors: 0
- Skipped: 4
- Duration (s): 49.849
- Pytest exit code: 0
- Coverage line-rate (`daedalus`, observational): 49.05%

- Pytest last line: `911 passed, 4 skipped, 1 warning in 49.86s`

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
- ms: 25

No honesty-suite case failures.
