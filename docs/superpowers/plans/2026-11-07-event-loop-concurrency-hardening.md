# Event-loop concurrency hardening (sync-in-async) Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement task-by-task.

**Goal:** Remove proven blocking patterns where synchronous work runs on the asyncio event loop inside `async` HTTP handlers and evolution’s async `run_cycle`: (1) `GET /health` and `GET /api/v1/llm/status` calling `get_brain_config()` / `get_config()`, (2) evolution `run_cycle` calling sync `study_fn(url)` / `study_repo(url)` after `await` radar.

**Architecture:** Use `asyncio.to_thread(...)` for those call sites only. Preserve response JSON shape and keys (`/health` `llm` block unchanged). No new public “liveness” API, no cache redesign, no worker-count assumptions. This is **concurrency hardening for a single async loop**, not a claim to fix Windows 12s client timeouts (explicitly out of scope until reproduced under known process topology).

**Tech Stack:** Python 3.11+, FastAPI, existing test stack (pytest, httpx ASGI).

## Global Constraints

- Do not merge MR114 or touch phase2 branches.
- No new dependencies.
- Do not change `/health` top-level keys or `llm` sub-key names; same exception behavior (`has_key: False` on failure).
- Do not disable evolution or local LLM probes globally; only move execution off the main thread for the two paths above.
- Document cancellation: `to_thread` does not cancel the underlying thread; acceptable for this narrow patch; single-flight evolution cycle unchanged (one `run_cycle` at a time in gardener).

## Tasks

- [x] Add sync helper(s) for health LLM snapshot and reuse in `llm_status` body construction; `await asyncio.to_thread(...)` from async handlers.
- [x] Evolution: `studied = await asyncio.to_thread(fn, url)` with same try/except shape.
- [x] Tests: httpx `AsyncClient` + `ASGITransport`; slow monkeypatch for `_health_llm_snapshot` during `/health`; concurrent `/capabilities` completes while health pending; evolution `run_cycle` with slow `study_fn` + heartbeat task proves loop not stuck.
- [x] Run `python3 -m pytest -q`; refresh baseline if `tests.modules`/`tests.functions` change (641→643 functions, +1 module).

---

**Scope distinction (binding):** Approved deliverable is **proven** asyncio responsiveness under slow sync LLM resolution and slow sync study work on **one** process/loop. **Windows** 12s health stall with concurrent fast routes remains **uncorrelated / unresolved** by this MR — no marketing as a cure.

## Studio persist test (order-dependent, not budget-route regression)

Independent review @ `fbf72e4`: full suite failed `tests/test_studio_phase1_header.py:267` (`aria-pressed` on budget after process restart); isolated module sometimes passed, `4918f74` full suite green; diff does not touch `run-budget` / `studio.js`.

**Reproduction:** `pytest tests/test_event_loop_concurrency.py tests/test_studio_phase1_header.py::test_settings_persist_across_process_and_browser_restart` → fail; same test alone on `4918f74` → pass; on MR118 without fix → fail reliably.

**Root cause:** Thread-pooled `/health` returns HTTP 200 while LLM snapshot threads may still run. `_restart()` tight `/health` poll no longer serializes startup like sync-in-handler health did. After reload, `loadMentor()` often wins before `loadBudget()` paints; test waited for mentor only post-reload.

**Fix:** Post-reload `wait_for_function` for budget `$5` `aria-pressed` (mirrors pre-restart); `set_enabled(False)` teardown on evolution concurrency test. Assertions unchanged.
