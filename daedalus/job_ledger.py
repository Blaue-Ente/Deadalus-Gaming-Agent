"""Job ledger — original ask, acceptance, slices, named artifacts.

Hard work survives a fresh sub-agent window. The master agent still decides;
this is disk memory, not a second orchestrator.

Storage: ``$DAEDALUS_DATA/jobs/{job_id}.json`` plus
``$DAEDALUS_DATA/jobs/active/{slug}.json`` for the live pointer.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from pathlib import Path
from typing import Any

from .paths import data_root

log = logging.getLogger(__name__)

ROLES = ("explorer", "coder", "verify")
TASK_STATUSES = ("open", "done", "failed", "skipped")
MAX_ARTIFACT_CHARS = 4000
MAX_PROGRESS = 40
MAX_ARTIFACTS = 48

_ARTIFACT_RE = re.compile(
    r"===ARTIFACT:\s*([a-zA-Z0-9_]{2,80})\s*===\r?\n(.*?)\r?\n?===END===",
    re.DOTALL,
)
_COMPLETE_RE = re.compile(r"\bTASK_COMPLETE\b|\bALL_TASKS_COMPLETE\b")
_BLOCKED_RE = re.compile(r"\bTASK_BLOCKED\b|^BLOCKED:", re.MULTILINE | re.IGNORECASE)
_CONTRADICTION_RE = re.compile(
    r"cannot be automated|could not complete|needs human|requires manual|"
    r"manual intervention|не мога да|не можах|нужда от човек",
    re.IGNORECASE,
)


def jobs_dir() -> Path:
    path = data_root() / "jobs"
    path.mkdir(parents=True, exist_ok=True)
    (path / "active").mkdir(parents=True, exist_ok=True)
    return path


def _job_path(job_id: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", (job_id or "").strip())[:80]
    if not safe:
        raise ValueError("job_id required")
    return jobs_dir() / f"{safe}.json"


def _active_path(slug: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", (slug or "").strip())[:80] or "default"
    return jobs_dir() / "active" / f"{safe}.json"


def _now() -> float:
    return time.time()


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def empty_job(
    *,
    slug: str,
    original_ask: str,
    session_id: str = "",
    acceptance: list[str] | None = None,
    tasks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    ask = (original_ask or "").strip()
    slices = tasks if tasks else [{
        "id": "t1",
        "title": ask[:80] or "slice",
        "detail": ask,
        "role": "coder",
        "status": "open",
        "verify": True,
    }]
    return {
        "job_id": _new_id("job"),
        "slug": (slug or "").strip(),
        "session_id": session_id or "",
        "original_ask": ask,
        "acceptance": [a.strip() for a in (acceptance or []) if a and a.strip()][:12],
        "tasks": [_norm_task(t, i) for i, t in enumerate(slices, start=1)],
        "artifacts": {},
        "progress": [],
        "created_at": _now(),
        "updated_at": _now(),
        "status": "in_progress",
    }


def _norm_task(task: dict[str, Any], index: int) -> dict[str, Any]:
    role = str(task.get("role") or "coder").strip().lower()
    if role not in ROLES:
        role = "coder"
    status = str(task.get("status") or "open").strip().lower()
    if status not in TASK_STATUSES:
        status = "open"
    title = str(task.get("title") or task.get("detail") or f"slice {index}").strip()[:160]
    return {
        "id": str(task.get("id") or f"t{index}")[:40],
        "title": title or f"slice {index}",
        "detail": str(task.get("detail") or title)[:2000],
        "role": role,
        "status": status,
        "verify": bool(task.get("verify", role == "verify")),
    }


def save(job: dict[str, Any]) -> dict[str, Any]:
    job = dict(job)
    job["updated_at"] = _now()
    job_id = str(job.get("job_id") or _new_id("job"))
    job["job_id"] = job_id
    path = _job_path(job_id)
    path.write_text(json.dumps(job, indent=2, ensure_ascii=False), encoding="utf-8")
    slug = str(job.get("slug") or "").strip()
    if slug and job.get("status") != "cleared":
        _active_path(slug).write_text(
            json.dumps({"job_id": job_id, "slug": slug}, indent=2),
            encoding="utf-8",
        )
    return job


def load(job_id: str) -> dict[str, Any] | None:
    path = _job_path(job_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.debug("job ledger load failed: %s", exc)
        return None
    if not isinstance(data, dict):
        return None
    return data


def active_job_id(slug: str) -> str | None:
    path = _active_path(slug)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    job_id = str((data or {}).get("job_id") or "")
    return job_id or None


def load_active(slug: str) -> dict[str, Any] | None:
    job_id = active_job_id(slug)
    return load(job_id) if job_id else None


def load_for_session(session_id: str) -> dict[str, Any] | None:
    sid = (session_id or "").strip()
    if not sid:
        return None
    root = jobs_dir()
    newest: dict[str, Any] | None = None
    newest_ts = 0.0
    for path in root.glob("job_*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        if str(data.get("session_id") or "") != sid:
            continue
        if data.get("status") in ("done", "cleared"):
            continue
        ts = float(data.get("updated_at") or 0)
        if ts >= newest_ts:
            newest = data
            newest_ts = ts
    return newest


def ensure_job(
    *,
    slug: str,
    original_ask: str,
    session_id: str = "",
    job_id: str = "",
    acceptance: list[str] | None = None,
) -> dict[str, Any]:
    if job_id:
        existing = load(job_id)
        if existing:
            if session_id and not existing.get("session_id"):
                existing["session_id"] = session_id
                return save(existing)
            return existing
    if slug:
        active = load_active(slug)
        if active and active.get("status") not in ("done", "cleared"):
            if session_id and not active.get("session_id"):
                active["session_id"] = session_id
                return save(active)
            return active
    job = empty_job(
        slug=slug,
        original_ask=original_ask,
        session_id=session_id,
        acceptance=acceptance,
    )
    return save(job)


def add_artifact(
    job_id: str,
    artifact_id: str,
    content: str,
    *,
    source: str = "subagent",
) -> dict[str, Any] | None:
    job = load(job_id)
    if not job:
        return None
    aid = re.sub(r"[^a-zA-Z0-9_]+", "_", (artifact_id or "").strip())[:80]
    if not aid:
        return job
    arts = dict(job.get("artifacts") or {})
    if len(arts) >= MAX_ARTIFACTS and aid not in arts:
        # Drop oldest by insertion order (json object order).
        oldest = next(iter(arts))
        arts.pop(oldest, None)
    arts[aid] = {
        "id": aid,
        "content": (content or "").strip()[:MAX_ARTIFACT_CHARS],
        "source": (source or "subagent")[:80],
        "updated_at": _now(),
    }
    job["artifacts"] = arts
    return save(job)


def artifacts_for(job: dict[str, Any], refs: list[str] | None) -> list[dict[str, Any]]:
    arts = job.get("artifacts") or {}
    if not refs:
        return list(arts.values())[:12]
    out: list[dict[str, Any]] = []
    wanted = [re.sub(r"[^a-zA-Z0-9_]+", "_", r.strip()) for r in refs if r and r.strip()]
    for key in wanted:
        if key in arts:
            out.append(arts[key])
            continue
        # task_id ref → all artifacts from that source
        for art in arts.values():
            if str(art.get("source") or "") == key:
                out.append(art)
    return out[:16]


def append_progress(job_id: str, note: str) -> dict[str, Any] | None:
    job = load(job_id)
    if not job:
        return None
    text = " ".join((note or "").split())
    if not text:
        return job
    rows = list(job.get("progress") or [])
    rows.append({"ts": _now(), "note": text[:600]})
    job["progress"] = rows[-MAX_PROGRESS:]
    return save(job)


def next_open_task(job: dict[str, Any] | None) -> dict[str, Any] | None:
    if not job:
        return None
    for task in job.get("tasks") or []:
        if str(task.get("status") or "open") == "open":
            return task
    return None


def mark_task(
    job_id: str,
    task_id: str,
    status: str,
    *,
    note: str = "",
) -> dict[str, Any] | None:
    job = load(job_id)
    if not job:
        return None
    st = status if status in TASK_STATUSES else "open"
    found = False
    for task in job.get("tasks") or []:
        if str(task.get("id") or "") == task_id:
            task["status"] = st
            found = True
            break
    if not found:
        return job
    if note:
        rows = list(job.get("progress") or [])
        rows.append({"ts": _now(), "note": f"[{task_id}:{st}] {note.strip()[:500]}"})
        job["progress"] = rows[-MAX_PROGRESS:]
    remaining_open = [
        t for t in (job.get("tasks") or [])
        if str(t.get("status") or "open") == "open"
    ]
    remaining_failed = [
        t for t in (job.get("tasks") or [])
        if str(t.get("status") or "") == "failed"
    ]
    if remaining_open:
        job["status"] = "in_progress"
    elif remaining_failed:
        job["status"] = "blocked"
    else:
        job["status"] = "done"
    return save(job)


def open_task_count(job: dict[str, Any] | None) -> int:
    if not job or job.get("status") in ("done", "cleared"):
        return 0
    return sum(
        1 for t in (job.get("tasks") or [])
        if str(t.get("status") or "open") in ("open", "failed")
    )


def open_count_for_session(session_id: str) -> int:
    return open_task_count(load_for_session(session_id))


def parse_artifacts(text: str) -> list[tuple[str, str]]:
    if not text or "===ARTIFACT:" not in text:
        return []
    out: list[tuple[str, str]] = []
    for aid, body in _ARTIFACT_RE.findall(text):
        content = (body or "").strip()
        if aid and content:
            out.append((aid, content[:MAX_ARTIFACT_CHARS]))
    return out[:12]


def completion_verdict(text: str) -> str:
    """Return complete | blocked | contradiction | none."""
    blob = text or ""
    complete = bool(_COMPLETE_RE.search(blob))
    blocked = bool(_BLOCKED_RE.search(blob))
    admits = bool(_CONTRADICTION_RE.search(blob))
    if complete and (blocked or admits):
        return "contradiction"
    if complete:
        return "complete"
    if blocked:
        return "blocked"
    return "none"


def ingest_report(
    job_id: str,
    report: dict[str, Any],
    *,
    task_id: str = "",
) -> dict[str, Any] | None:
    summary = str(report.get("summary") or "")
    job = load(job_id)
    if not job:
        return None
    source = task_id or str(report.get("task_id") or "subagent")
    for aid, body in parse_artifacts(summary):
        add_artifact(job_id, aid, body, source=source)
        job = load(job_id) or job
    verdict = completion_verdict(summary)
    note = (summary.replace("\n", " ")[:240] or verdict)
    append_progress(job_id, f"{verdict}: {note}")
    tid = task_id or str(report.get("task_id") or "")
    role = str(report.get("role") or "")
    if not tid and verdict == "complete" and role == "coder":
        nxt = next_open_task(job)
        tid = str((nxt or {}).get("id") or "")
    if tid and verdict == "complete":
        mark_task(job_id, tid, "done", note=note)
    elif tid and verdict in ("blocked", "contradiction"):
        mark_task(job_id, tid, "failed", note=note)
    return load(job_id)


def format_pack(
    job: dict[str, Any] | None,
    *,
    refs: list[str] | None = None,
    include_progress: bool = True,
) -> str:
    if not job:
        return ""
    lines = [
        "━━━ JOB LEDGER (original ask is the north star) ━━━",
        f"job: {job.get('job_id')}  slug: {job.get('slug') or '—'}",
        "",
        "### Original ask",
        str(job.get("original_ask") or "").strip()[:1200] or "(empty)",
    ]
    acceptance = job.get("acceptance") or []
    if acceptance:
        lines.append("")
        lines.append("### Acceptance")
        for item in acceptance[:12]:
            lines.append(f"- {item}")
    tasks = job.get("tasks") or []
    if tasks:
        lines.append("")
        lines.append("### Slices")
        for t in tasks[:20]:
            mark = {"done": "x", "failed": "!", "skipped": "-"}.get(str(t.get("status")), " ")
            lines.append(
                f"- [{mark}] {t.get('id')} ({t.get('role')}): {t.get('title')}"
            )
    arts = artifacts_for(job, refs)
    if arts:
        lines.append("")
        lines.append("### Named artifacts")
        for art in arts:
            lines.append(f"#### {art.get('id')}")
            lines.append(str(art.get("content") or "")[:1800])
    if include_progress:
        notes = job.get("progress") or []
        if notes:
            lines.append("")
            lines.append("### Progress")
            for row in notes[-8:]:
                lines.append(f"- {row.get('note')}")
    lines.append("━━━ END JOB LEDGER ━━━")
    return "\n".join(lines)


def format_session_block(session_id: str) -> str:
    job = load_for_session(session_id)
    if not job:
        return ""
    return format_pack(job, refs=None, include_progress=True)
