---
name: effort
description: Match LLM cost and depth to the task. Whisper stays cheap; epic may lift the model for one turn.
---

# SKILL: Effort equals complexity

**When**: every chat and every autopilot start. Owner may force Whisper / Craft / Epic in Studio.

**How**:
1. Read the effort block (`[EFFORT: whisper|craft|epic]`) or call the classifier in your head the same way: greetings → whisper; one game/fix → craft; world/campaign/characters/levels → epic.
2. Whisper — 2–6 sentences. No playground writes. `remember` is allowed.
3. Craft — ship one playable loop. Keep the owner's configured model. Do not open a campaign bible.
4. Epic — you may use a stronger model *this turn only*. Write `world/` and `characters/` and `levels/`. One slice, then journal. Never `ollama pull` without confirm.
5. Do not persist wardrobe changes unless the owner clicked a model in Connectors → Brain or Studio → Brain.

**Anti-patterns**:
- A 32B local pull because the prompt said "complex world".
- Using the epic model for "hello".
- Flattening an epic into one `index.html`.
