# Provider model catalog — manual refresh

Status: **written spec, waiting for Nikolay's review.** User approved the bounded design on 2026-11-07 (manual refresh in Studio Settings, retain last good cache on error, warn when selected model is missing from cache, **no** automatic model swap). **This file does not authorize implementation** or an implementation plan.

Base of this branch: `origin/main` at `c9c4f62` (includes MR125 checkpoint/stream diagnostics). **Out of scope for this spec branch:** MR126 (radar/journal), MR114, build-stream product race fix, dynamic catalog work on other branches.

## Problem

Today, model pickers are fed from **static Python lists** (`PRESET_MODELS` in `daedalus/llm_config.py`, `CATALOG` in `daedalus/mentor_helm.py`, plus `preset_extras()`). OpenRouter and other gateways add, rename, and retire model ids faster than the repo. The owner sees ids that may no longer exist, with **no** way to refresh from the **configured** provider without editing code.

Local backends already expose a short-lived probe (`daedalus/local_llm.py` → `GET /v1/models`, ~20s TTL) via `/api/v1/llm/local/models`. Cloud gateways have **no** equivalent refresh path. Connectors (`/admin/daedalus/llm-config`) and Studio Settings (`paintBrain` via `/api/v1/llm/status`) show **effective** provider/model but do not list live catalog data.

A failed static id fails only at completion time. There is no advisory when the saved model is absent from a successful provider listing. There is no distinction in the UI between **pinned static suggestions**, **live cached catalog rows**, and the **owner's current selection**.

## Goals

1. **Manual refresh only** — owner clicks **Refresh model catalog** in **Studio → Settings** (primary). Optional read-only mirror on Connectors may reuse the same API; Settings is canonical for discoverability.
2. **Server-side keys only** — refresh uses `resolve_provider_api_key` / env; never accept API keys from the browser for catalog fetch.
3. **Last good cache** — a failed, empty, or malformed refresh **must not** erase the last successful catalog snapshot for the same cache scope.
4. **Selection integrity** — if the saved brain or mentor model is **not** in the last good cache, show a **warning**; do **not** auto-change `get_config()` / DB / mentor KV.
5. **Honest provider matrix** — document which providers get a list adapter in v1 vs manual custom entry only; do not assume Novita/AllToken/OpenAI list APIs work until verified in implementation behind mocks + optional live smoke (smoke is **not** CI).

## Non-goals

- No automatic refresh on page load, timer, or deploy.
- No paid **completion** probing as part of catalog refresh (`POST /admin/daedalus/test-provider` stays separate and manual).
- No change to `get_config()` / `get_brain_config()` priority chain or runtime provider defaults.
- No new external catalog service (ToolsMaster or other); this repo owns cache + APIs.
- No "clear cache" admin action in v1 unless added later with explicit review.
- No broad refactor of `PRESET_MODELS`, mentor `CATALOG`, or wardrobe.
- No arbitrary `custom` `base_url` remote discovery until an explicit secure opt-in (separate env/flag + host allowlist extension); v1 uses default endpoints only for custom provider refresh.
- No implementation or task plan in this document.

## Current surfaces (baseline)

| Surface | Model list source today | Refresh |
|---------|-------------------------|---------|
| Studio Settings `#settings-brain` | Read-only effective provider · model (`GET /api/v1/llm/status`) | None |
| Studio header Mentor | `GET /api/v1/daedalus/mentor` → `catalog` + datalist from presets | None |
| Connectors #brain | `GET /admin/daedalus/llm-config` → `list_preset_models()` | None |
| Local wardrobe / health pills | `GET /api/v1/llm/local` → `probe_all()` | Implicit 20s probe TTL |

Runtime LLM calls: `llm_client.complete()` → `get_config()`; keys from env + DB (`daedalus/llm_config.py`).

## Decision

Ship a **canonical server-side catalog cache** plus **Settings-first manual refresh**. UI merges:

1. **Live cached models** (from last successful refresh for scoped provider/base URL).
2. **Static fallback pins** (existing preset rows), clearly labeled **not verified live** when a good cache exists and the id is missing from cache.
3. **Owner selection** (always shown), with advisory if missing from last good cache.

Unsupported providers: refresh returns honest `unsupported` / `manual_only`; owner keeps **manual model id** entry (existing custom paths). Listing never implies account entitlement or capability (tools, vision, etc.).

## Provider adapters (v1)

Implementation must treat this table as the contract. **Do not** ship a adapter until its list endpoint is verified (fixture tests + doc link in code comment).

| Provider | List strategy v1 | Notes |
|----------|------------------|-------|
| **openrouter** | **Supported** — `GET https://openrouter.ai/api/v1/models` | First adapter; pagination/fields per [OpenRouter models API](https://openrouter.ai/docs/api-reference/models/get-models) at implementation time. Use existing `openai_compat_headers()` for the HTTP client if applicable. |
| **ollama**, **lmstudio**, **llamacpp**, **vllm**, **local** | **Supported** — reuse `local_llm.probe_endpoint` / existing `/v1/models` | Not subject to cloud SSRF rules; loopback/link-local allowed per existing local rules. |
| **omniroute** | **Supported** if `base_url` is loopback gateway in `LOOPBACK_GATEWAYS` | Same as local OpenAI-compat probe. |
| **novita**, **alltoken**, **poolside**, **nvidia**, **groq**, **openai**, **gemini**, **anthropic**, **custom** (non-opt-in URL) | **Unsupported for auto-list v1** | Return `ok: false`, `reason: "unsupported_provider"`, `manual_entry: true`. Do **not** assume `GET /v1/models` works. Future adapters are separate approved changes. |
| **custom** with saved `base_url` | **Blocked for remote discovery v1** | Manual model id only unless owner later enables explicit opt-in (out of v1). |

Refresh uses **effective brain** provider + resolved base URL from `get_brain_config()` unless the request body names an allowed provider override (same provider family only; no cross-tenant URLs).

## Cache identity and storage

**Path:** `{DAEDALUS_DATA}/catalog/llm_models.json` (or KV key `adn:llm_model_catalog` — pick one at implementation; spec prefers **file** under data root for atomic replace).

**Scope key:** `{provider_normalized}:{base_url_normalized}` where `base_url_normalized` is scheme + host + path through `/v1` (no query, no userinfo). Separate scopes do not overwrite each other.

**Schema (version 1):**

```json
{
  "version": 1,
  "scope": "openrouter:https://openrouter.ai/api/v1",
  "provider": "openrouter",
  "base_url": "https://openrouter.ai/api/v1",
  "last_success": {
    "fetched_at": "ISO-8601 UTC",
    "source": "openrouter/models",
    "model_count": 0,
    "models": [{"id": "string", "name": "string", "context_length": null}]
  },
  "last_attempt": {
    "at": "ISO-8601 UTC",
    "ok": false,
    "error": "human-safe string",
    "http_status": null
  }
}
```

**Bounds:**

- `models` max **2000** entries per success (truncate with `truncated: true` if provider returns more).
- Per-model string fields capped (e.g. id 256, name 256).
- Full file max **2 MiB**; reject write if exceeded (keep previous `last_success`).
- **Atomic write:** write temp file in same directory + `os.replace`.

**Retention policy:**

- On refresh **success** with **≥1** parsed model id: update `last_success`, set `last_attempt.ok: true`, clear prior attempt error.
- On refresh **failure**, **timeout**, **empty parse**, or **invalid JSON**: update `last_attempt` only; **`last_success` unchanged** (may be absent on first-ever refresh).
- Never write `last_success` with `model_count: 0` from a failed call.

## HTTP API (server-only)

All routes require existing **owner auth** (same as Connectors / Studio admin), not public.

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/v1/llm/catalog` | Merged view for UI: `scopes[]` summary, active scope, `last_success` metadata (no secrets), `static_pins[]`, `selection` + `selection_warnings[]`. |
| `POST` | `/api/v1/llm/catalog/refresh` | Manual refresh. Body optional: `{ "provider": "openrouter" }`. Default provider = `get_brain_config().provider`. **No** completion call. |

Response must never include: `api_key`, raw `Authorization`, query tokens, `owner.token`, full request URLs with secrets.

**Timeouts:** catalog HTTP client **15s** total; **no** retries in v1 (owner can click refresh again).

**Redaction:** log provider, scope, counts, http status — not response bodies.

## SSRF and URL policy

- Catalog fetch allowed only to hosts matching `PROVIDER_DEFAULTS[provider].base_url` host for that provider, or loopback for local/omniroute providers per `local_llm` / `LOOPBACK_GATEWAYS`.
- **Block:** private IPs, link-local (except local providers), metadata IPs, and non-HTTP(S) schemes for cloud providers.
- **Do not** follow redirects to a different host (or treat redirect as failure).
- Local probes: use existing `local_llm` code paths; **do not** apply cloud SSRF rules in a way that blocks valid `127.0.0.1` Ollama/LM Studio URLs.

`custom` provider remote list: **disabled** in v1 (manual id only).

## Studio Settings UX (canonical)

**Panel:** Settings (`loadSettings` in `studio-workspace.js`).

Add a **Model catalog** block below or beside existing brain summary:

1. Shows **effective** brain provider · model (existing).
2. **Refresh from provider** button → `POST /api/v1/llm/catalog/refresh` (confirm not required; action is read-only on provider side except OpenRouter list quota).
3. Status line: `Last refreshed: {time} · {count} models` or `No successful refresh yet` / `Last refresh failed: {error}` (use i18n EN + BG).
4. **Picker aid** (v1 minimal): expandable list or datalist populated from `GET /api/v1/llm/catalog` **cached ids** for active scope; static pins in a separate group **Suggested (repo pins)** with subtitle when cache is fresh: *"Not confirmed on provider unless also listed above."*
5. If `selection_warnings` contains `missing_from_cache`: show **warning** banner; do **not** change saved model.
6. **Manual model id** field remains for unsupported providers and custom ids (wired to existing brain/mentor save flows when those exist in Settings; if only Connectors saves brain today, Settings refresh still stands alone and warns — **implementation** may add "copy id" only in v1 without new save path; spec allows read-only catalog in Settings + save still via Connectors/Mentor if needed — prefer single save path in implementation MR after review).

**Mentor header:** May consume the same `GET /api/v1/llm/catalog` to enrich datalist; not required for spec approval but **same canonical cache**. No separate mentor cache file.

**No auto-refresh** when opening Settings or switching provider.

## Static pins vs live cache (presentation rules)

| UI label | Meaning |
|----------|---------|
| **From provider (cached)** | Id present in `last_success` for active scope |
| **Repo pin** | From `PRESET_MODELS` / mentor `CATALOG` |
| **Your selection** | Effective saved model |

Rules:

- If `last_success` exists and is younger than a configured stale threshold (default **7 days**, show stale badge only — still usable), repo pins that are **not** in cache show **"Not in last provider list"** hint (not hidden).
- Do **not** present a static pin as **"confirmed current on provider"** unless it also appears in `last_success`.
- Deprecated provider metadata (if OpenRouter returns it) may show optional `deprecated` badge; no auto-removal from selection.

## Selection and warnings

On `GET /api/v1/llm/catalog`:

- `selection`: `{ provider, model, source }` from `get_brain_config()` and mentor choice (mentor warnings may be a second block).
- If `last_success` exists and `selection.model` not in cached ids (normalize case/provider rules at implementation): append `selection_warnings: [{ "code": "missing_from_cache", "model": "...", "scope": "..." }]`.
- **Do not** disable custom valid ids solely due to missing cache.
- **Do not** auto-call `set_config` or mentor `set_choice`.

Listing is **not entitlement**: warning text must state that visibility does not guarantee API access, routing, or tool support.

## Security

- Secrets only server-side; browser never sends keys on refresh.
- Catalog responses and logs: no secrets in URLs (strip query per `studio_stream_diag` patterns where relevant).
- Auth: same session/owner token as other Studio admin APIs.

## Test matrix (implementation MR; mock HTTP only in CI)

| Case | Expectation |
|------|-------------|
| OpenRouter refresh success | Mock `GET /models` fixture; `last_success` updated; `last_attempt.ok` true |
| OpenRouter timeout / 5xx | `last_attempt` error; prior `last_success` unchanged |
| Malformed JSON / empty data array | No `last_success` update; prior cache kept |
| First refresh failure, no prior cache | No `last_success`; UI shows manual-only state |
| Provider switch | Scope B refresh does not delete scope A cache |
| Unsupported provider refresh | `unsupported_provider`; no HTTP call; manual entry still allowed |
| Local ollama refresh | Mock loopback `/v1/models`; not blocked by cloud SSRF helper |
| `GET /api/v1/llm/catalog` | No `api_key` in JSON |
| Selection missing from cache | Warning present; no DB write |
| Atomic write | Simulated partial write does not corrupt prior file |
| UI wiring | Playwright or DOM test: Settings refresh button calls POST once (intercepted) |
| Persist selection | After failed refresh, effective model unchanged (config read) |

**CI:** no live OpenRouter/Novita/network list calls.

**Explicitly out of test scope for this feature:** changing `get_config()` priority tests, automatic `test-provider`, completion-based discovery.

## Acceptance criteria

1. Owner can trigger catalog refresh from **Studio Settings** without putting API keys in the browser.
2. Successful refresh stores timestamp, provider scope, and model ids server-side; Settings shows count + time.
3. Failed, empty, or invalid refresh leaves **last successful** cache intact for that scope.
4. Saved model absent from last good cache shows a **visible warning**; runtime model is **unchanged**.
5. Static repo pins and live cached rows are **visually distinct**; static-only ids are not labeled as provider-confirmed when a fresh cache exists.
6. OpenRouter is the first implemented cloud list adapter, documented against verified API shapes.
7. Unsupported providers return honest manual-only status; no assumed list API.
8. No automatic refresh, no completion probe, no external catalog service.
9. SSRF policy: default provider hosts only; local providers use existing probes; custom remote URL discovery off in v1.
10. All CI tests use mocked HTTP; full suite remains green.

## Open questions (resolve before implementation)

1. Whether Settings v1 includes **save brain model** or only catalog + warnings (Connectors remains save path short-term).
2. Exact stale threshold for UI badge (default 7 days proposed).
3. Whether mentor `CATALOG` pins get the same "not in cache" hint as `PRESET_MODELS`.

## References (read-only baseline `main` @ `c9c4f62`)

- `daedalus/llm_config.py` — `PRESET_MODELS`, `get_brain_config`, `PROVIDER_DEFAULTS`
- `daedalus/mentor_helm.py` — `CATALOG`, `preset_extras()`
- `daedalus/local_llm.py` — local `/v1/models` probe
- `app/static/studio-workspace.js` — `loadSettings`, `paintBrain`
- `app/admin_compat.py` — `/admin/daedalus/llm-config`, `/admin/daedalus/test-provider` (manual completion; not part of refresh)

---

*Self-review (spec author): Scope is docs-only; contradictions resolved — Settings is primary UX; Connectors optional mirror; unsupported providers explicit; custom URL discovery deferred; no implementation plan; no merge of MR126/MR114; cache failure never wipes last success; no auto swap; tests mock-only.*
