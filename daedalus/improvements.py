"""Daedalus self-improvement helpers — 12-point intelligence upgrade.

Modules:
- SQLGuard          : multi-statement split + schema-aware column validation
- ErrorTracker      : per-session tool-error hashes (kill repeated-failure loops)
- RateLimitFallback : LLM provider fallback chain (Anthropic → OpenAI → Gemini)
- ProposalEnforcer  : auto-redirect NEED_PG INSERT trading_* → NEED_DATA_PROPOSAL
- ErrorLessons      : promote repeating cross-session tool errors to lessons

All helpers are stateless or use small in-memory caches. No external deps.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from collections import defaultdict, deque
from typing import Any

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# 1. SQLGuard — multi-statement split + schema validation
# ═══════════════════════════════════════════════════════════════════════════

_SCHEMA_CACHE: dict[str, set[str]] = {}     # table -> {column,...}
_SCHEMA_LOADED_AT: float = 0.0
_SCHEMA_TTL_S = 600.0                       # refresh every 10 min


async def _load_schema_cache() -> None:
    """Populate _SCHEMA_CACHE from information_schema. Cheap, runs once per TTL."""
    global _SCHEMA_LOADED_AT
    try:
        from argos_data_nexus.db import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = 'public'"
            )
        cache: dict[str, set[str]] = defaultdict(set)
        for r in rows:
            cache[r["table_name"]].add(r["column_name"])
        _SCHEMA_CACHE.clear()
        _SCHEMA_CACHE.update(cache)
        _SCHEMA_LOADED_AT = time.time()
        log.info("SQLGuard: schema cache loaded — %d tables", len(_SCHEMA_CACHE))
    except Exception as exc:
        log.warning("SQLGuard: schema load failed (non-fatal): %s", exc)


def _split_sql_statements(sql: str) -> list[str]:
    """Split SQL by `;`, respecting quoted strings and comments. Returns non-empty stmts."""
    out: list[str] = []
    buf: list[str] = []
    in_squote = False
    in_dquote = False
    in_line_comment = False
    in_block_comment = False
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if in_line_comment:
            buf.append(ch)
            if ch == "\n":
                in_line_comment = False
        elif in_block_comment:
            buf.append(ch)
            if ch == "*" and nxt == "/":
                buf.append(nxt)
                i += 2
                in_block_comment = False
                continue
        elif in_squote:
            buf.append(ch)
            if ch == "'" and nxt != "'":
                in_squote = False
            elif ch == "'" and nxt == "'":
                buf.append(nxt)
                i += 2
                continue
        elif in_dquote:
            buf.append(ch)
            if ch == '"':
                in_dquote = False
        else:
            if ch == "-" and nxt == "-":
                in_line_comment = True
                buf.append(ch)
            elif ch == "/" and nxt == "*":
                in_block_comment = True
                buf.append(ch)
            elif ch == "'":
                in_squote = True
                buf.append(ch)
            elif ch == '"':
                in_dquote = True
                buf.append(ch)
            elif ch == ";":
                stmt = "".join(buf).strip()
                if stmt:
                    out.append(stmt)
                buf.clear()
            else:
                buf.append(ch)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


_COL_FROM_INSERT_RE = re.compile(
    r"INSERT\s+INTO\s+(?:public\.)?(\w+)\s*\(([^)]+)\)", re.IGNORECASE
)
_COL_FROM_SELECT_RE = re.compile(
    r"SELECT\s+(.+?)\s+FROM\s+(?:public\.)?(\w+)", re.IGNORECASE | re.DOTALL
)


def _validate_columns(stmt: str) -> dict | None:
    """Return error-dict if statement references columns missing from schema cache.

    None = OK (or unknown table — pass through). Catches the
    `column "title" does not exist` repeat-failure pattern.
    """
    if not _SCHEMA_CACHE:
        return None

    # INSERT INTO table (col1, col2) ...
    m = _COL_FROM_INSERT_RE.search(stmt)
    if m:
        table = m.group(1).lower()
        cols = [c.strip().strip('"').lower() for c in m.group(2).split(",")]
        known = _SCHEMA_CACHE.get(table)
        if known is None:
            return None  # unknown table, let DB error out naturally
        known_lower = {c.lower() for c in known}
        missing = [c for c in cols if c not in known_lower and c]
        if missing:
            return {
                "error": f'column(s) not in table "{table}": {missing}',
                "available": sorted(known)[:30],
                "hint": "Check spelling. Use NEED_DB_SCHEMA: {table} to inspect.",
                "table": table,
            }
        return None

    # SELECT col1, col2 FROM table — but skip `*` and aggregates
    m = _COL_FROM_SELECT_RE.search(stmt)
    if m:
        col_part = m.group(1).strip()
        table = m.group(2).lower()
        if "*" in col_part or "(" in col_part:
            return None  # too complex to parse reliably; trust the DB
        known = _SCHEMA_CACHE.get(table)
        if known is None:
            return None
        known_lower = {c.lower() for c in known}
        # Strip aliases (col AS x) and qualifiers (t.col)
        raw_cols = [c.strip() for c in col_part.split(",")]
        cols: list[str] = []
        for rc in raw_cols:
            base = rc.split(" AS ", 1)[0].split(" as ", 1)[0].strip()
            if "." in base:
                base = base.split(".", 1)[1]
            base = base.strip().strip('"').lower()
            if base and base != "*":
                cols.append(base)
        missing = [c for c in cols if c not in known_lower]
        if missing:
            return {
                "error": f'column(s) not in table "{table}": {missing}',
                "available": sorted(known)[:30],
                "hint": "Check spelling. Use NEED_DB_SCHEMA: {table} to inspect.",
                "table": table,
            }
    return None


async def sql_guard_execute(query: str, executor) -> dict[str, Any]:
    """Run SQL with auto multi-statement split + column validation.

    `executor(stmt)` should be an async fn that runs ONE statement and returns
    a dict with {ok, row_count, rows, error}.
    """
    if time.time() - _SCHEMA_LOADED_AT > _SCHEMA_TTL_S:
        await _load_schema_cache()

    stmts = _split_sql_statements(query)
    if not stmts:
        return {"ok": False, "error": "empty query"}

    # Pre-validate every statement before touching the DB
    for stmt in stmts:
        bad = _validate_columns(stmt)
        if bad:
            return {
                "ok": False,
                "error": bad["error"],
                "hint": bad["hint"].replace("{table}", bad["table"]),
                "available_columns": bad["available"],
                "rejected_query": stmt[:200],
            }

    if len(stmts) == 1:
        return await executor(stmts[0])

    # Multi-statement: run sequentially, aggregate
    results: list[dict] = []
    total_rows = 0
    for stmt in stmts:
        r = await executor(stmt)
        results.append(r)
        if r.get("ok"):
            total_rows += int(r.get("row_count", 0))
        else:
            return {
                "ok": False,
                "error": f"statement {len(results)}/{len(stmts)} failed: {r.get('error')}",
                "failed_query": stmt[:200],
                "succeeded_count": len(results) - 1,
            }
    return {
        "ok": True,
        "row_count": total_rows,
        "statements": len(stmts),
        "rows": [r.get("rows", []) for r in results],
    }


# ═══════════════════════════════════════════════════════════════════════════
# 2. ErrorTracker — break tool loops on repeated identical failures
# ═══════════════════════════════════════════════════════════════════════════

class ErrorTracker:
    """Session-scoped error fingerprint tracker.

    Use: tracker.record(session, tool, error) → True if this exact error has
    been seen ≥2 times in the same session (signal: stop the loop).
    """
    def __init__(self) -> None:
        self._hits: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        # session -> {error_hash: count}
        self._tool_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        # session -> {tool: count}

    @staticmethod
    def _fingerprint(tool: str, error: str) -> str:
        # Normalize numbers/ids out so similar errors collide
        norm = re.sub(r"\d+", "N", (error or "")[:200])
        return hashlib.sha1(f"{tool}::{norm}".encode()).hexdigest()[:12]

    def record(self, session_id: str, tool: str, error: str) -> int:
        """Record a FAILED tool call. Returns repeat count of this fingerprint."""
        fp = self._fingerprint(tool, error)
        self._hits[session_id][fp] += 1
        return self._hits[session_id][fp]

    def track_call(self, session_id: str, tool: str) -> int:
        """Track ANY tool call (success or failure). Use for budget enforcement."""
        self._tool_counts[session_id][tool] += 1
        return self._tool_counts[session_id][tool]

    def should_break(self, session_id: str, tool: str, error: str,
                     threshold: int = 2) -> bool:
        return self.record(session_id, tool, error) >= threshold

    def tool_call_count(self, session_id: str, tool: str) -> int:
        return self._tool_counts[session_id].get(tool, 0)

    def reset(self, session_id: str) -> None:
        self._hits.pop(session_id, None)
        self._tool_counts.pop(session_id, None)


# Global singleton
_error_tracker = ErrorTracker()
def get_error_tracker() -> ErrorTracker:
    return _error_tracker


# ═══════════════════════════════════════════════════════════════════════════
# 3. RateLimitFallback — try alt provider when primary rate-limits
# ═══════════════════════════════════════════════════════════════════════════

# Fallback chain: (label, model, sdk, base_url_env, api_key_env)
FALLBACK_CHAIN: list[tuple[str, str, str, str, str]] = [
    # (label, model, sdk, base_url_env_OR_literal_https_url, api_key_env)
    # If base_url field starts with "https://", it is used as-is (literal URL).
    # Otherwise it is treated as an env var name.
    # NOTE: Replit AI Integrations (replit_openai / replit_gemini) are intentionally
    # excluded — ADN runs only on external keys (OpenRouter + Novita).
    # OpenRouter — Claude Sonnet as first external fallback (proven protocol follower)
    ("openrouter_claude", "anthropic/claude-sonnet-4-5", "openai",
     "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    # Novita — cheapest meaningful fallback: DeepSeek V3.2 ($0.30/$0.90)
    # Tested ✅: deepseek/deepseek-v3.2, qwen/qwen3-235b-a22b-instruct-2507,
    #            qwen/qwen3-coder-30b-a3b-instruct, meta-llama/llama-3.1-8b-instruct
    ("novita", "deepseek/deepseek-v3.2", "openai",
     "https://api.novita.ai/v3/openai", "NOVITA_API_KEY"),
]


def _is_rate_limit_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return ("429" in msg or "rate" in msg or "too many requests" in msg
            or "ratelimit" in msg or "overloaded" in msg or "529" in msg)


async def call_with_fallback(primary_call_fn, system: str,
                             messages: list[dict], **kwargs) -> str:
    """Try primary; on RateLimitError fall back to alt providers in order."""
    try:
        return await primary_call_fn(system, messages, **kwargs)
    except Exception as exc:
        if not _is_rate_limit_error(exc):
            raise
        try: record_rate_limit_hit("primary")
        except Exception: pass
        log.warning("RateLimitFallback: primary rate-limited (%s); trying alternates",
                    str(exc)[:120])

    for label, model, sdk, burl_env, akey_env in FALLBACK_CHAIN:
        try:
            # Support literal "https://..." URLs directly in the chain
            base_url = burl_env if burl_env.startswith("https://") else os.environ.get(burl_env, "")
            api_key  = os.environ.get(akey_env, "")
            if not (base_url and api_key):
                log.debug("RateLimitFallback: skip %s (missing env)", label)
                continue
            from openai import AsyncOpenAI
            c = AsyncOpenAI(api_key=api_key, base_url=base_url)
            is_reasoning = model.startswith(("gpt-5", "o4", "o3"))
            k: dict = {
                "model": model,
                "messages": [{"role": "system", "content": system}] + messages,
            }
            if is_reasoning:
                k["max_completion_tokens"] = 4000
            else:
                k["max_tokens"] = 4000
            r = await c.chat.completions.create(**k)
            txt = r.choices[0].message.content or ""
            log.info("RateLimitFallback: served by %s/%s (%d chars)", label, model, len(txt))
            try: record_fallback_use(label)
            except Exception: pass
            try:
                from argos_data_nexus.modules.llm_live_trace import record_llm_call
                record_llm_call(
                    channel="daedalus_fallback",
                    provider=label,
                    model=model,
                    model_requested="primary_rate_limited",
                    configured_model="(fallback)",
                    config_source="fallback_chain",
                    client_id="daedalus",
                    ok=True,
                    extra={"fallback_label": label},
                )
            except Exception:
                pass
            return f"[fallback:{label}] {txt}" if txt else txt
        except Exception as exc2:
            log.warning("RateLimitFallback: %s/%s failed: %s", label, model, exc2)
            if _is_rate_limit_error(exc2):
                try: record_rate_limit_hit(label)
                except Exception: pass
            continue

    raise RuntimeError("Rate-limited by primary and all fallbacks failed")


# ═══════════════════════════════════════════════════════════════════════════
# 4. ProposalEnforcer — auto-redirect direct INSERT into curated tables
# ═══════════════════════════════════════════════════════════════════════════

_CURATED_TABLES = {"trading_concepts", "signal_criteria", "trading_patterns",
                   "asset_behavior_patterns"}
_INSERT_TABLE_RE = re.compile(
    r"INSERT\s+INTO\s+(?:public\.)?(\w+)", re.IGNORECASE
)


def proposal_enforcement_check(pg_query: str) -> dict | None:
    """Return rejection-dict if pg_query is INSERT into curated tables.

    None = OK (let it through). dict = block with structured guidance.
    """
    m = _INSERT_TABLE_RE.search(pg_query or "")
    if not m:
        return None
    table = m.group(1).lower()
    if table not in _CURATED_TABLES:
        return None
    return {
        "ok": False,
        "blocked_by": "ProposalEnforcer",
        "error": (
            f"Direct INSERT INTO {table} is disallowed — this is a curated table. "
            f"Use NEED_DATA_PROPOSAL instead:\n"
            f"  <NEED_DATA_PROPOSAL: {table} | Batch Title | [{{...}}, {{...}}]>\n"
            f"Owner approves via outbox UI before the data goes live."
        ),
        "table": table,
        "redirect_marker": "NEED_DATA_PROPOSAL",
    }


# ═══════════════════════════════════════════════════════════════════════════
# 5. ErrorLessons — promote repeating cross-session tool errors → lessons
# ═══════════════════════════════════════════════════════════════════════════

# Persistent (Replit DB) counter for error fingerprints seen across sessions.
_LESSON_PROMOTION_THRESHOLD = 2

def record_tool_error_for_promotion(memory, tool: str, error: str,
                                     query: str = "") -> bool:
    """Track error in Replit DB; if seen in 2+ sessions → promote to lesson.

    Returns True if a new lesson was created this call.
    """
    try:
        from .kv import db
    except Exception:
        return False
    try:
        fp = ErrorTracker._fingerprint(tool, error)
        key = f"adn:tool_err:{fp}"
        prev = db.get(key, None)
        if prev is None:
            db[key] = {"tool": tool, "error_sample": error[:240],
                       "count": 1, "promoted": False,
                       "query_sample": query[:200]}
            return False
        prev["count"] = int(prev.get("count", 0)) + 1
        if prev["count"] >= _LESSON_PROMOTION_THRESHOLD and not prev.get("promoted"):
            try:
                lesson_text = (
                    f"При [{tool}] с pattern «{error[:160]}» — "
                    f"повтаряща се грешка (виждана {prev['count']}+ пъти). "
                    f"Примерен query: {query[:160]}. "
                    f"Избягвай тази форма; провери схема преди опит."
                )
                memory.capture_lesson(
                    lesson=lesson_text,
                    kind="tool_error_pattern",
                    confidence=0.85,
                )
                prev["promoted"] = True
                db[key] = prev
                log.info("ErrorLessons: promoted error pattern → lesson (fp=%s)", fp)
                return True
            except Exception as exc:
                log.debug("ErrorLessons: lesson capture failed: %s", exc)
        else:
            db[key] = prev
        return False
    except Exception as exc:
        log.debug("ErrorLessons: tracking failed: %s", exc)
        return False


# ═══════════════════════════════════════════════════════════════════════════
# 6. OutcomeScorer — record per-session success/failure to Replit DB
# ═══════════════════════════════════════════════════════════════════════════

def _outcome_key(session_id: str) -> str:
    return f"adn:outcome:{session_id}"

_OUTCOMES_INDEX = "adn:outcomes:index"


def record_outcome(session_id: str, *, user_message: str, reply: str,
                   elapsed: float, tool_calls: list, tool_iterations: int,
                   intent: str = "",
                   intent_without_action: bool = False) -> dict:
    """Score and persist session outcome. Heuristic-based, cheap, additive.

    Score: 'success' if non-empty reply + no breaker + <=normal iter,
           'partial' if reply ok but failures present,
           'fail' if empty reply or breaker engaged or >120s.

    `intent_without_action`: if True, the action-forcing detector caught
    Дедал announcing work without doing it → force min score = 'partial'.
    """
    try:
        from .kv import db as _rdb
        failed = sum(1 for c in (tool_calls or []) if c.get("ok") is False)
        total = len(tool_calls or [])
        reply_len = len((reply or "").strip())
        breaker = "BREAKER ENGAGED" in (reply or "")
        if not reply_len or breaker or elapsed > 120:
            score = "fail"
        elif failed > 0 or tool_iterations > 4:
            score = "partial"
        else:
            score = "success"
        # Action-forcing override: announcing without doing → at least partial
        if intent_without_action and score == "success":
            score = "partial"
        rec = {
            "session_id": session_id,
            "ts": int(time.time()),
            "score": score,
            "elapsed_s": round(elapsed, 2),
            "tool_calls": total,
            "tool_failures": failed,
            "tool_iterations": tool_iterations,
            "reply_len": reply_len,
            "intent": intent,
            "intent_without_action": bool(intent_without_action),
            "user_msg_head": (user_message or "")[:120],
            "breaker": breaker,
        }
        _rdb[_outcome_key(session_id)] = rec
        idx = list(_rdb.get(_OUTCOMES_INDEX) or [])
        idx = [session_id] + [s for s in idx if s != session_id]
        _rdb[_OUTCOMES_INDEX] = idx[:500]
        log.info("outcome[%s]: %s (%.1fs, %d tools, %d fail)",
                 session_id[:10], score, elapsed, total, failed)
        return rec
    except Exception as exc:
        log.debug("OutcomeScorer: record failed: %s", exc)
        return {"score": "unknown", "error": str(exc)}


def get_recent_outcomes(limit: int = 50) -> list[dict]:
    try:
        from .kv import db as _rdb
        idx = list(_rdb.get(_OUTCOMES_INDEX) or [])[:limit]
        out: list[dict] = []
        for sid in idx:
            r = _rdb.get(_outcome_key(sid))
            if r:
                out.append(dict(r))
        return out
    except Exception:
        return []


# ═══════════════════════════════════════════════════════════════════════════
# 7. QuestionBudget — cap clarifying questions per session (max 1)
# ═══════════════════════════════════════════════════════════════════════════

_QUESTION_COUNT: dict[str, int] = defaultdict(int)


def _trim_question_budget(max_sessions: int = 200) -> None:
    """LRU-style trim to prevent unbounded growth across long-lived processes."""
    if len(_QUESTION_COUNT) > max_sessions:
        # Drop oldest half (dict preserves insertion order in py3.7+)
        keys_to_drop = list(_QUESTION_COUNT.keys())[: len(_QUESTION_COUNT) - max_sessions // 2]
        for k in keys_to_drop:
            _QUESTION_COUNT.pop(k, None)


def question_budget_check_and_increment(session_id: str, reply: str,
                                        max_per_session: int = 1) -> tuple[bool, str]:
    """Return (allowed, advice). If reply ends with '?' and budget exhausted,
    return (False, advice_text) so agent.py can override the reply."""
    text = (reply or "").strip()
    if not text.endswith("?"):
        return (True, "")
    _QUESTION_COUNT[session_id] += 1
    _trim_question_budget()
    if _QUESTION_COUNT[session_id] > max_per_session:
        log.info("QuestionBudget[%s]: %d > %d — blocking further questions",
                 session_id[:10], _QUESTION_COUNT[session_id], max_per_session)
        return (False, (
            "Вече попитах веднъж за уточнение в тази сесия. Вместо да питам пак, "
            "ще предположа най-разумното и ще продължа — ако греша, кажи ми."
        ))
    return (True, "")


# ═══════════════════════════════════════════════════════════════════════════
# 8. ReadFirstEnforcer — inject reminder if DML attempted without schema read
# ═══════════════════════════════════════════════════════════════════════════

_RECENT_SCHEMA_READS: dict[str, float] = {}  # session_id -> last ts
# Per-session set of tables whose schema Дедал has explicitly read (NEED_DB_SCHEMA)
_SESSION_TABLES_READ: dict[str, set[str]] = defaultdict(set)


def mark_schema_read(session_id: str, tables: list[str] | None = None) -> None:
    _RECENT_SCHEMA_READS[session_id] = time.time()
    if tables:
        for t in tables:
            if t:
                _SESSION_TABLES_READ[session_id].add(t.lower().strip())


def session_has_read_table(session_id: str, table: str) -> bool:
    return (table or "").lower().strip() in _SESSION_TABLES_READ.get(session_id, set())


def needs_read_first_hint(session_id: str, reply: str,
                          stale_after_s: float = 120.0) -> str:
    """If reply contains INSERT/UPDATE/DELETE without recent NEED_DB_SCHEMA →
    return a hint string (otherwise empty)."""
    if not reply:
        return ""
    upper = reply.upper()
    has_dml = any(kw in upper for kw in ("INSERT INTO", "UPDATE ", "DELETE FROM", "ALTER TABLE"))
    if not has_dml:
        return ""
    last_read = _RECENT_SCHEMA_READS.get(session_id, 0.0)
    if time.time() - last_read < stale_after_s:
        return ""
    return (
        "\n⚠️ READ-FIRST: опитваш DML без скорошен NEED_DB_SCHEMA. "
        "Преди следващия INSERT/UPDATE, emit <NEED_DB_SCHEMA: <table>> и провери "
        "колоните. Това избягва 80% от SQL грешките."
    )


# ═══════════════════════════════════════════════════════════════════════════
# 9. SkillRegistry — load skill .md files and select relevant ones via Navigator
# ═══════════════════════════════════════════════════════════════════════════

_SKILLS_DIR = os.path.join(os.path.dirname(__file__), "skills")
_SKILLS_CACHE: dict[str, str] = {}
_SKILLS_LOADED = False


def _load_skills() -> None:
    global _SKILLS_LOADED
    if _SKILLS_LOADED:
        return
    try:
        if os.path.isdir(_SKILLS_DIR):
            for fn in os.listdir(_SKILLS_DIR):
                if fn.endswith(".md"):
                    path = os.path.join(_SKILLS_DIR, fn)
                    try:
                        with open(path, encoding="utf-8") as f:
                            _SKILLS_CACHE[fn[:-3]] = f.read()
                    except Exception:
                        pass
        log.info("SkillRegistry: loaded %d skills: %s",
                 len(_SKILLS_CACHE), list(_SKILLS_CACHE.keys()))
    except Exception as exc:
        log.debug("SkillRegistry: load failed: %s", exc)
    _SKILLS_LOADED = True


def select_skills(user_message: str, intent: str, hint: str = "") -> list[str]:
    """Return up to 3 skill names matching the request."""
    _load_skills()
    if not _SKILLS_CACHE:
        return []
    text = (user_message + " " + (hint or "") + " " + (intent or "")).lower()
    scored: list[tuple[int, str]] = []
    # Tightened keyword sets to reduce false-positive skill injection.
    # Require at least one specific (not generic) keyword to match.
    keywords = {
        "insert_concept": ["trading_concept", "trading_criteria", "fvg",
                           "order block", "volume profile", "smc концепт",
                           "добави концепт", "ingest concept", "signal_criteria"],
        "mesh_call": ["iyveris", "stockargos", "karthago", "argonaft",
                      "deutschesrechtsportal", "renorma", "mesh snapshot", "mesh call",
                      "извикай услуга", "извикай mesh"],
        "planning": ["имплементирай модул", "imeplement module", "phase 5",
                     "phase 4", "multi-step plan", "конкретен план",
                     "step-by-step", "роадмап", "roadmap"],
        "effort": ["[effort:", "усилие =", "шепот", "занаят", "епос"],
        "world_kit": ["world/bible", "world kit", "необят", "open world",
                      "кампания", "campaign bible", "world/map", "фракци"],
        "long_horizon": ["продължи", "journal resume", "long-horizon",
                         "need_progress", "across sessions"],
        "hard_jobs": ["spawn_subagent", "need_subagent", "трудна задача",
                      "много файлове", "съществуващия проект", "named artifact",
                      "acceptance criteria", "достави точно", "explorer then coder"],
        "narrative": ["сюжет", "персонаж", "диалог", "квест", "narrative",
                      "character want", "lore dump"],
        "campaign": ["levels/graph", "vertical slice", "актове", "различни нива",
                     "кампания", "act-1"],
        "save_flags": ["save system", "сейв система", "checkpoint",
                       "inventory flag", "world flags"],
        "dialogue": ["dialogue graph", "диалогов граф", "npc реплик",
                     "разклонен диалог"],
        "arcade_craft": ["игра", "game", "racing", "racer", "f-zero", "wipeout",
                         "three.js", "3d", "състезан", "arcade", "phaser",
                         "formula minus", "fm1"],
        "godot": ["godot", "gdscript", "metroidvania", ".tscn", "tscn", "puzzle room", "characterbody3d"],
        "godot_api": ["kinematicbody", "characterbody2d", "godot 4.3", ".import", "daedalus-bridge"],
        "blender_jobs": ["blender", "bpy", "gltf", "glb export", "armature"],
        "playtest": ["playtest", "тествай играта", "готови ли сме"],
        "platform_acceptance_qa": [
            "platform acceptance", "acceptance qa", "приемни тестове",
            "render_proof", "gameplay_proof", "device_smoke",
            "artifact hash", "release gate", "fail-closed", "fail closed",
            "ship gate", "platform matrix", "платформена qa",
        ],
        "gameplay_bot": ["scenario.json", "gameplay bot", "pixel-diff", "fps budget",
                         "play-check", "balance.json"],
        "asset_pipeline": ["sprite atlas", "tileset", "sfx kit", "bitmap font", "tripo",
                           "meshy", "hunyuan", "glb job", "asset-check", "greybox"],
        "skill_memory": ["learned skill", "одобри урока", "skill catalog",
                         "progressive disclosure", "skill memory", "уроци"],
        "production_layer": ["production-check", "prototype wall", "store-ready",
                             "системи", "продукционен слой", "systems.json",
                             "готово за стора"],
        "unity_runtime": ["unity-check", "unity mcp", "DAEDALUS_UNITY_MCP",
                          "unity hub", "batchmode", "не вендорираме unity",
                          "unity editor in the cloud"],
        "game_agent_bench": ["game agent bench", "honesty suite", "не bake-off",
                             "vendor bake-off", "game-agent-bench",
                             "честност suite"],
        "dissection": ["дисекция", "dissect", "мостра", "play lab",
                       "лаборатория", "разглоби играта", "specimen game"],
        "transform_math": ["quaternion", "slerp", "ray-triangle", "ray triangle",
                           "look-at", "look_at", "projection matrix", "math3d",
                           "dot product", "cross product", "кватернион"],
        "data_oriented_design": ["data-oriented", "data oriented", "cache miss",
                                 "cache line", "soa", "aos", "dod layout",
                                 "contiguous", "cpu cache"],
        "netcode": ["netcode", "client-side prediction", "lag compensation",
                    "server reconciliation", "lockstep", "rubber-band",
                    "hitscan", "multiplayer tick"],
        "game_canon": ["doom 3", "doom3", "mindustry", "tinyrenderer",
                       "game programming patterns", "stb_ds", "idlib",
                       "glm", "directxmath", "g-truc", "milo yip",
                       "game programmer", "awesome-game-engine", "chop chop",
                       "event channel", "game architecture"],
        "senior_tools": ["clang-tidy", "clang-format", "roslyn", "unity test framework",
                         "profiler log", "game_lint", "tracy", "renderdoc"],
        "spatial_simd": ["octree", "quadtree", "bvh", "spatial partition",
                         "broadphase", "simd", "o(n^2)", "uniform grid"],
        "capacitor_ship": ["capacitor", "ionic", "pakeplus", "hybrid mobile",
                           "webview wrap", "npx cap", "ios+android wrap",
                           "същата игра на телефон"],
        "karpathy": ["karpathy", "think before coding", "simplicity first",
                     "surgical change", "overcomplicat", "drive-by"],
    }
    for name in _SKILLS_CACHE:
        kws = keywords.get(name, [name.split("_")[0]])
        n = sum(1 for k in kws if k in text)
        if n > 0:
            scored.append((n, name))
    scored.sort(reverse=True)
    return [n for _, n in scored[:3]]


def render_skills_block(skill_names: list[str]) -> str:
    """Catalog + at most one body. Leftover callers stay progressive."""
    if not skill_names:
        return ""
    from .learned_skills import disclose
    return disclose("", extra_ids=skill_names)


# ═══════════════════════════════════════════════════════════════════════════
# 10. ReviewGate — quick Haiku critique of draft reply before final return
# ═══════════════════════════════════════════════════════════════════════════

async def review_gate_critique(user_message: str, draft_reply: str,
                               tool_calls: list, elapsed: float) -> dict:
    """Cheap Haiku self-review. Returns {ok: bool, issue: str, suggested_fix: str}.

    Triggered only on complex sessions (caller decides). Failure → ok=True (skip)."""
    try:
        from openai import AsyncOpenAI
        key = os.environ.get("OPENROUTER_API_KEY", "") or os.environ.get("NOVITA_API_KEY", "")
        url = "https://openrouter.ai/api/v1" if os.environ.get("OPENROUTER_API_KEY") else "https://api.novita.ai/v3/openai"
        if not key:
            return {"ok": True, "issue": "", "suggested_fix": "", "skipped": "no_creds"}
        tools_summary = "\n".join(
            f"  - {c.get('tool')} → {'OK' if c.get('ok', True) else 'FAIL'}"
            for c in (tool_calls or [])[-8:]
        ) or "  (no tools)"
        sys_prompt = (
            "Ти си строг рецензент. Анализирай дали draft отговорът на агент "
            "Дедал ИСТИНСКИ изпълнява заявката на owner-а. "
            "Отговори с JSON: {\"ok\": bool, \"issue\": \"...\", \"suggested_fix\": \"...\"}. "
            "ok=false САМО ако: (a) draft обещава нещо което не е направено, "
            "(b) draft съдържа фактическа грешка, или (c) draft не отговаря "
            "на същинския въпрос. Иначе ok=true. Кратко, на български."
        )
        user_prompt = (
            f"OWNER REQUEST:\n{user_message[:500]}\n\n"
            f"TOOLS USED ({elapsed:.1f}s):\n{tools_summary}\n\n"
            f"DRAFT REPLY:\n{(draft_reply or '')[:2000]}\n\n"
            "Качествен ли е draft-ът? Отговори САМО с JSON."
        )
        review_model = (
            "anthropic/claude-haiku-4-5"
            if os.environ.get("OPENROUTER_API_KEY")
            else "meta-llama/llama-3.1-8b-instruct"
        )
        client = AsyncOpenAI(api_key=key, base_url=url)
        resp = await client.chat.completions.create(
            model=review_model,
            max_tokens=400,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user",   "content": user_prompt},
            ],
        )
        raw = resp.choices[0].message.content or "{}"
        import json as _json
        # Strip possible code fences
        clean = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
        try:
            parsed = _json.loads(clean)
        except Exception:
            # Find first {...} block
            m = re.search(r"\{[\s\S]*\}", clean)
            parsed = _json.loads(m.group(0)) if m else {"ok": True}
        ok = bool(parsed.get("ok", True))
        return {
            "ok": ok,
            "issue": str(parsed.get("issue", ""))[:500],
            "suggested_fix": str(parsed.get("suggested_fix", ""))[:500],
        }
    except Exception as exc:
        log.debug("ReviewGate: critique failed (skip): %s", exc)
        return {"ok": True, "issue": "", "suggested_fix": "", "error": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════
# 11. ActionForcing — detect "intent without action" replies
# ═══════════════════════════════════════════════════════════════════════════

# Bulgarian + English future/intent phrases that announce work without doing it.
# Tightened: require concrete action verb + object/context, avoid generic
# "сега ще" / "първо ще" alone which appeared in many neutral explanations.
_INTENT_PATTERNS = [
    r"\bще\s+(?:извлека|пусна|тествам|емитнам|изпълня)\s+\w+",
    r"\bще\s+(?:проверя|добавя|направя|анализирам|потърся|опитам)\s+(?:в|на|за|с|от|към)\s+\w+",
    r"\bI\s+(?:will|am\s+going\s+to)\s+(?:check|fetch|run|analyze|create|insert|query)\s+\w+",
    r"\blet\s+me\s+(?:check|fetch|run|analyze|query)\s+(?:the|my|your)\b",
]
_INTENT_RE = re.compile("|".join(_INTENT_PATTERNS), re.IGNORECASE)
_MARKER_RE = re.compile(r"<NEED_[A-Z_]+", re.IGNORECASE)


def detect_intent_without_action(reply: str, tool_calls_used: list[dict]) -> dict:
    """Return {'detected': bool, 'phrase': str} when Дедал announced an action
    but emitted no marker and ran no tools.
    """
    if not reply:
        return {"detected": False, "phrase": ""}
    if tool_calls_used:
        return {"detected": False, "phrase": ""}
    if _MARKER_RE.search(reply):
        return {"detected": False, "phrase": ""}
    m = _INTENT_RE.search(reply)
    if not m:
        return {"detected": False, "phrase": ""}
    return {"detected": True, "phrase": m.group(0)[:80]}


# ═══════════════════════════════════════════════════════════════════════════
# 12. RateLimitTelemetry — rolling counters of 429 hits per provider
# ═══════════════════════════════════════════════════════════════════════════

# Each entry: (ts, label) where label is "primary" or fallback name.
_RATE_LIMIT_HITS: deque[tuple[float, str]] = deque(maxlen=2000)
_FALLBACK_USES: deque[tuple[float, str]] = deque(maxlen=2000)


def record_rate_limit_hit(label: str = "primary") -> None:
    _RATE_LIMIT_HITS.append((time.time(), label))


def record_fallback_use(label: str) -> None:
    _FALLBACK_USES.append((time.time(), label))


def get_rate_limit_stats() -> dict:
    """Return rolling counts of rate-limit hits and fallback usages."""
    now = time.time()
    windows = {"1m": 60, "5m": 300, "15m": 900, "60m": 3600}

    def _count(dq: deque, sec: float) -> int:
        cutoff = now - sec
        return sum(1 for ts, _ in dq if ts >= cutoff)

    def _breakdown(dq: deque, sec: float) -> dict:
        cutoff = now - sec
        out: dict[str, int] = defaultdict(int)
        for ts, label in dq:
            if ts >= cutoff:
                out[label] += 1
        return dict(out)

    return {
        "rate_limit_hits": {w: _count(_RATE_LIMIT_HITS, s) for w, s in windows.items()},
        "fallback_uses":   {w: _count(_FALLBACK_USES,   s) for w, s in windows.items()},
        "fallback_breakdown_60m": _breakdown(_FALLBACK_USES, 3600),
        "last_hit_ts": (_RATE_LIMIT_HITS[-1][0] if _RATE_LIMIT_HITS else None),
        "last_fallback_ts": (_FALLBACK_USES[-1][0] if _FALLBACK_USES else None),
        "total_buffered": {
            "rate_limit_hits": len(_RATE_LIMIT_HITS),
            "fallback_uses":   len(_FALLBACK_USES),
        },
        "pressure": _pressure_level(_count(_RATE_LIMIT_HITS, 60),
                                    _count(_RATE_LIMIT_HITS, 300)),
    }


def _pressure_level(hits_1m: int, hits_5m: int) -> str:
    """Heuristic warning level for UI."""
    if hits_1m >= 5 or hits_5m >= 15:
        return "critical"
    if hits_1m >= 2 or hits_5m >= 6:
        return "high"
    if hits_5m >= 1:
        return "warn"
    return "ok"


# ═══════════════════════════════════════════════════════════════════════════
# 13. PreExecuteReadFirst — block DML if session never read the table's schema
# ═══════════════════════════════════════════════════════════════════════════

# Match tables in FROM/JOIN/INTO/UPDATE/DELETE clauses. Supports schema-qualified
# names (schema.table), quoted identifiers ("Tbl"), and optional ONLY keyword.
_TABLE_REF_RE = re.compile(
    r"\b(?:FROM|JOIN|INTO|UPDATE|DELETE\s+FROM)\s+(?:ONLY\s+)?"
    r"(?:(?:[a-zA-Z_]\w*|\"[^\"]+\")\.)?"           # optional schema. prefix
    r"([a-zA-Z_]\w*|\"[^\"]+\")",                   # the table identifier
    re.IGNORECASE,
)
# Extract CTE aliases declared by `WITH name AS (...)` and `, name AS (...)`.
# We exclude these from missing_tables since they're not real DB tables.
_CTE_NAME_RE = re.compile(
    r"(?:WITH\s+(?:RECURSIVE\s+)?|,\s*)([a-zA-Z_]\w*)\s+AS\s*\(",
    re.IGNORECASE,
)

# Tables we never enforce read-first for (system / info schema / well-known).
_READ_FIRST_EXEMPT = {
    "information_schema", "pg_catalog", "pg_tables", "pg_class",
    "pg_attribute", "pg_indexes", "pg_stat_activity",
    "dual",  # oracle compat
    # ADN internal — Дедал знае схемата им наизуст
    "daedalus_proposals",
    "trading_concepts",
    "signal_criteria",
    "asset_behavior_patterns",
}


def _extract_cte_names(query: str) -> set[str]:
    """Return lowercased set of CTE aliases declared via WITH/AS()."""
    return {m.group(1).lower() for m in _CTE_NAME_RE.finditer(query or "")}


def pre_execute_read_first_check(session_id: str, query: str) -> dict | None:
    """Return rejection-dict if the query touches a table the session hasn't
    explicitly read schema for (via NEED_DB_SCHEMA). None = OK to proceed.

    Applies to SELECT, JOIN, INSERT, UPDATE, DELETE — anywhere a column-level
    mistake would surface as 'column does not exist'. CTE aliases declared in
    `WITH x AS (...)` are correctly excluded from the missing-tables list.
    """
    if not session_id or not query:
        return None
    matches = _TABLE_REF_RE.findall(query)
    if not matches:
        return None
    cte_aliases = _extract_cte_names(query)
    missing = []
    seen = set()
    for t in matches:
        tl = t.strip('"').lower()
        if tl in seen or tl in _READ_FIRST_EXEMPT or tl in cte_aliases:
            continue
        seen.add(tl)
        if not session_has_read_table(session_id, tl):
            missing.append(tl)
    if not missing:
        return None
    table = missing[0]
    schema_marker = ", ".join(f"<NEED_DB_SCHEMA: {t}>" for t in missing[:3])
    return {
        "ok": False,
        "blocked_by": "PreExecuteReadFirst",
        "error": (
            f"READ-FIRST блокаж: питаш за таблица(и) '{', '.join(missing)}' "
            f"без преди това да си прочел схемата им в тази сесия.\n"
            f"Добави в СЪЩИЯ отговор (или следващия) схема-маркер:\n  {schema_marker}\n"
            f"Можеш да комбинираш в един reply:\n"
            f"  {schema_marker}\n"
            f"  <NEED_PG: SELECT ... FROM {table} ...>\n"
            f"Системата обработва NEED_DB_SCHEMA ПРЕДИ NEED_PG — няма нужда от отделна итерация."
        ),
        "table": table,
        "missing_tables": missing,
    }
