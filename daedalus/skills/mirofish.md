---
name: mirofish
description: Optional AGPL sidecar for population-scale social simulation of a GDD or press release.
---

# SKILL: MiroFish sidecar

**When**: the owner wants public-reaction simulation ("какво ще каже интернет-ът", PR crisis, sentiment swarm).

**License**: MiroFish-Offline is **AGPL-3.0**. Daedalus only speaks HTTP. Never copy that backend into this repo.

**How**:
1. Owner runs https://github.com/nikmcfly/MiroFish-Offline (`docker compose up` → UI `:3000`, API `:5001`).
2. `MIROFISH_URL=http://127.0.0.1:5001` (optional `MIROFISH_UI`).
3. Native tool `mirofish` action `ping` / `submit` with GDD or press-release text.
4. Finish the swarm in the MiroFish UI (graph → prepare → start → report).

**Anti-patterns**:
- Do not git-submodule or paste MiroFish Python into `daedalus/`.
- If `/health` is down, say so and give the clone URL — do not pretend the swarm ran.
