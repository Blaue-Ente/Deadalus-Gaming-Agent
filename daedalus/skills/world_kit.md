---
name: world-kit
description: Build vast games as a living folder with a real save/quest/dialogue/graph runtime — not empty JSON.
---

# SKILL: World kit

**When**: owner asks for a world, campaign, RPG, many levels, factions, plot, or Studio effort is Епос.

**Layout** (under `playground/projects/{slug}/`):
- `CAMPAIGN.md` — long-horizon contract
- `world/bible.md` — premise, tone, player fantasy, open questions
- `world/map.json` — regions + exits (`require_flags` on gated rooms)
- `world/quests.json` — complete_when flags → reward_flags
- `world/save.schema.json` — `{version, region, act, flags, inventory, quests}`
- `world/factions.json` — who wants what
- `world/journal.md` — what shipped this session
- `characters/{id}.md` + `characters/{id}.dialogue.json`
- `levels/graph.json` — acts and gated edges
- `src/world/runtime.js` — game-loop copy of the Python runtime

**How**:
1. If the kit is missing, write it before gameplay code. The scaffold is a **playable slice** (hearth → gated wilds via keeper).
2. A quest beat flips a named flag. Travel reads that flag. `world({action: prove, slug})` must pass.
3. Dialogue choices that never set a flag are flavour. At least one choice must change the map.
4. Next sessions: new region or quest; update edges and journal. Do not rewrite the bible on "продължи".
5. Gameplay stays in modules (`src/world`, `src/levels`), not a 500-line `index.html`.

**Anti-patterns**:
- Empty `map.json` / `graph.json` with no quests or dialogue.
- CSS-gradient "worlds".
- Ten empty screens and no loop.
- Claiming done because the markdown exists.
