---
name: senior_tools
description: math3d validator, game_lint (ruff/clang-tidy/Roslyn), profiler log parser, Unity Test Framework CLI.
---

# SKILL: Senior game-engineer tools

**When**: writing 3D/C++/C#/Unity/Godot gameplay, failing tests, frame spikes, "is this quaternion right?".

**Tools** (native; also `python -m daedalus.<module>`):
1. **math3d** — named ops + optional `claimed` (stdlib reference; numpy cross-check if installed).
2. **game_lint** — Python ruff; C++ clang-format/tidy when present + leak heuristics; C# Unity heuristics + `dotnet format`; GDScript Godot-3 API smells. `format` returns text, does not write.
3. **profiler** — parse NUnit/Unity Test XML, Chrome traces, Godot/Unity/generic FPS logs. `unity_tests` needs `UNITY_PATH` and is **not** Play.

**How**:
1. Verify math → write code → `game_lint` on the new file → parse test/profiler output on failure.
2. Missing clang-tidy/dotnet is an honest skip of that backend, not a leak pass.

**Anti-patterns**:
- `NEED_SANDBOX` with ad-hoc numpy instead of `math3d` for SLERP/rays.
- Claiming clang-tidy ran when `backends.clang-tidy.ran` is false.
- Unity `-runTests` as a playtest.
