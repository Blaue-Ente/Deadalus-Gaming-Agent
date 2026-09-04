# Game canon — OSS trees to study

Daedalus does not vendor these trees. `game_canon` lists them, can shallow-clone into `$DAEDALUS_DATA/canon/{id}/`, and may open an allowlisted file (local or GitHub raw). Use them as a мостра of structure. Write original code. Do not copy assets, trademarks, or book text into Playground games or this harness.

## doom3-bfg — https://github.com/id-Software/DOOM-3-BFG

GPL-3.0, large. Read idLib math (`Vector.h`, `Quat.h`, `Matrix.h`) and `List.h`. Lesson: game math is concrete structs + SIMD variants, not a framework of virtual `IVector`. Containers stay contiguous. Pair with `math3d` when you reimplement a formula.

## mindustry — https://github.com/Anuken/Mindustry

GPL-3.0. Read `NetClient.java` / `NetServer.java` / `Net.java`. Lesson: simulation does not assume a renderer; net is deltas on packed entity state. Do not drop GPL sources into an MIT slug without a license plan.

## game-programming-patterns — https://github.com/munificent/game-programming-patterns

Book source. License is restrictive (CC-BY-NC-ND for the prose). Study `book/data-locality.markdown`, `component`, `object-pool`, `game-loop`, `event-queue`. Name the pattern in your own comment and implement it yourself. Do not paste markdown into the product.

## tinyrenderer — https://github.com/ssloy/tinyrenderer

Small software rasterizer. Lesson files stay tiny: barycentric fill, z-buffer, then textures. Verify projection with `math3d` `look_at` / `perspective`. Do not ship the tutorial as the game.

## stb — https://github.com/nothings/stb

Single-header C. `stb_ds.h` is the DOD cousin: stretchy buffers, caller-owned memory. `README.md` states licenses per file. Do not gulp 8k-line headers into the harness; vendor into a game only with the license comment intact.

## glm — https://github.com/g-truc/glm

GLSL-shaped C++ math (`vec3`, `mat4`, `quat`). Default when Daedalus writes a native engine or shader-side CPU code. Pair with `math3d` `claimed`. Do not vendor the whole tree into this harness.

## directxmath — https://github.com/microsoft/DirectXMath

SIMD `XMVECTOR` / `XMMATRIX` for Windows / D3D. Not the Godot or Three.js default.

## tracy — https://github.com/wolfpld/tracy

C++ zone profiler. Export CSV → `profiler` `kind=tracy`. RenderDoc frame captures are owner-side; Daedalus does not run that GUI in this cloud.

## Workflow

`list` → `study` → `open` a file → write new code in the slug → `spatial` / `game_lint` / `math3d` / `profiler`. `clone` is optional. Offline: study notes still work; fetch/clone do not.
