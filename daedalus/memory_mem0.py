"""Mem0-style hybrid memory layer for Daedalus — V2 (Phase 1 + Phase 2).

Adds on top of DaedalusMemory (ChromaDB):

  Working memory  — per-session in-RAM buffer of recent turns + extracted facts.
                    Fast O(1) access; cleared when session ends.

  Episodic memory — mid-term: key facts/entities extracted from each session turn
                    and stored in a dedicated ChromaDB collection so they survive
                    restarts.  Now with importance scoring (recency × access_count).

  Hybrid retrieval — BM25 (lexical) + dense (ChromaDB) merged via Reciprocal
                     Rank Fusion (RRF).  Catches exact tokens that embeddings
                     blur (file paths, function names, error codes).

  LLM extraction  — fire-and-forget Claude call for richer fact extraction.
                    Regex remains as the synchronous fallback.

  Semantic fusion — at retrieval time, merges scores from all memory tiers
                    (working / episodic / semantic / lessons / global) into a
                    single ranked list, with configurable weights.

Design goals
------------
* Zero new infrastructure — runs on existing ChromaDB PersistentClient + ONNX embeddings.
* Drop-in enhancement — wraps DaedalusMemory; agent.py opts-in by calling
  `Mem0Memory(base=self.memory)` and using `fused_context()`.
* Lightweight sync path — heuristic fact extraction stays inline so latency
  remains low on every turn capture.  Optional LLM extraction runs in background.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)

try:
    from rank_bm25 import BM25Okapi  # type: ignore
    _HAS_BM25 = True
except ImportError:
    _HAS_BM25 = False
    log.warning("rank_bm25 not installed — hybrid retrieval will fall back to dense-only")


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

@dataclass
class MemoryEntry:
    id: str
    content: str
    source: str          # 'working' | 'episodic' | 'semantic' | 'lesson' | 'global'
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Working memory (in-RAM, per-session)
# ---------------------------------------------------------------------------

class WorkingMemory:
    """Bounded circular buffer of recent turns + a small fact ledger per session."""

    def __init__(self, max_turns: int = 12, max_facts: int = 30) -> None:
        self._turns: dict[str, deque[dict[str, Any]]] = defaultdict(
            lambda: deque(maxlen=max_turns)
        )
        self._facts: dict[str, list[str]] = defaultdict(list)
        self._max_facts = max_facts

    def push_turn(self, session_id: str, role: str, content: str) -> None:
        self._turns[session_id].append({"role": role, "content": content, "ts": time.time()})

    def add_fact(self, session_id: str, fact: str) -> None:
        facts = self._facts[session_id]
        if fact not in facts:
            facts.append(fact)
            if len(facts) > self._max_facts:
                facts.pop(0)

    def get_recent_turns(self, session_id: str, n: int = 6) -> list[dict[str, Any]]:
        turns = list(self._turns.get(session_id, []))
        return turns[-n:]

    def get_facts(self, session_id: str) -> list[str]:
        return list(self._facts.get(session_id, []))

    def clear_session(self, session_id: str) -> None:
        self._turns.pop(session_id, None)
        self._facts.pop(session_id, None)

    def search(self, session_id: str, query: str, top_k: int = 4) -> list[MemoryEntry]:
        """Keyword-overlap search over working memory (no embedding needed)."""
        q_tokens = set(_tokenize(query))
        candidates: list[tuple[float, str, str]] = []

        for turn in self._turns.get(session_id, []):
            text = turn["content"]
            overlap = len(q_tokens & set(_tokenize(text)))
            if overlap:
                candidates.append((overlap / max(len(q_tokens), 1), "working_turn", text))

        for fact in self._facts.get(session_id, []):
            overlap = len(q_tokens & set(_tokenize(fact)))
            if overlap:
                candidates.append((overlap / max(len(q_tokens), 1), "working_fact", fact))

        candidates.sort(key=lambda x: x[0], reverse=True)
        return [
            MemoryEntry(
                id=f"wm:{hashlib.md5(c[2].encode()).hexdigest()[:8]}",
                content=c[2],
                source="working",
                score=c[0],
            )
            for c in candidates[:top_k]
        ]


# ---------------------------------------------------------------------------
# BM25 hybrid index (lexical retrieval over a ChromaDB collection)
# ---------------------------------------------------------------------------

class BM25Index:
    """Lightweight BM25 wrapper around a ChromaDB collection.

    Rebuilt lazily on first query and on demand.  Keeps a parallel id↔doc
    map so we can map BM25 rankings back to ChromaDB ids and merge with
    dense results via Reciprocal Rank Fusion.
    """

    def __init__(self, collection: Any, name: str) -> None:
        self._col = collection
        self._name = name
        self._bm25: Any = None
        self._ids: list[str] = []
        self._docs: list[str] = []
        self._metas: list[dict[str, Any]] = []
        self._built_at: float = 0.0
        self._doc_count_at_build: int = 0

    def rebuild(self) -> None:
        """Snapshot the collection and rebuild the BM25 index."""
        if not _HAS_BM25:
            return
        try:
            res = self._col.get(limit=10_000)
        except Exception as exc:
            log.warning("BM25Index(%s).rebuild: collection.get failed: %s", self._name, exc)
            return
        self._ids = list(res.get("ids") or [])
        self._docs = list(res.get("documents") or [])
        self._metas = list(res.get("metadatas") or [])
        if not self._docs:
            self._bm25 = None
            self._built_at = time.time()
            self._doc_count_at_build = 0
            return
        tokenized = [_tokenize(d or "") for d in self._docs]
        self._bm25 = BM25Okapi(tokenized)
        self._built_at = time.time()
        self._doc_count_at_build = len(self._docs)
        log.debug("BM25Index(%s) rebuilt: %d docs", self._name, len(self._docs))

    def _maybe_rebuild(self) -> None:
        """Rebuild if not built yet, or if collection has grown >20% since last build."""
        if self._bm25 is None and self._built_at == 0.0:
            self.rebuild()
            return
        try:
            current = self._col.count()
        except Exception:
            return
        if current == 0:
            return
        if self._doc_count_at_build == 0:
            self.rebuild()
            return
        growth = (current - self._doc_count_at_build) / max(1, self._doc_count_at_build)
        if growth > 0.2 or (time.time() - self._built_at) > 600:  # 10 min stale
            self.rebuild()

    def search(self, query: str, top_k: int = 10) -> list[MemoryEntry]:
        if not _HAS_BM25:
            return []
        self._maybe_rebuild()
        if self._bm25 is None or not self._docs:
            return []
        try:
            q_tokens = _tokenize(query)
            if not q_tokens:
                return []
            scores = self._bm25.get_scores(q_tokens)
        except Exception as exc:
            log.warning("BM25Index(%s).search failed: %s", self._name, exc)
            return []
        # Top-K by score
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        out: list[MemoryEntry] = []
        max_score = ranked[0][1] if ranked else 0.0
        if max_score <= 0:
            return []
        for idx, sc in ranked[:top_k]:
            if sc <= 0:
                break
            out.append(MemoryEntry(
                id=self._ids[idx] if idx < len(self._ids) else f"bm25:{idx}",
                content=self._docs[idx] if idx < len(self._docs) else "",
                source=f"bm25:{self._name}",
                score=float(sc / max_score),  # normalize 0..1
                metadata=self._metas[idx] if idx < len(self._metas) else {},
            ))
        return out


def reciprocal_rank_fusion(
    rankings: list[list[MemoryEntry]],
    k: int = 60,
    top_k: int = 10,
) -> list[MemoryEntry]:
    """Merge multiple ranked lists into one via RRF.

    RRF score for a doc = sum over rankings of 1 / (k + rank_in_that_ranking).
    Documents are de-duped by content (first occurrence wins for the carrier
    MemoryEntry; source becomes "hybrid").  Returns top_k merged entries.
    """
    fused: dict[str, dict[str, Any]] = {}
    for ranking in rankings:
        for rank, entry in enumerate(ranking):
            key = _content_key(entry.content)
            if key not in fused:
                fused[key] = {
                    "entry": entry,
                    "score": 0.0,
                    "sources": set(),
                }
            fused[key]["score"] += 1.0 / (k + rank + 1)
            fused[key]["sources"].add(entry.source)
    merged = sorted(fused.values(), key=lambda x: x["score"], reverse=True)
    out: list[MemoryEntry] = []
    for m in merged[:top_k]:
        e = m["entry"]
        e.score = m["score"]
        if len(m["sources"]) > 1:
            e.source = "hybrid"
        out.append(e)
    return out


def _content_key(content: str) -> str:
    """Stable de-dup key for a content string."""
    return hashlib.md5((content or "").strip().lower().encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Episodic memory (ChromaDB-backed, with importance scoring)
# ---------------------------------------------------------------------------

_COL_EPISODIC = "daedalus_episodic"

# Importance-scoring tunables
_HALF_LIFE_HOURS = 24 * 14  # facts lose half their recency weight every 2 weeks


class EpisodicMemory:
    """Stores extracted facts/entities from conversation turns in ChromaDB.

    Each fact metadata carries:
      - session_id, role, ts (epoch seconds)
      - access_count: int (incremented on every search hit)
      - importance: float (1.0 default; can be bumped by reflection)
      - kind: 'preference' | 'decision' | 'fact' | 'todo' | 'general'
      - last_accessed: float (epoch)

    Importance score at retrieval time =
        base_score (semantic relevance)
        × recency_weight (exp decay by HALF_LIFE_HOURS)
        × log(1 + access_count) × importance_factor
    """

    def __init__(self, chroma_client: Any) -> None:
        self._col = chroma_client.get_or_create_collection(_COL_EPISODIC)
        self._bm25 = BM25Index(self._col, "episodic")
        log.info("EpisodicMemory: ready (%d facts)", self._col.count())

    def store_facts(
        self,
        session_id: str,
        facts: list[str | dict[str, Any]],
        role: str,
    ) -> None:
        """Store facts. Each fact can be a string or a dict with kind/importance.

        Idempotent: if a fact with the same id already exists, preserves its
        access_count / last_accessed / promoted_to_lesson metadata (only
        importance and kind may be upgraded by the new value).
        """
        if not facts:
            return
        ts = time.time()
        candidates: list[tuple[str, str, dict[str, Any]]] = []
        for fact in facts:
            if isinstance(fact, dict):
                text = str(fact.get("text", "")).strip()
                kind = str(fact.get("kind", "general"))[:30]
                importance = float(fact.get("importance", 1.0))
            else:
                text = str(fact).strip()
                kind = "general"
                importance = 1.0
            if not text:
                continue
            fid = f"ep:{session_id}:{hashlib.md5(text.encode()).hexdigest()[:12]}"
            candidates.append((fid, text[:2000], {
                "session_id": session_id,
                "role": role,
                "ts": ts,
                "access_count": 0,
                "importance": importance,
                "kind": kind,
                "last_accessed": ts,
            }))
        if not candidates:
            return
        # Look up existing rows so we can preserve mutable metadata
        ids_all = [c[0] for c in candidates]
        existing_meta: dict[str, dict[str, Any]] = {}
        try:
            res = self._col.get(ids=ids_all)
            for i, eid in enumerate(res.get("ids") or []):
                metas_list = res.get("metadatas") or []
                if i < len(metas_list):
                    existing_meta[eid] = dict(metas_list[i] or {})
        except Exception:
            pass
        # Merge: preserve counts, take max() of importance, keep older ts
        merged_metas: list[dict[str, Any]] = []
        for fid, _, new_meta in candidates:
            old = existing_meta.get(fid)
            if old:
                new_meta["access_count"] = int(old.get("access_count", 0))
                new_meta["last_accessed"] = float(old.get("last_accessed", new_meta["last_accessed"]))
                new_meta["ts"] = float(old.get("ts", new_meta["ts"]))  # original first-seen
                if old.get("promoted_to_lesson"):
                    new_meta["promoted_to_lesson"] = True
                new_meta["importance"] = max(
                    float(old.get("importance", 1.0)),
                    float(new_meta["importance"]),
                )
            merged_metas.append(new_meta)
        try:
            self._col.upsert(
                ids=ids_all,
                documents=[c[1] for c in candidates],
                metadatas=merged_metas,
            )
        except Exception as exc:
            log.warning("EpisodicMemory.store_facts failed: %s", exc)

    def _recency_weight(self, ts: float, now: float) -> float:
        """Exponential decay: 1.0 at now, 0.5 at HALF_LIFE_HOURS ago."""
        if not ts or ts <= 0:
            return 0.5
        age_h = max(0.0, (now - ts) / 3600.0)
        return 0.5 ** (age_h / _HALF_LIFE_HOURS)

    def _bump_access(self, ids: list[str]) -> None:
        """Increment access_count + update last_accessed for a list of fact ids."""
        if not ids:
            return
        try:
            res = self._col.get(ids=ids)
        except Exception:
            return
        now = time.time()
        existing_ids = res.get("ids") or []
        metas = res.get("metadatas") or []
        new_metas: list[dict[str, Any]] = []
        for i in range(len(existing_ids)):
            m = dict(metas[i]) if i < len(metas) else {}
            m["access_count"] = int(m.get("access_count", 0)) + 1
            m["last_accessed"] = now
            new_metas.append(m)
        try:
            self._col.update(ids=list(existing_ids), metadatas=new_metas)
        except Exception as exc:
            log.debug("EpisodicMemory._bump_access failed (non-fatal): %s", exc)

    def search(
        self,
        query: str,
        top_k: int = 5,
        session_id: str | None = None,
        bump: bool = True,
    ) -> list[MemoryEntry]:
        if self._col.count() == 0:
            return []
        # Dense search
        where = {"session_id": session_id} if session_id else None
        try:
            res = self._col.query(
                query_texts=[query[:2000]],
                n_results=min(top_k * 2, self._col.count()),
                where=where,
            )
        except Exception as exc:
            log.warning("EpisodicMemory.search dense failed: %s", exc)
            return []
        dense: list[MemoryEntry] = []
        ids_l = (res.get("ids") or [[]])[0]
        docs_l = (res.get("documents") or [[]])[0]
        dists_l = (res.get("distances") or [[]])[0]
        metas_l = (res.get("metadatas") or [[]])[0]
        now = time.time()
        for i, eid in enumerate(ids_l):
            dist = dists_l[i] if i < len(dists_l) else 1.0
            base = max(0.0, 1.0 - dist)
            meta = metas_l[i] if i < len(metas_l) else {}
            rec_w = self._recency_weight(float(meta.get("ts", now)), now)
            access = int(meta.get("access_count", 0))
            imp = float(meta.get("importance", 1.0))
            # boost = 1 + 0.2 * log(1 + access) capped at 2.0
            from math import log as _log
            access_boost = 1.0 + 0.2 * _log(1 + access)
            score = base * rec_w * access_boost * imp
            dense.append(MemoryEntry(
                id=eid,
                content=docs_l[i] if i < len(docs_l) else "",
                source="episodic",
                score=score,
                metadata=meta,
            ))
        # Re-rank dense by importance-adjusted score BEFORE RRF — otherwise RRF
        # uses raw ChromaDB distance order and the recency/access/importance
        # weighting is effectively ignored.
        dense.sort(key=lambda e: e.score, reverse=True)
        dense = dense[:top_k]
        # BM25 lexical (no session filter for now — limitation of our snapshot index)
        bm25_hits = self._bm25.search(query, top_k=top_k * 2)
        if session_id:
            bm25_hits = [h for h in bm25_hits if h.metadata.get("session_id") == session_id]
        for h in bm25_hits:
            h.source = "episodic"  # unify source label
        # RRF fusion
        merged = reciprocal_rank_fusion([dense, bm25_hits], top_k=top_k)
        if bump and merged:
            self._bump_access([m.id for m in merged if m.id.startswith("ep:")])
        return merged

    def count(self) -> int:
        return self._col.count()

    def all_facts(self, limit: int = 200) -> list[dict[str, Any]]:
        try:
            res = self._col.get(limit=limit)
        except Exception:
            return []
        out = []
        ids = res.get("ids") or []
        docs = res.get("documents") or []
        metas = res.get("metadatas") or []
        for i, fid in enumerate(ids):
            out.append({
                "id": fid,
                "content": docs[i] if i < len(docs) else "",
                "metadata": metas[i] if i < len(metas) else {},
            })
        out.sort(key=lambda x: x["metadata"].get("ts", 0), reverse=True)
        return out

    def exists(self, fact_id: str) -> bool:
        """True if a fact with this id is stored (raises if the store cannot be read)."""
        return bool(self._col.get(ids=[fact_id]).get("ids"))

    def delete(self, fact_ids: list[str]) -> int:
        if not fact_ids:
            return 0
        try:
            self._col.delete(ids=fact_ids)
            return len(fact_ids)
        except Exception as exc:
            log.warning("EpisodicMemory.delete failed: %s", exc)
            return 0

    def rebuild_bm25(self) -> None:
        self._bm25.rebuild()

    # Reflection helpers --------------------------------------------------

    def update_metadata(self, fact_id: str, patch: dict[str, Any]) -> bool:
        try:
            res = self._col.get(ids=[fact_id])
        except Exception:
            return False
        if not res.get("ids"):
            return False
        meta = dict((res.get("metadatas") or [{}])[0])
        meta.update(patch)
        try:
            self._col.update(ids=[fact_id], metadatas=[meta])
            return True
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Fact extraction (heuristic — fast, sync, no LLM)
# ---------------------------------------------------------------------------

_PREF_PATTERNS = [
    re.compile(r"(?:предпочитам|обичам|искам|харесвам|ползвам|използвам)\s+(.+)", re.I),
    re.compile(r"(?:prefer|use|like|love|always\s+use|i\s+want)\s+(.+)", re.I),
]
_DECISION_PATTERNS = [
    re.compile(r"(?:решихме|решавам|избирам|избрах|ще\s+ползваме)\s+(.+)", re.I),
    re.compile(r"(?:we\s+decided|i\s+decided|chose|will\s+use)\s+(.+)", re.I),
]
_IMPORTANT = re.compile(r"(?:важно|критично|задължително|never|always|must|important|critical)[\s:]+(.+)", re.I)


def extract_facts(text: str, max_facts: int = 5) -> list[str]:
    """Sync heuristic fact extraction (regex). Always runs inline."""
    facts: list[str] = []
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())

    for pat in _PREF_PATTERNS + _DECISION_PATTERNS + [_IMPORTANT]:
        for sent in sentences:
            m = pat.search(sent)
            if m:
                fact = m.group(0).strip()[:200]
                if fact and fact not in facts:
                    facts.append(fact)

    for sent in sentences:
        s = sent.strip()
        if 20 < len(s) < 150 and s[0].isupper() and re.search(r"\b(?:е|are|is|was|has|have|can|will|трябва|може)\b", s, re.I):
            if s not in facts:
                facts.append(s)

    return facts[:max_facts]


# ---------------------------------------------------------------------------
# LLM-based fact extraction (Claude — runs in background)
# ---------------------------------------------------------------------------

_LLM_EXTRACT_SYSTEM = """You are Daedalus's fact-extraction subroutine.
From one conversation turn, extract 0–5 ATOMIC facts worth remembering long-term.

Each fact must be:
- One sentence, ≤200 chars
- Self-contained (no dangling references like "this" or "it" — name the thing)
- About durable info: preferences, decisions, facts about the codebase, names, urls, identifiers

DO NOT include:
- Generic pleasantries, acknowledgements, or chitchat
- Hypothetical or speculative statements
- One-off task instructions

Respond ONLY with JSON in this shape:
{"facts": [{"text": "<one sentence>", "kind": "preference|decision|fact|todo|general", "importance": 0.5-1.0}]}

If nothing is worth remembering, return {"facts": []}."""


async def extract_facts_llm(
    llm_call: Callable,
    text: str,
    role: str = "user",
    timeout: float = 30.0,
) -> list[dict[str, Any]]:
    """Async LLM-based fact extraction. Returns list of {text, kind, importance} dicts.

    Returns [] on any failure (timeout, JSON parse, network).  Safe to fire-and-forget.
    """
    if not text or len(text.strip()) < 20:
        return []
    prompt = f"[{role}] {text[:4000]}\n\nExtract facts (or empty list)."
    try:
        raw = await asyncio.wait_for(
            llm_call(_LLM_EXTRACT_SYSTEM, [{"role": "user", "content": prompt}]),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        log.debug("extract_facts_llm: timeout")
        return []
    except Exception as exc:
        log.debug("extract_facts_llm: LLM call failed: %s", exc)
        return []
    parsed = _safe_json(raw)
    if not isinstance(parsed, dict):
        return []
    facts = parsed.get("facts", [])
    if not isinstance(facts, list):
        return []
    out: list[dict[str, Any]] = []
    for f in facts[:5]:
        if not isinstance(f, dict):
            continue
        text_v = str(f.get("text", "")).strip()
        if len(text_v) < 10:
            continue
        out.append({
            "text": text_v[:300],
            "kind": str(f.get("kind", "general"))[:30],
            "importance": max(0.3, min(1.5, float(f.get("importance", 1.0)))),
        })
    return out


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


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", (text or "").lower())


# ---------------------------------------------------------------------------
# Mem0Memory — the main facade
# ---------------------------------------------------------------------------

class Mem0Memory:
    """Hybrid memory: Working + Episodic + Semantic fusion over DaedalusMemory.

    Phase-1 additions:
      - BM25 hybrid retrieval over episodic + sessions
      - Importance scoring (recency × access_count) in EpisodicMemory
      - Optional LLM-based fact extraction (fire-and-forget, attached via
        ``attach_llm_extractor()``)

    Usage in agent.py::

        from .memory_mem0 import Mem0Memory
        self.mem0 = Mem0Memory(base=self.memory)
        self.mem0.attach_llm_extractor(_call_claude)  # optional

        # On each turn (after DaedalusMemory.capture_turn):
        self.mem0.capture(session_id, "user", user_message)
        self.mem0.capture(session_id, "assistant", reply)

        # Instead of manual multi-col retrieval:
        ctx = self.mem0.fused_context(session_id, query, top_k=12)
    """

    # Fusion weights (relative; sum is not required)
    _W_WORKING  = 1.4
    _W_EPISODIC = 1.2   # bumped up after importance scoring
    _W_SEMANTIC = 1.0
    _W_LESSON   = 1.3   # bumped up — lessons are owner-validated
    _W_GLOBAL   = 0.8

    def __init__(self, base: Any, max_turns: int = 12, max_facts: int = 30) -> None:
        self._base = base
        self._working = WorkingMemory(max_turns=max_turns, max_facts=max_facts)
        self._episodic = EpisodicMemory(base._client)
        # BM25 over sessions_col + lessons_col for hybrid retrieval
        self._bm25_sessions = BM25Index(base.sessions_col, "sessions")
        self._bm25_lessons = BM25Index(base.lessons_col, "lessons")
        self._llm_extractor: Callable | None = None
        self._background_tasks: set[asyncio.Task] = set()
        log.info("Mem0Memory: initialised (episodic_facts=%d, bm25=%s)",
                 self._episodic.count(), _HAS_BM25)

    # ------------------------------------------------------------------
    # LLM extractor wiring
    # ------------------------------------------------------------------

    def attach_llm_extractor(self, llm_call: Callable) -> None:
        """Wire up the LLM-based fact extractor (fire-and-forget per turn)."""
        self._llm_extractor = llm_call
        log.info("Mem0Memory: LLM extractor attached")

    # ------------------------------------------------------------------
    # Capture
    # ------------------------------------------------------------------

    def capture(self, session_id: str, role: str, content: str) -> None:
        """Push a turn into working memory and extract facts (sync + async).

        Call AFTER DaedalusMemory.capture_turn() — this adds the Mem0 layer.
        Sync: regex fact extraction (always runs).
        Async (if LLM extractor attached): Claude extraction in background.
        """
        if not content or not content.strip():
            return
        self._working.push_turn(session_id, role, content)

        # Sync regex pass (cheap, always)
        facts = extract_facts(content)
        if facts:
            self._working.add_fact(session_id, "\n".join(facts))
            self._episodic.store_facts(session_id, facts, role)

        # Async LLM pass (richer, runs in background)
        if self._llm_extractor is not None and len(content.strip()) >= 40:
            try:
                t = asyncio.create_task(
                    self._llm_extract_and_store(session_id, role, content)
                )
                self._background_tasks.add(t)
                t.add_done_callback(self._background_tasks.discard)
            except RuntimeError:
                # No running loop — extractor will be skipped
                pass

    async def _llm_extract_and_store(self, session_id: str, role: str, content: str) -> None:
        """Background LLM extraction + episodic store."""
        try:
            facts = await extract_facts_llm(self._llm_extractor, content, role)
            if facts:
                self._episodic.store_facts(session_id, facts, role)
                for f in facts:
                    self._working.add_fact(session_id, f["text"])
                log.debug("LLM extraction: %d facts stored for session=%s", len(facts), session_id)
        except Exception as exc:
            log.debug("LLM extraction failed (non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def fused_context(
        self,
        session_id: str,
        query: str,
        top_k: int = 12,
        include_working: bool = True,
        include_episodic: bool = True,
        include_semantic: bool = True,
        include_lessons: bool = True,
        include_global: bool = True,
    ) -> list[MemoryEntry]:
        """Return *top_k* most relevant memories across all tiers, fused.

        Uses BM25+dense hybrid for sessions and lessons (via RRF), then merges
        with weighted source-level scores.
        """
        seen: set[str] = set()
        merged: list[MemoryEntry] = []

        def _add(entries: list[MemoryEntry], weight: float) -> None:
            for e in entries:
                key = _content_key(e.content)
                if key in seen:
                    continue
                seen.add(key)
                e.score = e.score * weight
                merged.append(e)

        # 1. Working memory (keyword overlap, fastest)
        if include_working:
            _add(self._working.search(session_id, query, top_k=4), self._W_WORKING)

        # 2. Episodic (BM25 + dense fused internally, importance-scored)
        if include_episodic:
            _add(self._episodic.search(query, top_k=6), self._W_EPISODIC)

        # 3. Sessions (BM25 + dense fused via RRF)
        if include_semantic:
            dense_sessions = [
                MemoryEntry(
                    id=r["id"],
                    content=r["content"],
                    source="semantic",
                    score=max(0.0, 1.0 - (r.get("distance") or 0.5)),
                    metadata=r.get("metadata", {}),
                )
                for r in self._base.retrieve_relevant(query, top_k=6)
            ]
            bm25_sessions = self._bm25_sessions.search(query, top_k=6)
            for h in bm25_sessions:
                h.source = "semantic"
            fused_sessions = reciprocal_rank_fusion([dense_sessions, bm25_sessions], top_k=6)
            _add(fused_sessions, self._W_SEMANTIC)

        # 4. Lessons (BM25 + dense fused via RRF)
        if include_lessons:
            dense_lessons = [
                MemoryEntry(
                    id=r["id"],
                    content=r["content"],
                    source="lesson",
                    score=max(0.0, 1.0 - (r.get("distance") or 0.5)),
                    metadata=r.get("metadata", {}),
                )
                for r in self._base.retrieve_lessons(query, top_k=5)
            ]
            bm25_lessons = self._bm25_lessons.search(query, top_k=5)
            for h in bm25_lessons:
                h.source = "lesson"
            fused_lessons = reciprocal_rank_fusion([dense_lessons, bm25_lessons], top_k=5)
            _add(fused_lessons, self._W_LESSON)

        # 5. Global knowledge (dense only — usually short docs, BM25 less useful)
        if include_global:
            raw = self._base.retrieve_knowledge(query, top_k=4)
            _add([
                MemoryEntry(
                    id=r["id"],
                    content=r["content"],
                    source="global",
                    score=max(0.0, 1.0 - (r.get("distance") or 0.5)),
                    metadata=r.get("metadata", {}),
                )
                for r in raw
            ], self._W_GLOBAL)

        merged.sort(key=lambda e: e.score, reverse=True)
        return merged[:top_k]

    def format_context_block(
        self,
        session_id: str,
        query: str,
        top_k: int = 10,
    ) -> str:
        """Return a formatted string ready for injection into the system prompt."""
        entries = self.fused_context(session_id, query, top_k=top_k)
        if not entries:
            return ""
        SOURCE_LABEL = {
            "working":  "🟡 Working",
            "episodic": "🔵 Episodic",
            "semantic": "🟢 Semantic",
            "lesson":   "⭐ Lesson",
            "global":   "📚 Global",
            "hybrid":   "🟣 Hybrid",
        }
        lines = ["=== MEMORY CONTEXT (Mem0 v2 fused) ==="]
        for e in entries:
            label = SOURCE_LABEL.get(e.source, e.source)
            lines.append(f"[{label} | score={e.score:.2f}] {e.content[:400]}")
        lines.append("=== END MEMORY CONTEXT ===")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Probing / debug
    # ------------------------------------------------------------------

    def get_working_facts(self, session_id: str) -> list[str]:
        return self._working.get_facts(session_id)

    def get_working_turns(self, session_id: str, n: int = 6) -> list[dict[str, Any]]:
        return self._working.get_recent_turns(session_id, n=n)

    def clear_working(self, session_id: str) -> None:
        self._working.clear_session(session_id)

    def rebuild_bm25_indices(self) -> None:
        """Force-rebuild all BM25 indices (after consolidation, deletes, etc.)."""
        self._episodic.rebuild_bm25()
        self._bm25_sessions.rebuild()
        self._bm25_lessons.rebuild()

    # Convenience accessor for reflection layer
    @property
    def episodic(self) -> EpisodicMemory:
        return self._episodic

    def stats(self) -> dict[str, Any]:
        return {
            "episodic_facts": self._episodic.count(),
            "working_sessions": len(self._working._turns),
            "bm25_enabled": _HAS_BM25,
            "llm_extractor_attached": self._llm_extractor is not None,
        }
