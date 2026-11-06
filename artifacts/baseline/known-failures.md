# Known failures — P0.00 golden baseline

This file records current behavior. It is not permission to delete, skip, or weaken tests.

## Environment

- Generated at: 2026-11-06T16:02:19.666280+00:00
- Git commit: `d0e137ede8046b199fb4195df2886cbfa6ff7e4a`
- Git branch: `main`
- Python: 3.12.3
- Extras installed: `dev` only (no playwright / computer / gui)
- `DAEDALUS_STANDALONE=1`
- Honesty suite: `python -m daedalus.game_agent_bench --no-persist --json`

## Pytest summary

- Collected / reported tests: 590
- Passed: 589
- Failed: 0
- Errors: 0
- Skipped: 1
- Duration (s): 24.046
- Pytest exit code: 0
- Coverage line-rate (`daedalus`, observational): 41.97%

- Pytest last line: `589 passed, 1 skipped in 24.05s`

## Failed or errored tests

No product test failures or errors were recorded.
## Skipped tests

- `tests/test_gameplay_bot.py::test_chromium_tiny_canvas_bot` — could not import 'playwright': No module named 'playwright'

## Honesty suite (`game_agent_bench`)

- ok: True
- passed/total: 15/15
- bake_off: False
- ms: 33

No honesty-suite case failures.
