---
name: arcade_craft
description: Build FM1-grade (or better) games — vertical slices, specialist 3D/audio tools, fun gates, no pixel-art default.
---

# SKILL: Arcade craft

**When**: owner wants a real game (especially 3D, racing, F-Zero/Wipeout-class, "like that Reddit racer"), or Playground kind is vite/unity and the brief is not a toy.

**Field study**: Formula Minus One — https://fm1.moises.cloud/ (v0.8). Vite + Three.js **WebGPU + TSL**, procedural city/ocean, custom VehicleFactory, Web Audio engine, PWA landscape, championship/arcade modes. Not Phaser. Not one prompt.

**How**:
1. Plan with a strong model; implement with cheaper ones. Hundreds of tiny steps. Never "build me this game".
2. M0 = vertical slice (one ship, one track segment, camera, fail, visible boost). City/cups/1v1 later.
3. Start from the Vite `src/racer/` scaffold (already a playable slice). Then assets: `assets({action:mesh})` only with FAL/Tripo/Meshy keys — fail closed otherwise; greybox is a cube, not a hero. Do not auto-inject a 2D atlas into the racer. Procedural CSG/splines for buildings/track. Polyhaven PBR. Compress GLB.
4. Audio: `assets({action:sfx})` kit WAV; Suno is a file drop (`assets/music.mp3`); no AI-lyric BGM.
5. After every playable slice: smoke + browser + `playtest/scenario.json` bot (pixel-diff, win/lose) + persona `playtest`. Fun gate = wayfinding, fail, readable pickups, risk on the gas.
6. Cul-de-sac (same error 3×): change approach. Fresh session for new features; long session for the bug.
7. Web first. Unity/Godot only if the owner needs a store build.

**Anti-patterns**:
- Pixel-art or GitHub low-fi templates on a 3D brief.
- LLM-authored hero meshes as raw vertices.
- Declaring done because it boots.
- Multiplayer before the single-player slice is fun.
- Skipping mobile landscape / load budget.
