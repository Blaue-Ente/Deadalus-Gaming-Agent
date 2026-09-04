---
name: gameplay_bot
description: Drive a web game with playtest/scenario.json — pixel-diff, FPS/memory, required win/lose.
---

# SKILL: Gameplay bot

**When**: visual web kind (static / vite / Phaser / racer), owner says "тествай играта", or a gate mentions play-check / FPS / win-lose.

**How**:
1. Keep `playtest/scenario.json` (input sequence → expected observations). The racer M0 scaffold already has one (`require_lose`, no win).
2. Optional `playtest/balance.json` — 1000-round economy/combat sim. A resource that only grows, or a fight that never/always wins, fails.
3. Do not claim "готово" on Click-Start. The bot must see the canvas change (pixel-diff) and reach **lose** / **win** when those flags are set.
4. Headless FPS with fewer than 10 frames is a warning. A real sample under the budget fails. `performance.memory` missing is a skip, not a fail.
5. Godot/Unity stay on the `engine` runner — this skill is the web bot.

**Anti-patterns**:
- Keyboard event *count* as proof the player moved.
- `require_win` + `require_lose` on a game that only has a fail state (the racer).
- Deleting `playtest/scenario.json` from the racer scaffold.
