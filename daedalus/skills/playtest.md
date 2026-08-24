---
name: playtest
description: Run MatrAIx-style simulated players on a Playground game before claiming it is done.
---

# SKILL: Playtest

**When**: a game (static / vite / Phaser) just built, or the owner says "тествай играта", "playtest", "готови ли сме".

**Pattern** (from MatrAIx Persona-8B, MIT): heterogeneous users, not one critic. Daedalus uses a **tiny built-in panel** (casual / core / kids / hardcore / impatient / non-gamer). We do **not** download the 1M HuggingFace coreset.

**How**:
1. Smoke + browser verify first.
2. Native tool `playtest` with the project `slug` (or Studio → Свят → Playtest).
3. Read `blockers` and `quotes`. Fix loop / controls / first 30s / win-lose.
   For racers also: wayfinding from distance, fail/damage, visible boost pads, risk on the gas.
4. Do not say "готово" while playtest `ship_ready` is false.

**Anti-patterns**:
- One LLM "looks fine" review is not a playtest.
- Do not vendor MatrAIx Docker / Harbor.
