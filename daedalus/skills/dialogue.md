---
name: dialogue
description: Branching spoken lines that change flags or map edges. Not a lore dump on a splash screen.
---

# SKILL: Dialogue graph

**When**: NPCs, quests, spoken plot, or a character file exists under `characters/`.

**How**:
1. One graph per conversation: `characters/{slug}.dialogue.json` with nodes `{id, speaker, line, choices[]}`.
2. A choice either (a) goes to another node or (b) sets a flag / opens a map exit.
3. Lines are short and spoken. One tell, not a lecture.
4. The character's `want` and `wound` from `characters/{slug}.md` must appear in the graph.
5. Hook the graph from the level loop — talking is a verb, like shooting or walking. `world({action: talk, slug, who, pick})` walks it.

**Anti-patterns**:
- A paragraph of lore on the title screen.
- Choices that do not change a flag.
- Generating a novel instead of ten spoken nodes.
