---
name: dissection
description: Analyze a reference game (name, URL, git, or Play Lab install) and use it as a specimen when building the owner's game.
---

# SKILL: Дисекция

**When**: owner says «дисекция», «като тази игра», «мостра», gives an itch.io / GitHub / live demo URL, a game name, or asks to install a specimen on Daedalus's virtual computer.

**What it is**: field study of a *reference* game so the *new* game can borrow loop, feel, and stack — not a clone factory.

**How**:
1. Native tool `play_lab` — create / list / install / start / stop the Play Lab (jailed `$DAEDALUS_DATA/labs/{id}/`). Docker is optional; local HTTP on 127.0.0.1 always works. This **is** Daedalus's virtual computer for specimens.
2. Native tool `dissect_game` with `source` = name | URL | git | path, optional `slug` of the project being built, optional `lab_id`.
3. Read `specimen_block`. Put it in the GDD / planner / builder. Follow **steal** (ideas) and **do_not_copy** (code, assets, trademarks, music).
4. After attach: `assets/_dissection.json` + `design/dissection.md` on the target slug.

**Sources**:
- Name → research + optional playable demo URL.
- URL → fetch + live Chromium probe + stack fingerprint.
- Git → shallow clone into the lab, then probe.
- Path / zip → copy into the lab (under data, playground, or `/tmp`). Native `.exe`/`.msi`/`.dmg` are refused.

**Anti-patterns**:
- Shipping a 1:1 clone of a commercial game.
- Declaring dissection done from a store page with no loop extracted.
- Skipping Play Lab when the owner handed you an HTML5 zip — install it.
- Treating QEMU/Wine as required. Detected, not auto-started.
