# Model roles

Studio picks a model per job, the way Cursor does. One API key can feed every
role; each role may use a different provider if that provider also has a key.

| Role | What it does | Default pick |
|------|----------------|--------------|
| **Chat** | Voice in Studio | The primary brain (Connectors / `DAEDALUS_MODEL`) |
| **Build** | Native tool rounds that write the game | A cheap tool-calling coder (GLM 5.2, Kimi, DeepSeek, Qwen coder) |
| **Mentor** | Kernel-run senior engineer when the builder is stuck, or `ask_mentor` | Claude Haiku. **Sonnet** when a typical build with the chosen builder is estimated under 10 ¢ |
| **Wrap** | One short closing message over the kernel ledger | Cheapest keyed model |

Auto-assign runs after a cloud key is present (`DAEDALUS_ROLE_AUTO=1`, default).
The owner can pin any role from the Studio header chip, or from env.

## The 10 ¢ Mentor rule

A “typical build” is priced as 80k prompt + 40k completion tokens on the
**builder**, using the **live** OpenRouter catalog when a key is present
(fallback table only when offline). If that estimate is under
`DAEDALUS_MENTOR_UPGRADE_BELOW_USD` (default `0.10`), auto-assign may spend
more on Mentor — Claude Sonnet instead of Haiku.

Published fallback rates put GLM 5.2 around 8 ¢ (Sonnet). Live OpenRouter
rates move; if GLM is ~20 ¢, Mentor stays on Haiku. Pin Mentor in Studio
when you want Sonnet regardless of the estimate.

There is no free ongoing Claude API. Haiku via OpenRouter
(`anthropic/claude-haiku-4.5`) does not need a native Anthropic key.

If the owner left Mentor on auto Sonnet and the **live** turn ledger already
reaches 10 ¢, further Mentor calls drop to Haiku for that turn. A manual pin
is never swapped.

## Env (highest priority per role)

| Variable | Role |
|---|---|
| `DAEDALUS_MODEL` / `DAEDALUS_PROVIDER` | Chat (primary) |
| `DAEDALUS_MODEL_BUILD` / `DAEDALUS_PROVIDER_BUILD` | Build |
| `DAEDALUS_MODEL_MENTOR` / `DAEDALUS_PROVIDER_MENTOR` | Mentor |
| `DAEDALUS_MODEL_WRAP_UP` / `DAEDALUS_PROVIDER_WRAP_UP` | Wrap |
| `DAEDALUS_ROLE_AUTO` | `0` keeps every role on the primary model |
| `DAEDALUS_MENTOR_UPGRADE_BELOW_USD` | Mentor upgrade cap (default `0.10`) |

A role env that sets only the model keeps the primary provider and key.

## API

- `GET /api/v1/llm/catalog` — keyed models, prices, typical-build estimate
- `GET` / `POST /api/v1/llm/roles` — assignment (no keys in the body)
- `GET /api/v1/llm/status` — primary + roles

Stored picks live in `$DAEDALUS_DATA/llm_config.json` under `roles`. API keys
are never written there.

## What we do **not** send through OpenRouter hosted tools

Playground files live on this disk. OpenRouter Shell / Bash, Apply Patch, and
Fusion are not the build path. The kernel already has `edit_file` (surgical
search/replace + write gate) and `ask_mentor` (the Mentor decides, not the
weak builder). Web search / Fusion for epic design docs is a later opt-in.
