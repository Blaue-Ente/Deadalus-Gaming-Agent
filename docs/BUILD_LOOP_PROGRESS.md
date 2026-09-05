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
| S3 | Tool results as navigation (paths, slug, NEXT) | **Done** — MR !49 (`cursor/build-loop-s3-nav-948f`) |
| S4 | Write-time quality gate (syntax) + arcade lint | **Done** — MR !50 (`cursor/build-loop-s4-gate-948f`) |
| S2 | Kernel-run scaffold/verify, phase-gated tools | **This MR** — awaiting owner approval |
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

## S3 — Tool results as navigation (done)

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

## S4 — Write-time quality gate + arcade lint (done)

**Intent:** `write_file` reported OK for `game.js` with a duplicate
`let gameLoopId`, and the snake started at (20,20) on a 0..19 grid. A file that
does not parse is not written OK; canvas smells a parser cannot see are
reported next to the write so the model fixes them while the file is in hand.

**Shipped**

- `daedalus/write_gate.py` — `check_source(path, content, project_root)`:
  `.js/.mjs/.cjs` → `node --check` (line + message; heuristic duplicate
  top-level declaration check when node is absent); `.html` → inline
  `<script>` blocks through the same check with the real line offset,
  unclosed `<script>` is an error, a relative `<script src>` that does not
  exist yet is a *warning* (write order is legitimate); `.py` → `compile`;
  `.json` → `json.loads`; other types pass with `skipped`.
  `DAEDALUS_WRITE_GATE=0` disables.
- `game_lint.heuristic_web()` — `web-double-loop` (same tick started twice by
  `setInterval`), `web-mixed-loop` (`setInterval` + `requestAnimationFrame`
  on one function), `web-no-input` (canvas drawn, no key/pointer/touch
  listener), `web-off-grid-start` (start cell `(N,N)` with a grid constant
  `N`). `game_lint check` now handles `web` (`.js/.html`): syntax blockers
  from the gate, heuristics as warnings.
- `write_file` — after saving: `── write_file FAILED syntax slug/path ──`
  with `syntax: line N: …` and "fix and write_file again with the full
  content", or `── write_file OK … ──` with `gate: node --check ok` and
  `lint: …` warnings. The file is saved either way so `read_file` works.
- `job_contract._repair_key()` — a failed write of `game.js` is repaired only
  by a later good write of `game.js`, not by writing `index.html`. The build
  loop therefore keeps the bad file open and its nudge names it.
- TASK PLAN prompt line: "'FAILED syntax' means the step is still open".

**Done when (acceptance)**

| Criterion | How we know |
|-----------|-------------|
| Duplicate `let` fails with the line | `test_duplicate_let_is_a_syntax_failure_with_line` |
| Works without node (heuristic) | `test_heuristic_fallback_without_node` |
| Python / JSON / unknown types | `test_python_json_and_unknown_types` |
| HTML inline script line offset; missing src is a warning | `test_html_inline_script_reports_offset_line_and_missing_src` |
| Snake smells: double loop, no input, off-grid start | `test_web_heuristics_catch_snake_smells` |
| `game_lint check` covers web | `test_game_lint_check_handles_web` |
| `write_file` FAILED keeps the file, OK carries gate/lint | `test_write_file_fails_on_syntax_but_keeps_the_file` |
| Gate can be disabled | `test_write_gate_can_be_disabled` |
| Repair is per file | `test_failed_write_is_repaired_only_by_the_same_file` |
| Build loop keeps the bad write open, then completes | `test_build_loop_keeps_bad_write_open` |

**Not in S4:** GDScript parse (needs `GODOT_BIN`; the `engine` tool and S2
verify own that), CSS, TypeScript, kernel-run `playtest`/`browser` after the
last write (S2).

## S2 — Kernel-run scaffold and verify, phase-gated tools (this MR)

**Intent:** take the two decisions the cheap model kept getting wrong out of
its hands. It no longer creates or searches for the project — the kernel does,
deterministically, before the first model round. It no longer chooses whether
to verify — the kernel lints every written file and runs the playtest
heuristic when the plan's writes are in, and feeds blockers back as
unrecovered failures the model must fix. Exploration of the harness during a
build is redirected to the model's own project.

**Shipped**

- `BuildLoop.scaffold(session_id)` — for routes with a `playground_new` step:
  adopt the session's project (`playground:<slug>`) when it exists, else
  `create_project` with a deterministic name (`project_name_from_ask`: the
  game the owner named + `-game`/`-demo`/`-godot`, or `<playbook>-<hash4>`;
  an existing folder gets a hashed sibling, never a silent overwrite). The
  create step is ✓ before the model talks; the system prompt gets a
  `PROJECT (kernel created)` block with dir / preview / first `write_file`.
  On failure the model keeps `playground_new`. `DAEDALUS_BUILD_SCAFFOLD=0`
  disables.
- `BuildLoop.drop_tools()` + `tool_router.native_specs(rt, drop=…)` —
  `playground_new` is removed from the model's tool list once the kernel
  scaffolded (phase gate).
- `BuildLoop.run_verify()` — when all required steps are done and there are
  new writes since the last verify: `game_lint check` per written web/py
  file (blockers → failed `game_lint` observation, warnings → `lint:`),
  `playtest` heuristic (canvas / input / loop / win-lose hints), optional
  Chromium `verify_app` behind `DAEDALUS_BUILD_BROWSER_VERIFY=1`. Results
  appear under `VERIFY (kernel ran it — not the model)` in the nudge; a
  failed file gets `NEXT: fix game.js — write_file … the kernel re-verifies
  after your write`. A good re-write of the same file repairs it (`_repair_key`).
  `DAEDALUS_BUILD_VERIFY=0` disables.
- `BuildLoop.on_quiet()` is async and verifies before accepting; the native
  pass awaits it.
- `prepare("list_dir")` — during a build, `/workspace`, `daedalus/…`, `app/…`
  become `list_dir slug=<project> path=.` with a visible note; project paths
  just get the slug.
- `job_contract`: `playtest` / `game_lint` / `engine` / `browser` / `world`
  count as verification.
- `agent.py`: scaffold runs between consult and the tool subset; the PROJECT
  block joins the plan prompt.

**Done when (acceptance)**

| Criterion | How we know |
|-----------|-------------|
| Deterministic names; session slug parsing | `test_project_name_is_deterministic_and_named_after_the_game` |
| Kernel creates the project, step ✓, `playground_new` gated, collision → hashed | `test_kernel_scaffold_creates_the_project_and_gates_playground_new` |
| Existing session project is adopted | `test_kernel_scaffold_adopts_the_session_project` |
| No create step / disabled → no scaffold | `test_scaffold_skips_routes_without_a_create_step_and_when_disabled` |
| Scaffold failure leaves `playground_new` to the model | `test_scaffold_failure_leaves_playground_new_to_the_model` |
| Verify blocks a bad file, does not re-run without new writes, passes after the fix | `test_kernel_verify_blocks_bad_file_then_passes_after_fix` |
| Verify can be disabled | `test_verify_can_be_disabled` |
| Harness `list_dir` redirected to the project | `test_list_dir_during_build_is_redirected_to_the_project` |
| End-to-end: write → quiet → kernel verify → nudge → fix → complete | `test_native_pass_verifies_before_accepting` |
| Agent wiring | `test_agent_scaffolds_before_building` |

**Not in S2:** Godot `engine` verify for `kind=godot` (needs `GODOT_BIN`;
the plan step stays model-driven), LLM persona playtest (only the heuristic
runs — deterministic and free), browser verify on by default.

## How to approve S2

1. Review `scaffold`, `run_verify`, `prepare("list_dir")` in `build_loop.py`,
   the `drop=` gate in `tool_router.native_specs`, and the agent wiring.
2. `DAEDALUS_STANDALONE=1 python3 -m pytest tests/test_kernel_build_phases.py tests/test_build_loop.py -q`.
3. Reply that S2 is approved. S6 (router negations) is next.
