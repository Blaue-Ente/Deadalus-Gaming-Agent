---
name: campaign
description: Multi-act games — acts, level graph, save, vertical slice before expanse.
---

# SKILL: Campaign

**When**: multiple levels, acts, bosses, or "различни нива".

**How**:
1. `levels/graph.json` is the source of truth. An act is a list of level ids plus edges with `require_flags`.
2. Each level folder has a loop (input, update, render, win/lose) and a listed exit.
3. Save: `{version, region, flags, inventory, quests}`. The Python runtime + `src/world/runtime.js` ship with the kit — not a comment.
4. Vertical slice first (one act, one gated exit). `world({action: prove})` must pass before expanse.
5. Unity stays a C# scaffold + Git. Phaser / canvas / Vite for in-process play.

**Anti-patterns**:
- Twenty level names and one empty canvas.
- No way back to the hearth.
- New engine mid-campaign without the owner asking.
