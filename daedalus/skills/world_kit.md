---
name: world-kit
description: Build vast games as a living folder — bible, map, factions, characters, levels — not a single toy page.
---

# SKILL: World kit

**When**: owner asks for a world, campaign, RPG, many levels, factions, plot, or Studio effort is Епос.

**Layout** (under `playground/projects/{slug}/`):
- `CAMPAIGN.md` — long-horizon contract
- `world/bible.md` — premise, tone, player fantasy, open questions
- `world/map.json` — regions + exits
- `world/factions.json` — who wants what
- `world/journal.md` — what shipped this session
- `characters/{name}.md` — want, wound, tell, secret
- `levels/graph.json` — acts and edges
- `levels/{id}/` — one playable space

**How**:
1. If the kit is missing, write it before gameplay code.
2. First session: bible + one region + one character + one playable slice.
3. Next sessions: new region or quest beat; update map edges and journal.
4. Gameplay stays in modules (`src/world`, `src/levels`), not a 500-line `index.html`.
5. Assets are real PNGs referenced by `load.image` / `drawImage`.

**Anti-patterns**:
- CSS-gradient "worlds".
- Ten empty screens and no loop.
- Rewriting the bible from scratch on "продължи".
- Copying an AGPL game into the tree.
