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
| S1 | Closed build loop instead of post-mortem DoD | **Done** — MR !48 (`cursor/build-loop-s1-948f`) |
| S3 | Tool results as navigation (paths, slug, NEXT) | **This MR** — awaiting owner approval |
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

## S1 — Closed build loop (done)

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

## S3 — Tool results as navigation (this MR)

**Intent:** the snake run lost eight rounds because `write_file OK
snake-game/game.js` said nothing about *where* that is, `run_command
cwd=snake-game` silently ran in the product root, and the jail message only
said what was forbidden. Every Playground tool result now tells the model the
project dir, the preview URL, and that `slug=` is the only address it needs;
the kernel fills a forgotten slug itself and appends the plan's NEXT step.

**Shipped**

- `native_tools.project_nav(slug, path)` — `dir:` / `preview:` / `tools: …
  slug=<slug> (not cwd, not /workspace)`. Appended to `playground_new` and
  `write_file` results, to `read_file`/`list_dir` failures, and to the jail
  text when the slug is known.
- `read_file` and `list_dir` accept `slug` (project-relative path); schemas and
  `tool_catalog` hints updated. The harness-listing refusal now says *use
  `list_dir slug=<project>`*.
- `run_command` FAILED with `cwd` and no `slug` adds a one-line hint.
- `shell_center.project_cwd()` / `resolve_cwd()` — a `cwd` written as
  `<slug>`, `<slug>/sub`, or `/anything/<slug>` resolves into that Playground
  project instead of falling back to the product root.
- `BuildLoop.prepare()` — before a call: fills `slug` for slug-taking tools when
  the model omitted it and the turn already has a project; `run_command
  cwd=<slug>` becomes `slug=<slug>`. A deliberate `cwd` elsewhere in the jail
  is left alone. The correction is appended to the result, not silent.
- `BuildLoop.annotate()` — after a call: `NEXT (plan): <tool> <args>` while a
  required step is pending; `PLAN: all required steps done — stop and
  summarize` once none is. Idempotent.
- `run_native_discovery_pass` applies `prepare` → execute → `observe` →
  `annotate`; the tool log records the corrected arguments.

**Done when (acceptance)**

| Criterion | How we know |
|-----------|-------------|
| `cwd=snake-game` runs inside the project | `test_relative_cwd_named_after_project_lands_in_the_project` |
| Results name dir, preview, slug tools | `test_project_nav_names_dir_preview_and_slug_tools` |
| `read_file`/`list_dir slug=` work and are in the schema | `test_read_and_list_take_slug` |
| Jail/harness refusals point to the slug | `test_harness_listing_in_playground_session_points_to_slug` |
| Failed `cwd` command hints `slug=` | `test_failed_run_command_with_cwd_hints_slug` |
| Kernel fills the slug, leaves deliberate cwd alone | `test_loop_prepare_fills_slug_and_drops_cwd` |
| NEXT then stop, idempotent | `test_loop_annotate_points_next_then_stop` |
| End-to-end through the native pass | `test_native_pass_applies_prepare_and_annotate` |

**Not in S3:** syntax gate on write (S4), kernel-run verify (S2), router
negations (S6), single build layer (S5).

## How to approve S3

1. Review `project_nav` / `_jail_text` in `native_tools.py`,
   `project_cwd` in `shell_center.py`, and `prepare` / `annotate` in
   `build_loop.py`.
2. `DAEDALUS_STANDALONE=1 python3 -m pytest tests/test_tool_navigation.py tests/test_build_loop.py -q`.
3. Reply that S3 is approved. S4 (write-time quality gate) starts next.
