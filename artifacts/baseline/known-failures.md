# Known failures — P0.00 golden baseline

This file records current behavior. It is not permission to delete, skip, or weaken tests.

## Environment

- Generated at: 2026-09-11T20:22:30.696662+00:00
- Git commit: `b24fe61ad717485939bfba90f5bb23644a746b67`
- Git branch: `p0/00-baseline`
- Python: 3.12.3
- Extras installed: `dev` only (no playwright / computer / gui)
- `DAEDALUS_STANDALONE=1`
- Honesty suite: `python -m daedalus.game_agent_bench --no-persist --json`

## Pytest summary

- Collected / reported tests: 580
- Passed: 579
- Failed: 0
- Errors: 0
- Skipped: 1
- Duration (s): 63.017
- Pytest exit code: 0
- Coverage line-rate (`daedalus`, observational): 41.93%

- Pytest last line: `579 passed, 1 skipped, 1 warning in 63.02s (0:01:03)`

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
