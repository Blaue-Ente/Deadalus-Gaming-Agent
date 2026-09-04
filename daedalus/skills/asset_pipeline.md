---
name: asset_pipeline
description: Pack a 2D atlas/tileset/SFX/font without paid APIs; 3D mesh jobs fail closed without keys.
---

# SKILL: Asset pipeline v2

**When**: sprites, tiles, SFX, bitmap font, hero GLB, Tripo/Meshy/Hunyuan, or gate `asset-check`.

**2D kit** (no API keys):
1. `assets({action: atlas, slug})` → `assets/atlas.png` + `assets/atlas.json` (frames `{name,x,y,w,h}`). Default 16×16 floor/wall/door/hazard/water/grass/dirt/crate.
2. `assets({action: tileset, slug})` → grid PNG + `tilewidth`/`columns`/`tiles[]`.
3. `assets({action: sfx, slug})` → `assets/sfx/{blip,hit,boost,pickup,fail,ui}.wav` (22050 Hz mono).
4. `assets({action: font, slug})` → `assets/font.png` + glyph map for ASCII 32–126.
5. Pass existing `sources: ["assets/player.png", …]` to pack instead of the default kit.

**3D**:
- `assets({action: mesh, slug, prompt})` submits only when `FAL_KEY` / `FAL_API_KEY`, `TRIPO_API_KEY`, or `MESHY_API_KEY` is set. Poll with `action: status, job_id`.
- Without a key: `{ok:false, need_key:true}` — not a skip-pass. Never write a fake hero GLB.
- `assets({action: greybox, slug})` writes an honest cube. It is **not** a hero mesh. Then `asset_contract.check`.
- Suno: no submit. Drop `assets/music.mp3`. Kit SFX does not need a key.

**Anti-patterns**:
- Auto-injecting an atlas into the Vite racer M0 (procedural 3D).
- Claiming a hero ship because a greybox cube exists.
- Empty `assets/*.glb` or a claimed `studio/asset_contract.json` target that is missing.
- Pillow as a required dependency (PNG is stdlib zlib).
