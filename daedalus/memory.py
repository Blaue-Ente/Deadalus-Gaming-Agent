"""Daedalus persistent memory layer.

Inspired by claude-mem (capture → compress → inject) and jcode (project vs global split),
implemented natively in Python with ChromaDB + ONNX embeddings (no external API needed).

Three collections:
- daedalus_global    — long-term Iyveris knowledge (loaded once on boot from skills/plans)
- daedalus_sessions  — conversation turns (every user/assistant message)
- daedalus_artifacts — code artifacts Daedalus has generated (filename + summary + content)

All three live on disk at DATA_DIR/chroma/ and survive restarts.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import json

import chromadb
from chromadb.config import Settings

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Replit DB persistence key for cross-deployment discoveries sync
# ---------------------------------------------------------------------------
_RDB_DISCOVERIES_KEY = "adn:discoveries_v1"

def _rdb_get() -> list[dict[str, Any]]:
    """Load discoveries snapshot (Replit DB, else DAEDALUS_DATA/kv)."""
    try:
        from . import kv
        raw = kv.get(_RDB_DISCOVERIES_KEY)
        if raw is None:
            return []
        data = raw if isinstance(raw, list) else json.loads(str(raw))
        return data if isinstance(data, list) else []
    except Exception as exc:
        log.warning("RDB load failed: %s", exc)
        return []

def _rdb_put(items: list[dict[str, Any]]) -> None:
    """Save discoveries snapshot (silently ignores failures)."""
    try:
        from . import kv
        kv.set(_RDB_DISCOVERIES_KEY, items)
    except Exception as exc:
        log.warning("RDB save failed: %s", exc)

# ---------------------------------------------------------------------------
# Storage paths — resolved at init so DAEDALUS_DATA (Docker volume) wins
# ---------------------------------------------------------------------------

def _chroma_dir() -> Path:
    from .paths import chroma_dir
    return chroma_dir()

# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------

_COL_GLOBAL = "daedalus_global"
_COL_SESSIONS = "daedalus_sessions"
_COL_ARTIFACTS = "daedalus_artifacts"
_COL_LESSONS = "daedalus_lessons"     # self-reflection: lessons learned across sessions
_COL_EPISODES = "daedalus_episodes"   # hierarchical memory: session summaries
_COL_DISCOVERIES = "daedalus_discoveries"  # web/github finds awaiting owner approval


class DaedalusMemory:
    """ChromaDB-backed persistent memory for Daedalus.

    Single instance per process. Auto-initialises three collections on first access.
    Uses ChromaDB's default ONNX MiniLM embedding (no API key, ~80MB model auto-downloaded).
    """

    _instance: "DaedalusMemory | None" = None

    def __new__(cls) -> "DaedalusMemory":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self) -> None:
        chroma = _chroma_dir()
        log.info("Daedalus memory: initialising ChromaDB at %s", chroma)
        self._client = chromadb.PersistentClient(
            path=str(chroma),
            settings=Settings(anonymized_telemetry=False, allow_reset=False),
        )
        self.global_col = self._client.get_or_create_collection(_COL_GLOBAL)
        self.sessions_col = self._client.get_or_create_collection(_COL_SESSIONS)
        self.artifacts_col = self._client.get_or_create_collection(_COL_ARTIFACTS)
        self.lessons_col = self._client.get_or_create_collection(_COL_LESSONS)
        self.episodes_col = self._client.get_or_create_collection(_COL_EPISODES)
        self.discoveries_col = self._client.get_or_create_collection(_COL_DISCOVERIES)
        # Restore discoveries from Replit DB if ChromaDB is empty (e.g. fresh deployment)
        self._rdb_restore()
        log.info(
            "Daedalus memory: ready (global=%d, sessions=%d, artifacts=%d, lessons=%d, episodes=%d, discoveries=%d)",
            self.global_col.count(),
            self.sessions_col.count(),
            self.artifacts_col.count(),
            self.lessons_col.count(),
            self.episodes_col.count(),
            self.discoveries_col.count(),
        )

    # ------------------------------------------------------------------
    # Replit DB — cross-deployment persistence for discoveries
    # ------------------------------------------------------------------

    def _rdb_snapshot(self) -> None:
        """Dump all current discoveries into Replit DB."""
        try:
            res = self.discoveries_col.get()
            items = []
            for i, did in enumerate(res.get("ids", [])):
                items.append({
                    "id": did,
                    "document": (res.get("documents") or [""])[i],
                    "metadata": (res.get("metadatas") or [{}])[i],
                })
            _rdb_put(items)
            log.debug("RDB snapshot: %d discoveries saved", len(items))
        except Exception as exc:
            log.warning("RDB snapshot failed: %s", exc)

    def _rdb_restore(self) -> None:
        """Import discoveries from Replit DB into ChromaDB if the collection is empty."""
        try:
            if self.discoveries_col.count() > 0:
                return  # ChromaDB already has data — skip restore
            items = _rdb_get()
            if not items:
                return
            ids = [item["id"] for item in items]
            docs = [item.get("document", "") for item in items]
            metas = [item.get("metadata", {}) for item in items]
            self.discoveries_col.add(ids=ids, documents=docs, metadatas=metas)
            log.info("RDB restore: %d discoveries loaded from Replit DB", len(items))
        except Exception as exc:
            log.warning("RDB restore failed: %s", exc)

    # ------------------------------------------------------------------
    # Session memory — every user/assistant turn
    # ------------------------------------------------------------------

    def capture_turn(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Store a single conversation turn. Returns the assigned id."""
        if not content or not content.strip():
            return ""
        turn_id = f"{session_id}:{uuid.uuid4().hex[:12]}"
        meta = {
            "session_id": session_id,
            "role": role,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if metadata:
            # ChromaDB metadata must be primitive — flatten/skip complex values
            for k, v in metadata.items():
                if isinstance(v, (str, int, float, bool)):
                    meta[k] = v
        self.sessions_col.add(
            ids=[turn_id],
            documents=[content[:8000]],  # cap per-turn size
            metadatas=[meta],
        )
        return turn_id

    def retrieve_relevant(
        self,
        query: str,
        top_k: int = 5,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Semantic search across past turns. Optionally restrict to a single session."""
        if self.sessions_col.count() == 0:
            return []
        where = {"session_id": session_id} if session_id else None
        try:
            res = self.sessions_col.query(
                query_texts=[query[:2000]],
                n_results=min(top_k, self.sessions_col.count()),
                where=where,
            )
        except Exception as exc:
            log.warning("Daedalus memory: query failed: %s", exc)
            return []
        out: list[dict[str, Any]] = []
        ids_list = (res.get("ids") or [[]])[0]
        docs_list = (res.get("documents") or [[]])[0]
        metas_list = (res.get("metadatas") or [[]])[0]
        dist_list = (res.get("distances") or [[]])[0]
        for i, doc_id in enumerate(ids_list):
            out.append({
                "id": doc_id,
                "content": docs_list[i] if i < len(docs_list) else "",
                "metadata": metas_list[i] if i < len(metas_list) else {},
                "distance": dist_list[i] if i < len(dist_list) else None,
            })
        return out

    def session_history(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """Get raw chronological history of a single session (most recent N turns)."""
        try:
            res = self.sessions_col.get(where={"session_id": session_id}, limit=10_000)
        except Exception:
            return []
        items = []
        for i, doc_id in enumerate(res.get("ids", [])):
            items.append({
                "id": doc_id,
                "content": (res.get("documents") or [])[i],
                "metadata": (res.get("metadatas") or [{}])[i],
            })
        items.sort(key=lambda x: x["metadata"].get("timestamp", ""))
        return items[-limit:]

    def list_sessions(self, limit: int = 60) -> list[dict[str, Any]]:
        """Return unique sessions sorted by last activity (newest first)."""
        try:
            res = self.sessions_col.get(limit=10_000)
        except Exception:
            return []
        sessions: dict[str, dict] = {}
        docs = res.get("documents") or []
        metas = res.get("metadatas") or []
        for i in range(len(res.get("ids", []))):
            meta = metas[i] if i < len(metas) else {}
            doc = docs[i] if i < len(docs) else ""
            sid = meta.get("session_id", "")
            if not sid:
                continue
            role = meta.get("role", "")
            ts = meta.get("timestamp", "")
            if sid not in sessions:
                sessions[sid] = {
                    "session_id": sid,
                    "turn_count": 0,
                    "preview": "",
                    "first_ts": ts,
                    "last_ts": ts,
                }
            s = sessions[sid]
            s["turn_count"] += 1
            if ts and (not s["last_ts"] or ts > s["last_ts"]):
                s["last_ts"] = ts
            if ts and (not s["first_ts"] or ts < s["first_ts"]):
                s["first_ts"] = ts
            if role == "owner" and not s["preview"]:
                s["preview"] = (doc or "")[:80]
        return sorted(sessions.values(), key=lambda x: x.get("last_ts", ""), reverse=True)[:limit]

    # ------------------------------------------------------------------
    # Global knowledge — loaded once on boot
    # ------------------------------------------------------------------

    def upsert_knowledge(
        self,
        doc_id: str,
        content: str,
        source: str,
        category: str,
        title: str = "",
    ) -> None:
        """Insert or update a single piece of global knowledge."""
        if not content or not content.strip():
            return
        # Chunk if very long (ChromaDB embedding max ~512 tokens; we cap at ~2000 chars per chunk)
        chunks = _chunk_text(content, max_chars=2000)
        ids = [f"{doc_id}#{i}" for i in range(len(chunks))]
        # Remove old chunks for this doc_id (if any) before re-adding
        try:
            self.global_col.delete(ids=ids)
        except Exception:
            pass
        metas = [{
            "source": source,
            "category": category,
            "title": title or doc_id,
            "chunk_index": i,
            "chunk_total": len(chunks),
        } for i in range(len(chunks))]
        self.global_col.add(ids=ids, documents=chunks, metadatas=metas)

    def retrieve_knowledge(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Semantic search across all loaded knowledge (skills, plans, concepts)."""
        if self.global_col.count() == 0:
            return []
        try:
            res = self.global_col.query(
                query_texts=[query[:2000]],
                n_results=min(top_k, self.global_col.count()),
            )
        except Exception as exc:
            log.warning("Daedalus knowledge query failed: %s", exc)
            return []
        out = []
        ids_list = (res.get("ids") or [[]])[0]
        docs_list = (res.get("documents") or [[]])[0]
        metas_list = (res.get("metadatas") or [[]])[0]
        dist_list = (res.get("distances") or [[]])[0]
        for i, doc_id in enumerate(ids_list):
            out.append({
                "id": doc_id,
                "content": docs_list[i] if i < len(docs_list) else "",
                "metadata": metas_list[i] if i < len(metas_list) else {},
                "distance": dist_list[i] if i < len(dist_list) else None,
            })
        return out

    # ------------------------------------------------------------------
    # Artifacts — code Daedalus has generated
    # ------------------------------------------------------------------

    def capture_artifact(
        self,
        session_id: str,
        filename: str,
        language: str,
        code: str,
        summary: str,
    ) -> str:
        """Record a generated code artifact for future reference."""
        art_id = f"art:{session_id}:{uuid.uuid4().hex[:10]}"
        # Index summary + filename + first 1500 chars of code together for retrieval
        document = f"FILE: {filename}\nLANG: {language}\nSUMMARY: {summary}\n\nCODE:\n{code[:1500]}"
        self.artifacts_col.add(
            ids=[art_id],
            documents=[document],
            metadatas=[{
                "session_id": session_id,
                "filename": filename,
                "language": language,
                "summary": summary[:500],
                "code_length": len(code),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }],
        )
        return art_id

    def find_similar_artifacts(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        if self.artifacts_col.count() == 0:
            return []
        try:
            res = self.artifacts_col.query(
                query_texts=[query[:2000]],
                n_results=min(top_k, self.artifacts_col.count()),
            )
        except Exception:
            return []
        out = []
        ids_list = (res.get("ids") or [[]])[0]
        docs_list = (res.get("documents") or [[]])[0]
        metas_list = (res.get("metadatas") or [[]])[0]
        for i, art_id in enumerate(ids_list):
            out.append({
                "id": art_id,
                "snippet": (docs_list[i] if i < len(docs_list) else "")[:500],
                "metadata": metas_list[i] if i < len(metas_list) else {},
            })
        return out

    # ------------------------------------------------------------------
    # Lessons — self-reflection capture
    # ------------------------------------------------------------------

    def capture_lesson(
        self,
        lesson: str,
        kind: str = "general",
        session_id: str | None = None,
        confidence: float = 0.7,
    ) -> str:
        """Store a single lesson Daedalus has learned about itself or the owner."""
        if not lesson or not lesson.strip():
            return ""
        lesson_id = f"les:{uuid.uuid4().hex[:12]}"
        meta = {
            "kind": kind,
            "confidence": float(confidence),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if session_id:
            meta["session_id"] = session_id
        self.lessons_col.add(
            ids=[lesson_id],
            documents=[lesson.strip()[:2000]],
            metadatas=[meta],
        )
        return lesson_id

    def retrieve_lessons(self, query: str, top_k: int = 4) -> list[dict[str, Any]]:
        if self.lessons_col.count() == 0:
            return []
        try:
            res = self.lessons_col.query(
                query_texts=[query[:2000]],
                n_results=min(top_k, self.lessons_col.count()),
            )
        except Exception:
            return []
        out = []
        ids_list = (res.get("ids") or [[]])[0]
        docs_list = (res.get("documents") or [[]])[0]
        metas_list = (res.get("metadatas") or [[]])[0]
        for i, lid in enumerate(ids_list):
            out.append({
                "id": lid,
                "content": docs_list[i] if i < len(docs_list) else "",
                "metadata": metas_list[i] if i < len(metas_list) else {},
            })
        return out

    def all_lessons(self, limit: int = 100) -> list[dict[str, Any]]:
        try:
            res = self.lessons_col.get(limit=limit)
        except Exception:
            return []
        items = []
        for i, lid in enumerate(res.get("ids", [])):
            items.append({
                "id": lid,
                "content": (res.get("documents") or [])[i],
                "metadata": (res.get("metadatas") or [{}])[i],
            })
        items.sort(key=lambda x: x["metadata"].get("timestamp", ""), reverse=True)
        return items

    # ------------------------------------------------------------------
    # Episodes — session summaries (hierarchical memory)
    # ------------------------------------------------------------------

    def capture_episode(
        self,
        session_id: str,
        summary: str,
        turn_count: int,
        started_at: str = "",
        ended_at: str = "",
    ) -> str:
        """Store a compressed summary of a chunk of session history."""
        if not summary or not summary.strip():
            return ""
        ep_id = f"ep:{session_id}:{uuid.uuid4().hex[:10]}"
        self.episodes_col.add(
            ids=[ep_id],
            documents=[summary.strip()[:4000]],
            metadatas=[{
                "session_id": session_id,
                "turn_count": int(turn_count),
                "started_at": started_at or "",
                "ended_at": ended_at or datetime.now(timezone.utc).isoformat(),
                "stored_at": datetime.now(timezone.utc).isoformat(),
            }],
        )
        return ep_id

    def retrieve_episodes(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        if self.episodes_col.count() == 0:
            return []
        try:
            res = self.episodes_col.query(
                query_texts=[query[:2000]],
                n_results=min(top_k, self.episodes_col.count()),
            )
        except Exception:
            return []
        out = []
        ids_list = (res.get("ids") or [[]])[0]
        docs_list = (res.get("documents") or [[]])[0]
        metas_list = (res.get("metadatas") or [[]])[0]
        for i, eid in enumerate(ids_list):
            out.append({
                "id": eid,
                "content": docs_list[i] if i < len(docs_list) else "",
                "metadata": metas_list[i] if i < len(metas_list) else {},
            })
        return out

    def session_turn_count(self, session_id: str) -> int:
        try:
            res = self.sessions_col.get(where={"session_id": session_id}, limit=10_000)
            return len(res.get("ids", []))
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # Stats / admin
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        return {
            "data_dir": str(_chroma_dir()),
            "global_knowledge_chunks": self.global_col.count(),
            "session_turns": self.sessions_col.count(),
            "artifacts": self.artifacts_col.count(),
            "lessons": self.lessons_col.count(),
            "episodes": self.episodes_col.count(),
            "discoveries": self.discoveries_col.count(),
        }

    # ------------------------------------------------------------------
    # Discoveries — web/github finds awaiting owner approval
    # ------------------------------------------------------------------

    def capture_discovery(
        self,
        title: str,
        url: str,
        source: str,           # "duckduckgo" | "github" | "manual"
        why_useful: str,
        status: str = "pending",  # pending | approved | rejected
        confidence: float = 0.5,
        kind: str = "library",    # library | article | pattern | tool | doc
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Store a candidate discovery (library/article/pattern) for owner review."""
        if not title or not url:
            return ""
        # Dedup by URL (exact match)
        if self.find_discovery_by_url(url):
            return ""
        # Dedup by title — skip if same title already exists as pending or approved
        try:
            norm_title = title.strip().lower()[:150]
            res = self.discoveries_col.get(limit=500)
            for m in (res.get("metadatas") or []):
                existing_title = (m.get("title") or "").strip().lower()[:150]
                existing_status = m.get("status", "")
                if existing_title == norm_title and existing_status in ("pending", "approved"):
                    return ""
        except Exception:
            pass
        d_id = f"disc:{uuid.uuid4().hex[:12]}"
        meta = {
            "title": title.strip()[:300],
            "url": url.strip()[:500],
            "source": source,
            "kind": kind,
            "status": status,
            "confidence": float(confidence),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        if metadata:
            for k, v in metadata.items():
                if isinstance(v, (str, int, float, bool)) and k not in meta:
                    meta[k] = v
        self.discoveries_col.add(
            ids=[d_id],
            documents=[(why_useful or title).strip()[:2000]],
            metadatas=[meta],
        )
        self._rdb_snapshot()
        self._pg_upsert_discovery(d_id, meta, why_useful or title)
        return d_id

    def list_discoveries(
        self,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        try:
            kwargs: dict[str, Any] = {"limit": limit}
            if status:
                kwargs["where"] = {"status": status}
            res = self.discoveries_col.get(**kwargs)
        except Exception:
            return []
        items = []
        for i, did in enumerate(res.get("ids", [])):
            items.append({
                "id": did,
                "content": (res.get("documents") or [])[i],
                "metadata": (res.get("metadatas") or [{}])[i],
            })
        items.sort(key=lambda x: x["metadata"].get("timestamp", ""), reverse=True)
        return items

    def find_discovery_by_url(self, url: str) -> dict[str, Any] | None:
        """Dedup helper — return existing discovery for a URL if any."""
        if not url:
            return None
        try:
            res = self.discoveries_col.get(where={"url": url.strip()[:500]}, limit=1)
        except Exception:
            return None
        ids = res.get("ids", [])
        if not ids:
            return None
        return {
            "id": ids[0],
            "content": (res.get("documents") or [""])[0],
            "metadata": (res.get("metadatas") or [{}])[0],
        }

    def set_discovery_status(self, discovery_id: str, status: str) -> bool:
        """Approve / reject / reset a discovery."""
        if status not in ("pending", "approved", "rejected"):
            return False
        try:
            res = self.discoveries_col.get(ids=[discovery_id])
            if not res.get("ids"):
                return False
            meta = (res.get("metadatas") or [{}])[0] or {}
            meta["status"] = status
            meta["status_changed_at"] = datetime.now(timezone.utc).isoformat()
            self.discoveries_col.update(ids=[discovery_id], metadatas=[meta])
            self._rdb_snapshot()
            self._pg_upsert_discovery(discovery_id, meta, "")
            return True
        except Exception:
            return False

    def delete_discovery(self, discovery_id: str) -> bool:
        try:
            self.discoveries_col.delete(ids=[discovery_id])
            self._rdb_snapshot()
            self._pg_delete_discovery(discovery_id)
            return True
        except Exception:
            return False

    def delete_lesson(self, lesson_id: str) -> bool:
        """Remove a single learned lesson (admin veto for self-poisoning guard)."""
        try:
            self.lessons_col.delete(ids=[lesson_id])
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # PostgreSQL mirror helpers (fire-and-forget, never block/raise)
    # ------------------------------------------------------------------

    def _pg_upsert_discovery(self, disc_id: str, meta: dict, why_useful: str) -> None:
        """Schedule async upsert of discovery into Postgres (non-blocking)."""
        try:
            import asyncio
            from argos_data_nexus.db import upsert_discovery
            coro = upsert_discovery(
                disc_id,
                meta.get("title", ""),
                url=meta.get("url"),
                source=meta.get("source"),
                kind=meta.get("kind"),
                status=meta.get("status", "pending"),
                confidence=meta.get("confidence"),
                why_useful=why_useful[:500] if why_useful else None,
            )
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(coro)
            except RuntimeError:
                pass
        except Exception:
            pass

    def _pg_delete_discovery(self, disc_id: str) -> None:
        """Schedule async delete of discovery from Postgres (non-blocking)."""
        try:
            import asyncio
            from argos_data_nexus.db import get_pool

            async def _do_delete():
                try:
                    pool = await get_pool()
                    async with pool.acquire() as conn:
                        await conn.execute(
                            "DELETE FROM daedalus_discoveries WHERE id = $1", disc_id
                        )
                except Exception:
                    pass

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_do_delete())
            except RuntimeError:
                pass
        except Exception:
            pass

    def clear_session(self, session_id: str) -> int:
        """Delete all turns for a given session_id. Returns count deleted."""
        try:
            res = self.sessions_col.get(where={"session_id": session_id})
            ids = res.get("ids", [])
            if ids:
                self.sessions_col.delete(ids=ids)
            return len(ids)
        except Exception:
            return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _chunk_text(text: str, max_chars: int = 2000) -> list[str]:
    """Split text on paragraph boundaries, capping each chunk at max_chars."""
    text = text.strip()
    if len(text) <= max_chars:
        return [text]
    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current = ""
    for p in paragraphs:
        if len(current) + len(p) + 2 > max_chars:
            if current:
                chunks.append(current.strip())
            # If single paragraph too big, hard-split
            if len(p) > max_chars:
                for i in range(0, len(p), max_chars):
                    chunks.append(p[i:i + max_chars])
                current = ""
            else:
                current = p
        else:
            current = f"{current}\n\n{p}" if current else p
    if current.strip():
        chunks.append(current.strip())
    return chunks
