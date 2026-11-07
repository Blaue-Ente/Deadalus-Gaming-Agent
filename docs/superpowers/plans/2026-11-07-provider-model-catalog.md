# Provider model catalog implementation plan

> **For agentic workers:** Implement task-by-task with TDD. The owner already required inline execution on 2026-11-07 07:56 Berlin. Do not ask which execution method to use.

**Goal:** Studio Settings can manually refresh a server-side model list for the effective brain provider, keep the last good cache, and save a picked or typed model only through the existing brain Save route.

**Architecture:** `daedalus/model_catalog.py` owns the cache file, the provider allowlist, and the OpenRouter / local fetch. `GET/POST /api/v1/llm/catalog` in `app/main.py` call that module and never call `set_config` or `set_choice`. Studio Settings holds a draft and posts Save to the existing `POST /admin/daedalus/llm-config`. Mentor reads the same catalog for its datalist and does not change the saved director unless the owner uses the existing mentor POST.

**Tech stack:** Python 3, `httpx` with `follow_redirects=False` for OpenRouter, existing `local_llm.probe_endpoint` for loopback backends, FastAPI, Studio `studio.html` / `studio-workspace.js` / `studio.js` / `i18n.js`, pytest, Playwright. No new package. No live provider call in tests.

**Branch:** `cursor/provider-model-catalog-4549`, merged normally with verified main `18f41d09` (parents `c9c4f62` + `d5a99e46`). Approved spec commit `90992a8142a9c59085aa649f8c807533c6962ca4` stays in history. The spec file is not rewritten. MR126 is not in this branch.

---

## Global constraints

Copied from `docs/superpowers/specs/2026-11-07-provider-model-catalog-design.md` and the owner's implementation approval.

- Manual Refresh in Studio Settings. Refresh updates only `{DAEDALUS_DATA}/catalog/llm_models.json`. Refresh never calls `llm_config.set_config` or `mentor_helm.set_choice`.
- Save uses existing `POST /admin/daedalus/llm-config` (`set_config` inside `router_write_context`). Body sends an empty `api_key` so the server keeps the env/DB key. No new browser key field.
- v1 refresh scope is the effective brain from `get_brain_config()` plus that row's `base_url`, or `PROVIDER_DEFAULTS` when the row has none. The draft provider is not the refresh scope.
- OpenRouter list URL is exactly `https://openrouter.ai/api/v1/models`. Send `openai_compat_headers("openrouter")` and `Authorization: Bearer` only when `OPENROUTER_API_KEY` is set. `follow_redirects=False`. Any 3xx is a failure and is not followed.
- Local providers (`ollama`, `lmstudio`, `llamacpp`, `vllm`, `local`) and `omniroute` (`LOOPBACK_GATEWAYS`) use `probe_endpoint` only after the host is loopback (`127.0.0.0/8`, `::1`, `localhost`). `probe_endpoint` itself still follows redirects; this plan does not change that function. A non-loopback base is `url_not_allowed` and is not passed to the probe.
- `novita`, `alltoken`, `poolside`, `nvidia`, `groq`, `openai`, `gemini`, `anthropic`, `custom` are `manual_only`. `replit_openai` and `replit_gemini` are also `manual_only` because the spec forbids an unapproved models API. No outbound list HTTP. UI copy: "Manual model id only; list refresh not available for this provider in v1."
- No custom `base_url` discovery. No completion probe. No auto-refresh on load, timer, or deploy. No `get_config()` / `get_brain_config()` priority change. No mentor KV write on refresh. No clear-cache. No `PRESET_MODELS` rewrite.
- Cache scope key is `{provider}:{scheme}://{host}/v1` with userinfo, query, and fragment removed. Per scope, `last_success` and `last_attempt` are separate. At least one id updates `last_success`. Empty, malformed, HTTP error, redirect, oversize, or `url_not_allowed` updates `last_attempt` only. Atomic `os.replace`. At most 2000 models. File at most 2 MiB. Oversize keeps the previous `last_success`. An unreadable cache file is not overwritten.
- Age over 7 days sets `stale_hint` and does not block pick, save, or refresh. `last_attempt.ok === false` sets `catalog_outdated`. An unsupported provider does not pretend the list was refreshed.
- Search needs at least 1 character, is case-insensitive on id and name, and paints at most 100 rows. New strings exist twice in `app/static/i18n.js` (EN and BG), including `aria-label` on the search box and the listbox.
- Repo pins come from `list_preset_models()` for the effective provider. A pin is confirmed only when its id is in `last_success`. Otherwise the label says it is not confirmed on the provider.
- `missing_from_cache` refers to the saved effective model. The UI shows it when the draft still equals that saved model. No silent swap.
- Keys never appear in catalog JSON, the cache file, or logs. Tests use `httpx.MockTransport` or an injected fetcher/probe. `PYTEST_CURRENT_TEST` and `DAEDALUS_CATALOG_NETWORK=0` refuse a live catalog socket.
- No owner-data or project writes. Tests use a temporary `DAEDALUS_DATA`.

### Response shape

`GET /api/v1/llm/catalog` returns HTTP 200:

```json
{
  "ok": true,
  "mode": "catalog",
  "reason": "",
  "scope": {"provider": "openrouter", "base_url": "https://openrouter.ai/v1", "key": "openrouter:https://openrouter.ai/v1"},
  "last_success": {"fetched_at": "2026-11-07T00:00:00+00:00", "models": [{"id": "a/b", "name": "AB"}], "count": 1},
  "last_attempt": {"fetched_at": "2026-11-07T00:00:00+00:00", "ok": true, "error": "", "reason": ""},
  "stale_hint": false,
  "catalog_outdated": false,
  "pins": [{"id": "deepseek/deepseek-v3.2", "label": "DeepSeek", "provider": "openrouter", "confirmed": false}],
  "effective_selection": {"provider": "openrouter", "model": "missing/id", "base_url": "https://openrouter.ai/v1", "source": "db"},
  "mentor_selection": {"id": "custom", "provider": "openrouter", "model": "mentor/id"},
  "warnings": [{"code": "missing_from_cache", "target": "brain", "model": "missing/id"}],
  "providers": [{"id": "openrouter", "manual_only": false, "show_base": true, "show_cache": false}]
}
```

`mode` is `manual_only` and `reason` is `unsupported_provider` for the unsupported set. `last_success` is `null` when that scope has never succeeded. `POST /api/v1/llm/catalog/refresh` returns the same view plus `wrote_success`, with HTTP 200 even when `ok` is false, so the Studio client can read `last_success` and `reason`. The POST body is ignored.

---

## Task 1: Cache, allowlist, and fetch

**Files:**
- Create: `daedalus/model_catalog.py`
- Test: `tests/test_model_catalog.py`

**Interfaces:**
- Consumes: `llm_config.get_brain_config`, `llm_config.provider_default_base_url`, `llm_config.PROVIDER_DEFAULTS`, `llm_config.openai_compat_headers`, `llm_config.resolve_provider_api_key` via env only, `llm_config.list_preset_models`, `llm_config.is_loopback_gateway`, `local_llm.probe_endpoint`, `local_llm.is_local_provider`, `mentor_helm.get_choice`, `paths.data_root`.
- Produces: `normalize_base_url(url) -> str`, `scope_key(provider, base_url) -> str`, `catalog_view(*, now=None) -> dict`, `refresh_catalog(*, now=None, transport=None, probe=None, fetcher=None) -> dict`, `reset_state() -> None`.

**Steps:**
1. Write `tests/test_model_catalog.py` covering normalization (query secret dropped), OpenRouter success fixture, redirect not followed, malformed, empty, HTTP error, oversize count, oversize bytes, failure preserves `last_success`, stale at 7 days plus 1 second and not at exactly 7 days, outdated with a young success, provider-switch cross-cache, nested out-of-order refresh, missing effective and mentor ids, manual_only does not call the fetcher, metadata and private hosts do not call the probe, `127.0.0.1` still probes, secrets absent from the file and the dict, `set_config` / `set_choice` / `get_config` not called, pytest refuses `httpx.Client`.
2. Run `python3 -m pytest tests/test_model_catalog.py -q --tb=short` and see it fail because the module is missing.
3. Implement `daedalus/model_catalog.py` until that command exits 0.

## Task 2: HTTP routes

**Files:**
- Modify: `app/main.py` (add the two routes next to `llm_status`; do not edit `get_config`)
- Test: `tests/test_model_catalog_http.py`

**Interfaces:**
- `GET /api/v1/llm/catalog` -> `catalog_view`
- `POST /api/v1/llm/catalog/refresh` -> `refresh_catalog` with no body argument

**Steps:**
1. Write HTTP tests: owner token required (401 via `raw_http`), 401 body has no planted secret, authed GET/POST omit `api_key`, refresh ignores a posted provider and secret, failure keeps the previous file, manual_only performs no dispatch, `set_config` and `set_choice` stay at 0 calls.
2. Run `python3 -m pytest tests/test_model_catalog_http.py -q --tb=short` and see it fail.
3. Add the routes. Return the module dict with HTTP 200. On an unexpected exception return `{"ok": false, "error": "catalog_failed"}` and do not interpolate the exception text.
4. Re-run the command and expect exit 0.

## Task 3: Settings and mentor UI

**Files:**
- Modify: `app/static/studio.html`, `app/static/studio-workspace.js`, `app/static/studio.js`, `app/static/i18n.js`, `app/static/studio.css`
- Test: `tests/test_studio_model_catalog.py`

**Behavior:**
- `#settings-brain` stays the effective summary from `GET /api/v1/llm/status`. Catalog controls are the next sibling, so a failed status still leaves `#settings-brain` empty.
- Draft provider/model/base/`use_cache` (anthropic only; base field for `custom`, `alltoken`, `novita`, `openrouter`, matching the existing Connectors form). Banner when the draft differs from `effective_selection`.
- Refresh posts `{}` to `/api/v1/llm/catalog/refresh` and does not post `llm-config`. A later response with an older sequence number does not replace the newer list.
- Save posts the draft to `/admin/daedalus/llm-config` with `api_key: ""` and does not post refresh. A typed id is allowed. Buttons stay enabled for a `manual_only` effective provider, and the manual-only sentence is shown.
- Search and listbox have `aria-label`. Empty query paints no rows. More than 100 matches paints 100 and the refine hint.
- `missing_from_cache` for the saved model does not change the input.
- A failed refresh leaves the previous ids on screen and shows the outdated sentence.
- An `api_key` field on a catalog payload is not written into the panel.
- Mentor curated options keep their labels. Catalog ids are appended to `#mentor-model-list`. A cached scope marks a pin that is absent from `last_success` with `dataset.catalogState = "unconfirmed"`. Refresh does not POST mentor.
- EN and BG values for every new key. `t()` placeholders use `{provider}`, `{model}`, and `{time}`.

**Steps:**
1. Add the failing markup, i18n-count, and Playwright tests.
2. Run `python3 -m pytest tests/test_studio_model_catalog.py -q --tb=short` and see it fail.
3. Implement the UI. `node --check` the three JS files.
4. Re-run the command and expect exit 0.

## Task 4: Inventory and full suite

**Commands:**
- `python3 scripts/record_baseline.py --refresh-inventory`
- `python3 scripts/record_baseline.py --check`
- `python3 -m pytest tests -q --tb=line` twice on the final commit, with no `tail` and no retry. The process exit code is the result.
- `node --check app/static/studio.js && node --check app/static/studio-workspace.js && node --check app/static/i18n.js`

Do not merge, install, or push MR126 / the frozen 127 branch.

## Self-review against the spec

| Spec section | Task |
| --- | --- |
| Goals 1–6 | Tasks 1–3 |
| Non-goals | Constraints; no code for auto-refresh, test-provider, get_config, clear-cache, custom discovery |
| Resolved defaults | Task 3 draft/save/stale/outdated/mentor/pins |
| Existing APIs | Save and mentor POST unchanged; new routes only in Task 2 |
| Provider matrix | Task 1 allowlist and fixtures |
| Cache storage | Task 1 atomic file, bounds, preserve |
| Freshness | `stale_hint`, `catalog_outdated`, UI sentences |
| Settings UX | Task 3 |
| Mentor | Task 3 datalist; Task 1 does not call `set_choice` |
| SSRF | Fixed OpenRouter URL, loopback gate, no redirect follow |
| Test matrix plus owner cases | Tasks 1–3: success, failure preserve, save vs refresh, manual_only, outdated, stale, mentor, secrets, EN/BG, provider switch, out of order, missing select, auth, manual id |
| Acceptance 1–9 | Covered above |

No placeholder tasks. No open product questions. Replit rows are `manual_only` under the spec rule "no assumed models API," not a new adapter. Local probes stay on `probe_endpoint` and are refused unless the host is loopback.
