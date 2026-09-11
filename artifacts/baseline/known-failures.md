# Known failures — P0.00 golden baseline

This file records current behavior. It is not permission to delete, skip, or weaken tests.

## Environment

- Generated at: 2026-09-11T23:54:13.686835+00:00
- Git commit: `f2d7cee0e82a4d625c10dc6c4b8dd0c87c3914be`
- Git branch: `p0/17-execution-backend`
- Python: 3.12.3
- Extras installed: `dev` only (no playwright / computer / gui)
- `DAEDALUS_STANDALONE=1`
- Honesty suite: `python -m daedalus.game_agent_bench --no-persist --json`

## Pytest summary

- Collected / reported tests: 896
- Passed: 895
- Failed: 0
- Errors: 0
- Skipped: 1
- Duration (s): 64.393
- Pytest exit code: 0
- Coverage line-rate (`daedalus`, observational): 48.86%

- Pytest last line: `895 passed, 1 skipped, 1 warning in 64.40s (0:01:04)`

## Failed or errored tests

No product test failures or errors were recorded.
## Skipped tests

- `tests/test_gameplay_bot.py::test_chromium_tiny_canvas_bot` — could not import 'playwright': No module named 'playwright'

## Honesty suite (`game_agent_bench`)

- ok: True
- passed/total: 15/15
- bake_off: False
- ms: 25

No honesty-suite case failures.
