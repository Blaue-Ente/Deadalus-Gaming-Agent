"""Daedalus Planner — автономно изпълнение на планове.

Таблици в PostgreSQL (създадени от db.ensure_schema):
  - daedalus_plans      — план (title, description, status)
  - daedalus_plan_steps — стъпки (plan_id, order, title, description, status, proposal_id)

Background scheduler (run_planner_loop):
  - Всеки SCHEDULER_INTERVAL секунди взима следващата чакаща стъпка
  - Изпраща я на Дедал (chat with for_self=True)
  - Актуализира статуса на стъпката
  - При proposal_id → маркира awaiting_approval
  - Owner approve-ва proposal → step → done

CRUD функции са синхронни (asyncpg wrapper) + async версии за FastAPI.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

log = logging.getLogger("argos_data_nexus.daedalus.planner")

SCHEDULER_INTERVAL = 5 * 60  # секунди между проверките


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_id(prefix: str = "p") -> str:
    return f"{prefix}_{int(time.time())}_{uuid.uuid4().hex[:8]}"


async def _pool():
    from .host import get_pool
    return await get_pool()


# ---------------------------------------------------------------------------
# Plans CRUD
# ---------------------------------------------------------------------------

async def create_plan(title: str, description: str = "", status: str = "active") -> dict[str, Any]:
    pid = _new_id("plan")
    db = await _pool()
    async with db.acquire() as conn:
        await conn.execute(
            "INSERT INTO daedalus_plans (id, title, description, status) VALUES ($1,$2,$3,$4)",
            pid, title, description, status,
        )
    log.info("Planner: created plan %s — %s (status=%s)", pid, title, status)
    return {"ok": True, "plan_id": pid, "status": status}


async def auto_plan_from_goal(goal: str) -> dict[str, Any]:
    """Питаме Дедал да разбие естественоезикова цел на стъпки.

    Връща draft план (status='draft') със стъпки. Owner го одобрява чрез
    /admin/daedalus/plans/{id}/approve преди scheduler да го стартира.
    """
    import json as _json
    import re as _re
    from .agent import get_daedalus

    daedalus = get_daedalus()
    prompt = (
        f"━━━ AUTO-PLAN REQUEST ━━━\n"
        f"Николай иска да разбиеш следната цел на конкретни, изпълними стъпки за planner-а:\n\n"
        f"ЦЕЛ: {goal}\n\n"
        f"Върни САМО валиден JSON в следния формат (никакъв друг текст, без markdown):\n"
        f'{{\n'
        f'  "title": "Кратко заглавие на плана (до 60 chars)",\n'
        f'  "description": "1-2 изречения за общата цел",\n'
        f'  "steps": [\n'
        f'    {{"title": "Стъпка 1 кратко", "description": "Какво конкретно трябва да се направи"}},\n'
        f'    {{"title": "Стъпка 2 кратко", "description": "Описание..."}}\n'
        f'  ]\n'
        f'}}\n\n'
        f"ПРАВИЛА:\n"
        f"- Между 2 и 8 стъпки\n"
        f"- Всяка стъпка трябва да е конкретна — да можеш да я изпълниш с NEED_PROPOSE или анализ\n"
        f"- НЕ емитвай NEED_* маркери в този отговор\n"
        f"- НЕ обвивай JSON-а в markdown\n"
        f"- Започни директно с {{ и завърши с }}"
    )

    result = await daedalus.chat(
        message=prompt,
        session_id=f"autoplan_{int(time.time())}",
        for_self=True,
    )
    raw = result.get("reply", "") or ""

    # Опит за извличане на JSON блок
    raw_stripped = raw.strip()
    # Премахваме markdown fence ако има
    raw_stripped = _re.sub(r"^```(?:json)?\s*", "", raw_stripped)
    raw_stripped = _re.sub(r"\s*```$", "", raw_stripped)
    # Намираме първия {...} блок
    m = _re.search(r"\{.*\}", raw_stripped, _re.DOTALL)
    if not m:
        return {"ok": False, "error": "Дедал не върна валиден JSON", "raw_reply": raw[:1000]}

    try:
        parsed = _json.loads(m.group(0))
    except _json.JSONDecodeError as exc:
        return {"ok": False, "error": f"JSON parse fail: {exc}", "raw_reply": raw[:1000]}

    title = str(parsed.get("title", "Untitled plan"))[:120]
    description = str(parsed.get("description", ""))[:500]
    steps = parsed.get("steps", [])
    if not isinstance(steps, list) or not steps:
        return {"ok": False, "error": "няма стъпки в JSON-а", "parsed": parsed}

    # Pre-валидираме стъпките преди да пишем нещо в DB
    valid_steps: list[tuple[str, str]] = []
    for i, s in enumerate(steps[:10]):  # max 10 стъпки safety
        if not isinstance(s, dict):
            continue
        s_title = str(s.get("title", f"Step {i+1}")).strip()[:200]
        s_desc = str(s.get("description", "")).strip()[:1000]
        if not s_title:
            continue
        valid_steps.append((s_title, s_desc))

    if not valid_steps:
        return {"ok": False, "error": "няма валидни стъпки след филтриране", "parsed": parsed}

    # Atomic insert: plan + всички стъпки в една транзакция
    pid = _new_id("plan")
    db = await _pool()
    step_results: list[dict[str, Any]] = []
    async with db.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO daedalus_plans (id, title, description, status) VALUES ($1,$2,$3,'draft')",
                pid, title, description,
            )
            for i, (s_title, s_desc) in enumerate(valid_steps):
                sid = _new_id("step")
                await conn.execute(
                    """INSERT INTO daedalus_plan_steps
                       (id, plan_id, step_order, title, description, status)
                       VALUES ($1, $2, $3, $4, $5, 'pending')""",
                    sid, pid, i + 1, s_title, s_desc,
                )
                step_results.append({"step_id": sid, "title": s_title})

    log.info("Auto-plan: created draft plan %s with %d steps", pid, len(step_results))
    return {
        "ok": True,
        "plan_id": pid,
        "title": title,
        "description": description,
        "steps": step_results,
        "status": "draft",
        "next_action": f"POST /admin/daedalus/planner/plans/{pid}/approve to activate",
    }


async def list_plans(status: str | None = None) -> list[dict[str, Any]]:
    db = await _pool()
    async with db.acquire() as conn:
        if status:
            rows = await conn.fetch(
                "SELECT * FROM daedalus_plans WHERE status=$1 ORDER BY created_at DESC", status
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM daedalus_plans ORDER BY created_at DESC"
            )
    return [dict(r) for r in rows]


async def get_plan(plan_id: str) -> dict[str, Any] | None:
    db = await _pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM daedalus_plans WHERE id=$1", plan_id)
        if not row:
            return None
        steps = await conn.fetch(
            "SELECT * FROM daedalus_plan_steps WHERE plan_id=$1 ORDER BY step_order ASC",
            plan_id,
        )
    plan = dict(row)
    plan["steps"] = [dict(s) for s in steps]
    return plan


async def update_plan_status(plan_id: str, status: str) -> bool:
    db = await _pool()
    async with db.acquire() as conn:
        r = await conn.execute(
            "UPDATE daedalus_plans SET status=$1, updated_at=NOW() WHERE id=$2",
            status, plan_id,
        )
    return r.split()[-1] == "1"


async def delete_plan(plan_id: str) -> bool:
    db = await _pool()
    async with db.acquire() as conn:
        r = await conn.execute("DELETE FROM daedalus_plans WHERE id=$1", plan_id)
    return r.split()[-1] == "1"


# ---------------------------------------------------------------------------
# Steps CRUD
# ---------------------------------------------------------------------------

async def add_step(
    plan_id: str,
    title: str,
    description: str = "",
    step_order: int | None = None,
    depends_on: list[str] | None = None,
    max_retries: int = 3,
) -> dict[str, Any]:
    sid = _new_id("step")
    depends_str = ",".join(depends_on) if depends_on else ""
    db = await _pool()
    async with db.acquire() as conn:
        if step_order is None:
            row = await conn.fetchrow(
                "SELECT COALESCE(MAX(step_order),0)+1 AS next FROM daedalus_plan_steps WHERE plan_id=$1",
                plan_id,
            )
            step_order = row["next"]
        await conn.execute(
            """INSERT INTO daedalus_plan_steps
               (id, plan_id, step_order, title, description, status, depends_on, max_retries)
               VALUES ($1,$2,$3,$4,$5,'pending',$6,$7)""",
            sid, plan_id, step_order, title, description, depends_str, max_retries,
        )
    log.info("Planner: added step %s to plan %s — %s (deps=%s)", sid, plan_id, title, depends_str or "none")
    return {"ok": True, "step_id": sid, "plan_id": plan_id}


async def get_next_pending_step() -> dict[str, Any] | None:
    """Взима следващата изпълнима стъпка:
    - status='pending' без неизпълнени зависимости
    - или status='waiting_retry' с retry_at <= NOW()
    """
    db = await _pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT s.*, p.title AS plan_title
               FROM daedalus_plan_steps s
               JOIN daedalus_plans p ON p.id = s.plan_id
               WHERE p.status = 'active'
                 AND (
                   s.status = 'pending'
                   OR (s.status = 'waiting_retry'
                       AND s.retry_at IS NOT NULL
                       AND s.retry_at <= NOW())
                 )
               ORDER BY p.created_at ASC, s.step_order ASC
               LIMIT 1"""
        )
    if not row:
        return None
    step = dict(row)

    # Проверяваме зависимостите (depends_on = comma-separated step IDs)
    raw_deps = step.get("depends_on") or ""
    dep_ids = [d.strip() for d in raw_deps.split(",") if d.strip()]
    if dep_ids:
        async with db.acquire() as conn:
            not_done = await conn.fetchval(
                "SELECT COUNT(*) FROM daedalus_plan_steps WHERE id = ANY($1::text[]) AND status != 'passed'",
                dep_ids,
            )
        if not_done and not_done > 0:
            log.debug("Planner: step %s blocked by %d unresolved dependencies", step["id"], not_done)
            return None

    return step


async def _set_retry(
    step_id: str,
    retry_count: int,
    retry_at: Any,
    error_msg: str | None = None,
) -> None:
    """Маркира стъпка като waiting_retry с актуализиран retry_count и retry_at."""
    db = await _pool()
    async with db.acquire() as conn:
        await conn.execute(
            """UPDATE daedalus_plan_steps
               SET status='waiting_retry', retry_count=$1, retry_at=$2, error_msg=$3
               WHERE id=$4""",
            retry_count, retry_at, error_msg, step_id,
        )
    log.info("Planner: step %s → waiting_retry (attempt %d, retry at %s)", step_id, retry_count, retry_at)


async def mark_step(
    step_id: str,
    status: str,
    *,
    proposal_id: str | None = None,
    error_msg: str | None = None,
) -> None:
    """Обновява статуса на стъпка.

    Валидни статуси: pending | running | passed | done | failed |
                     waiting_retry | blocked | awaiting_approval | in_progress
    passed = канонично "успешно завършена"; done е алиас за backward compat.
    """
    db = await _pool()
    terminal = status in ("passed", "done", "failed", "blocked")
    async with db.acquire() as conn:
        if status == "running":
            await conn.execute(
                """UPDATE daedalus_plan_steps
                   SET status='running', started_at=NOW(),
                       proposal_id=COALESCE($1,proposal_id)
                   WHERE id=$2""",
                proposal_id, step_id,
            )
        elif terminal:
            await conn.execute(
                """UPDATE daedalus_plan_steps
                   SET status=$1, proposal_id=COALESCE($2,proposal_id),
                       error_msg=COALESCE($3,error_msg), completed_at=NOW()
                   WHERE id=$4""",
                status, proposal_id, error_msg, step_id,
            )
        else:
            await conn.execute(
                """UPDATE daedalus_plan_steps
                   SET status=$1, proposal_id=COALESCE($2,proposal_id),
                       error_msg=COALESCE($3,error_msg)
                   WHERE id=$4""",
                status, proposal_id, error_msg, step_id,
            )
    log.info("Planner: step %s → %s", step_id, status)


# ---------------------------------------------------------------------------
# Checkpoint system — 6 контролни точки на стъпка
# ---------------------------------------------------------------------------
# Дефиниция (Николай поиска "6 чекпойнта"):
#   C1_RECEIVED   — Дедал получи задачата (prompt sent)
#   C2_RESPONDED  — Дедал върна непразен отговор
#   C3_REASONED   — Tools executed ИЛИ съдържателен анализ (>200 chars текст)
#   C4_PRODUCED   — Конкретен output (proposal_id, inbox_msg, plan_step добавена)
#   C5_VALIDATED  — Output минава sanity check (proposal exists в DB / inbox непразен)
#   C6_COMMITTED  — DB състоянието е terminal (awaiting_approval/passed/done)
#
# Завършена = всички 6 ИЛИ manual_override от owner.
# < 5 → trigger self-repair. Self-repair × 2 неуспешни → needs_review + inbox.

CHECKPOINT_KEYS = [
    "C1_RECEIVED", "C2_RESPONDED", "C3_REASONED",
    "C4_PRODUCED", "C5_VALIDATED", "C6_COMMITTED",
]
MAX_REPAIR_ATTEMPTS = 2


def _new_checkpoints() -> dict[str, Any]:
    return {k: {"passed": False, "note": None, "at": None} for k in CHECKPOINT_KEYS}


async def _mark_checkpoint(
    step_id: str, key: str, passed: bool, note: str | None = None
) -> None:
    """Atomically updates one checkpoint inside the JSONB column."""
    import datetime as _dt
    import json as _json
    if key not in CHECKPOINT_KEYS:
        log.warning("Unknown checkpoint key: %s", key)
        return
    db = await _pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT checkpoints FROM daedalus_plan_steps WHERE id=$1", step_id
        )
        if not row:
            return
        existing = row["checkpoints"] or {}
        if isinstance(existing, str):
            try:
                existing = _json.loads(existing)
            except Exception:
                existing = {}
        if not existing:
            existing = _new_checkpoints()
        existing[key] = {
            "passed": bool(passed),
            "note": (note[:300] if note else None),
            "at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        }
        await conn.execute(
            "UPDATE daedalus_plan_steps SET checkpoints=$1::jsonb WHERE id=$2",
            _json.dumps(existing), step_id,
        )
    log.debug("Checkpoint: step=%s %s passed=%s note=%s", step_id, key, passed, note)


async def update_step_progress(step_id: str, note: str) -> None:
    """Записва текущ progress note (показва се в UI като 'какво прави сега')."""
    db = await _pool()
    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE daedalus_plan_steps SET progress_note=$1 WHERE id=$2",
            note[:500], step_id,
        )


async def _evaluate_checkpoints(step_id: str) -> dict[str, Any]:
    """Връща summary: {passed_count, total, missing, complete, overridden}."""
    import json as _json
    db = await _pool()
    async with db.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT checkpoints, manual_override FROM daedalus_plan_steps WHERE id=$1",
            step_id,
        )
    if not row:
        return {"passed_count": 0, "total": 6, "missing": CHECKPOINT_KEYS,
                "complete": False, "overridden": False}
    cps = row["checkpoints"] or {}
    if isinstance(cps, str):
        try:
            cps = _json.loads(cps)
        except Exception:
            cps = {}
    passed = [k for k in CHECKPOINT_KEYS if cps.get(k, {}).get("passed")]
    missing = [k for k in CHECKPOINT_KEYS if k not in passed]
    overridden = bool(row["manual_override"])
    return {
        "passed_count": len(passed),
        "total": len(CHECKPOINT_KEYS),
        "passed": passed,
        "missing": missing,
        "complete": len(passed) == len(CHECKPOINT_KEYS) or overridden,
        "overridden": overridden,
    }


async def _self_repair_step(step: dict[str, Any], eval_result: dict[str, Any]) -> dict[str, Any]:
    """Пита Дедал какво е пропуснал и иска от него или (a) да го завърши сега,
    или (b) да обясни конкретно какво му трябва.

    Връща: {ok, action: 'completed'|'needs_review'|'error', note}
    Резултатът се записва в `diagnostics` колоната и инкрементира repair_attempts.
    """
    import datetime as _dt
    from .agent import get_daedalus  # type: ignore
    step_id = step["id"]
    current_attempts = step.get("repair_attempts") or 0

    if current_attempts >= MAX_REPAIR_ATTEMPTS:
        return {"ok": False, "action": "needs_review",
                "note": f"Max repair attempts ({MAX_REPAIR_ATTEMPTS}) изчерпани"}

    missing_str = ", ".join(eval_result["missing"]) or "none"
    diag = (
        f"━━━ SELF-REPAIR: непълно изпълнение ━━━\n"
        f"Завърши: {eval_result['passed_count']}/{eval_result['total']} чекпойнта.\n"
        f"Пропуснати: {missing_str}\n\n"
        f"Оригинална задача:\n"
        f"План: {step.get('plan_title', '?')}\n"
        f"Стъпка #{step['step_order']}: {step['title']}\n"
        f"{step['description']}\n\n"
        f"Имаш ДВЕ опции:\n"
        f"1) АКО можеш да я завършиш сега — направи го: емитни NEED_PROPOSE_START "
        f"за код, или NEED_INBOX_MSG за анализен резултат. Не пиши друго.\n"
        f"2) АКО НЕ можеш — обясни конкретно с едно изречение какво ти трябва "
        f"(достъп, информация, разяснение от Николай). Започни с 'BLOCKED: '."
    )

    try:
        d = get_daedalus()
        result = await d.chat(
            message=diag,
            session_id=f"selfrepair_{step_id}_{current_attempts + 1}",
            for_self=True,
        )
        reply = (result.get("reply") or "")[:2000]
        pid = result.get("pending_proposal", {}).get("proposal_id")

        inbox_used = any(
            tc.get("tool") == "inbox_msg" and tc.get("ok")
            for tc in ((result.get("context") or {}).get("tool_calls", []) or [])
        )
        produced_pid: str | None = None
        if pid:
            await _mark_checkpoint(step_id, "C4_PRODUCED", True, "self-repair → proposal")
            await _mark_checkpoint(step_id, "C5_VALIDATED", True, f"proposal_id={pid}")
            # Persist proposal_id immediately — caller relies on this.
            db_pid = await _pool()
            async with db_pid.acquire() as conn_pid:
                await conn_pid.execute(
                    "UPDATE daedalus_plan_steps SET proposal_id=$1 WHERE id=$2",
                    pid, step_id,
                )
            produced_pid = pid
            note = f"Self-repair успешен (опит {current_attempts + 1}): proposal {pid}"
            action = "completed"
        elif inbox_used:
            await _mark_checkpoint(step_id, "C4_PRODUCED", True, "self-repair → inbox_msg")
            await _mark_checkpoint(step_id, "C5_VALIDATED", True, "inbox_msg ok")
            note = f"Self-repair успешен (опит {current_attempts + 1}): inbox_msg изпратен"
            action = "completed"
        elif "BLOCKED:" in reply.upper() or "BLOCKED " in reply.upper():
            note = f"Self-repair → BLOCKED (опит {current_attempts + 1}):\n{reply[:800]}"
            action = "needs_review"
        else:
            note = f"Self-repair неясен резултат (опит {current_attempts + 1}):\n{reply[:800]}"
            action = "needs_review"
    except Exception as exc:
        note = f"Self-repair грешка (опит {current_attempts + 1}): {exc}"
        action = "error"
        produced_pid = None

    db = await _pool()
    async with db.acquire() as conn:
        await conn.execute(
            """UPDATE daedalus_plan_steps
               SET repair_attempts = COALESCE(repair_attempts,0) + 1,
                   diagnostics = COALESCE(diagnostics,'') ||
                       CASE WHEN diagnostics IS NULL OR diagnostics='' THEN '' ELSE E'\n---\n' END ||
                       $1
               WHERE id=$2""",
            f"[{_dt.datetime.now(_dt.timezone.utc).isoformat()}] {note}", step_id,
        )
    log.info("Self-repair: step %s → %s | %s", step_id, action, note[:120])
    return {
        "ok": action == "completed",
        "action": action,
        "note": note,
        "proposal_id": produced_pid,
    }


async def manual_override_step(step_id: str, approved_by: str = "nikolai") -> dict[str, Any]:
    """Owner manual override — маркира стъпката като complete без проверка на чекпойнти."""
    import datetime as _dt
    db = await _pool()
    async with db.acquire() as conn:
        await conn.execute(
            """UPDATE daedalus_plan_steps
               SET manual_override=TRUE,
                   diagnostics = COALESCE(diagnostics,'') ||
                       CASE WHEN diagnostics IS NULL OR diagnostics='' THEN '' ELSE E'\n---\n' END ||
                       $1
               WHERE id=$2""",
            f"[{_dt.datetime.now(_dt.timezone.utc).isoformat()}] Manual override by {approved_by}",
            step_id,
        )
    await mark_step(step_id, "passed")
    return {"ok": True, "step_id": step_id, "status": "passed", "overridden_by": approved_by}


# ---------------------------------------------------------------------------
# Agent Inbox (Дедал → Replit Agent)
# ---------------------------------------------------------------------------

async def write_inbox(
    subject: str,
    body: str,
    from_who: str = "daedalus",
    priority: int = 2,
) -> dict[str, Any]:
    """Записва съобщение в inbox-а на Agent-а."""
    mid = _new_id("msg")
    db = await _pool()
    async with db.acquire() as conn:
        await conn.execute(
            """INSERT INTO daedalus_agent_inbox
               (id, from_who, subject, body, priority)
               VALUES ($1,$2,$3,$4,$5)""",
            mid, from_who, subject, body, priority,
        )
    log.info("Inbox: new message %s from %s — %s", mid, from_who, subject)
    return {"ok": True, "message_id": mid}


async def list_inbox(unread_only: bool = False) -> list[dict[str, Any]]:
    db = await _pool()
    async with db.acquire() as conn:
        if unread_only:
            rows = await conn.fetch(
                "SELECT * FROM daedalus_agent_inbox WHERE read_at IS NULL ORDER BY priority DESC, created_at DESC"
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM daedalus_agent_inbox ORDER BY created_at DESC LIMIT 50"
            )
    return [dict(r) for r in rows]


async def mark_inbox_read(message_id: str) -> None:
    db = await _pool()
    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE daedalus_agent_inbox SET read_at=NOW() WHERE id=$1",
            message_id,
        )


async def get_unread_inbox_for_prompt() -> str:
    """Форматира непрочетените съобщения за инжектиране в prompt-а на Дедал.

    Маркира съобщенията като прочетени веднага след инжектирането — Дедал ги е
    "видял" щом са в контекста му, независимо дали ги е коментирал.
    """
    msgs = await list_inbox(unread_only=True)
    if not msgs:
        return ""
    batch = msgs[:5]
    lines = [f"━━━ INBOX: {len(batch)} НЕПРОЧЕТЕНИ СЪОБЩЕНИЯ ━━━"]
    for m in batch:
        created = m.get("created_at")
        if hasattr(created, "strftime"):
            ts = created.strftime("%Y-%m-%d %H:%M")
        elif created:
            ts = str(created)[:16]
        else:
            ts = "?"
        lines.append(f"[{ts}] От: {m['from_who']} | Приоритет: {m.get('priority', 2)}\nТема: {m['subject']}\n{m['body']}")
        # Mark as read — Дедал ги вижда в контекста си, те са вече "прочетени"
        try:
            await mark_inbox_read(m["id"])
        except Exception:
            pass
    lines.append("━━━ КРАЙ НА INBOX ━━━")
    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# Planner scheduler loop
# ---------------------------------------------------------------------------

async def run_planner_loop() -> None:
    """Background loop — проверява за чакащи стъпки и ги подава на Дедал.

    State machine:
        pending / waiting_retry → running → passed | failed
        failed (retry < max) → waiting_retry (exponential backoff)
        failed (retry >= max) → blocked + urgent inbox msg
        awaiting_approval — scheduler не докосва; admin route auto-advance при approve
    """
    import datetime as _dt
    await asyncio.sleep(90)  # warmup — изчакваме Дедал да се зареди
    log.info("Planner scheduler: started (interval=%ds)", SCHEDULER_INTERVAL)

    while True:
        try:
            step = await get_next_pending_step()
            if step:
                step_id = step["id"]
                log.info("Planner: working on step %s — %s", step_id, step["title"])
                await mark_step(step_id, "running")
                # Reset checkpoints за новия опит
                import json as _json
                _db = await _pool()
                async with _db.acquire() as _conn:
                    await _conn.execute(
                        "UPDATE daedalus_plan_steps SET checkpoints=$1::jsonb, progress_note=$2 WHERE id=$3",
                        _json.dumps(_new_checkpoints()), "стартирана…", step_id,
                    )

                # Текущ retry_count (може да дойде от waiting_retry)
                current_retries = step.get("retry_count") or 0
                max_retries = step.get("max_retries") or 3

                deps_info = ""
                raw_deps = step.get("depends_on") or ""
                if raw_deps.strip():
                    deps_info = f"Зависимости (вече passed): {raw_deps}\n"

                prompt = (
                    f"━━━ PLANNER — АВТОНОМНА ЗАДАЧА ━━━\n"
                    f"План: {step['plan_title']}\n"
                    f"Стъпка #{step['step_order']}: {step['title']}\n"
                    + (f"(Опит {current_retries + 1}/{max_retries})\n" if current_retries > 0 else "")
                    + f"{deps_info}\n"
                    f"{step['description']}\n\n"
                    f"Работи автономно по тази задача. Ако е нужно да пишеш код — "
                    f"емитни NEED_PROPOSE_START блок. Ако стъпката изисква само анализ — "
                    f"напиши резюме и емитни NEED_INBOX_MSG с резултата за Agent-а.\n"
                    f"ВАЖНО: Имаш няколко итерации с tools (можеш първо да прочетеш файлове "
                    f"с NEED_READ_FILE, после да пишеш код с NEED_PROPOSE_START в същата сесия).\n"
                    f"Step ID: {step_id}"
                )

                try:
                    from .agent import get_daedalus  # type: ignore
                    d = get_daedalus()
                    await _mark_checkpoint(step_id, "C1_RECEIVED", True, "prompt подаден на Дедал")
                    await update_step_progress(step_id, "Дедал работи…")

                    result = await d.chat(
                        message=prompt,
                        session_id=f"planner_{step_id}",
                        for_self=True,
                    )

                    # ── Checkpoint оценка на резултата ────────────────────
                    reply_text = (result.get("reply") or "")
                    tool_calls = (result.get("context") or {}).get("tool_calls", []) or []
                    pid = (result.get("pending_proposal") or {}).get("proposal_id")
                    inbox_used = any(tc.get("tool") == "inbox_msg" and tc.get("ok") for tc in tool_calls)

                    if reply_text.strip():
                        await _mark_checkpoint(step_id, "C2_RESPONDED", True, f"{len(reply_text)} chars")
                    if tool_calls or len(reply_text.strip()) > 200:
                        await _mark_checkpoint(
                            step_id, "C3_REASONED", True,
                            f"{len(tool_calls)} tool calls, {len(reply_text)} chars"
                        )
                    if pid or inbox_used:
                        await _mark_checkpoint(
                            step_id, "C4_PRODUCED", True,
                            f"proposal={pid}" if pid else "inbox_msg sent"
                        )
                        await _mark_checkpoint(step_id, "C5_VALIDATED", True, "output exists")

                    # ── Terminal status ───────────────────────────────────
                    if pid:
                        await mark_step(step_id, "awaiting_approval", proposal_id=pid)
                        await _mark_checkpoint(step_id, "C6_COMMITTED", True, "awaiting_approval")
                        await update_step_progress(step_id, "Чака одобрение")
                        log.info("Planner: step %s → awaiting_approval (proposal %s)", step_id, pid)
                    else:
                        # Без proposal — оценяваме чекпойнтите преди да решим.
                        # C6 се сетва ПОСЛЕ (terminal mark), затова clean-pass прагът
                        # е 5/6 — C1..C5 трябва да са pass-нати преди commit.
                        eval_res = await _evaluate_checkpoints(step_id)
                        pre_commit_passed = eval_res["passed_count"] - (
                            1 if "C6_COMMITTED" not in eval_res["missing"] else 0
                        )
                        # pre_commit_passed = брой чекпойнти C1..C5 които са pass-нати
                        if pre_commit_passed >= 5 or eval_res["overridden"]:
                            await mark_step(step_id, "passed")
                            await _mark_checkpoint(step_id, "C6_COMMITTED", True, "passed clean")
                            await update_step_progress(step_id, "Завършена ✓")
                            log.info("Planner: step %s → passed (5/5 pre-commit чекпойнта)", step_id)
                        elif pre_commit_passed >= 3:
                            # Има анализ/tools но няма concrete output → self-repair
                            log.warning(
                                "Planner: step %s incomplete (%d/6), triggering self-repair",
                                step_id, eval_res["passed_count"]
                            )
                            await update_step_progress(
                                step_id,
                                f"Самопоправка ({eval_res['passed_count']}/6)…"
                            )
                            repair = await _self_repair_step(step, eval_res)
                            if repair["action"] == "completed":
                                # Re-evaluate след self-repair
                                eval_res2 = await _evaluate_checkpoints(step_id)
                                # Проверка дали self-repair върна proposal
                                final_pid = None
                                async with (await _pool()).acquire() as _c:
                                    _r = await _c.fetchrow(
                                        "SELECT proposal_id FROM daedalus_plan_steps WHERE id=$1", step_id
                                    )
                                    final_pid = _r["proposal_id"] if _r else None
                                if final_pid:
                                    await mark_step(step_id, "awaiting_approval", proposal_id=final_pid)
                                    await _mark_checkpoint(step_id, "C6_COMMITTED", True, "self-repair → awaiting_approval")
                                    await update_step_progress(step_id, "Чака одобрение (след самопоправка)")
                                else:
                                    await mark_step(step_id, "passed")
                                    await _mark_checkpoint(step_id, "C6_COMMITTED", True, "self-repair → passed")
                                    await update_step_progress(step_id, "Завършена ✓ (самопоправка)")
                            else:
                                await mark_step(
                                    step_id, "blocked",
                                    error_msg=f"Self-repair: {repair['action']}. {repair['note'][:200]}"
                                )
                                await update_step_progress(step_id, "ЗАСЕДНАЛА — чака owner")
                                await write_inbox(
                                    subject=f"[NEEDS REVIEW] {step['title'][:60]}",
                                    body=(
                                        f"Стъпка завърши с {eval_res['passed_count']}/6 чекпойнта.\n"
                                        f"Пропуснати: {', '.join(eval_res['missing'])}\n\n"
                                        f"Self-repair резултат: {repair['action']}\n"
                                        f"{repair['note'][:1000]}\n\n"
                                        f"Step ID: {step_id}\n"
                                        f"Manual override: POST /admin/daedalus/planner/steps/{step_id}/override"
                                    ),
                                    from_who="planner",
                                    priority=3,
                                )
                        else:
                            # Тотален провал — нищо съществено не се случи
                            await mark_step(
                                step_id, "blocked",
                                error_msg=f"Стъпката върна само {eval_res['passed_count']}/6 чекпойнта без output."
                            )
                            await update_step_progress(step_id, "ПРОВАЛЕНА — нищо не се случи")
                            await write_inbox(
                                subject=f"[BLOCKED] {step['title'][:60]} — total fail",
                                body=(
                                    f"Стъпка завърши с {eval_res['passed_count']}/6 чекпойнта.\n"
                                    f"Дедал не върна нито proposal, нито inbox_msg.\n"
                                    f"Reply length: {len(reply_text)} chars\n"
                                    f"Tool calls: {len(tool_calls)}\n\n"
                                    f"Step: {step['title']}\n{step['description']}\n\n"
                                    f"Step ID: {step_id}\n"
                                    f"Manual override: POST /admin/daedalus/planner/steps/{step_id}/override"
                                ),
                                from_who="planner",
                                priority=3,
                            )

                except Exception as exc:
                    log.error("Planner: step %s error (attempt %d/%d): %s",
                               step_id, current_retries + 1, max_retries, exc)
                    next_retries = current_retries + 1
                    if next_retries < max_retries:
                        # Exponential backoff: 2min → 4min → 8min …
                        backoff_s = (2 ** next_retries) * 60
                        retry_at = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=backoff_s)
                        await _set_retry(step_id, next_retries, retry_at, str(exc))
                        log.warning(
                            "Planner: step %s → waiting_retry (attempt %d/%d, retry at %s)",
                            step_id, next_retries, max_retries, retry_at.isoformat()
                        )
                    else:
                        await mark_step(step_id, "blocked",
                                        error_msg=f"Max retries ({max_retries}) reached. Last: {exc}")
                        await write_inbox(
                            subject=f"[BLOCKED] Plan step: {step['title'][:60]}",
                            body=(
                                f"Step BLOCKED after {max_retries} failed attempts.\n\n"
                                f"Plan: {step['plan_title']}\n"
                                f"Step #{step['step_order']}: {step['title']}\n"
                                f"Step ID: {step_id}\n\n"
                                f"Last error:\n{exc}\n\n"
                                f"Action required: fix the underlying issue and re-queue the step manually "
                                f"via POST /admin/daedalus/planner/plans/{step['plan_id']}/steps/{step_id}/mark "
                                f"with {{status: 'pending'}} to retry."
                            ),
                            from_who="planner",
                            priority=3,
                        )
                        log.error("Planner: step %s → BLOCKED (max retries exhausted)", step_id)

        except asyncio.CancelledError:
            log.info("Planner scheduler: cancelled.")
            return
        except Exception as exc:
            log.error("Planner scheduler error (non-fatal): %s", exc)

        await asyncio.sleep(SCHEDULER_INTERVAL)
