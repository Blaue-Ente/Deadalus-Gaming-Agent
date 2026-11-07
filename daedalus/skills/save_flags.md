---
name: save-flags
description: Persist region, flags, and inventory so a campaign can resume. Write a real save module on the second slice.
---

# SKILL: Save and flags

**When**: second playable slice of an epic, or the owner says save / checkpoint / inventory.

**How**:
1. Source of truth is a small JSON document: `{region, flags, inventory, act}`.
2. Put it in `src/world/runtime.js` (or `scripts/world_runtime.gd` / `SaveService.cs`). Load on boot, write on gate. Schema: `{version, region, act, flags, inventory, quests}`.
3. A quest beat flips a named flag. The next region reads that flag — do not hide plot in closures.
4. Autosave at exits listed in `levels/graph.json`. Manual save is a bonus, not a substitute.
5. Prove: `world({action: prove, slug})`. Die/reload is the JS/Godot hook; the Python runtime is the DoD.

**Anti-patterns**:
- `localStorage.score = 1` and calling it a campaign save.
- Flags that nothing in the map reads.
- Resetting the bible because the save schema changed — migrate, do not restart lore.
