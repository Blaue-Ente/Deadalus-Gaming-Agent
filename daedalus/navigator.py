"""Daedalus Navigator — Phase 2 + 5.

A small Claude Haiku model that acts as Daedalus's "prefrontal cortex":
- Receives the user message + already-retrieved memory context
- Decides intent, focus areas, and which tools matter
- Returns a compact Briefing (≤ 500 tokens) for the main Sonnet call

Two paths:
  • FAST PATH (rules)  — for trivial messages (greetings, short queries,
    obvious intents). No LLM call, ~5ms, $0.
  • SMART PATH (Haiku) — for everything else. ~150-400ms, ~$0.001.

Toggle with env var DAEDALUS_NAVIGATOR_ENABLED (default: true).

The Navigator NEVER calls mutating tools (no NEED_PG writes, no NEED_PROPOSE,
no NEED_MESH). It only reads. The main Sonnet model is the one that acts.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Any

from .navigator_vocab import (
    Briefing,
    INTENTS,
    CONTEXTS,
    BUDGETS,
    TOOLS_AVAILABLE,
    parse_briefing,
    format_briefing,
)

log = logging.getLogger(__name__)

# Cheap wrap-up / navigator model is resolved in cheap_llm.resolve_cheap_openai
# (NVIDIA lightning → OpenRouter :free → Novita DeepSeek). Never Claude.
_HAIKU_MAX_TOKENS = 400
_HAIKU_TIMEOUT_S = 5.0

# Estimated Sonnet tokens saved per Navigator call.
# Raw memory dump ≈ 2 000 tok → briefing ≈ 150 tok → saving ≈ 1 850.
# Haiku path also costs ~350 tok on its own, so net saving ≈ 1 500.
_TOKENS_SAVED_FAST_PATH = 1_850
_TOKENS_SAVED_HAIKU_PATH = 1_500


# ---------------------------------------------------------------------------
# In-memory telemetry counters
# ---------------------------------------------------------------------------

_stats: dict[str, int | float] = {
    "total_calls": 0,
    "fast_path_calls": 0,
    "haiku_calls": 0,
    "haiku_errors": 0,
    "estimated_tokens_saved": 0,
    "total_latency_ms": 0.0,
}


def get_navigator_stats() -> dict[str, Any]:
    """Return a snapshot of the current in-memory telemetry counters."""
    s = dict(_stats)
    total = s["total_calls"]
    s["fast_path_pct"] = round(s["fast_path_calls"] / total * 100, 1) if total else 0.0
    s["haiku_pct"]     = round(s["haiku_calls"]     / total * 100, 1) if total else 0.0
    s["avg_latency_ms"] = round(s["total_latency_ms"] / total, 1) if total else 0.0
    s["enabled"] = is_enabled()
    return s


def reset_navigator_stats() -> None:
    """Reset counters to zero (useful for testing / manual resets)."""
    for k in _stats:
        _stats[k] = 0


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------

def is_enabled() -> bool:
    """Check if Navigator is active (default: True)."""
    val = os.environ.get("DAEDALUS_NAVIGATOR_ENABLED", "true").strip().lower()
    return val not in ("0", "false", "no", "off")


# ---------------------------------------------------------------------------
# FAST PATH (Phase 5) — rule-based, no LLM call
# ---------------------------------------------------------------------------

_GREETING_PATTERNS = [
    r"^\s*(hi|hello|hey|здравей|здрасти|добро\s+утро|добър\s+ден|добър\s+вечер|поздрави|здр)\s*[!.?]*\s*$",
    r"^\s*(благодаря|мерси|thanks?|thank\s+you|gracias)\s*[!.?]*\s*$",
    r"^\s*(чао|bye|довиждане|goodbye)\s*[!.?]*\s*$",
    r"^\s*(yes|no|да|не|ok|окей|добре)\s*[!.?]*\s*$",
]
_GREETING_RES = [re.compile(p, re.IGNORECASE) for p in _GREETING_PATTERNS]

# Discussion/question patterns — no tools needed, just a thoughtful reply
_DISCUSSION_PATTERNS = [
    r"защо\s+(е|не|се|го|ти|трябва|правиш|така|го\s+прави)",
    r"защо\s+е\s+така",
    r"мислиш\s+ли",
    r"какво\s+мислиш",
    r"какво\s+смяташ",
    r"обясни\s+ми",
    r"как\s+работи",
    r"не\s+ми\s+харесва",
    r"не\s+(ми\s+)?харесва",
    r"интересно\s+е",
    r"има\s+ли\s+(начин|практики|смисъл)",
    r"общоприети\s+практики",
    r"как\s+се\s+настройва",
    r"как\s+се\s+освобождава",
    r"what\s+do\s+you\s+think",
    r"why\s+(is|are|does|not|doesn)",
    r"can\s+you\s+explain",
    r"don'?t\s+(you\s+)?think",
    r"какво\s+е\s+(разликата|смисълът|целта)",
    r"имаш\s+ли\s+мнение",
    r"дай\s+ми\s+мнение",
    r"поговорим",
    r"нормална\s+дискусия",
    r"свободно\s+(да\s+)?(говориш|комуникираш)",
]
_DISCUSSION_RES = [re.compile(p, re.IGNORECASE) for p in _DISCUSSION_PATTERNS]

# Strong intent signals — if any matches, we skip Haiku
_INTENT_SIGNALS: list[tuple[str, list[str]]] = [
    ("db_query", [
        "покажи", "колко", "show me", "list", "count", "how many",
        "proposals", "plans", "lessons", "discoveries", "history",
        "одобрени", "приети", "отхвърлени",
    ]),
    ("plan_realize", [
        "реализирай плана", "приложи плана", "изпълни плана",
        "realize plan", "apply plan", "execute plan",
    ]),
    ("code_change", [
        "промени", "поправи", "напиши", "добави към", "редактирай",
        "направи", "създай", "playground", "игра",
        "change", "fix", "implement", "refactor", "edit", "create",
    ]),
    ("debug", [
        "грешка", "не работи", "счупено", "error", "broken", "doesn't work",
        "failing", "exception", "traceback",
    ]),
    ("search", [
        "намери", "потърси", "къде е", "find", "search", "where is",
    ]),
]


def _fast_path(user_msg: str) -> Briefing | None:
    """Try to build a Briefing without calling Haiku. Returns None if unsure."""
    msg = (user_msg or "").strip()
    if not msg:
        return None

    # Trivially short — greeting / acknowledgement
    if len(msg) < 60:
        for r in _GREETING_RES:
            if r.match(msg):
                return Briefing(
                    intent="chat",
                    budget="short",
                    hint="Greeting or simple acknowledgement — reply briefly, no tools.",
                    fast_path=True,
                )

    # Discussion / opinion / meta-question — no tools needed, just a thoughtful answer.
    # These patterns signal "the user wants to TALK, not get work done."
    for r in _DISCUSSION_RES:
        if r.search(msg):
            return Briefing(
                intent="chat",
                budget="normal",
                hint="Discussion or opinion question — answer thoughtfully in plain text, no NEED_* markers.",
                fast_path=True,
            )

    # Strong keyword-based intent classification — require ≥2 signals for confidence
    msg_lower = msg.lower()
    scores: dict[str, int] = {}
    for intent, keywords in _INTENT_SIGNALS:
        n = sum(1 for kw in keywords if kw in msg_lower)
        if n:
            scores[intent] = n

    if not scores:
        return None  # let Haiku decide

    # Pick highest-scoring intent
    top_intent = max(scores, key=lambda k: scores[k])
    top_score = scores[top_intent]
    if top_score < 1:
        return None

    # Heuristic context detection
    ctxs: list[str] = []
    for ctx_key in CONTEXTS:
        if ctx_key in msg_lower:
            ctxs.append(ctx_key)

    # Map intent → likely tools
    tool_map = {
        "db_query":     ["NEED_DB_SCHEMA", "NEED_PG"],
        "plan_realize": ["NEED_CODE_SEARCH", "NEED_PROPOSE"],
        "code_change":  ["NEED_CODE_SEARCH", "NEED_READ_FILE", "NEED_PROPOSE"],
        "debug":        ["NEED_LOGS", "NEED_CODE_SEARCH"],
        "search":       ["NEED_CODE_SEARCH", "NEED_ENDPOINTS"],
    }
    tools = tool_map.get(top_intent, [])
    try:
        from .game_engineer import looks_like_game
        if looks_like_game(msg) and top_intent == "code_change":
            tools = []
    except Exception:
        pass

    # Only use fast-path if signal is moderately strong (≥2) OR query is very short
    if top_score >= 2 or len(msg) < 80:
        hint = f"Fast-path classification (score={top_score})."
        if not tools and top_intent == "code_change":
            hint = "Playground/game build — kernel native tools, not NEED_PROPOSE."
        return Briefing(
            intent=top_intent,
            contexts=ctxs,
            tools=tools,
            budget="normal",
            hint=hint,
            fast_path=True,
        )
    return None


_ACTION_RE = re.compile(
    r"(направи|създай|напиши|поправи|playground|игра|"
    r"create|build|implement|fix|write)\b",
    re.I,
)


def _unavailable_briefing(user_msg: str) -> Briefing:
    """When cheap Navigator LLM is down, do not classify build work as chat.

    intent=chat skips native tools (math3d / spatial / game_canon) in agent.py.
    """
    msg = user_msg or ""
    if _ACTION_RE.search(msg) or len(msg) > 120:
        return Briefing(
            intent="code_change",
            tools=["NEED_READ_FILE"],
            budget="normal",
            hint="(navigator unavailable — treat as build, keep tools)",
            fast_path=True,
        )
    return Briefing(
        intent="chat",
        budget="normal",
        hint="(navigator unavailable — Sonnet acting alone with full context)",
        fast_path=True,
    )


# ---------------------------------------------------------------------------
# Haiku Navigator system prompt
# ---------------------------------------------------------------------------

_NAVIGATOR_SYSTEM = """You are the NAVIGATOR for Daedalus, the autonomous AI agent inside ArgosDataNexus (ADN).

YOUR JOB: read the owner's message + retrieved memory snippets, then output a compact briefing that tells the main Daedalus (a larger Sonnet model) what to focus on. You do NOT answer the owner directly. You orient.

OUTPUT FORMAT — exactly these tags on one line each:

[INTENT:<one of: db_query, code_change, knowledge, plan_realize, debug, search, admin, mesh, chat>]
[CTX:<comma-separated area names from: proposals, plans, auth, mesh, memory, code_index, admin, knowledge, schema, tools, navigator, stock, indicators>]
[TOOLS:<comma-separated NEED_* markers Sonnet should prepare to use>]
[BUDGET:<short | normal | deep>]
[HINT:<≤200 chars: the key insight Sonnet needs — e.g. "user means approved proposals; use status='applied' filter">]

Available NEED_* markers Sonnet can use:
  NEED_ENDPOINTS, NEED_MODULES, NEED_DB_SCHEMA, NEED_CODE_SEARCH (read-only)
  NEED_PG (SQL), NEED_PROPOSE (write code)
  NEED_WEB, NEED_GITHUB, NEED_READ_FILE, NEED_LOGS

RULES:
- Be concise. 5 tags max, each on its own line.
- HINT is the most valuable part — distill the user's real intent in plain Bulgarian or English.
- If the user request is ambiguous, set BUDGET:normal and put your best guess in HINT.

INTENT SELECTION — critical:
- For greetings, acks, one-word replies: [INTENT:chat] [BUDGET:short] [HINT:greet briefly]
- For "why", "how does X work", opinion questions ("мислиш ли", "какво смяташ", "не ми харесва"),
  meta-questions about Daedalus behavior, philosophical/reflective discussion, brainstorming WITHOUT
  a concrete task — use [INTENT:chat] [BUDGET:normal] [TOOLS:] [HINT:answer conversationally, no markers]
- RULE: if the message does NOT require reading/writing DB, code, or files → it is [INTENT:chat]
- "knowledge" intent means the user asked for Iyveris-SYSTEM-specific info that requires lookup.
  Pure questions ("защо", "как работи като концепция", "мислиш ли") do NOT need lookup → chat.
- Only use TOOLS when you are confident a specific tool result is needed to answer the question.
- Never include explanation outside the tags. Tags only.
"""


def _build_navigator_user_msg(
    user_msg: str,
    lessons: list[dict[str, Any]],
    episodes: list[dict[str, Any]],
    session_recent: list[dict[str, Any]],
    code_hits: list[dict[str, Any]] | None = None,
) -> str:
    """Compose the prompt body for Haiku."""
    parts = [f"OWNER MESSAGE:\n{user_msg.strip()}\n"]

    if session_recent:
        parts.append("\nRECENT CHAT TURNS:")
        for t in session_recent[-4:]:
            meta = t.get("metadata", {})
            role = meta.get("role", "?")
            content = (t.get("content", "") or "")[:200]
            parts.append(f"  [{role}] {content}")

    if lessons:
        parts.append("\nRELEVANT LESSONS (Daedalus has learned):")
        for L in lessons[:5]:
            content = (L.get("content", "") or "")[:160]
            parts.append(f"  • {content}")

    if episodes:
        parts.append("\nRECENT EPISODIC MEMORIES:")
        for ep in episodes[:2]:
            content = (ep.get("content", "") or "")[:300]
            parts.append(f"  • {content}")

    if code_hits:
        parts.append("\nCODE INDEX SEMANTIC HITS (auto-fetched):")
        for h in code_hits[:5]:
            parts.append(
                f"  • {h.get('qualified_name','?')} "
                f"({h.get('symbol_type','?')}) @ {h.get('filepath','?')}:{h.get('lineno','?')}"
            )

    parts.append("\nOutput your briefing tags now.")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def navigate(
    user_msg: str,
    *,
    lessons: list[dict[str, Any]] | None = None,
    episodes: list[dict[str, Any]] | None = None,
    session_recent: list[dict[str, Any]] | None = None,
) -> Briefing:
    """Main entry point — returns a Briefing for the given user message.

    Pulls relevant code-index hits internally (semantic search) so the
    briefing reflects what symbols actually exist in the codebase.

    Will never raise — always returns a Briefing (possibly minimal).
    """
    t0 = time.monotonic()
    _stats["total_calls"] += 1

    if not is_enabled():
        _stats["fast_path_calls"] += 1
        return Briefing(intent="chat", budget="normal", hint="(navigator disabled)", fast_path=True)

    # Try fast path first
    fp = _fast_path(user_msg)
    if fp is not None:
        elapsed = (time.monotonic() - t0) * 1000
        _stats["fast_path_calls"] += 1
        _stats["estimated_tokens_saved"] += _TOKENS_SAVED_FAST_PATH
        _stats["total_latency_ms"] += elapsed
        log.debug("navigator fast-path: %s (%.0fms)", format_briefing(fp), elapsed)
        return fp

    # Pre-fetch code index hits (cheap — already-built ChromaDB query)
    code_hits: list[dict[str, Any]] = []
    try:
        from .code_index import semantic_search, get_stats
        if get_stats().get("symbols", 0) > 0:
            loop = asyncio.get_event_loop()
            code_hits = await loop.run_in_executor(
                None, lambda: semantic_search(user_msg, n_results=5)
            )
    except Exception as exc:
        log.debug("navigator code_index lookup failed: %s", exc)

    # Build Haiku prompt
    user_content = _build_navigator_user_msg(
        user_msg,
        lessons or [],
        episodes or [],
        session_recent or [],
        code_hits,
    )

    # Call Haiku
    raw = ""
    haiku_ok = False
    try:
        raw = await asyncio.wait_for(
            _call_haiku(_NAVIGATOR_SYSTEM, user_content),
            timeout=_HAIKU_TIMEOUT_S,
        )
        haiku_ok = bool(raw.strip())
    except asyncio.TimeoutError:
        log.warning("navigator Haiku timeout after %.1fs — falling back to minimal briefing", _HAIKU_TIMEOUT_S)
        _stats["haiku_errors"] += 1
    except Exception as exc:
        log.warning("navigator Haiku call failed: %s — falling back", exc)
        _stats["haiku_errors"] += 1

    elapsed = (time.monotonic() - t0) * 1000
    _stats["total_latency_ms"] += elapsed

    if not haiku_ok:
        # Minimal fallback briefing — count as fast-path (no Haiku saving)
        _stats["fast_path_calls"] += 1
        return _unavailable_briefing(user_msg)

    _stats["haiku_calls"] += 1
    _stats["estimated_tokens_saved"] += _TOKENS_SAVED_HAIKU_PATH
    briefing = parse_briefing(raw)
    log.info(
        "navigator: %s  (%.0fms, %d code_hits)",
        format_briefing(briefing),
        elapsed,
        len(code_hits),
    )
    return briefing


# ---------------------------------------------------------------------------
# T10 — Pre-flight feasibility check
# ---------------------------------------------------------------------------

# Red-flag patterns that signal common feasibility problems before Sonnet acts.
# Each tuple = (regex pattern, advice to append to briefing.hint).
_FEASIBILITY_RULES: list[tuple[str, str]] = [
    # Write SQL without permission check first
    (r"(?i)\b(вмъкни|вкарай|добави в.*таблица|delete from|drop table|truncate|insert into|update\s+\w+\s+set)\b",
     "PREFLIGHT: тази задача иска write SQL — започни с <NEED_CHECK_PERM: insert_<category>> "
     "ПРЕДИ да емитираш NEED_PG; ако DENIED, използвай NEED_DATA_PROPOSAL."),
    # References to specific non-trivial table that might not exist
    (r"(?i)\b(backtest|strategy_run|positions|trades)\b",
     "PREFLIGHT: преди да query-ваш тази таблица, провери с <NEED_DB_SCHEMA: *> "
     "че реално съществува — част от Strategy Lab плана не е още имплементиран."),
    # Massive batch insert request
    (r"(?i)\b(всички|цялата|пълна|целия|10\+|20\+|50\+|всичко)\b.*\b(концепт|criteria|patterns|таблица|knowledge|theory|book|книга)\b",
     "PREFLIGHT: голяма batch задача — обмисли <NEED_PROJECT_PLAN: ...> вместо да я "
     "побереш в един tool loop (20 итерации лимит)."),
]


# Code-change verbs (BG/EN) — preflight only fires when no real path token exists in the message
_CODE_VERB_RE = re.compile(
    r"(?i)\b(промени|поправи|редактирай|рефакторирай|fix|refactor|edit|modify|change)\b"
)
# Real file path token: any token containing `/` + extension, or bare filename with code extension
_PATH_TOKEN_RE = re.compile(
    r"(?:[\w./-]+/[\w.-]+\.\w{1,5}|[\w-]+\.(?:py|html|ts|tsx|js|jsx|css|md|yaml|yml|json|toml|sql))"
)


def preflight_feasibility(user_msg: str, briefing: Briefing) -> str:
    """Return up to 2 prioritised feasibility hints for the upcoming Sonnet call.

    Empty string means "no red flags detected — proceed". Hints are short
    (≤200 chars each) and appended to briefing.hint via the agent wiring.
    """
    if not user_msg or len(user_msg) < 8:
        return ""
    hits: list[str] = []
    for pattern, advice in _FEASIBILITY_RULES:
        try:
            if re.search(pattern, user_msg):
                hits.append(advice)
                if len(hits) >= 2:
                    break
        except re.error:
            continue
    # Code-change rule (tokenized — anywhere in message, not regex-anchored)
    if len(hits) < 2 and _CODE_VERB_RE.search(user_msg) and not _PATH_TOKEN_RE.search(user_msg):
        hits.append(
            "PREFLIGHT: code_change без посочен файл — започни с "
            "<NEED_CODE_SEARCH: ...> за да намериш реалния файл, не предполагай."
        )
    if not hits:
        return ""
    return " · ".join(hits)


async def _call_haiku(system: str, user_content: str) -> str:
    """Cheap LLM for navigator hints — via LLM Router (cheap intent) when enabled."""
    try:
        from . import daedalus_mesh as dm
        if dm.use_mesh_router():
            res = await dm.complete(
                system=system,
                user=user_content,
                intent="cheap",
                max_tokens=_HAIKU_MAX_TOKENS,
                client_id=dm.client_id("navigator"),
                use_cache=True,
            )
            text = dm.extract_text(res)
            if text:
                log.debug(
                    "navigator mesh_router: %d chars, $%.6f, %s",
                    len(text), float(res.get("cost_usd") or 0.0), res.get("provider") or "?",
                )
                return text
    except Exception as exc:
        log.debug("navigator mesh_router failed (%s) — fallback to direct SDK", exc)

    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _call_haiku_sync, system, user_content)


def _call_haiku_sync(system: str, user_content: str) -> str:
    """Direct SDK fallback when mesh_router is disabled or unavailable."""
    from .harness import gateway
    if gateway.path_enabled("navigator"):
        route = gateway.cheap_route()
        if not route:
            return ""
        res = gateway.call_sync(
            "navigator", [{"role": "user", "content": user_content}], system=system,
            max_tokens=_HAIKU_MAX_TOKENS, route=route, client_id="daedalus/navigator",
        )
        if not res.ok:
            log.debug("navigator cheap-llm call failed (%s)", res.error.type if res.error else "?")
        return res.text if res.ok else ""
    try:
        from openai import OpenAI
        from .cheap_llm import resolve_cheap_openai
        cheap = resolve_cheap_openai()
        if not cheap:
            return ""
        client = OpenAI(api_key=cheap["api_key"], base_url=cheap["base_url"])
        resp = client.chat.completions.create(
            model=cheap["model"],
            max_tokens=_HAIKU_MAX_TOKENS,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
        )
        return resp.choices[0].message.content or ""
    except Exception as exc:
        log.debug("navigator cheap-llm call failed (%s)", exc)
        return ""
