---
name: narrative
description: Plots and characters that change the map, not flavour text on a splash screen.
---

# SKILL: Narrative

**When**: story, characters, dialogue, quests, tone.

**How**:
1. One want and one wound per character. They must change a level or a faction flag.
2. Dialogue is short and spoken. No lore dumps in the first minute.
3. A quest is a graph edge: pickup → complication → change to `world/map.json` or `factions.json`.
4. Tone is a constraint on art and copy. Pick one word and keep it.
5. Playtest the first 30 seconds with the impatient persona before adding act two.

**Anti-patterns**:
- NPCs that only say exposition.
- Plot that cannot be saved in JSON.
- Generating a novel instead of a playable beat.
