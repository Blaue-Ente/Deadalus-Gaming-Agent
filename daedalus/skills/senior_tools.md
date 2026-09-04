---
name: senior_tools
description: math3d + spatial (no O(N²)), game_lint, profiler (Tracy CSV), game_canon (GLM/Tracy).
---

# SKILL: Senior game-engineer tools

**When**: writing 3D/C++/C#/Unity/Godot gameplay, failing tests, frame spikes, "is this quaternion right?", lots of colliders.

**Tools** (native; also `python -m daedalus.<module>`):
1. **math3d** — named ops + optional `claimed` (stdlib reference; numpy cross-check if installed).
2. **spatial** — `advise` before nested collision (grid / quad / octree / BVH). `aabb` overlap. `scan` a slug for O(N²) loops.
3. **game_lint** — Python ruff; C++ clang-format/tidy when present + leak heuristics; C# Unity heuristics + `dotnet format`; GDScript Godot-3 API smells. `format` returns text, does not write.
4. **profiler** — NUnit/Unity Test XML, Chrome traces, **Tracy CSV** (`kind=tracy`), Godot/Unity/generic FPS. `unity_tests` needs `UNITY_PATH` and is **not** Play. RenderDoc is owner-side capture.
5. **game_canon** — GLM, DirectXMath, Tracy, idTech, Mindustry, tinyrenderer, stb. Steal structure, never copy.

**How**:
1. `spatial(advise)` / `math3d(claimed)` → write code (GLM in C++) → `game_lint` → parse Tracy/test logs on failure.
2. Missing clang-tidy/dotnet is an honest skip of that backend, not a leak pass.

**Anti-patterns**:
- `NEED_SANDBOX` with ad-hoc numpy instead of `math3d` for SLERP/rays.
- Every object vs every object when N is more than a handful.
- Claiming clang-tidy ran when `backends.clang-tidy.ran` is false.
- Unity `-runTests` as a playtest.
- Faking a RenderDoc capture in this cloud.
