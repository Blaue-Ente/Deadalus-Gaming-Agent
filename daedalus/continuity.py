"""Daedalus Continuity Memory — Task Ledger + Daily Digests.

Persistent storage via Replit DB. Provides:
- Task management (open / done / rework tracking)
- Daily digest generation (LLM-based summaries of each day)
- build_continuity_context() → formatted block for system prompt injection
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from datetime import date, datetime, timezone, timedelta
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Replit DB helpers
# ---------------------------------------------------------------------------

def _rdb_get(key: str) -> Any:
    from . import kv
    return kv.get(key)




def _rdb_set(key: str, value: Any) -> None:
    from . import kv
    kv.set(key, value)




def _rdb_delete(key: str) -> None:
    from . import kv
    kv.delete(key)




# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

_TASK_PREFIX = "adn:task:"
_TASKS_INDEX = "adn:tasks:index"


def create_task(
    title: str,
    summary: str = "",
    session_id: str | None = None,
    related_files: list[str] | None = None,
) -> dict:
    """Create a new task and persist it."""
    task_id = "task_" + uuid.uuid4().hex[:8]
    now = int(time.time())
    task: dict[str, Any] = {
        "id": task_id,
        "title": title.strip()[:80],
        "summary": summary.strip()[:400],
        "status": "open",
        "created_ts": now,
        "updated_ts": now,
        "session_ids": [session_id] if session_id else [],
        "related_files": related_files or [],
    }
    _rdb_set(_TASK_PREFIX + task_id, task)
    index: list[str] = _rdb_get(_TASKS_INDEX) or []
    index = [task_id] + [i for i in index if i != task_id]
    _rdb_set(_TASKS_INDEX, index[:200])
    log.info("continuity: task created %s %r", task_id, title[:40])
    return task


def get_task(task_id: str) -> dict | None:
    return _rdb_get(_TASK_PREFIX + task_id)


def update_task(
    task_id: str,
    *,
    status: str | None = None,
    summary: str | None = None,
    add_session_id: str | None = None,
    add_files: list[str] | None = None,
) -> dict | None:
    """Update fields on an existing task. Returns updated task or None if not found."""
    task = get_task(task_id)
    if not task:
        return None
    if status and status in ("open", "done", "rework"):
        task["status"] = status
    if summary is not None:
        task["summary"] = summary.strip()[:400]
    if add_session_id and add_session_id not in task.get("session_ids", []):
        task.setdefault("session_ids", []).append(add_session_id)
    if add_files:
        existing = set(task.get("related_files", []))
        existing.update(add_files)
        task["related_files"] = list(existing)
    task["updated_ts"] = int(time.time())
    _rdb_set(_TASK_PREFIX + task_id, task)
    return task


def delete_task(task_id: str) -> bool:
    if not get_task(task_id):
        return False
    _rdb_delete(_TASK_PREFIX + task_id)
    index: list[str] = _rdb_get(_TASKS_INDEX) or []
    _rdb_set(_TASKS_INDEX, [i for i in index if i != task_id])
    return True


def list_tasks(status: str | None = None, limit: int = 50) -> list[dict]:
    """List tasks, optionally filtered by status (open/done/rework)."""
    index: list[str] = _rdb_get(_TASKS_INDEX) or []
    out: list[dict] = []
    for task_id in index[:200]:
        t = get_task(task_id)
        if not t:
            continue
        if status and t.get("status") != status:
            continue
        out.append(t)
        if len(out) >= limit:
            break
    return out


def find_task_by_hint(hint: str) -> dict | None:
    """Fuzzy match: exact ID → substring title → word overlap (≥2 words)."""
    if not hint:
        return None
    hint = hint.strip()
    t = get_task(hint)
    if t:
        return t
    hint_lower = hint.lower()
    hint_words = set(hint_lower.split())
    best: dict | None = None
    best_score = 0
    for task in list_tasks(limit=200):
        title_lower = task.get("title", "").lower()
        if hint_lower in title_lower or title_lower in hint_lower:
            score = 100 + (10 if task.get("status") == "open" else 0)
            if score > best_score:
                best_score = score
                best = task
        else:
            title_words = set(title_lower.split())
            common = hint_words & title_words
            if len(common) >= 2:
                score = len(common) + (5 if task.get("status") == "open" else 0)
                if score > best_score:
                    best_score = score
                    best = task
    return best


# ---------------------------------------------------------------------------
# Daily Digests
# ---------------------------------------------------------------------------

_DIGEST_PREFIX = "adn:digest:"


def _digest_key(date_str: str) -> str:
    return _DIGEST_PREFIX + date_str


def get_digest(date_str: str) -> dict | None:
    return _rdb_get(_digest_key(date_str))


def list_digests(limit: int = 30) -> list[dict]:
    """Return last N digest metadata entries (without full summary text)."""
    today = datetime.now(timezone.utc).date()
    out: list[dict] = []
    for i in range(limit * 2):
        d = (today - timedelta(days=i)).isoformat()
        digest = get_digest(d)
        if digest:
            meta = {k: v for k, v in digest.items() if k != "summary"}
            meta["summary_preview"] = (digest.get("summary") or "")[:120]
            out.append(meta)
        if len(out) >= limit:
            break
    return out


def _llm_call(system: str, user: str) -> str:
    """Synchronous cheap LLM call for digest summaries — Novita Ling or OpenRouter Llama."""
    try:
        from openai import OpenAI
        nv_key = os.environ.get("NOVITA_API_KEY", "")
        or_key = os.environ.get("OPENROUTER_API_KEY", "")
        if nv_key:
            api_key = nv_key
            base_url = "https://api.novita.ai/v3/openai"
            model = "inclusionai/ling-2.6-flash"
        elif or_key:
            api_key = or_key
            base_url = "https://openrouter.ai/api/v1"
            model = "meta-llama/llama-3.3-70b-instruct"
        else:
            log.warning("continuity: no LLM provider configured (NOVITA_API_KEY or OPENROUTER_API_KEY)")
            return ""
        client = OpenAI(api_key=api_key, base_url=base_url)
        resp = client.chat.completions.create(
            model=model,
            max_tokens=1024,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content or ""
    except Exception as exc:
        log.warning("continuity: LLM call failed: %s", exc)
        return ""


def _get_day_turns(date_str: str) -> list[dict]:
    """Retrieve all ChromaDB session turns for a given date."""
    try:
        from .memory import DaedalusMemory
        mem = DaedalusMemory()
        res = mem.sessions_col.get(limit=10_000)
        ids_list = res.get("ids") or []
        docs_list = res.get("documents") or []
        metas_list = res.get("metadatas") or []
        day_turns = []
        for i, tid in enumerate(ids_list):
            meta = metas_list[i] if i < len(metas_list) else {}
            ts = meta.get("timestamp", "")
            if ts and ts[:10] == date_str:
                day_turns.append({
                    "id": tid,
                    "content": docs_list[i] if i < len(docs_list) else "",
                    "metadata": meta,
                })
        day_turns.sort(key=lambda x: x["metadata"].get("timestamp", ""))
        return day_turns
    except Exception as exc:
        log.warning("continuity: _get_day_turns(%s) failed: %s", date_str, exc)
        return []


def generate_daily_digest(date_str: str, force: bool = False) -> dict:
    """Generate and store a LLM daily digest for date_str (YYYY-MM-DD).

    If force=False and digest already exists, returns existing without re-generating.
    """
    existing = get_digest(date_str)
    if existing and not force:
        return existing

    turns = _get_day_turns(date_str)
    session_ids = list({t["metadata"].get("session_id", "") for t in turns
                        if t["metadata"].get("session_id")})
    session_count = len(session_ids)
    turn_count = len(turns)
    now = int(time.time())

    if turn_count == 0:
        digest: dict[str, Any] = {
            "date": date_str,
            "generated_ts": now,
            "session_count": 0,
            "summary": "Нямаше активност на тази дата.",
            "key_topics": [],
            "decisions": [],
            "artifacts": [],
            "task_changes": [],
        }
        _rdb_set(_digest_key(date_str), digest)
        return digest

    numbered = []
    for idx, t in enumerate(turns[:200], 1):
        meta = t["metadata"]
        role = meta.get("role", "?")
        content = t["content"][:300]
        numbered.append(f"{idx}. [{role}]: {content}")
    turns_text = "\n".join(numbered)

    prompt = (
        f"Резюмирай разговорите от един работен ден между Николай и Дедал.\n\n"
        f"Дата: {date_str}\n"
        f"Брой сесии: {session_count}\n"
        f"Общо съобщения: {turn_count}\n\n"
        f"РАЗГОВОРИ (хронологично):\n{turns_text}\n\n"
        "Върни STRICT JSON само с тези полета:\n"
        '{\n'
        '  "summary": "3-5 кратки параграфа на български, фокус върху работата свършена",\n'
        '  "key_topics": ["до 8 кратки етикета на теми"],\n'
        '  "decisions": ["до 5 ключови решения"],\n'
        '  "artifacts": ["файлове създадени/променени"]\n'
        '}\n\n'
        "Бъди фактически. Пиши на български, в трето лице (\"обсъдиха X\")."
    )

    raw = _llm_call(
        system="You are a factual summarizer. Respond with valid JSON only, no markdown.",
        user=prompt,
    )

    parsed: dict | None = None
    if raw:
        raw_clean = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw_clean = re.sub(r"\s*```$", "", raw_clean).strip()
        try:
            parsed = json.loads(raw_clean)
        except Exception:
            m = re.search(r"\{[\s\S]+\}", raw_clean)
            if m:
                try:
                    parsed = json.loads(m.group(0))
                except Exception:
                    pass

    if not parsed:
        first = turns[0]["content"][:100] if turns else ""
        parsed = {
            "summary": f"Fallback digest: {first}... [auto-generated]",
            "key_topics": [],
            "decisions": [],
            "artifacts": [],
        }

    digest = {
        "date": date_str,
        "generated_ts": now,
        "session_count": session_count,
        "summary": str(parsed.get("summary", ""))[:2000],
        "key_topics": list(parsed.get("key_topics", []))[:8],
        "decisions": list(parsed.get("decisions", []))[:5],
        "artifacts": list(parsed.get("artifacts", []))[:20],
        "task_changes": [],
    }
    _rdb_set(_digest_key(date_str), digest)
    log.info("continuity: digest generated %s (sessions=%d turns=%d)", date_str, session_count, turn_count)
    return digest


def ensure_yesterday_digest() -> dict | None:
    """Generate yesterday's digest if missing. Designed for background thread."""
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
    if get_digest(yesterday):
        return None
    try:
        return generate_daily_digest(yesterday)
    except Exception as exc:
        log.warning("continuity: ensure_yesterday_digest failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Continuity context for system prompt
# ---------------------------------------------------------------------------

def _relative_time(ts: int) -> str:
    diff = int(time.time()) - ts
    if diff < 3600:
        return f"преди {max(1, diff // 60)}м"
    if diff < 86400:
        return f"преди {diff // 3600}ч"
    return f"преди {diff // 86400} дни"


def build_continuity_context(
    max_open_tasks: int = 10,
    include_days: int = 3,
    session_id: str | None = None,
) -> str:
    """Build formatted continuity block for system prompt injection.

    *session_id*: when set, filter open tasks to only those linked to this
    session_id (Playground isolation). Digests are filtered by topic
    overlap — if a digest mentions a project name matching the session_id,
    it is shown.

    Returns empty string on any failure (never throws).
    """
    try:
        return _build_context_inner(max_open_tasks, include_days, session_id)
    except Exception as exc:
        log.warning("continuity: build_continuity_context failed: %s", exc)
        return ""


def _build_context_inner(
    max_open_tasks: int,
    include_days: int,
    session_id: str | None,
) -> str:
    today_date = datetime.now(timezone.utc).date()
    today_str = today_date.isoformat()
    yesterday_str = (today_date - timedelta(days=1)).isoformat()

    day_names_bg = ["Пон", "Вт", "Ср", "Чет", "Пет", "Съб", "Нед"]
    day_name = day_names_bg[today_date.weekday()]

    lines: list[str] = []
    lines.append("━━━ CONTINUITY CONTEXT ━━━")
    lines.append(f"📅 Днес: {today_str} ({day_name})")

    # ── Session filter: extract project slug from session_id ──
    _slug_hint = ""
    if session_id:
        # Session IDs like "playground:my-project" or "pg-auto:my-project:123"
        for prefix in ("playground:", "pg-auto-research:", "pg-auto-rev:", "pg-auto-plan:", "autopilot-plan:"):
            if session_id.startswith(prefix):
                _slug_hint = session_id[len(prefix):].split(":")[0]
                break
        if not _slug_hint:
            _slug_hint = session_id.split(":")[-1]

    yesterday_digest = get_digest(yesterday_str)
    if yesterday_digest and yesterday_digest.get("summary"):
        # If session_id is set, only show digest if it mentions this project
        if _slug_hint and _slug_hint not in (yesterday_digest.get("summary", "") + " " + " ".join(yesterday_digest.get("key_topics", []))):
            pass  # skip digest from unrelated project
        else:
            summary_first = (yesterday_digest["summary"].split("\n")[0])[:300]
            topics_list = yesterday_digest.get("key_topics", [])[:6]
            decisions_list = yesterday_digest.get("decisions", [])[:3]
            lines.append(f"\n📝 Вчера ({yesterday_str}) — digest:")
            lines.append(summary_first)
            if topics_list:
                lines.append("Ключови теми: " + ", ".join(topics_list))
            if decisions_list:
                lines.append("Решения: " + " · ".join(decisions_list))
    else:
        lines.append(f"\n📝 Вчера ({yesterday_str}): без digest (нова инсталация или без активност)")

    # ── Filter tasks by session_id when in Playground mode ──
    open_tasks = list_tasks(status="open", limit=max_open_tasks)
    rework_tasks = list_tasks(status="rework", limit=5)
    if session_id and _slug_hint:
        open_tasks = [
            t for t in open_tasks
            if any(_slug_hint in (sid or "") for sid in (t.get("session_ids") or []))
            or _slug_hint in (t.get("title", "") + " " + (t.get("summary") or ""))
        ]
        rework_tasks = [
            t for t in rework_tasks
            if any(_slug_hint in (sid or "") for sid in (t.get("session_ids") or []))
            or _slug_hint in (t.get("title", "") + " " + (t.get("summary") or ""))
        ]
    all_active = open_tasks + rework_tasks

    if all_active:
        lines.append(f"\n📋 АКТИВНИ ЗАДАЧИ ({len(open_tasks)} open, {len(rework_tasks)} rework):")
        for t in all_active[:max_open_tasks]:
            icon = "🔵" if t.get("status") == "open" else "🔄"
            age = _relative_time(t["created_ts"])
            updated = _relative_time(t["updated_ts"])
            lines.append(f"  {icon} [{t['id']}] {t['title']} — отворена {age}, последно {updated}")
            smry = (t.get("summary") or "")[:100]
            if smry:
                lines.append(f"    {smry}")
    else:
        lines.append("\n📋 АКТИВНИ ЗАДАЧИ: няма")

    recent_lines: list[str] = []
    for i in range(1, include_days + 1):
        d = (today_date - timedelta(days=i)).isoformat()
        dig = get_digest(d)
        if not dig:
            continue
        # Skip digests from unrelated projects when in Playground mode
        if _slug_hint and _slug_hint not in (dig.get("summary", "") + " " + " ".join(dig.get("key_topics", []))):
            continue
        topics = " + ".join((dig.get("key_topics") or [])[:3])
        sess = dig.get("session_count", 0)
        recent_lines.append(f"  • {d}: {topics or '(без теми)'} · {sess} сесии")

    if recent_lines:
        lines.append("\n🕘 Последни дни:")
        lines.extend(recent_lines)

    lines.append("\nВАЖНО: Когато Николай попита \"продължи от снощи/последния път/по тема X\",")
    lines.append("ПЪРВО провери АКТИВНИ ЗАДАЧИ и recent digests преди да приемеш че е нов въпрос.")
    if session_id:
        lines.append(f"PLAYGROUND ISOLATION: Виждаш САМО задачи и digests свързани с този проект ({_slug_hint or session_id}).")
        lines.append("НЕ реферирай други проекти. НЕ мешай код от други проекти.")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    return "\n".join(lines)
