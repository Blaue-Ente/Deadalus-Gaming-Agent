# Known failures — P0.00 golden baseline

This file records current behavior. It is not permission to delete, skip, or weaken tests.

## Environment

- Generated at: 2026-11-06T17:16:06.095152+00:00
- Git commit: `b03aa6d78bf1a5c34d5c67d4a29921022703a918`
- Git branch: `cursor/p1-harness-6-7-9-6daf`
- Python: 3.12.3
- Extras installed: `dev` only (no playwright / computer / gui)
- `DAEDALUS_STANDALONE=1`
- Honesty suite: `python -m daedalus.game_agent_bench --no-persist --json`

## Pytest summary

- Collected / reported tests: 599
- Passed: 599
- Failed: 0
- Errors: 0
- Skipped: 0
- Duration (s): 37.362
- Pytest exit code: 0
- Coverage line-rate (`daedalus`, observational): 42.64%

- Pytest last line: `599 passed in 37.42s`

## Failed or errored tests

No product test failures or errors were recorded.
## Skipped tests

No skipped tests were recorded.

## Honesty suite (`game_agent_bench`)

- ok: True
- passed/total: 15/15
- bake_off: False
- ms: 33

No honesty-suite case failures.
