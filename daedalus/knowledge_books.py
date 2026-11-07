"""Daedalus Knowledge Books — PDF upload + ChromaDB semantic search.

Endpoints:
  POST /admin/daedalus/knowledge/upload-book   (admin session required)
  GET  /api/v1/daedalus/knowledge/search       (API key required)
"""

from __future__ import annotations

import hashlib
import io
import logging
import uuid
from typing import Annotated

import chromadb
from chromadb.config import Settings
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status

from .host import require_admin
from .host import require_api_key

log = logging.getLogger(__name__)

router = APIRouter(tags=["Daedalus Knowledge"])

# ---------------------------------------------------------------------------
# ChromaDB client — persistent, stored alongside the module
# ---------------------------------------------------------------------------

def _chroma_path() -> str:
    """Books index: SQLite/Docker volume in standalone; ADN keeps /tmp path."""
    from .host import standalone_mode
    from .paths import data_root

    if standalone_mode():
        path = data_root() / "books_chroma"
        path.mkdir(parents=True, exist_ok=True)
        return str(path)
    return "/tmp/argos_chroma"


def _get_collection() -> chromadb.Collection:
    client = chromadb.PersistentClient(
        path=_chroma_path(),
        settings=Settings(anonymized_telemetry=False),
    )
    return client.get_or_create_collection(
        name="daedalus_books",
        metadata={"hnsw:space": "cosine"},
    )


# ---------------------------------------------------------------------------
# PDF text extraction (no heavy deps — uses pypdf if available, else raw)
# ---------------------------------------------------------------------------

def _extract_text(data: bytes) -> str:
    try:
        import pypdf  # type: ignore

        reader = pypdf.PdfReader(io.BytesIO(data))
        parts: list[str] = []
        for page in reader.pages:
            text = page.extract_text() or ""
            parts.append(text)
        return "\n".join(parts)
    except ImportError:
        pass
    try:
        return data.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _chunk(text: str, size: int = 800, overlap: int = 100) -> list[str]:
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        chunks.append(text[start:end])
        start += size - overlap
    return [c.strip() for c in chunks if c.strip()]


# ---------------------------------------------------------------------------
# POST /admin/daedalus/knowledge/upload-book
# ---------------------------------------------------------------------------

@router.post(
    "/admin/daedalus/knowledge/upload-book",
    summary="Upload a PDF book into Daedalus knowledge base",
    status_code=status.HTTP_201_CREATED,
)
async def upload_book(
    file: Annotated[UploadFile, File(description="PDF or plain-text file")],
    _admin: str = Depends(require_admin),
) -> dict:
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file.")

    doc_id = hashlib.sha256(data).hexdigest()[:16]
    filename = file.filename or "unknown"

    text = _extract_text(data)
    if not text.strip():
        raise HTTPException(status_code=422, detail="Could not extract text from file.")

    chunks = _chunk(text)
    if not chunks:
        raise HTTPException(status_code=422, detail="No usable text chunks found.")

    col = _get_collection()

    ids = [f"{doc_id}_{i}" for i in range(len(chunks))]
    metadatas = [{"source": filename, "doc_id": doc_id, "chunk": i} for i in range(len(chunks))]

    col.upsert(documents=chunks, ids=ids, metadatas=metadatas)

    log.info("knowledge_books: ingested %s — %d chunks (doc_id=%s)", filename, len(chunks), doc_id)
    return {
        "doc_id": doc_id,
        "filename": filename,
        "chunks_indexed": len(chunks),
        "status": "ok",
    }


# ---------------------------------------------------------------------------
# GET /api/v1/daedalus/knowledge/search
# ---------------------------------------------------------------------------

@router.get(
    "/api/v1/daedalus/knowledge/search",
    summary="Semantic search in Daedalus knowledge base",
)
async def search_knowledge(
    q: Annotated[str, Query(description="Search query", min_length=1)],
    n: Annotated[int, Query(description="Number of results", ge=1, le=20)] = 5,
    _key: dict = Depends(require_api_key),
) -> dict:
    col = _get_collection()
    count = col.count()
    if count == 0:
        return {"query": q, "results": [], "total_indexed": 0}

    results = col.query(
        query_texts=[q],
        n_results=min(n, count),
        include=["documents", "metadatas", "distances"],
    )

    hits = []
    docs = results.get("documents") or [[]]
    metas = results.get("metadatas") or [[]]
    dists = results.get("distances") or [[]]

    for doc, meta, dist in zip(docs[0], metas[0], dists[0]):
        hits.append({
            "text": doc,
            "source": meta.get("source", ""),
            "doc_id": meta.get("doc_id", ""),
            "chunk": meta.get("chunk", 0),
            "score": round(1 - dist, 4),
        })

    return {"query": q, "results": hits, "total_indexed": count}
