---
name: spatial_simd
description: Use spatial(advise) instead of O(N²) collision; SIMD/SoA on native hot paths; GLM or DirectXMath in C++.
---

# SKILL: Spatial partitioning + SIMD

**When**: many entities, physics, raycasts, "slow when there are lots of enemies", C++/SIMD, cache misses.

**How**:
1. `spatial({action: advise, n, dim: 2|3})` before writing collision. N≤24 may brute-force; above that grid / quadtree / octree / BVH.
2. `spatial({action: aabb, a_min, a_max, b_min, b_max})` for overlap. `math3d` `ray_aabb` / `ray_triangle` for traces.
3. `spatial({action: scan, slug})` after code — nested `for` + distance is a fail.
4. C++: GLM (GLSL-like) or DirectXMath (SIMD on Windows). `game_canon` ids `glm` / `directxmath`. Then `game_lint`.
5. SIMD = SoA arrays + engine/Burst/GLM. Hand intrinsics only after `profiler` / Tracy CSV.

**Anti-patterns**:
- Every object vs every object in a 3D sandbox.
- Inventing `class Vec3` in C++ when GLM exists.
- Claiming SSE in a Phaser/Three.js file.
