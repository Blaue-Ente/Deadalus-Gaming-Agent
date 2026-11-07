# Provider model catalog — manual refresh

Status: **written spec, waiting for Nikolay's review.** User approved the bounded design on 2026-11-07 (manual refresh in Studio Settings, retain last good cache on error, warn when selected model is missing from cache, **no** automatic model swap). **This file does not authorize implementation** or an implementation plan.

Base of this branch: `origin/main` at `c9c4f62` (includes MR125 checkpoint/stream diagnostics). **Out of scope:** MR126 product merge, MR114, build-stream race fix, implementation code.

## Problem

Model pickers use **static Python lists** (`PRESET_MODELS` in `daedalus/llm_config.py`, mentor `CATALOG` in `daedalus/mentor_helm.py`, plus `preset_extras()`). Gateway ids drift faster than the repo. Local backends probe `GET /v1/models` briefly (`daedalus/local_llm.py`, `/api/v1/llm/local`). Cloud gateways have no owner-driven refresh. Studio Settings today only shows **effective** brain text via `GET /api/v1/llm/status` (`paintBrain` in `studio-workspace.js`); **brain model is not chosen or saved in Settings** — only on Connectors (`POST /admin/daedalus/llm-config`).

There is no distinction between **live cached** provider ids, **repo pins**, and **saved selection**, and no advisory when the saved model is absent from a successful list response.

## Goals

1. **Manual refresh** in **Studio → Settings** (primary UX); Connectors may reuse the same read APIs later.
2. **Settings v1 includes explicit brain model choice** — pick from cached list or type a manual id; **Save** uses the **existing** brain write API (below). **Refresh never saves.**
3. **Server-side keys only** for catalog fetch; no API keys from the browser on refresh.
4. **Last good cache** — failed/empty/malformed refresh does not erase `last_success` for that scope.
5. **Selection integrity** — warn when saved model ∉ last good cache; **never** auto-swap `get_config()` / DB / mentor KV.
6. **Honest provider matrix** — OpenRouter first with verified list contract; other cloud providers **manual_only** in v1 unless a future adapter is approved.

## Non-goals

- No auto-refresh on load, timer, or deploy.
- No completion probe inside refresh (`POST /admin/daedalus/test-provider` stays manual).
- No change to `get_config()` / `get_brain_config()` **priority chain** or env/DB resolution rules.
- No external catalog service; no implementation plan in this file.
- No `clear cache` v1; no broad `PRESET_MODELS` / wardrobe refactor.
- No remote list fetch for arbitrary `custom` `base_url` until a separate secure opt-in (v1: manual id only for custom).
- Keys stay on Connectors secrets (`POST /admin/daedalus/connectors/secrets`); catalog feature does not add browser key fields.

## Resolved defaults (no open questions)

| Topic | Decision |
|-------|----------|
| Settings brain save v1 | **Required** — explicit **Save** via existing `POST /admin/daedalus/llm-config`. |
| Refresh vs save | **Refresh** updates catalog cache only; **Save** updates brain model/provider/base_url/use_cache via `set_config` only. |
| Draft vs live | UI holds **draft** provider/model until Save; banner shows **effective** (`get_brain_config`) vs draft when they differ. |
| Stale 7 days | **Informational badge only** — does not refuse pick, save, or refresh. |
| Failed last attempt | If `last_attempt.ok` is false, UI shows **catalog may be outdated** even when `last_success` is younger than 7 days. |
| Mentor | Same `GET /api/v1/llm/catalog` for datalist/search; **curated `CATALOG` pins stay separate**; picking a cached id still requires explicit `POST /api/v1/daedalus/mentor` — **no** automatic director change on refresh. |
| Repo pins | Always labeled **not confirmed on provider** unless the id also appears in `last_success` for the active scope. |

## Existing write and read APIs (do not invent)

Implementation **must** reuse these contracts; no parallel brain-write route.

| Action | Method | Path | Server handler |
|--------|--------|------|----------------|
| Read brain + presets + drift warnings | `GET` | `/admin/daedalus/llm-config` | `describe_effective_config()`, `list_preset_models()` — response strips `api_key` |
| **Save brain** (master model) | `POST` | `/admin/daedalus/llm-config` | `llm_config.set_config(model, api_key, provider, base_url, use_cache)` inside `router_write_context()` in `admin_compat.set_llm_config` |
| Read effective (Studio summary) | `GET` | `/api/v1/llm/status` | `get_brain_config()` + turn overlays |
| Read mentor + catalog rows | `GET` | `/api/v1/daedalus/mentor` | `mentor.status()` → `llm.catalog`, choices |
| **Save mentor director** | `POST` | `/api/v1/daedalus/mentor` | `choice_id` and/or `provider`+`model` → `mentor_helm.set_choice` (KV `adn:daedalus_mentor_choice`) — **separate** from brain `set_config` |
| Apply preset combo | `POST` | `/admin/daedalus/llm-presets/{id}/apply` | `apply_preset` under `router_write_context()` |
| Store secrets | `POST` | `/admin/daedalus/connectors/secrets` | unchanged |
| Manual completion test | `POST` | `/admin/daedalus/test-provider` | **Out of scope** for catalog refresh |

**Write guard:** `set_config` calls `require_llm_write_permission()`, satisfied when the request runs inside `router_write_context()` (the llm-config handler already does this). Middleware treats `/admin/daedalus/…` POST paths as router-write paths; Studio Save must call the same endpoints Connectors uses.

**New catalog routes (v1):**

| Method | Path | Writes brain DB? |
|--------|------|------------------|
| `GET` | `/api/v1/llm/catalog` | No — read merge for UI |
| `POST` | `/api/v1/llm/catalog/refresh` | No — only cache file; **must not** call `set_config` or mentor setters |

`POST /api/v1/llm/catalog/refresh` lives under `/api/v1/llm` prefix (middleware may enable write **context** for other llm routes); implementation must still **only** touch catalog cache storage, not `adn:daedalus_llm_config`.

## Provider adapters (v1)

| Provider | Refresh | UI copy when active brain provider |
|----------|---------|----------------------------------|
| **openrouter** | **Supported** — `GET https://openrouter.ai/api/v1/models` ([docs](https://openrouter.ai/docs/api-reference/models/get-models)); use `openai_compat_headers()` on the server client | Normal refresh + cache labels |
| **ollama**, **lmstudio**, **llamacpp**, **vllm**, local | **Supported** — `local_llm.probe_endpoint` | Normal refresh |
| **omniroute** on loopback | **Supported** if `LOOPBACK_GATEWAYS` | Normal refresh |
| **novita**, **alltoken**, **poolside**, **nvidia**, **groq**, **openai**, **gemini**, **anthropic**, **custom** | **`manual_only`** — refresh handler returns `reason: unsupported_provider`, **no outbound list HTTP** | **Must not** claim “refreshed from your provider.” Show: *Manual model id only; list refresh not available for this provider in v1.* Owner may still **Save** a typed id via `POST /admin/daedalus/llm-config`. |

Do **not** assume Novita/AllToken/etc. expose a working OpenAI-style `/models` until a separate adapter MR verifies with fixtures.

Refresh default scope: `get_brain_config().provider` + normalized default `base_url` from `PROVIDER_DEFAULTS` / DB (not draft-only fields).

## Cache storage

- **File:** `{DAEDALUS_DATA}/catalog/llm_models.json` (atomic `os.replace`).
- **Scope key:** `{provider}:{base_url_normalized}` (scheme+host+/v1, no secrets in query).
- **Per scope:** `last_success` + `last_attempt` (separate).
- **Bounds:** ≤2000 models/success; file ≤2 MiB; reject oversize writes (keep prior `last_success`).
- **Retention:** success with ≥1 id updates `last_success`; failure updates `last_attempt` only; never replace good cache with empty/error payload.

## Catalog freshness (UI)

Three independent indicators:

1. **`last_success.fetched_at`** — “Last refreshed …”
2. **Age > 7 days** — informational “stale” badge only (still show list; still allow Save).
3. **`last_attempt.ok === false`** — “Last refresh failed; list may be outdated” (even if `last_success` is recent).

None of these block Save, manual id entry, or picking a row.

## Studio Settings UX (v1, canonical)

Extend `loadSettings` / `#settings-brain` block:

1. **Effective** line — from `GET /api/v1/llm/status` (unchanged semantics).
2. **Draft** — provider select (values from `PROVIDER_DEFAULTS` keys already used on Connectors), model field, optional `base_url` when provider requires it (mirror Connectors rules), `use_cache` for anthropic only when applicable.
3. **Refresh catalog** — `POST /api/v1/llm/catalog/refresh` using **effective** brain provider for scope (not draft unless implementation adds explicit “refresh draft provider” later — **v1: effective only**).
4. **Browse** — cached models from `GET /api/v1/llm/catalog`:
   - **Search/filter** (required for large OpenRouter lists): typeahead filter by id/name, min 1 character, case-insensitive; **do not** render all 2000 rows in DOM — show filtered subset (cap displayed rows e.g. 100) with “refine search” hint.
   - **i18n EN + BG** for all new strings (`app/static/i18n.js`), including search placeholder, stale/outdated/failed refresh, manual_only, missing_from_cache warning, pin vs cached labels, Save/Refresh buttons, `aria-label` on search and listbox.
5. **Repo pins** — separate group; hint if pin ∉ cache when `last_success` exists.
6. **Save brain** — explicit button → `POST /admin/daedalus/llm-config` with draft fields; **empty `api_key` in body** (server keeps env/DB key). On success, reload effective line and catalog warnings.
7. **Warnings** — `missing_from_cache` for **saved** model (effective), not draft, unless draft equals effective.

**Refresh never** calls `llm-config` POST or mentor POST.

## Mentor header (same release)

- `GET /api/v1/llm/catalog` adds rows to mentor custom datalist / search (provider+model id).
- **`CATALOG` curated optgroups unchanged** in purpose; static pins keep “not confirmed live” hint when cache exists.
- **`POST /api/v1/daedalus/mentor`** only on user picking mentor choice / custom dialog — refresh does not change `choice_id`.

## Selection warnings

On `GET /api/v1/llm/catalog`:

- `effective_selection` from `get_brain_config()`.
- `mentor_selection` from mentor helm (read-only).
- `warnings[]` e.g. `missing_from_cache` when `last_success` exists and effective model id ∉ cached set (normalization rules in implementation).
- **No** auto `set_config` / `set_choice`.

**Listing ≠ entitlement** — copy in EN/BG states list visibility does not guarantee routing, tools, or account access.

## SSRF / URL policy

- Fetch only to hosts allowed for the provider: `PROVIDER_DEFAULTS[provider].base_url` host, or loopback for local/omniroute via existing `local_llm` paths.
- Block private/metadata hosts for cloud adapters; no cross-host redirects.
- Local providers: must not break valid `127.0.0.1` probes under generic SSRF rules.

## Test matrix (implementation; mock HTTP in CI)

| Case | Expectation |
|------|-------------|
| Refresh success (OpenRouter fixture) | `last_success` updated; `last_attempt.ok` true |
| Refresh failure | `last_attempt` set; prior `last_success` kept |
| Settings Save (intercepted) | `POST /admin/daedalus/llm-config` with model; **no** refresh POST in same action |
| Refresh only | `POST /api/v1/llm/catalog/refresh`; **no** `llm-config` POST |
| `manual_only` provider | refresh returns unsupported; UI shows manual-only copy; Save still allowed with typed id |
| `last_attempt` failed + young `last_success` | API exposes `catalog_outdated: true`; UI warning |
| 7d+ `last_success` | `stale_hint: true`; pick/save still allowed |
| Mentor refresh | catalog GET enriches list; mentor KV unchanged without POST |
| Secrets | catalog GET/refresh responses omit `api_key` |
| Settings i18n | EN/BG keys present for new UI strings |

No live provider HTTP in CI. Do not add automatic `test-provider` to refresh flow.

## Acceptance criteria

1. Settings: **Refresh** (cache only) + **Save** (existing `llm-config` POST) + pick from cache or manual id.
2. Refresh never persists brain or mentor selection.
3. Failed refresh never wipes last good cache.
4. Missing effective model from cache → visible warning; runtime unchanged until Save.
5. Pins vs cached rows visually distinct; pins never labeled provider-confirmed without cache membership.
6. OpenRouter list adapter verified against documented API; unsupported providers honest `manual_only`.
7. Large list searchable (EN/BG); no requirement to paint full catalog in DOM.
8. `get_config()` priority chain untouched; no auto completion probe in refresh.
9. SSRF policy as above; locals still probed via existing code paths.

## References (`main` @ `c9c4f62`)

- `daedalus/llm_config.py` — `set_config`, `get_brain_config`, `PRESET_MODELS`, `PROVIDER_DEFAULTS`
- `daedalus/llm_router_config.py` — `router_write_context`, `require_llm_write_permission`, write path prefixes
- `daedalus/mentor_helm.py` — `CATALOG`, `set_choice`
- `daedalus/local_llm.py` — `probe_endpoint`
- `app/admin_compat.py` — `get_llm_config`, `set_llm_config`, `test_provider`
- `app/harness_api.py` — `/api/v1/daedalus/mentor`
- `app/static/studio-workspace.js` — `loadSettings`, `paintBrain`
- `app/static/studio.js` — mentor custom dialog → `setMentorCustom`

---

*Self-review: Settings Save uses existing `POST /admin/daedalus/llm-config` only; refresh uses new catalog POST without `set_config`; mentor unchanged unless user POSTs mentor; 7d stale is informational; failed last_attempt forces outdated UX; unsupported providers cannot imply live refresh; no open questions; docs-only; no MR126/MR114/race implementation.*
