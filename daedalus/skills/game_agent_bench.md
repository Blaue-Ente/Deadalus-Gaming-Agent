---
name: game_agent_bench
description: In-process honesty suite for this harness. Not a vendor bake-off. Skip is not a playtest. Empty JSON is not a campaign.
---

# SKILL: GameAgentBench suite

**When**: owner asks whether Daedalus is honest enough to measure, mentions GameAgentBench, bake-off, честност suite, or `python -m daedalus.game_agent_bench`.

**How**:
1. Run `python -m daedalus.game_agent_bench` or `GET /api/v1/daedalus/bench`.
2. Cases are in-process: skip ≠ ok, racer “ship” ≠ store, empty world/systems JSON fail closed, Unity without Editor is an honest refuse, explorer cannot write, native tools exist, guided racing gates do not block.
3. Results may land in `$DAEDALUS_DATA/bench/latest.json`. That is a local report, not a public leaderboard.
4. Chromium, Godot, and Unity are not required for the default path.

**Anti-patterns**:
- Shipping a vendor bake-off or a competitor leaderboard in this repo.
- Treating a skipped browser check as a playtest pass.
- Claiming we beat another product. Public docs name only Daedalus.
