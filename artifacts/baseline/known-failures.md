# Known failures — P0.00 golden baseline

This file records current behavior. It is not permission to delete, skip, or weaken tests.

## Environment

- Generated at: 2026-09-11T23:27:36.004408+00:00
- Git commit: `80b8b0e1afc3aee3dfa1cd0cfa713007df78105d`
- Git branch: `p0/15-run-finalizer`
- Python: 3.12.3
- Extras installed: `dev` only (no playwright / computer / gui)
- `DAEDALUS_STANDALONE=1`
- Honesty suite: `python -m daedalus.game_agent_bench --no-persist --json`

## Pytest summary

- Collected / reported tests: 865
- Passed: 864
- Failed: 0
- Errors: 0
- Skipped: 1
- Duration (s): 61.556
- Pytest exit code: 0
- Coverage line-rate (`daedalus`, observational): 48.65%

- Pytest last line: `864 passed, 1 skipped, 1 warning in 61.59s (0:01:01)`

## Failed or errored tests

No product test failures or errors were recorded.
## Skipped tests

- `tests/test_gameplay_bot.py::test_chromium_tiny_canvas_bot` — could not import 'playwright': No module named 'playwright'

## Honesty suite (`game_agent_bench`)

- ok: True
- passed/total: 15/15
- bake_off: False
- ms: 29

No honesty-suite case failures.
