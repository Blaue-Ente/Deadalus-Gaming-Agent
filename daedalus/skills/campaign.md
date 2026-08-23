---
name: campaign
description: Multi-act games — acts, level graph, save, vertical slice before expanse.
---

# SKILL: Campaign

**When**: multiple levels, acts, bosses, or "различни нива".

**How**:
1. `levels/graph.json` is the source of truth. An act is a list of level ids plus edges.
2. Each level folder has a loop (input, update, render, win/lose) and a listed exit.
3. Save: region id, flags, inventory. Write a real `save` module on the second slice, not a comment.
4. Vertical slice first (one act, one boss or gate). Expanse after the slice plays.
5. Unity stays a C# scaffold + Git. Phaser / canvas / Vite for in-process play.

**Anti-patterns**:
- Twenty level names and one empty canvas.
- No way back to the hearth.
- New engine mid-campaign without the owner asking.
