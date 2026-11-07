---
name: evolution
description: Opt-in idle gardener that hunts LLM/coding/memory innovations and proposes, never silent-edits.
---

# SKILL: Evolution

**When**: owner says "еволюция", "самоподобрявай се", "търси нови репота", or Studio → **Еволюция** is on.

**How**:
1. Mode lives in `$DAEDALUS_DATA/settings.json` (`evolution_enabled`). Default **off**.
2. A gardener loop starts with the HTTP process. It uses spare load/RAM even with no open chat.
3. Each tick: rotate 2 radar sources → study one new GitHub repo via API → journal + learned-skill note.
4. Optional shallow clone only under `data/daedalus/evolution/research/` (never into `daedalus/`).
5. AGPL/SSPL is never cloned. Offline kit skips the network.
6. Autopilot running or high load → skip this tick.
7. Harness file patches still go through `self_improve` **pending**. `edit_modules` stays owner-gated. Auto-apply never includes it.

**Anti-patterns**:
- Do not vendor a GitHub tree into the MIT package.
- Do not copy MiroFish or other AGPL source.
- Do not treat a radar hit as "already integrated".
- Do not patch `self_improve.py`, `evolution.py`, `identity.py`, `job_contract.py`.
