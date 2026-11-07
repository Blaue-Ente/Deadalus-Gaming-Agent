"""Daedalus Rejection Lessons  [FIX #4]

При reject на proposal → автоматично записва lesson в daedalus_lessons ChromaDB.
При следващ proposal от същия тип → Дедал чете тези lessons ПРЕДИ да генерира код.

API:
    record_rejection_lesson(proposal, reject_reason)  → lesson_id
    retrieve_rejection_warnings(changed_files, skill) → str (за system prompt inject)
"""
from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger(__name__)


def record_rejection_lesson(
    proposal: dict[str, Any],
    reject_reason: str,
    user_id: str = "nikolay",
) -> str | None:
    """Записва structured lesson от отказан proposal в daedalus_lessons ChromaDB.

    Args:
        proposal: proposal dict (с id, skill, file_path, risk_level...).
        reject_reason: причина за отказ.
        user_id: кой е отказал.

    Returns: lesson_id или None при грешка.
    """
    try:
        from .memory import DaedalusMemory
        mem = DaedalusMemory()
        lessons_col = mem.lessons_col

        proposal_id  = proposal.get("id", "unknown")
        skill        = proposal.get("skill", "unknown")
        file_path    = proposal.get("file_path", "")
        risk_level   = proposal.get("risk_level") or "N/A"
        conf_score   = proposal.get("confidence_score") or "N/A"
        title        = proposal.get("reason", "")[:200]

        lesson_id = f"reject-{proposal_id}-{int(time.time())}"

        searchable_text = (
            f"REJECTED PROPOSAL LESSON\n"
            f"Skill: {skill}\n"
            f"File: {file_path}\n"
            f"What Daedalus tried: {title}\n"
            f"Why owner rejected: {reject_reason}\n"
            f"Risk level: {risk_level} | Confidence was: {conf_score}"
        )

        metadata = {
            "type": "rejection_lesson",
            "skill": skill,
            "risk": str(risk_level),
            "file_path": file_path[:200],
            "reject_reason": reject_reason[:500],
            "proposal_id": proposal_id,
            "user_id": user_id,
            "timestamp": str(time.time()),
            "confidence_was": str(conf_score),
        }

        lessons_col.add(
            documents=[searchable_text],
            metadatas=[metadata],
            ids=[lesson_id],
        )
        log.info("Rejection lesson recorded: %s (skill=%s, file=%s)", lesson_id, skill, file_path)
        return lesson_id

    except Exception as exc:
        log.warning("record_rejection_lesson failed (non-fatal): %s", exc)
        return None


def retrieve_rejection_warnings(
    changed_files: list[str],
    skill: str,
    top_k: int = 3,
    threshold: float = 0.6,
) -> str:
    """Връща formatted warning string за injection в Дедал system prompt.

    Ако няма similar rejections → празен string (безопасно за inject).
    Дизайн: извиква се от agent.py ПРЕДИ да генерира proposal.

    Returns:
        Многоредов string с past rejections, или "" ако няма.
    """
    try:
        from .memory import DaedalusMemory
        mem = DaedalusMemory()
        lessons_col = mem.lessons_col

        if lessons_col.count() == 0:
            return ""

        query = f"Skill: {skill} | Files: {', '.join(changed_files[:5])}"

        count_with_filter = lessons_col.count()
        if count_with_filter == 0:
            return ""

        try:
            results = lessons_col.query(
                query_texts=[query],
                n_results=min(top_k, lessons_col.count()),
                where={"type": "rejection_lesson"},
            )
        except Exception:
            results = lessons_col.query(
                query_texts=[query],
                n_results=min(top_k, lessons_col.count()),
            )
            docs   = results.get("documents", [[]])[0]
            metas  = results.get("metadatas", [[]])[0]
            dists  = results.get("distances", [[]])[0]
            filtered = [
                (d, m, doc)
                for d, m, doc in zip(dists, metas, docs)
                if m.get("type") == "rejection_lesson"
            ]
            if not filtered:
                return ""
            results = {
                "documents": [[doc for _, _, doc in filtered]],
                "metadatas": [[m for _, m, _ in filtered]],
                "distances": [[d for d, _, _ in filtered]],
            }

        docs   = results.get("documents", [[]])[0]
        metas  = results.get("metadatas", [[]])[0]
        dists  = results.get("distances", [[]])[0]

        relevant = [
            (1 - d, m)
            for d, m in zip(dists, metas)
            if d < (1 - threshold)
        ]

        if not relevant:
            return ""

        lines = ["\n\n⚠️ PAST REJECTIONS — подобни proposals са отказани преди:"]
        for similarity, meta in relevant:
            reason = meta.get("reject_reason", "no reason")
            fp     = meta.get("file_path", "")
            lines.append(
                f"  - [{similarity * 100:.0f}% similar | {fp}] Причина: {reason}"
            )
        lines.append("👉 Avoid repeating these mistakes. Ако пак ще предлагаш подобна промяна — обясни ЗАЩО сега е различно.\n")
        return "\n".join(lines)

    except Exception as exc:
        log.debug("retrieve_rejection_warnings failed (non-fatal): %s", exc)
        return ""
