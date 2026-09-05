# Build loop progress — finished products, not tool lists

Owner-approved plan (2026-09-05). Root cause from the live eval: Daedalus had the
tools and the plan, but nobody held the wheel. The Definition of Done ran once,
after the reply was final, and its verdict was appended as text nobody could act
on. There was no closed *write → verify → fix* loop.

Order of work: **S1 → S3 → S4 → S2 → S6 → S5**. One stage per merge request. The
next stage starts only after this file records the previous one and the owner
approves.

## Status

| Stage | Title | Status |
|-------|-------|--------|
| S1 | Closed build loop instead of post-mortem DoD | **This MR** — awaiting owner approval |
| S3 | Tool results as navigation (paths, slug, NEXT) | Pending |
| S4 | Write-time quality gate (syntax) + arcade lint | Pending |
| S2 | Kernel-run scaffold/verify, phase-gated tools | Pending |
| S6 | Router: negations, explicit merge only | Pending |
| S5 | One build layer, model roles, wrap-up sanitizing | Pending |

## Evidence the plan is built on (live eval, 2026-09-05, Nemotron 3.5 Lightning)

| Case | What happened |
|------|---------------|
| Snake | Files written in 3 rounds, then 8 rounds of `run_command`/`list_dir`/`find /workspace` hunting for them (jail BLOCKED). `game.js` had a duplicate `let gameLoopId`. Never opened in a browser. Reply ended with a DoD sticker the model never saw. |
| SLERP (mixed prompt) | Router merged `math3d+crowd+arcade` on negated keywords. 5× `list_dir`, zero `write_file` with content, zero `math3d`. |
| SLERP (clean prompt) | Router chose `playground.math3d` correctly. Model emitted `<need_list_dir>` XML, one `list_dir`, then quit. No files. |

## S1 — Closed build loop (this MR)

**Intent:** the plan drives the model. When the model goes quiet, the kernel
scores the plan *inside* the native round loop and, while fix rounds remain,
injects PLAN PROGRESS as the next user turn. The turn ends when the plan is
complete or the budget is spent — and the summary says which.

**Shipped**

- `daedalus/build_loop.py` — `BuildLoop`: observes every native tool result,
  marks plan steps `✓ / ✗ / ☐`, captures the Playground slug, computes a
  `BuildVerdict` (job contract + plan score + unrecovered failures), renders
  `PLAN PROGRESS` (nudge) and `BUILD LOOP` (summary for the persona turn).
- `native_tools.run_native_discovery_pass(loop=…)` — a quiet round no longer
  returns; `loop.on_quiet()` decides. Each nudge grants `ROUNDS_PER_FIX` (2)
  extra model rounds. Budget exhaustion while still calling tools is recorded
  as `round budget exhausted`.
- `job_contract.evaluate(strict=True)` — kernel mode: coding task by
  construction, plan scored without a "готово" claim.
- `job_contract.unrecovered_failure_indexes()` — a failed call repaired by a
  later successful call of the same tool is no longer a permanent blocker.
- `agent.py` — the Playground/build branch attaches a `BuildLoop`, appends its
  `log_row()` to the tool log and its `summary_block()` to the persona context.
- `DAEDALUS_BUILD_FIX_ROUNDS` (default 3; `0` = old passive behaviour).

**Done when (acceptance)**

| Criterion | How we know |
|-----------|-------------|
| Quiet round with incomplete plan → PLAN PROGRESS, model continues, plan completes | `test_quiet_round_with_incomplete_plan_gets_plan_progress` |
| Fix budget bounds the loop; summary is honest | `test_loop_stops_when_fix_budget_is_spent` |
| `fix_rounds=0` keeps the previous behaviour | `test_fix_rounds_zero_keeps_old_behaviour` |
| Missing required step is named with args (`math3d op=quat_slerp`) | `test_slerp_nudge_names_the_missing_math3d_step` |
| Budget exhausted mid-build is reported, not hidden | `test_budget_exhausted_while_still_calling_tools_is_reported` |
| Repaired failure is not a blocker | `test_failed_call_repaired_later_is_not_a_blocker` |
| Strict contract scores the plan without a done claim | `test_strict_contract_scores_plan_without_done_claim` |
| Agent wiring | `test_agent_attaches_build_loop_to_the_native_pass` |

**Not in S1:** kernel-run `playtest`/`browser` after the last write (S2),
syntax gate on `write_file` (S4), path/slug hints inside tool results (S3),
router negation handling (S6). The persona turn still runs after the build
turn (S5).

**What S1 changes for the snake case:** after the failed `run_command` the
model would have received `✗ run_command … No such file` + `NEXT: call
write_file with slug=snake-game`, instead of eight free rounds of exploring.
The syntax error in `game.js` is S4's job.

## How to approve S1

1. Review `daedalus/build_loop.py`, the `loop=` hook in
   `native_tools.run_native_discovery_pass`, and the `strict` contract.
2. `DAEDALUS_STANDALONE=1 python3 -m pytest tests/test_build_loop.py tests/test_tool_router.py -q`.
3. Reply that S1 is approved. S3 starts next.
