"""Daedalus self-reflection layer.

Two background processes that run after each interaction:

1. **Lessons extraction** — after every chat/generate turn, ask the LLM:
   "Did anything in this exchange teach you something about the owner,
   their preferences, the codebase, or your own mistakes? Extract 0-3
   concise lessons as JSON."
   Lessons go into the `daedalus_lessons` ChromaDB collection and are
   semantically retrieved on every future turn.

2. **Episodic summarisation** — when a session crosses N turns,
   summarise the oldest M turns into one compact "episode" and store
   it in the `daedalus_episodes` collection. This is the hippocampal
   pattern: working memory → episodic memory → semantic memory.

Both run as fire-and-forget background tasks so they never block the
user-facing reply.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

# Tunables
EPISODE_THRESHOLD = 14   # start summarising when session has this many turns
EPISODE_CHUNK = 10       # how many oldest turns to compress into one episode
LESSONS_MAX_PER_TURN = 3
REFLECTION_TIMEOUT = 45  # seconds — LLM call budget for background work

# Track in-flight reflections per session to avoid stacking
_active_reflections: set[str] = set()

# Bound global concurrent reflections so a slow LLM doesn't pile up tasks
MAX_PARALLEL_REFLECTIONS = 3
_reflection_sem: asyncio.Semaphore | None = None

# Minimum confidence to actually store an extracted lesson — guards against
# the LLM emitting low-quality or hallucinated "preferences" from a single turn.
MIN_LESSON_CONFIDENCE = 0.6


def _get_sem() -> asyncio.Semaphore:
    global _reflection_sem
    if _reflection_sem is None:
        _reflection_sem = asyncio.Semaphore(MAX_PARALLEL_REFLECTIONS)
    return _reflection_sem


# ---------------------------------------------------------------------------
# Lessons extraction
# ---------------------------------------------------------------------------

LESSONS_SYSTEM = """You are Daedalus's reflection subroutine. Your only job is to extract
durable lessons from a single conversation turn — things worth remembering
across future sessions.

Lessons fall into these kinds:
- "preference"  — owner's style, language, or tooling preference
- "fact"        — something true about the Iyveris codebase or environment
- "mistake"     — something Daedalus got wrong that should be avoided
- "pattern"     — a recurring problem/solution pattern in the codebase
- "decision"    — an architectural decision the owner made

Be strict: only emit a lesson if it is generalisable (will apply to future
unrelated tasks). Do NOT emit lessons that are specific to one file or task.

Respond ONLY with JSON in this exact shape:
{"lessons": [{"kind": "preference|fact|mistake|pattern|decision",
              "text": "<one sentence, <= 180 chars>",
              "confidence": 0.0-1.0}]}

If nothing is worth remembering, return {"lessons": []}.
Never write more than 3 lessons per turn."""


async def extract_lessons(
    llm_call: "callable",  # async fn (system, messages) -> str
    user_message: str,
    assistant_reply: str,
    session_id: str,
) -> list[dict[str, Any]]:
    """Ask the LLM to extract 0..3 lessons from one exchange."""
    if not user_message or not assistant_reply:
        return []
    prompt = (
        f"USER said:\n{user_message[:3000]}\n\n"
        f"DAEDALUS replied:\n{assistant_reply[:3000]}\n\n"
        f"Extract durable lessons (or empty list)."
    )
    try:
        raw = await asyncio.wait_for(
            llm_call(LESSONS_SYSTEM, [{"role": "user", "content": prompt}]),
            timeout=REFLECTION_TIMEOUT,
        )
    except asyncio.TimeoutError:
        log.warning("reflection: lessons extraction timed out for session %s", session_id)
        return []
    except Exception as exc:
        log.warning("reflection: LLM call failed: %s", exc)
        return []

    parsed = _safe_json(raw)
    if not parsed or not isinstance(parsed, dict):
        return []
    lessons = parsed.get("lessons", [])
    if not isinstance(lessons, list):
        return []
    out: list[dict[str, Any]] = []
    for L in lessons[:LESSONS_MAX_PER_TURN]:
        if not isinstance(L, dict):
            continue
        text = str(L.get("text", "")).strip()
        if not text or len(text) < 10:
            continue
        out.append({
            "text": text[:300],
            "kind": str(L.get("kind", "general"))[:30],
            "confidence": float(L.get("confidence", 0.7) or 0.7),
        })
    return out


# ---------------------------------------------------------------------------
# Episodic summarisation
# ---------------------------------------------------------------------------

EPISODE_SYSTEM = """You are Daedalus's episodic-memory compressor.
You receive a chronological chunk of conversation turns from one session.
Write a compact summary (200-500 words) capturing:

- What was the user's overall goal in this chunk?
- What concrete things were decided or built?
- Any open questions or follow-ups?
- Key code artifacts/files touched, by name.

Write in plain prose, no markdown headings, no bullet points unless natural.
Output ONLY the summary text — no preamble, no JSON."""


async def summarise_episode(
    llm_call: "callable",
    turns: list[dict[str, Any]],
    session_id: str,
) -> str:
    """Compress a list of session turns into a single episode summary."""
    if not turns:
        return ""
    lines: list[str] = []
    for t in turns:
        meta = t.get("metadata", {})
        role = meta.get("role", "?")
        ts = meta.get("timestamp", "?")[:19]
        content = (t.get("content") or "")[:1200]
        lines.append(f"[{role} @ {ts}]\n{content}")
    transcript = "\n\n".join(lines)
    prompt = (
        f"Session: {session_id}\n"
        f"Turns: {len(turns)}\n\n"
        f"TRANSCRIPT:\n{transcript[:18000]}\n\n"
        f"Write the episode summary."
    )
    try:
        raw = await asyncio.wait_for(
            llm_call(EPISODE_SYSTEM, [{"role": "user", "content": prompt}]),
            timeout=REFLECTION_TIMEOUT,
        )
    except asyncio.TimeoutError:
        log.warning("reflection: episode summarisation timed out for %s", session_id)
        return ""
    except Exception as exc:
        log.warning("reflection: episode LLM call failed: %s", exc)
        return ""
    return raw.strip()[:4000]


# ---------------------------------------------------------------------------
# Orchestrator entry point (fire-and-forget)
# ---------------------------------------------------------------------------

async def run_reflection_cycle(
    llm_call: "callable",
    memory,
    session_id: str,
    user_message: str,
    assistant_reply: str,
) -> dict[str, Any]:
    """One full reflection cycle: extract lessons + maybe compress old turns.

    Idempotent per session: if a reflection is already running, this is a no-op.
    """
    if session_id in _active_reflections:
        return {"skipped": "already_running"}
    sem = _get_sem()
    # Skip if we're already at the concurrency cap — never block the chat path
    if sem.locked() and sem._value == 0:  # type: ignore[attr-defined]
        return {"skipped": "max_parallel_reached"}
    async with sem:
        _active_reflections.add(session_id)
        started = datetime.now(timezone.utc)
        result: dict[str, Any] = {"lessons_extracted": 0, "lessons_filtered": 0, "episode_created": False}
        try:
            # 1. Lessons (with confidence threshold guard against self-poisoning)
            lessons = await extract_lessons(llm_call, user_message, assistant_reply, session_id)
            kept = 0
            filtered = 0
            for L in lessons:
                if L["confidence"] < MIN_LESSON_CONFIDENCE:
                    filtered += 1
                    continue
                memory.capture_lesson(
                    lesson=L["text"],
                    kind=L["kind"],
                    session_id=session_id,
                    confidence=L["confidence"],
                )
                kept += 1
            result["lessons_extracted"] = kept
            result["lessons_filtered"] = filtered

            # 2. Episode (only if session crossed threshold)
            turn_count = memory.session_turn_count(session_id)
            if turn_count >= EPISODE_THRESHOLD:
                history = memory.session_history(session_id, limit=10_000)
                chunk = history[:EPISODE_CHUNK]
                if len(chunk) >= EPISODE_CHUNK:
                    summary = await summarise_episode(llm_call, chunk, session_id)
                    if summary:
                        started_at = chunk[0].get("metadata", {}).get("timestamp", "")
                        ended_at = chunk[-1].get("metadata", {}).get("timestamp", "")
                        memory.capture_episode(
                            session_id=session_id,
                            summary=summary,
                            turn_count=len(chunk),
                            started_at=started_at,
                            ended_at=ended_at,
                        )
                        # Prune only the exact ids we summarised — safe even if
                        # new turns arrived during the LLM call.
                        ids_to_delete = [t["id"] for t in chunk if t.get("id")]
                        if ids_to_delete:
                            try:
                                memory.sessions_col.delete(ids=ids_to_delete)
                            except Exception as exc:
                                log.warning("reflection: prune failed: %s", exc)
                        result["episode_created"] = True
                        result["episode_turn_count"] = len(chunk)
        except Exception as exc:
            log.warning("reflection cycle failed for %s: %s", session_id, exc)
            result["error"] = str(exc)
        finally:
            _active_reflections.discard(session_id)
            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            result["elapsed_seconds"] = round(elapsed, 2)
            log.info(
                "reflection done: session=%s lessons=%d episode=%s in %.1fs",
                session_id, result["lessons_extracted"],
                result["episode_created"], elapsed,
            )
        return result


def _safe_json(raw: str) -> Any:
    raw = (raw or "").strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except Exception:
        m = re.search(r"\{[\s\S]+\}", raw)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


# ===========================================================================
# Phase-2: Memory Consolidation Cycle
# ===========================================================================
#
# Runs periodically (every N hours) over episodic memory and does:
#   1. Clustering — group near-duplicate facts (cosine similarity > threshold)
#   2. Merging    — for each cluster of 2+, ask Claude to synthesize one fact,
#                   delete the originals, store the synthesized one
#   3. Promotion  — facts with access_count >= PROMOTE_THRESHOLD become lessons
#   4. Contradiction detection — flag facts that semantically conflict
#   5. Stale pruning — facts with score < PRUNE_THRESHOLD (recency × importance)
#                       and access_count == 0 for >30 days are deleted
#
# Each run writes a JSON log entry to data/daedalus/consolidations.jsonl
# so the owner can review what changed in the Memory tab.
# ===========================================================================

from pathlib import Path


def _consolidation_log() -> Path:
    from .paths import data_root
    path = data_root() / "consolidations.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path

# Tunables
CLUSTER_THRESHOLD = 0.88        # cosine similarity to consider two facts duplicates
PROMOTE_ACCESS_THRESHOLD = 4    # access_count >= this → eligible for lesson promotion
STALE_AGE_DAYS = 30             # facts older than this with 0 access → prunable
STALE_IMPORTANCE = 0.7          # ...AND importance below this
CONSOLIDATION_BATCH = 100       # max facts to inspect per run (cost control)
CONSOLIDATION_LLM_TIMEOUT = 60
MIN_CLUSTER_SIZE = 2

CONSOLIDATE_SYSTEM = """You are Daedalus's memory consolidation subroutine.
You are given a small CLUSTER of near-duplicate facts about the owner / codebase / system.
Your job: synthesise ONE single fact that captures the shared meaning, more precisely
than any individual fact, dropping noise and ambiguity.

Rules:
- ≤ 200 chars, one sentence
- Self-contained (name the entity, don't use 'it' or 'this')
- If the facts are CONTRADICTORY (e.g. "user prefers X" vs "user prefers Y"),
  do NOT synthesise — return {"contradiction": true, "note": "<which two conflict>"}
- Otherwise return {"merged": "<one sentence>", "kind": "preference|decision|fact|todo|general"}

Respond ONLY with JSON."""


PROMOTE_SYSTEM = """You are Daedalus's lesson-promotion subroutine.
You are given an EPISODIC FACT that has been recalled many times.  Decide if it
deserves to be promoted to a permanent LESSON (a generalisable, durable insight
that should be retrieved on every future turn).

Criteria for promotion:
- Generalisable beyond a single task or file
- Owner preference, architectural decision, recurring gotcha, or codebase fact
- NOT a one-off task instruction or temporary state

Respond ONLY with JSON:
{"promote": true|false, "lesson_text": "<one sentence if promote else empty>",
 "kind": "preference|fact|mistake|pattern|decision"}"""


async def _cluster_facts(facts: list[dict[str, Any]], memory) -> list[list[int]]:
    """Cluster facts by ChromaDB-similarity within the episodic collection.

    For each fact, query the episodic collection for top-3 nearest neighbours;
    if a neighbour has dist < (1 - CLUSTER_THRESHOLD), they're considered same cluster.
    Returns list of clusters, each as a list of indices into `facts`.
    """
    epi_col = memory._client.get_or_create_collection("daedalus_episodic")
    if epi_col.count() < MIN_CLUSTER_SIZE:
        return []
    # Build content → index map
    by_content: dict[str, int] = {}
    for i, f in enumerate(facts):
        by_content[(f.get("content") or "").strip().lower()[:200]] = i
    parent: dict[int, int] = {i: i for i in range(len(facts))}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i, f in enumerate(facts):
        content = (f.get("content") or "")[:1500]
        if not content:
            continue
        try:
            res = epi_col.query(query_texts=[content], n_results=4)
        except Exception:
            continue
        docs = (res.get("documents") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]
        for j, doc in enumerate(docs):
            dist = dists[j] if j < len(dists) else 1.0
            sim = 1.0 - dist
            if sim < CLUSTER_THRESHOLD:
                continue
            key = (doc or "").strip().lower()[:200]
            if key in by_content and by_content[key] != i:
                union(i, by_content[key])

    # Group by root
    groups: dict[int, list[int]] = {}
    for i in range(len(facts)):
        r = find(i)
        groups.setdefault(r, []).append(i)
    return [g for g in groups.values() if len(g) >= MIN_CLUSTER_SIZE]


async def _merge_cluster(
    llm_call: "callable",
    cluster_facts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Ask the LLM to merge a cluster of duplicate facts into one. Returns
    {"merged": str, "kind": str} or {"contradiction": True, "note": str} or {}."""
    lines = []
    for i, f in enumerate(cluster_facts):
        content = (f.get("content") or "")[:200]
        meta = f.get("metadata") or {}
        kind = meta.get("kind", "general")
        access = meta.get("access_count", 0)
        lines.append(f"[{i+1}] (kind={kind}, recalled={access}x) {content}")
    prompt = "CLUSTER OF FACTS:\n" + "\n".join(lines) + "\n\nSynthesise or report contradiction."
    try:
        raw = await asyncio.wait_for(
            llm_call(CONSOLIDATE_SYSTEM, [{"role": "user", "content": prompt}]),
            timeout=CONSOLIDATION_LLM_TIMEOUT,
        )
    except Exception as exc:
        log.warning("consolidate cluster: LLM call failed: %s", exc)
        return {}
    parsed = _safe_json(raw)
    if not isinstance(parsed, dict):
        return {}
    return parsed


async def _should_promote(
    llm_call: "callable",
    fact: dict[str, Any],
) -> dict[str, Any]:
    """Ask the LLM whether a high-recall fact should become a lesson."""
    content = (fact.get("content") or "")[:300]
    meta = fact.get("metadata") or {}
    prompt = (
        f"FACT: {content}\n"
        f"kind: {meta.get('kind', 'general')}\n"
        f"recalled: {meta.get('access_count', 0)} times\n"
        f"importance: {meta.get('importance', 1.0)}\n\n"
        f"Decide: promote to lesson?"
    )
    try:
        raw = await asyncio.wait_for(
            llm_call(PROMOTE_SYSTEM, [{"role": "user", "content": prompt}]),
            timeout=CONSOLIDATION_LLM_TIMEOUT,
        )
    except Exception:
        return {}
    parsed = _safe_json(raw)
    return parsed if isinstance(parsed, dict) else {}


async def run_consolidation_cycle(
    llm_call: "callable",
    daedalus,                 # Daedalus agent instance (has .mem0, .memory)
    max_clusters: int = 8,    # cap LLM calls per run
    max_promotions: int = 5,
) -> dict[str, Any]:
    """One full consolidation pass over episodic memory.

    Returns a summary dict: {merged, deleted, promoted, contradictions, pruned}.
    Logs a JSONL line to CONSOLIDATION_LOG for owner review.
    """
    import time as _t
    started = _t.time()
    memory = daedalus.memory
    mem0 = daedalus.mem0
    summary: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "merged": 0,
        "deleted": 0,
        "promoted": 0,
        "contradictions": [],
        "pruned": 0,
        "errors": [],
    }

    try:
        episodic = mem0.episodic
        all_facts = episodic.all_facts(limit=CONSOLIDATION_BATCH)
        summary["scanned"] = len(all_facts)
        if not all_facts:
            return _finalize_consolidation(summary, started)

        # ---- 1. Cluster + merge duplicates ----
        clusters = await _cluster_facts(all_facts, memory)
        # Sort largest first; cap by max_clusters
        clusters.sort(key=len, reverse=True)
        for cluster_idxs in clusters[:max_clusters]:
            cluster_facts = [all_facts[i] for i in cluster_idxs]
            result = await _merge_cluster(llm_call, cluster_facts)
            if not result:
                continue
            if result.get("contradiction"):
                summary["contradictions"].append({
                    "note": str(result.get("note", "")),
                    "facts": [(f.get("content") or "")[:120] for f in cluster_facts],
                })
                continue
            merged_text = str(result.get("merged", "")).strip()
            if not merged_text or len(merged_text) < 10:
                continue
            kind = str(result.get("kind", "general"))[:30]
            # Pick the session_id of the most-accessed fact as the "owner" of the merged
            best = max(cluster_facts, key=lambda f: (f.get("metadata") or {}).get("access_count", 0))
            sid = (best.get("metadata") or {}).get("session_id", "consolidated")
            # Delete originals, then store merged fact (with importance boost)
            ids_to_delete = [f["id"] for f in cluster_facts if f.get("id")]
            episodic.delete(ids_to_delete)
            episodic.store_facts(sid, [{
                "text": merged_text,
                "kind": kind,
                "importance": 1.3,  # merged facts are more valuable
            }], role="consolidated")
            summary["merged"] += 1
            summary["deleted"] += len(ids_to_delete)

        # ---- 2. Promote high-recall facts to lessons ----
        # Re-fetch since some may have been deleted/merged
        remaining = episodic.all_facts(limit=CONSOLIDATION_BATCH)
        candidates = [
            f for f in remaining
            if (f.get("metadata") or {}).get("access_count", 0) >= PROMOTE_ACCESS_THRESHOLD
        ]
        candidates.sort(key=lambda f: (f.get("metadata") or {}).get("access_count", 0), reverse=True)
        for fact in candidates[:max_promotions]:
            decision = await _should_promote(llm_call, fact)
            if not decision.get("promote"):
                continue
            text = str(decision.get("lesson_text", "")).strip()
            if not text or len(text) < 10:
                continue
            kind = str(decision.get("kind", "fact"))[:30]
            memory.capture_lesson(
                lesson=text,
                kind=kind,
                session_id=(fact.get("metadata") or {}).get("session_id"),
                confidence=0.85,  # owner-implicit (high recall)
            )
            # Mark original as promoted (don't delete — it stays for recall)
            episodic.update_metadata(fact["id"], {"promoted_to_lesson": True})
            summary["promoted"] += 1

        # ---- 3. Stale pruning ----
        now_ts = _t.time()
        stale_ids: list[str] = []
        for fact in remaining:
            meta = fact.get("metadata") or {}
            age_days = (now_ts - float(meta.get("ts", now_ts))) / 86400.0
            if (
                meta.get("access_count", 0) == 0
                and age_days > STALE_AGE_DAYS
                and float(meta.get("importance", 1.0)) < STALE_IMPORTANCE
                and not meta.get("promoted_to_lesson")
            ):
                stale_ids.append(fact["id"])
        if stale_ids:
            episodic.delete(stale_ids)
            summary["pruned"] = len(stale_ids)

        # Rebuild BM25 after structural changes
        if summary["merged"] or summary["deleted"] or summary["pruned"]:
            mem0.rebuild_bm25_indices()

    except Exception as exc:
        log.exception("consolidation cycle failed")
        summary["errors"].append(str(exc))

    return _finalize_consolidation(summary, started)


def _finalize_consolidation(summary: dict[str, Any], started: float) -> dict[str, Any]:
    import time as _t
    summary["elapsed_seconds"] = round(_t.time() - started, 2)
    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    # Append to JSONL log
    try:
        with open(_consolidation_log(), "a", encoding="utf-8") as f:
            f.write(json.dumps(summary, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.warning("consolidation log write failed: %s", exc)
    log.info(
        "consolidation cycle done: scanned=%d merged=%d deleted=%d promoted=%d "
        "pruned=%d contradictions=%d in %.1fs",
        summary.get("scanned", 0), summary["merged"], summary["deleted"],
        summary["promoted"], summary["pruned"], len(summary["contradictions"]),
        summary["elapsed_seconds"],
    )
    return summary


def read_consolidation_log(limit: int = 20) -> list[dict[str, Any]]:
    """Read the most recent consolidation runs from JSONL log."""
    log_path = _consolidation_log()
    if not log_path.exists():
        return []
    try:
        with open(log_path, encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return []
    out = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    out.reverse()  # newest first
    return out
