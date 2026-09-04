---
name: game_canon
description: Study DOOM 3 BFG, Mindustry, Game Programming Patterns, tinyrenderer, and stb — then write original game code.
---

# SKILL: Game canon

**When**: "how do real games do X", 3D math, DOD, software raster, netcode, need a worked example.

**How**:
1. `game_canon({action: list})` then `study` with an `id`.
2. `open` an allowlisted file (local clone or GitHub raw). `clone` is optional and jailed under `$DAEDALUS_DATA/canon/`.
3. Steal *structure* (math types, tick loop, packed arrays). Never copy assets, trademarks, or paste the book into the Playground.

**Ids**: `doom3-bfg` · `mindustry` · `game-programming-patterns` · `tinyrenderer` · `stb`

**Pair with**: `math3d` for formulas, `game_lint` for C++/C#, `profiler` for frame logs.

**Anti-patterns**:
- Vendoring those trees into `daedalus/` or a player-facing game.
- Shipping a 1:1 Doom/Mindustry clone.
- Skipping `do_not_copy` in the study payload.
