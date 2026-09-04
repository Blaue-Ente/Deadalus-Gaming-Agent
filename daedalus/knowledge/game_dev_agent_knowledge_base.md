# Game-dev agent knowledge base

Industry tools and ideas Daedalus must *use* while building games — not trivia to recite. Pair every section with a native tool when one exists (`math3d`, `spatial`, `game_lint`, `profiler`, `game_canon`).

## 1. Vector / matrix libraries (do not roll your own in C++)

### GLM — https://github.com/g-truc/glm

OpenGL Mathematics. Header-only C++ API that mirrors GLSL (`vec3`, `mat4`, `quat`, `lookAt`, `perspective`, `mix`/`slerp`). Default choice when Daedalus writes an engine, a native plugin, or GLSL-side CPU code. Column-major like GLSL. After you pick GLM types, still verify a suspicious SLERP or ray with `math3d` (`claimed`) before pasting numbers into shaders.

Study: `game_canon` id `glm` (README). Do not vendor the whole tree into the harness.

### DirectXMath — https://github.com/microsoft/DirectXMath

Microsoft SIMD math (`XMVECTOR`, `XMMATRIX`) for Windows / Xbox / Direct3D. Use when the slug is native Win64 / D3D, not as a default for Godot or Three.js. Align loads (16-byte). Prefer `XMVector3Dot` / `XMMatrixLookAtRH` over scalar loops.

Study: `game_canon` id `directxmath`.

### Engine-native math (Daedalus stacks)

- **Godot 4.3:** `Vector3`, `Basis`, `Transform3D`, `Quaternion.slerp`. Do not invent `KinematicBody`.
- **Unity:** `Unity.Mathematics` (`float3`, `quaternion`) for Burst/Jobs; `UnityEngine.Vector3` on the main thread only.
- **Three.js / WebGPU:** `THREE.Vector3`, `Quaternion.slerp`, `Matrix4.lookAt`. Web has no GLM; do not fake SSE in JavaScript.

`math3d` is the referee when the formula is custom or the engine helper is in doubt.

## 2. Profiling and graphics capture

### RenderDoc — https://renderdoc.org/

Frame capture for GL/Vulkan/D3D/Metal. Inspect draw calls, textures, pipeline state, GPU timings. Daedalus does not run RenderDoc in this cloud. The owner captures a `.rdc` locally. The agent reads exported notes, GPU timing CSV, or a written list of slow passes — never claims it pressed Capture on the server.

### Tracy Profiler — https://github.com/wolfpld/tracy

Nanosecond CPU/GPU/memory zones for C++. Instrument hot functions (`ZoneScoped`), export a CSV/trace, then `profiler({action: parse, kind: tracy, path})`. Fix the heaviest named zones first (sort by duration). Do not sprinkle Tracy macros into a Vite racer.

Study: `game_canon` id `tracy`.

### Other logs Daedalus already parses

Unity Test / NUnit XML, Chrome `traceEvents`, Godot/Unity/generic FPS lines — `profiler` `kind=auto`. Unity `-runTests` is not Play.

## 3. Spatial partitioning (never O(N²) broadphase)

Brute-force “every object vs every object” is O(N²). At N≈20 a snake can brute-force. At N≈200+ on a tick, pick a structure:

| Structure | When |
|---|---|
| Uniform grid | Similar-sized objects, bounded world (bullets, boids, arcade) |
| Quadtree (2D) | Sparse 2D, wildly different sizes |
| Octree (3D) | Sparse 3D world, culling / physics |
| BVH | Ray tracing, static or slowly updated meshes, nearest-hit |
| Sweep and prune | Many moving AABBs along one axis |

Call `spatial({action: advise, n, dim})` before writing nested collision loops. `action: aabb` tests overlap. After code exists, `scan` flags nested `for`+distance patterns.

Books to *study* (do not paste): Ericson, *Real-Time Collision Detection*; the data-locality chapter via `game_canon` id `game-programming-patterns`.

## 4. SIMD (Single Instruction, Multiple Data)

One instruction, four/eight floats (SSE/AVX). Pays off on tight loops over SoA arrays (all `x`, then all `y`) — that is Data-Oriented Design. GLM and DirectXMath already wrap this. Godot/Unity Jobs/Burst do too. Hand-written intrinsics only on a measured hot path after `profiler` / Tracy.

JavaScript/TypeScript games: no AVX. Use typed arrays and avoid pointer graphs; SIMD is a C++/C# native story.

## 5. How Daedalus must behave

1. Game task → follow the injected GAME ENGINEER CONTRACT (not only this file sitting in RAG).
2. Custom 3D math → `math3d` with `claimed`.
3. Many entities / physics / rays → `spatial` advise, then grid/quad/octree/BVH in *original* code.
4. C++ vectors → GLM or DirectXMath, then `game_lint`.
5. Slow frame → `profiler` (Tracy CSV if the owner produced one). RenderDoc is owner-side capture.
6. Need a worked example → `game_canon` `study`/`open`. Never copy assets or book text into a Playground game.
