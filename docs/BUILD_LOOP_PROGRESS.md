# Build loop progress — finished products, not tool lists

Owner-approved plan (2026-09-05). Root cause from the live eval: Daedalus had the
tools and the plan, but nobody held the wheel. The Definition of Done ran once,
after the reply was final, and its verdict was appended as text nobody could act
on. There was no closed *write → verify → fix* loop.

Order of work was **S1 → S3 → S4 → S2 → S6 → S5**. S1 and S2 are on `main`
(MR !57 squashed S2). S3 and S4 are this MR. S5–S6 stay pending.

## Status

| Stage | Title | Status |
|-------|-------|--------|
| S1 | Closed build loop instead of post-mortem DoD | **Done** — on `main` |
| S2 | Kernel-run visual proof (browser / Godot) | **Done** — on `main` (MR !57) |
| S3 | Tool results as navigation (paths, slug, NEXT) | **This MR** |
| S4 | Write-time quality gate (syntax) + arcade lint | **This MR** |
| S6 | Router: negations, explicit merge only | Partial — do not merge `harness.fix` into a Playground game; strip «не чети / don't read» windows. math3d+crowd+arcade on leftover keywords still pending |
| S5 | One build layer, model roles, wrap-up sanitizing | Pending |

## Evidence the plan is built on (live eval, 2026-09-05, Nemotron 3.5 Lightning)

| Case | What happened |
|------|---------------|
| Snake | Files written in 3 rounds, then 8 rounds of `run_command`/`list_dir`/`find /workspace` hunting for them (jail BLOCKED). `game.js` had a duplicate `let gameLoopId`. Never opened in a browser. Reply ended with a DoD sticker the model never saw. |
| SLERP (mixed prompt) | Router merged `math3d+crowd+arcade` on negated keywords. 5× `list_dir`, zero `write_file` with content, zero `math3d`. |
| SLERP (clean prompt) | Router chose `playground.math3d` correctly. Model emitted `<need_list_dir>` XML, one `list_dir`, then quit. No files. |

## Live eval 2026-09-08 — MiniMax M2.7 dungeon (not Snake)

OpenRouter `minimax/minimax-m2.7` (~$0.24/$0.96 per 1M). DeepSeek V3.2 was the cheaper fallback; MiniMax returned a real `playground_new` tool call in ~3s so we kept it. Runner: `scripts/live_eval_build.py`.

| Pass | What happened |
|------|---------------|
| 1 | MiniMax wrote `dungeon-keys/index.html` (~23KB). Prompt said «Не чети daedalus/agent.py» → router merged `playground.arcade+harness.fix`. NEXT became `read_file`. Native `browser` guessed `file://` / localhost. Kernel proof never ran. Budget exhausted. |
| 2 (after router + browser→visual_proof) | Playbook `playground.arcade` only. `playground_new` → `write_file` → `browser` mapped to kernel `visual_proof`. **complete=true** in ~126s. Frame `studio/proof/frame_000.png`: painted room, HP/KEY/ROOM HUD, player, slime, gold key. Not a blank screen. |

S2's proof is "the page painted", not a playtest bot. MiniMax put the slime in room 1 with the key (the ask wanted three rooms). That is model layout, not a skip-pass.

## Live eval 2026-09-08 — Claude Sonnet 4.6 3D SLERP (not Snake)

OpenRouter `anthropic/claude-sonnet-4.6` (~$3/$15 per 1M). Budget cap $2–3. Runner: `scripts/live_eval_build.py --better --preset slerp`. Playbook `playground.math3d`. Isolated `$DAEDALUS_DATA` under `/tmp`.

| Pass | What happened |
|------|---------------|
| 1 (~$1.02, 14 rounds, ~296s) | `math3d` quat_slerp + look_at (first claim mismatched rounding, then pass). Wrote `slerp-lookat/index.html` (~18KB, raw WebGL). Chromium proof painted the HUD (Bulgarian targets, verified quat `[0, 0.38268, 0, 0.92388]`). **WebGL 1 `uniformMatrix4fv(..., true)` is INVALID_VALUE** — canvas cleared, no geometry. `complete=false` because leftover `run_command` failures. `kernel_proof_ran=false` because the model called `browser` itself (mapped to `visual_proof`). |
| 2 (~$0.39, 7 rounds, ~177s, after S4 `webgl-transpose`) | Claude wrote column-major mats with `transpose=false`. **complete=true**. HUD painted again (keys 1/2/3, SLERP LOOK-AT). Headless Chromium still shows a dark viewport (no greybox in the frame) — kernel proof is HUD text, not a geometry bot. No `run_command` this time. |

Total model spend ~**$1.41**. Screenshots: `slerp-lookat-pass1-hud.png`, `slerp-lookat-pass2.png`.

S4 now fails `uniformMatrix*fv(..., true)` so the next write can ship WebGL 1-legal matrices. The remaining gap is "page painted ≠ 3D greybox in view" — that is still model camera/matrix quality, not a skip-pass.

## Live eval 2026-09-08 — Claude Sonnet 4.6 Vite/Phaser (Lumen Bloom)

Full **`Daedalus.chat`** path (`scripts/live_eval_agent_chat.py --better --preset lumen`), not kernel-only. Isolated `$DAEDALUS_DATA` under `/tmp/daedalus-live-eval-chat`. Effort **craft**. ~464s wall.

Prompt named `kind=vite` + Phaser 3 and avoided arcade (`2d`/`canvas`/`snake`) and mobile (`app store`) traps.

| Probe | Result |
|-------|--------|
| Playbook | `playground.generic` (default kind **static**; model still passed `kind=vite` on `playground_new`) |
| RAG | Chroma indexed 49 files → 63 chunks. Turn used **4** knowledge hits. `rank_bm25` missing → dense-only. |
| Navigator | Fast-path `code_change` with `NEED_PROPOSE` / `NEED_READ_FILE` — ADN leftover, not Playground tools |
| Skills disclosed | `arcade_craft`, `playtest`, `godot`, `senior_tools`, `spatial_simd`, `platform_acceptance_qa`, `game_canon` |
| Native cap | 10 LLM rounds. Stop: **round budget exhausted**. Kernel visual proof **did not run**. |
| `generate_image` | **8/8 FAILED**. Novita 403; OpenRouter returned `data:` URLs; `httpx.get` → `URL too long`. No PNG sprites. |
| Workers / subagents | **None**. After writes the persona is told not to emit `NEED_*`; it started a **bundle** and dumped leftover JS into the chat reply. |
| playtest / production / browser | **Not called**. `production` is not on the generic playbook subset. |
| Review-gate (Haiku) | Ran. Flagged incomplete `main.js`, missing assets. Closest thing to a "Mentor" on the chat path. Visual critic (`playground_quality`) lives on **autopilot only**. |
| DoD | `complete=false`. `browser` check stayed true because chat `evaluate()` does not pass `browser_ok`. |

**As shipped:** `index.html` has `<canvas id="lumen-canvas">`; `src/main.js` is still the Vite scaffold (`#app` innerHTML). Chromium: **blank near-black page**. Scenes `Boot.js` / `Menu.js` / `Play.js` exist; `Play.js` write was FAILED by `js-duplicate-binding` on two legal `for (let i` loops. `UniformGrid.js` never written. `npm install` never ran.

**Evaluator wiring (not Daedalus):** boot `main.js` + stub grid + `vite build` → menu and play **paint** with procedural canvas textures (Boot.js). That is a polished prototype, not App Store PNG art, and it is **not** what the agent left on disk.

Fixes from this eval: decode `data:` image URLs; sibling `for (let i` is not a SyntaxError.

## Mentor helm (2026-09-08)

Toggle: Studio **Mentor ON|OFF** (default on). `DAEDALUS_MENTOR=0` forces off. KV `adn:daedalus_mentor`.

Director: Studio model picker (Claude Fable 5.1, GPT-6 Astra, GPT-5.5 / 5.6, Opus 4.8, Kimi K3, GLM 5.3, Grok 4.6; Auto prefers Grok 4.6 on OpenRouter). Custom ⋯ for any provider+model. KV `adn:daedalus_mentor_choice`. Token ledger `adn:daedalus_mentor_spend` (default 40k/day). `DAEDALUS_MENTOR_LLM=0` is rules-only. The director does **not** ingest harness RAG — a compact inspect digest plus the model's own knowledge. Unambiguous Vite `#app` scaffold stays on the cheap `steer()` path.

Always on even when Mentor is off: Phaser.Game counts as a loop; `playground.vite` playbook; kind sync; proof on exhaust; retry cap; `browser_ok` on chat DoD; no `NEED_PROPOSE` on game fast-path.

When Mentor is on: PLAN PROGRESS NEXT comes from `daedalus/mentor.py` (inspect slug, RAG, forbid `generate_image` after two fails), overlayed by the director cache after quiet/exhaust. Extra recover rounds after native budget exhaust (twice, +2 each). Persona wrap-up tells the model not to dump leftover JS.

A **failed check returns to the writer**. Missing `node_modules` → `npm install`. Phaser `parent` id missing in HTML → `write_file` the mount. Missing `from './scenes/X.js'` → that path. `Phaser.Scene` without `import Phaser` → that file. `Phaser.AUTO` + `config.canvas` → `type: Phaser.WEBGL` (Vite ESM custom environment). Chromium uncaught / blank → `write_file`, **not** another `browser`. Mentor still does **not** write game files.

The director inspect digest includes a compact **tool graph** for the matched playbook (tools, NEXT edges, standards, recovery). After the native turn ends on a failure, `record_outcome` queues a pending correction in Studio **Lessons** (`origin=mentor_correction`) with that NEXT. Owner Approve makes it a live skill; the agent cannot approve itself. Harness code patches still go through `self_improve` / Proposals.

**Hole closed 2026-09-08 (cheap MiniMax M3 Glass Orchard):** Mentor *did* say RETURN TO WRITER → `npm install`. The writer called `npm install … 2>&1 | tail`. Isolation refused the pipe, so `node_modules` never appeared and proof stayed "renders, 1 uncaught". Vite proof also used to serve the **source** tree when `dist/` was missing (stub `<canvas>` counted as painted). Now: (1) `run_command` peels npm/pnpm/npx pipes/`&&` and runs the argv prefix; (2) Vite proof runs `npm install` + `vite build` before Chromium — compile fail is a failed check, not a skip. Cheap eval pool: DeepSeek V4 Flash, GLM 5.2, MiniMax M3, Poolside — not Claude.

## Live eval 2026-09-08 — Glass Orchard, Qwen 3.8 Max writer + Fable mentor

Full `Daedalus.chat` (`scripts/live_eval_agent_chat.py --craft-writer --preset glass --mentor-choice or-fable-51`). Effort craft. Isolated `$DAEDALUS_DATA`. Writer: `qwen/qwen3.8-max-0902`. Mentor: `anthropic/claude-fable-5.1` (1 call, 376 tokens, 9.1s, NEXT=`run_command`).

| | Result |
|--|--|
| Wall | ~2132s |
| Playbook | `playground.vite` |
| Disk | `Phaser.Game` + Boot/Menu/Play/End, PNG art, sfx, `node_modules`, `dist/` |
| `generate_image` | Novita 403 → OpenRouter; two writes died on `assets/assets/*.png` (filename already had `assets/`) |
| Compile | **ok** after PlayScene.js landed — Chromium loaded hashed `index-*.js` |
| Proof | 1 uncaught. First it was missing scenes; then `Must set explicit renderType in custom environment` (`Phaser.AUTO` + `canvas: getElementById('game-canvas')`). Frame stayed CSS chrome around an empty canvas. |
| Mentor | First skip `rules_unambiguous`; then Fable said rebuild, not `write_file` the import/`WEBGL` defect |
| DoD | `complete=false` (fix budget exhausted). Review-gate: persona dumped chain-of-thought, not an owner summary |

Evaluator (not the agent): the same tree with `import Phaser from 'phaser'` in each scene and `type: Phaser.WEBGL` **paints the Glass Orchard menu** (emblem, title, CLICK or PRESS ENTER, 0 uncaught, PNG+sfx 200). Kernel now steers those two writes. `generate_image` strips a leading `assets/` prefix. Staging copies `sfx/`.

## Live eval 2026-09-09 — Glass Orchard, Kimi K3 writer + Fable mentor

Full `Daedalus.chat` (`scripts/live_eval_agent_chat.py --model moonshotai/kimi-k3 --preset glass --mentor-choice or-fable-51`). Effort craft. Isolated `$DAEDALUS_DATA`. Writer: `moonshotai/kimi-k3`. Mentor: `anthropic/claude-fable-5.1` (2 LLM calls, 482 tokens, NEXT=`run_command` both times).

| | Result |
|--|--|
| Wall | ~731s |
| Playbook | `playground.vite` only (Phaser.WEBGL no longer merges `math3d`) |
| Disk | `Phaser.WEBGL` + `canvas: getElementById('orchard-canvas')`, Boot/Menu/Play/GameOver, every scene `import Phaser from 'phaser'`, PNG + sfx, `npm`, `dist/` |
| Proof | **ok=true**, 0 uncaught, frame painted (dark navy menu, “GLASS ORCHARD”, WASD, PRESS ENTER). Sprite is a cheap green square, not boutique art. |
| DoD | **`complete=true`**, `browser=true`, no blockers |
| Honest GDD gaps | Scenes are Menu/Play/GameOver, not Grove/Dusk/Result. No `Lantern.js` / moon timer / 12-fruit Dusk. Review-gate flagged that. Kernel DoD still complete because the page painted. |
| Proof wording | Summary used to say “0 visible DOM characters” — Phaser HUD is on the canvas. Kernel now says canvas/WebGL painted. |

Mentor still does not write game files. After this eval the director digest carries the playbook **tool graph**, and a failed check queues a pending correction (Studio Lessons, Approve).

## Live eval 2026-09-08 — Glass Orchard, cheap writer DeepSeek V4 Flash

Full `Daedalus.chat` (`scripts/live_eval_agent_chat.py --cheap --model deepseek/deepseek-v4-flash --preset glass --mentor-choice or-glm-53-flash`). Effort craft. Isolated `$DAEDALUS_DATA`. **No Claude.** Mentor: GLM 5.3 Flash (rules skipped the director — inspect already knew NEXT).

| | Result |
|--|--|
| Wall | ~497s |
| Writer | `deepseek/deepseek-v4-flash` (tool-call probe ok) |
| Playbook | `playground.vite` |
| Disk | `Phaser.Game` + arcade physics, Boot/Menu/Game/Win scenes, `node_modules`, `dist/`, 5 PNG assets |
| Compile gate | **ran** — Chromium loaded `dist/assets/index-*.js` |
| Vision | OpenRouter 404 `Filter by Image Support` → kernel **retried without PNG**, turn continued |
| Proof | 5 broken requests (`this.load.image(..., 'assets/*.png')` not in `dist/`). Dark frame. |
| Mentor | **RETURN TO WRITER** `write_file` (`rules_unambiguous`). Not another `browser`. |
| DoD | `complete=false` (budget exhausted). Writer then hunted with `playground_read` instead of fixing paths. |

Kernel staging now copies `assets/*.png` into `dist/assets/` after `vite build`. Re-opening that same `dist/` with the five PNGs staged: Chromium **ok**, 0 uncaught, 0 failed requests, canvas present. Favicon 404 is ignored. The remaining dark pixels are the slice's `#0a0a12` canvas, not a skipped check.

## Live eval 2026-09-08 — Glass Orchard, Mentor GLM 5.3 vs GPT-6 Astra

Full `Daedalus.chat` (`scripts/live_eval_agent_chat.py --better --preset glass --mentor-choice …`). Builder: Claude Sonnet 4.6. Effort craft. Isolated `$DAEDALUS_DATA`. Long GDD (kind=vite + Phaser 3, lantern harvest, Grove/Dusk). Playbook **playground.vite**.

| | GLM 5.3 director | GPT-6 Astra director |
|--|--|--|
| Wall | ~494s | ~946s |
| Mentor LLM | **timeout** (30s, 0 tokens, `llm_error` → rules fallback NEXT=`browser`) | **ok** 4.4s, 335 tokens, NEXT=`run_command` (npm/build) |
| `src/main.js` | `Phaser.Game` parent `#game` | `Phaser.Game` parent `#app` (HTML has `#game-canvas`, no `#app`) |
| Scenes on disk | Boot / Menu / Play / Over (catch-and-sort, not the lantern GDD) | Boot / Menu / Grove / Dusk + GameScene / Lantern / Harvest / Hud |
| `npm install` | never | yes (`node_modules` present) |
| PNG | 7 via `generate_image` (Novita 403 → OpenRouter `data:` decode **works**) | 4 orbs + bg generated; Boot then **ignored** them (procedural canvas textures) |
| Proof | dark frame, 1 uncaught (no Phaser npm) | dark frame, 1 uncaught (missing `#app` mount) |
| DoD | `complete=false`, browser fail | `complete=false`, browser fail |
| Persona | leftover `NEED_` XML for Grove.js that was never written | claimed the game runs |

Kernel visual proof ran on exhaust both times. Mentor still does not write files. GLM-5.3 hidden reasoning did not return JSON inside 30s — rules helm then sent the model to `browser` instead of `npm install`. Astra's steer (`run_command`) is the right next move; the blank frame is a mount mismatch the director digest does not currently see.

## S1 — Closed build loop (done)

The plan drives the model. Quiet + incomplete → PLAN PROGRESS inside the native
round loop. See `daedalus/build_loop.py`. `DAEDALUS_BUILD_FIX_ROUNDS` (default 3).

## S2 — Kernel-run visual proof (done on main)

**Intent:** after the last write on a visual kind, the kernel opens the running
game. The model does not get to skip `browser` / `engine`. Skip is not success.
Frames land in `studio/proof/` and are injected as vision on the persona turn.

Shipped in `daedalus/visual_proof.py` + `BuildLoop.wants_kernel_proof()`.
`DAEDALUS_VISUAL_PROOF=0` restores the pre-S2 stop.

## S3 — Tool results as navigation (this MR)

**Intent:** after a write, the model hunted with `list_dir` / `find /workspace`.
S1 already injects `NEXT: call write_file with slug=…` on PLAN PROGRESS. S3 puts
the same breadcrumb on **every** Playground tool result, not only write/new/failed
shell.

**Shipped**

- `_nav_hint` in `native_tools.py` — `playground/projects/{slug}/`, do not find
  `/workspace`, NEXT tool with slug.
- `read_file` (ok and FAILED), `list_dir` (including harness-refused listings),
  `math3d`, `spatial`, `game_lint`, `engine`, `visual_proof`.
- `math3d` mismatch is **FAILED** (not a named MISMATCH that the loop treated as ok).
  NEXT is another `math3d` call, then `write_file`.
- Successful `math3d` says NEXT `write_file` with the verified numbers.

**Done when (acceptance)**

| Criterion | How we know |
|-----------|-------------|
| Failed `read_file` names the slug path | `test_read_file_failed_names_playground` |
| Successful `read_file` still names NEXT write_file | `test_read_file_ok_names_playground` |
| `list_dir /workspace` in a Playground session names projects | `test_list_dir_harness_refused_names_playground` |
| `math3d` ok → NEXT write_file | `test_math3d_ok_names_next_write` |
| `math3d` mismatch is FAILED | `test_math3d_mismatch_is_failed` |
| `spatial` / `game_lint` name slug | `test_spatial_and_game_lint_name_slug` |

## S4 — Write-time quality gate (this MR)

**Intent:** Snake's `game.js` had `duplicate let gameLoopId`. `game_lint` had no
JS/HTML parser. The gate runs inside `write_file` after the bytes hit disk:
FAILED so the loop's NEXT is a corrected `write_file`, not eight hunting rounds.

**Shipped**

- `daedalus/write_gate.py` — `DAEDALUS_WRITE_GATE=0` restores the old write.
- `game_lint` JS/HTML: duplicate `let`/`const` in the same brace scope, unbalanced
  delimiters, inline `<script>` in HTML. Python `ast.parse` is a blocker even
  without ruff.
- Arcade lint on main web files (`index.html`, `game.js`, …): missing `<canvas>`
  / `requestAnimationFrame`.
- File stays on disk so `read_file` can see the defect.

**Done when (acceptance)**

| Criterion | How we know |
|-----------|-------------|
| Duplicate `let gameLoopId` → write FAILED | `test_write_file_duplicate_let_returns_failed` |
| Clean canvas + rAF → write OK | `test_write_file_clean_canvas_ok` |
| `DAEDALUS_WRITE_GATE=0` skips | `test_write_gate_off_skips` |
| JS duplicate is a `game_lint` blocker | `test_game_lint_html_duplicate_let` |

## How to review S3+S4

1. `DAEDALUS_STANDALONE=1 python3 -m pytest tests/test_write_gate.py tests/test_visual_proof.py tests/test_build_loop.py tests/test_tool_router.py tests/test_or_free_preset.py -q`
2. Live 3D: `scripts/live_eval_build.py --better --preset slerp --max-rounds 12`

## Research

Internal study `docs/GODOGEN_STUDY.md`. Steal: pixels of the running game.
Keep: playbooks, scaffolds, build loop. Do not delete the kernel.

## S5 slice — Writer assist (Off / Auto / Kernel)

Kernel Phaser skeleton so a weak writer still *boots*. Mentor does not write
`index.html` / `src/main.js`. Default **Off** (Vite Hello). Studio segmented
control: Auto | Off | Kernel. `DAEDALUS_WRITER_ASSIST` locks the process.

- **Off** — today's Vite `#app` Hello (racer still wins on F-Zero briefs).
- **Auto** — Phaser seed when `kind=vite` and the brief names Phaser.
- **Kernel** — Phaser seed for every new Vite project that is not a racer.
  Files: `#game`, `Phaser.WEBGL`, `import Phaser from 'phaser'`, Boot/Menu/Play.

Do **not** reuse `playground_game_scaffold.startGame(parent='app')` / `Phaser.AUTO`.

A/B runner: `scripts/sim_writer_assist.py --pair` (Ember Walk, Mentor off).

### Live A/B 2026-09-09 — Ember Walk, `cohere/north-mini-code:free`

Same writer, Mentor **off**, craft, isolated `$DAEDALUS_DATA`. Probe ok in ~4s.
Bit Flows’ free-coder list is stale (`qwen/qwen3-coder:free` and
`poolside/laguna-m.1:free` are gone). Do not use `openrouter/free`.

| Cell | Wall | Proof | What landed |
|------|------|-------|-------------|
| **Off** | 264s | **black frame** | Model wrote Phaser.WEBGL in one `src/main.js`, npm install + build. `index.html` used `<canvas id="game">` as parent — Chromium WEBGL stays black. No `src/scenes/`. DoD complete=false (round budget). |
| **Kernel** | 173s | **Menu painted** | Seed `#game` div + WEBGL + Boot/Menu/Play. Proof shows title Ember Walk, “One playable slice.”, “Enter — begin”. Writer filled `Play.js` with cursor keys + orbs. Overwrote `main.js` (dropped `scale.FIT`; Play uses arcade physics). DoD complete=false (round budget / review-gate). |

**Where to help more (not done in this MR):** stop the writer swapping `#game` for `<canvas>`; keep arcade physics + `scale.FIT` if they rewrite `main.js`. Mentor already steers Vite Hello; it does not yet lock the mount node.

**Owner pick:** Studio stays **Off** until you flip it. **Auto** is the conservative default (Phaser briefs only). **Kernel** if every new Vite project should boot as Phaser.

### Crew protocol (design — not shipped)

Weak/cheap writers need a **program on disk**, not more chat. Mentor already
says NEXT + RETURN TO WRITER; it does not write files and does not spawn a
company. `NEED_WORKER` is text-only (no tools). `spawn_subagent` can write,
but after the native build the persona is told **not** to emit NEED_*.

Wrong: N weak models invent an org chart and talk to each other (they drop
the thread). Right: kernel owns `studio/crew.json` tickets; one current item;
exclusive `files[]`; verify is Chromium/compile, not another LLM opinion.

Same Studio family as Writer assist (`off | auto | crew`), not a second
product. Game vs app is the **playbook** (`playground.vite` vs fastapi/static),
not a Mentor debate.

### Live 2026-09-09 — Kernel + Mentor GLM 5.3 Flash, same cheap writer

`scripts/sim_writer_assist.py --assist kernel-scaffold --mentor-choice or-glm-53-flash`

| | Kernel alone (prior cell) | Kernel + Mentor |
|--|---------------------------|-----------------|
| Wall | 173s | 276s |
| Proof | Menu painted | Menu painted (same seed) |
| Play.js | writer filled orbs + cursors | **still the stub** “fill this scene” |
| Mentor | off | LLM `action=ship` — `kernel_proof_ran, no proof_failed, loop_ok → ship` |

Mentor told the writer to **stop** because Chromium painted the seed Menu.
Painted ≠ playable. This is why a ticket file must exist: Play.js not stub,
WASD, 3 orbs, then ship. Without that program, a director on a weak writer
can hurt.

OpenRouter pick for the first sim (tool-call probe 2026-09-09):

| Role | Model | Probe |
|------|--------|-------|
| Free writer | `cohere/north-mini-code:free` | ok, ~1.7s |
| Free writer alt | `nex-agi/nex-n2.5-mini:free` | ok, ~1.5s |
| Free writer alt | `poolside/laguna-xs-2.1:free` | ok, ~10s |
| Paid fallback | `qwen/qwen3-coder-30b-a3b-instruct` | ok, ~2.4s |
| Skip as writer | `nvidia/nemotron-3-ultra-550b-a55b:free` | too slow |
| Skip | `openrouter/free` | inconsistent routing |

Live A/B results land in this section after `sim_writer_assist.py --pair`.
