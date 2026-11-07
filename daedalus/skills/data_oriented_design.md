---
name: data_oriented_design
description: Layout game data in contiguous arrays (cache lines, SoA/AoS). Avoid pointer graphs on the hot path.
---

# SKILL: Data-Oriented Design

**When**: entities, particles, physics, net snapshots, "too many objects", cache misses, CPU frame spikes.

**How**:
1. Ask: what does the inner loop *touch*? Put those fields adjacent in memory (array of structs, or struct of arrays for SIMD).
2. Prefer `id` + packed components over `new Enemy()` graphs. Stable indices beat pointers that chase the heap.
3. Measure with `profiler` (parse FPS / Chrome trace / Unity XML). A beautiful class tree that misses cache still fails.
4. Study `game_canon` id `doom3-bfg` (idList / idVec) and `game-programming-patterns` file `book/data-locality.markdown` — then write **original** code.
5. After generating C++/C#, run `game_lint` (clang-tidy leak checks when installed).

**Rules of thumb**:
- Hot path: no virtual calls, no `FindObjectOfType` in Update, no per-frame `malloc`.
- Cold path (load, UI): objects are fine.
- Netcode state should be the same packed layout you simulate.

**Anti-patterns**:
- `List<GameObject>` as the world model.
- Inheritance for every enemy type on the 10k-unit path.
- Declaring DOD done because you named a class `ECS`.
