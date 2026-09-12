---
name: game_agent_bench
description: In-process honesty suite plus hermetic game_v1 tasks. Not a vendor bake-off. Skip is not a playtest. Empty JSON is not a campaign. Live engines are opt-in.
---

# SKILL: GameAgentBench suite

**When**: owner asks whether Daedalus is honest enough to measure, mentions GameAgentBench, game_v1, bake-off, честност suite, or `python -m daedalus.game_agent_bench`.

**How**:
1. Honesty (default, 15 cases): `python -m daedalus.game_agent_bench` or `GET /api/v1/daedalus/bench`.
2. Game_v1 (20 hermetic tasks: 10 Godot / 4 Unity / 3 Blender / 3 build): `python -m daedalus.game_agent_bench --suite game` or `GET /api/v1/daedalus/bench?suite=game`.
3. Both: `--suite all`. Live engine overlays: `--live` or `DAEDALUS_BENCH_LIVE=1` (fail-closed without `GODOT_BIN` / `UNITY_PATH` / `BLENDER_BIN`).
4. Honesty cases stay in-process: skip ≠ ok, racer “ship” ≠ store, empty world/systems JSON fail closed, Unity without Editor is an honest refuse, explorer cannot write, native tools exist, guided racing gates do not block.
5. Results may land in `$DAEDALUS_DATA/bench/latest.json`. That is a local report, not a public leaderboard.
6. Chromium, Godot, Unity, and Blender are not required for the default honesty or hermetic game_v1 path.

**P0.22 release gate** (`scripts/p0_release_gate.py` — not this module):
- Honesty snapshot: `artifacts/baseline/benchmark-results.json` from `--no-persist --json` (15 cases, `bake_off=false`).
- Game_v1 snapshot: `--suite game --no-persist --json` (20 hermetic ids) and sibling `artifacts/baseline/game-benchmark-results.json`.
- Live rows are never required for release. `known-failures.md` is observational.
- Human merge of the P0 MR stack and GitLab branch protection are owner work, not this suite.

**Anti-patterns**:
- Shipping a vendor bake-off or a competitor leaderboard in this repo.
- Treating a skipped browser check as a playtest pass.
- Counting `--live` without binaries as a pass.
- Claiming we beat another product. Public docs name only Daedalus.
