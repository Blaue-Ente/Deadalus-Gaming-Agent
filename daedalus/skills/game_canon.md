---
name: game_canon
description: Study OSS engines, Milo Yip / CIS roadmaps, engine-architecture samples, Chop Chop wiki — then write original game code. Never ingest ebook dumps.
---

# SKILL: Game canon

**When**: "how do real games do X", 3D math, DOD, software raster, netcode, events/FSM, engine architecture, study path.

**How**:
1. `game_canon({action: list})` then `study` with an `id`.
2. `open` an allowlisted file (local clone, GitHub raw, or `wiki/…`). `chunk` splits C++ class/struct or markdown headings for RAG.
3. `clone` is optional and jailed under `$DAEDALUS_DATA/canon/`. `game-program-books` clone/open is refused (copyrighted PDFs).
4. Steal *structure* (math types, tick loop, event channels, packed arrays). Never copy assets, trademarks, Chop Chop, or paste a book into the Playground.

**Ids**: `doom3-bfg` · `mindustry` · `game-programming-patterns` · `tinyrenderer` · `stb` · `glm` · `directxmath` · `tracy` · `milo-path` · `cis-roadmap` · `awesome-engine` · `engine-arch-samples` · `unity-chop` · `game-program-books` (refuse)

**Pair with**: `math3d` for formulas, `game_lint` for C++/C#, `profiler` for frame logs, `spatial` before O(N²).

**Anti-patterns**:
- Vendoring those trees into `daedalus/` or a player-facing game.
- Cloning GameProgramBooks or UOP1_Project into a slug.
- Skipping `do_not_copy` in the study payload.
