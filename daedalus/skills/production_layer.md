---
name: production_layer
description: M0–M3 is a prototype. Store-ready needs a systems map with files, persist, and pause/settings — empty JSON is not a production layer.
---

# SKILL: Production layer

**When**: owner asks for store-ready, production-ready, готово за стора, a production layer, or gate `production-check`.

**Layout** (under `playground/projects/{slug}/`):
- `design/systems.json` — `{version, layer: prototype|production, systems: [{id, purpose, mvp, files[], status}]}`
- `src/layer/runtime.js` — persist / restore / pause / resume / settings (optional until promote)

**How**:
1. Seed the map with `production(action=seed, slug)`. That writes `design/systems.json` only. Do **not** inject `src/layer/runtime.js` into the Vite racer M0.
2. Implement systems as real files. Status `implemented` with a missing path fails.
3. Persist (localStorage/save) and pause **or** settings must exist before `production(action=promote, slug)`.
4. `production(action=prove, slug)` fails on store-ready / готово за стора while `layer` is still prototype, or when the map is empty.

**Anti-patterns**:
- Claiming store-ready because M0–M3 plays.
- Empty `design/systems.json` with `layer: production`.
- A racer "ship" mesh as a store claim (it is not).
- Writing the layer runtime into the hover-racer scaffold.
