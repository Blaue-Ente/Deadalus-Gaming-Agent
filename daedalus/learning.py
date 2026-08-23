"""
Дедал Learning — self-learning loop за Playground autopilot builds.

Записва: успех/неуспех, модел, kind, coding_mode, грешки, причини.
Чете: pattern stats за autopilot advice.

Storage: PostgreSQL ``daedalus_learning_records`` (durable). Legacy Replit DB
keys ``adn:daedalus_learning:*`` се четат при липса на PG редове (migration).
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

try:
    from replit import db as _rdb
except Exception:
    from .kv import db as _rdb

_LEARNING_PREFIX = "adn:daedalus_learning:"
_LEARNING_INDEX = "adn:daedalus_learning:index"
_MAX_RECORDS = 500
_TOP_PATTERNS = 5


def _now() -> float:
    return time.time()


def _month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


async def _pool():
    from .host import get_pool
    return await get_pool()


def _row_to_dict(row) -> dict:
    d = dict(row)
    for k in ("errors", "acceptance_met", "acceptance_unmet"):
        v = d.get(k)
        if isinstance(v, str):
            try:
                d[k] = json.loads(v)
            except Exception:
                d[k] = []
    return d


# ── Record model ─────────────────────────────────────────────────────────────

async def record_learning(
    slug: str,
    description: str,
    kind: str,
    coding_mode: str,
    build_model: str,
    success: bool,
    stage_count: int = 0,
    total_attempts: int = 0,
    errors: list[str] | None = None,
    assets_generated: int = 0,
    files_count: int = 0,
    acceptance_met: list[str] | None = None,
    acceptance_unmet: list[dict] | None = None,
    runtime_verified: bool = False,
    browser_verified: bool = False,
    review_notes: str = "",
    duration_sec: float = 0.0,
    source: str = "autopilot",
    quality_score: float = 0.0,
    quality_detail: dict | None = None,
) -> str:
    """Write a learning record. Returns record_id."""
    record_id = f"{_month_key()}:{slug}:{int(_now())}"
    record = {
        "id": record_id,
        "slug": slug,
        "description": (description or "")[:400],
        "kind": kind,
        "coding_mode": coding_mode,
        "build_model": build_model,
        "success": success,
        "stage_count": stage_count,
        "total_attempts": total_attempts,
        "errors": errors or [],
        "assets_generated": assets_generated,
        "files_count": files_count,
        "acceptance_met": acceptance_met or [],
        "acceptance_unmet": acceptance_unmet or [],
        "runtime_verified": runtime_verified,
        "browser_verified": browser_verified,
        "review_notes": (review_notes or "")[:500],
        "duration_sec": round(duration_sec, 1),
        "source": source,
        "created_at": _now(),
        "quality_score": round(float(quality_score or 0.0), 2),
        "quality_detail": quality_detail or {},
    }

    try:
        pool = await _pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO daedalus_learning_records (
                    id, slug, description, kind, coding_mode, build_model, success,
                    stage_count, total_attempts, errors, assets_generated, files_count,
                    acceptance_met, acceptance_unmet, runtime_verified, browser_verified,
                    review_notes, duration_sec, source, created_at,
                    quality_score, quality_detail
                ) VALUES (
                    $1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11,$12,
                    $13::jsonb,$14::jsonb,$15,$16,$17,$18,$19,to_timestamp($20),
                    $21,$22::jsonb
                )
                """,
                record_id, slug, record["description"], kind, coding_mode, build_model,
                success, stage_count, total_attempts,
                json.dumps(record["errors"], ensure_ascii=False),
                assets_generated, files_count,
                json.dumps(record["acceptance_met"], ensure_ascii=False),
                json.dumps(record["acceptance_unmet"], ensure_ascii=False),
                runtime_verified, browser_verified, record["review_notes"],
                record["duration_sec"], source, record["created_at"],
                record["quality_score"],
                json.dumps(record["quality_detail"], ensure_ascii=False),
            )
        log.info(
            "learning: recorded %s success=%s kind=%s model=%s source=%s",
            record_id, success, kind, build_model, source,
        )
    except Exception as exc:
        log.warning("learning: postgres insert failed (%s), trying replit db", exc)
        _rdb_set_legacy(record_id, record)

    return record_id


def _rdb_set_legacy(record_id: str, record: dict) -> None:
    try:
        _rdb[_LEARNING_PREFIX + record_id] = record
        index: list[str] = _rdb.get(_LEARNING_INDEX) or []
        if not isinstance(index, list):
            index = []
        index.insert(0, record_id)
        _rdb[_LEARNING_INDEX] = index[:_MAX_RECORDS]
    except Exception as exc:
        log.warning("learning rdb_set legacy failed: %s", exc)


async def _load_records(
    *,
    kind: str | None = None,
    coding_mode: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Load records from Postgres; merge legacy Replit DB if PG empty."""
    records: list[dict] = []
    try:
        pool = await _pool()
        async with pool.acquire() as conn:
            clauses = []
            args: list[Any] = []
            n = 1
            if kind:
                clauses.append(f"kind = ${n}")
                args.append(kind)
                n += 1
            if coding_mode:
                clauses.append(f"coding_mode = ${n}")
                args.append(coding_mode)
                n += 1
            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            args.append(limit)
            rows = await conn.fetch(
                f"""
                SELECT * FROM daedalus_learning_records
                {where}
                ORDER BY created_at DESC
                LIMIT ${n}
                """,
                *args,
            )
            records = [_row_to_dict(r) for r in rows]
    except Exception as exc:
        log.warning("learning: postgres load failed: %s", exc)

    if not records:
        records = _load_records_legacy(kind=kind, coding_mode=coding_mode, limit=limit)
    return records


def _load_records_legacy(
    *,
    kind: str | None = None,
    coding_mode: str | None = None,
    limit: int = 100,
) -> list[dict]:
    try:
        index: list[str] = _rdb.get(_LEARNING_INDEX) or []
        if not isinstance(index, list):
            index = []
        out: list[dict] = []
        for rid in index[:limit]:
            rec = _rdb.get(_LEARNING_PREFIX + rid)
            if not isinstance(rec, dict):
                continue
            if kind and rec.get("kind") != kind:
                continue
            if coding_mode and rec.get("coding_mode") != coding_mode:
                continue
            out.append(rec)
        return out
    except Exception as exc:
        log.warning("learning: legacy load failed: %s", exc)
        return []


# ── Query patterns ───────────────────────────────────────────────────────────

async def get_stats(kind: str | None = None, coding_mode: str | None = None, limit: int = 100) -> dict:
    """Aggregate stats: success rate, avg attempts, top models, common errors."""
    records = await _load_records(kind=kind, coding_mode=coding_mode, limit=limit)

    if not records:
        return {
            "total": 0, "success_rate": 0.0, "avg_attempts": 0.0,
            "top_models": [], "common_errors": [], "patterns": [],
        }

    total = len(records)
    successes = sum(1 for r in records if r.get("success"))
    success_rate = round(successes / total * 100, 1)
    avg_attempts = round(sum(r.get("total_attempts", 0) for r in records) / total, 1)
    avg_duration = round(sum(r.get("duration_sec", 0) for r in records) / total, 1)

    model_stats: dict[str, dict] = {}
    for r in records:
        m = r.get("build_model", "unknown")
        if m not in model_stats:
            model_stats[m] = {"total": 0, "success": 0}
        model_stats[m]["total"] += 1
        if r.get("success"):
            model_stats[m]["success"] += 1
    top_models = sorted(
        [
            {
                "model": m,
                "total": s["total"],
                "success_rate": round(s["success"] / s["total"] * 100, 1),
            }
            for m, s in model_stats.items()
        ],
        key=lambda x: x["success_rate"],
        reverse=True,
    )[:_TOP_PATTERNS]

    error_counts: dict[str, int] = {}
    for r in records:
        for e in (r.get("errors") or []):
            key = str(e)[:60].strip()
            if key:
                error_counts[key] = error_counts.get(key, 0) + 1
    common_errors = sorted(
        [{"error": e, "count": c} for e, c in error_counts.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:_TOP_PATTERNS]

    pattern_stats: dict[tuple, dict] = {}
    for r in records:
        pk = (r.get("kind", "unknown"), r.get("coding_mode", "unknown"))
        if pk not in pattern_stats:
            pattern_stats[pk] = {"total": 0, "success": 0, "avg_attempts": 0.0, "models": set()}
        pattern_stats[pk]["total"] += 1
        if r.get("success"):
            pattern_stats[pk]["success"] += 1
        pattern_stats[pk]["avg_attempts"] += r.get("total_attempts", 0)
        pattern_stats[pk]["models"].add(r.get("build_model", "unknown"))
    patterns = []
    for pk, s in pattern_stats.items():
        patterns.append({
            "kind": pk[0],
            "coding_mode": pk[1],
            "total": s["total"],
            "success_rate": round(s["success"] / s["total"] * 100, 1),
            "avg_attempts": round(s["avg_attempts"] / s["total"], 1),
            "models": list(s["models"])[:3],
        })
    patterns.sort(key=lambda x: x["success_rate"], reverse=True)

    return {
        "total": total,
        "success_rate": success_rate,
        "avg_attempts": avg_attempts,
        "avg_duration_sec": avg_duration,
        "top_models": top_models,
        "common_errors": common_errors,
        "patterns": patterns[:_TOP_PATTERNS],
    }


async def get_advice(kind: str, coding_mode: str, question: str = "") -> dict:
    """Return actionable advice for the next build based on past data."""
    stats = await get_stats(kind=kind, coding_mode=coding_mode, limit=200)
    advice = {
        "suggested_model": None,
        "suggested_coding_mode": None,
        "warning": None,
        "tips": [],
    }

    for m in stats.get("top_models", []):
        if m["success_rate"] >= 60 and m["total"] >= 2:
            advice["suggested_model"] = m["model"]
            break

    for e in stats.get("common_errors", [])[:3]:
        err = e["error"].lower()
        if "truncat" in err or "отрязан" in err:
            advice["tips"].append("Раздели кода в повече файлове — truncation е честа причина за неуспех.")
        if "runtime" in err or "import" in err or "module" in err:
            advice["tips"].append("Провери зависимостите — честа грешка при runtime.")
        if "blank" in err or "бял" in err or "render" in err:
            advice["tips"].append("Провери JS mount point — бял екран често = грешка преди render.")
        if "asset" in err or "изображение" in err:
            advice["tips"].append("Използвай процедурна графика като fallback — asset generation не винаги работи.")

    if stats.get("success_rate", 0) < 50 and stats.get("total", 0) >= 3:
        advice["warning"] = (
            f"Внимание: success rate за {kind}/{coding_mode} е само {stats['success_rate']}%. "
            f"Препоръчваме premium mode или повече етапи."
        )

    return {"advice": advice, "stats": stats}


async def list_recent(limit: int = 50) -> list[dict]:
    """Return recent records for admin UI."""
    records = await _load_records(limit=limit)
    out = []
    for rec in records:
        created = rec.get("created_at")
        if isinstance(created, (int, float)):
            created_ts = created
        else:
            try:
                created_ts = created.timestamp() if created else 0
            except Exception:
                created_ts = 0
        out.append({
            "id": rec.get("id"),
            "slug": rec.get("slug"),
            "kind": rec.get("kind"),
            "coding_mode": rec.get("coding_mode"),
            "build_model": rec.get("build_model"),
            "success": rec.get("success"),
            "duration_sec": rec.get("duration_sec"),
            "created_at": created_ts,
            "source": rec.get("source", "autopilot"),
            "description": (rec.get("description") or "")[:60],
        })
    return out
