"""Semantic code index for Daedalus self-awareness.

Parses Python under ``daedalus/``, standalone ``app/``, and ``argos_data_nexus/``
when the monorepo is present. Symbols are stored in ChromaDB collection
``daedalus_code_index``.
"""

from __future__ import annotations

import ast
import hashlib
import inspect
import logging
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .paths import adn_package_root, package_root, product_root, repo_root

log = logging.getLogger(__name__)

_COL_CODE = "daedalus_code_index"

_DAEDALUS_PKG = package_root()
_REPO_ROOT = repo_root()


@dataclass
class Symbol:
    """One indexed symbol — a function, method, or class."""
    id: str                   # unique: rel_path::qualified_name
    symbol_type: str          # "function" | "async_function" | "class" | "method" | "async_method"
    name: str                 # bare name, e.g. "verify_token"
    qualified_name: str       # e.g. "DaedalusAgent.chat" or "verify_token"
    filepath: str             # repo-relative, e.g. "argos_data_nexus/auth.py"
    lineno: int
    signature: str            # def name(args) — best-effort
    docstring: str
    body_preview: str         # first 5 non-empty lines of body
    document: str             # full text fed to ChromaDB for embedding


@dataclass
class IndexStats:
    files_parsed: int = 0
    files_failed: int = 0
    symbols_indexed: int = 0
    symbols_skipped: int = 0
    elapsed_s: float = 0.0


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

def _get_sig(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Reconstruct a readable signature string from an AST function node."""
    try:
        args = node.args
        parts: list[str] = []
        # positional args (skip 'self'/'cls' for methods)
        all_args = args.posonlyargs + args.args
        defaults_offset = len(all_args) - len(args.defaults)
        for i, a in enumerate(all_args):
            if a.arg in ("self", "cls"):
                continue
            di = i - defaults_offset
            if di >= 0:
                try:
                    default = ast.unparse(args.defaults[di])
                    parts.append(f"{a.arg}={default}")
                except Exception:
                    parts.append(a.arg)
            else:
                parts.append(a.arg)
        if args.vararg:
            parts.append(f"*{args.vararg.arg}")
        if args.kwarg:
            parts.append(f"**{args.kwarg.arg}")
        kind = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        return f"{kind} {node.name}({', '.join(parts)})"
    except Exception:
        return f"def {node.name}(...)"


def _body_preview(node: ast.AST, src_lines: list[str], max_lines: int = 6) -> str:
    """Extract the first few non-empty source lines of a node's body."""
    try:
        start = node.lineno  # 1-indexed
        body = getattr(node, "body", [])
        if not body:
            return ""
        body_start = body[0].lineno - 1  # convert to 0-indexed
        end = min(body_start + max_lines, len(src_lines))
        snippet = src_lines[body_start:end]
        non_empty = [l.rstrip() for l in snippet if l.strip()]
        return "\n".join(non_empty[:max_lines])
    except Exception:
        return ""


def _extract_symbols(py_file: Path, src: str) -> list[Symbol]:
    """Parse one Python file and return all indexable symbols."""
    try:
        tree = ast.parse(src, filename=str(py_file))
    except SyntaxError:
        return []

    try:
        rel = str(py_file.relative_to(_REPO_ROOT))
    except ValueError:
        rel = str(py_file)
    src_lines = src.splitlines()
    symbols: list[Symbol] = []

    # Annotate parent references so we can distinguish methods from functions
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            child._parent = node  # type: ignore[attr-defined]

    def _make_document(sym_type: str, qname: str, sig: str, doc: str, preview: str, filepath: str) -> str:
        parts = [
            f"FILE: {filepath}",
            f"SYMBOL: {qname}  [{sym_type}]",
        ]
        if sig:
            parts.append(f"SIGNATURE: {sig}")
        if doc:
            parts.append(f"DOCSTRING: {doc[:400]}")
        if preview:
            parts.append(f"BODY:\n{preview}")
        return "\n".join(parts)

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            # Index the class itself
            doc = ast.get_docstring(node) or ""
            qname = node.name
            sym_id = f"{rel}::{qname}"
            doc_text = _make_document("class", qname, f"class {node.name}", doc, "", rel)
            symbols.append(Symbol(
                id=sym_id,
                symbol_type="class",
                name=node.name,
                qualified_name=qname,
                filepath=rel,
                lineno=node.lineno,
                signature=f"class {node.name}",
                docstring=doc,
                body_preview="",
                document=doc_text,
            ))
            # Index methods
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    mdoc = ast.get_docstring(item) or ""
                    mqname = f"{node.name}.{item.name}"
                    msig = _get_sig(item)
                    mpreview = _body_preview(item, src_lines)
                    mtype = "async_method" if isinstance(item, ast.AsyncFunctionDef) else "method"
                    msym_id = f"{rel}::{mqname}"
                    mdoc_text = _make_document(mtype, mqname, msig, mdoc, mpreview, rel)
                    symbols.append(Symbol(
                        id=msym_id,
                        symbol_type=mtype,
                        name=item.name,
                        qualified_name=mqname,
                        filepath=rel,
                        lineno=item.lineno,
                        signature=msig,
                        docstring=mdoc,
                        body_preview=mpreview,
                        document=mdoc_text,
                    ))

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Top-level functions only (methods are captured above via ClassDef)
            parent = getattr(node, "_parent", None)
            if not isinstance(parent, ast.Module):
                continue
            doc = ast.get_docstring(node) or ""
            sig = _get_sig(node)
            preview = _body_preview(node, src_lines)
            ftype = "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function"
            sym_id = f"{rel}::{node.name}"
            doc_text = _make_document(ftype, node.name, sig, doc, preview, rel)
            symbols.append(Symbol(
                id=sym_id,
                symbol_type=ftype,
                name=node.name,
                qualified_name=node.name,
                filepath=rel,
                lineno=node.lineno,
                signature=sig,
                docstring=doc,
                body_preview=preview,
                document=doc_text,
            ))

    return symbols


# ---------------------------------------------------------------------------
# Index build & search
# ---------------------------------------------------------------------------

def _get_col():
    """Return the ChromaDB code index collection (creates if missing)."""
    from .memory import DaedalusMemory
    mem = DaedalusMemory()
    return mem._client.get_or_create_collection(_COL_CODE)


def _index_roots() -> list[Path]:
    """Python trees to index: Daedalus package, standalone app/, ADN when present."""
    roots = [_DAEDALUS_PKG]
    app_dir = product_root() / "app"
    if app_dir.is_dir():
        roots.append(app_dir)
    adn = adn_package_root()
    if adn is not None and adn.is_dir():
        roots.append(adn)
    return roots


def build_index(force: bool = False) -> IndexStats:
    """Parse Python files under the product (and ADN when present) and rebuild the index.

    Skips files whose content hash hasn't changed since last index (incremental).
    Set force=True to re-index everything regardless.

    This is a synchronous function — call it via asyncio.run_in_executor.
    """
    t0 = time.monotonic()
    stats = IndexStats()
    col = _get_col()

    # Load existing IDs and hashes for incremental update
    existing: dict[str, str] = {}  # id -> hash stored in metadata
    if not force:
        try:
            all_items = col.get(include=["metadatas"])
            for i, mid in enumerate(all_items.get("ids") or []):
                meta = (all_items.get("metadatas") or [{}])[i] or {}
                existing[mid] = meta.get("content_hash", "")
        except Exception:
            pass

    py_files: list[Path] = []
    for root in _index_roots():
        py_files.extend(root.rglob("*.py"))
    py_files = sorted(set(py_files))
    # Skip pycache and test files
    py_files = [
        f for f in py_files
        if "/__pycache__/" not in str(f) and "/test" not in str(f)
    ]

    batch_ids: list[str] = []
    batch_docs: list[str] = []
    batch_metas: list[dict] = []

    BATCH = 64  # upsert in chunks

    def _flush():
        if batch_ids:
            col.upsert(ids=batch_ids, documents=batch_docs, metadatas=batch_metas)
            batch_ids.clear()
            batch_docs.clear()
            batch_metas.clear()

    for py_file in py_files:
        try:
            src = py_file.read_text("utf-8", errors="replace")
        except OSError:
            stats.files_failed += 1
            continue

        file_hash = hashlib.sha1(src.encode("utf-8", errors="replace")).hexdigest()[:16]
        symbols = _extract_symbols(py_file, src)

        if not symbols:
            stats.files_parsed += 1
            continue

        stats.files_parsed += 1

        for sym in symbols:
            # Incremental: skip if same hash
            if not force and existing.get(sym.id) == file_hash:
                stats.symbols_skipped += 1
                continue

            batch_ids.append(sym.id)
            batch_docs.append(sym.document)
            batch_metas.append({
                "symbol_type": sym.symbol_type,
                "name": sym.name,
                "qualified_name": sym.qualified_name,
                "filepath": sym.filepath,
                "lineno": sym.lineno,
                "signature": sym.signature,
                "docstring": sym.docstring[:300] if sym.docstring else "",
                "content_hash": file_hash,
            })
            stats.symbols_indexed += 1

            if len(batch_ids) >= BATCH:
                _flush()

    _flush()
    stats.elapsed_s = round(time.monotonic() - t0, 2)
    log.info(
        "Code index built: files=%d failed=%d symbols=%d skipped=%d elapsed=%.2fs",
        stats.files_parsed,
        stats.files_failed,
        stats.symbols_indexed,
        stats.symbols_skipped,
        stats.elapsed_s,
    )
    return stats


def semantic_search(query: str, n_results: int = 12) -> list[dict[str, Any]]:
    """Semantic vector search over the code index.

    Returns list of dicts with keys:
        filepath, qualified_name, symbol_type, lineno, signature, docstring,
        score (distance — lower = better match)
    """
    if not query or not query.strip():
        return []
    col = _get_col()
    if col.count() == 0:
        return []
    try:
        n = min(n_results, col.count(), 50)
        res = col.query(
            query_texts=[query.strip()],
            n_results=n,
            include=["metadatas", "distances", "documents"],
        )
        out: list[dict] = []
        ids = res.get("ids", [[]])[0]
        metas = res.get("metadatas", [[]])[0]
        dists = res.get("distances", [[]])[0]
        for i, mid in enumerate(ids):
            meta = metas[i] if metas else {}
            dist = dists[i] if dists else 1.0
            out.append({
                "id": mid,
                "filepath": meta.get("filepath", ""),
                "qualified_name": meta.get("qualified_name", meta.get("name", "")),
                "symbol_type": meta.get("symbol_type", ""),
                "lineno": meta.get("lineno", 0),
                "signature": meta.get("signature", ""),
                "docstring": meta.get("docstring", ""),
                "score": round(float(dist), 4),
            })
        return out
    except Exception as exc:
        log.warning("semantic_search failed: %s", exc)
        return []


def get_stats() -> dict[str, Any]:
    """Return current index statistics."""
    try:
        col = _get_col()
        count = col.count()
        # Sample a few to show filepaths covered
        sample = col.get(limit=5, include=["metadatas"])
        files = {
            (m or {}).get("filepath", "")
            for m in (sample.get("metadatas") or [])
        }
        return {"ok": True, "symbols": count, "sample_files": sorted(files)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
