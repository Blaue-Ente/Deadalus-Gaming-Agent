"""Navigator symbolic vocabulary — Phase 1.

Defines the compact shorthand language spoken between Navigator (Haiku) and
Main (Sonnet). Human-readable, debuggable, token-efficient.

Format on the wire:
    [INTENT:db_query] [CTX:proposals,auth] [MEM:29,31] [TOOLS:NEED_DB_SCHEMA,NEED_PG]
    [BUDGET:short] [HINT:user means approved proposals; use status='applied']

All tags are optional. Parser is lenient — unknown tags are silently ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Vocabulary definitions
# ---------------------------------------------------------------------------

INTENTS = {
    "db_query":      "Read or query the PostgreSQL database",
    "code_change":   "Propose, edit, or explain a code change",
    "knowledge":     "General knowledge, explanation, or Iyveris system question",
    "plan_realize":  "Realize or execute a plan via NEED_PROPOSE",
    "debug":         "Debug, diagnose, or investigate an error or anomaly",
    "search":        "Search code, docs, or the web",
    "admin":         "Admin panel navigation, settings, or configuration",
    "mesh":          "Inter-service mesh call or Iyveris network operation",
    "chat":          "General chat, greeting, small-talk, opinion questions, discussions, meta-questions (no tool needed)",
}

CONTEXTS = {
    "proposals":    "daedalus code proposals (self_improve, daedalus_proposals table)",
    "plans":        "daedalus plans (daedalus_plans table, planner.py)",
    "auth":         "authentication, sessions, login, API keys",
    "mesh":         "Iyveris mesh, inter-service calls, health monitor",
    "memory":       "ChromaDB memory, lessons, episodes, discoveries",
    "code_index":   "semantic code index, code_index.py, symbol search",
    "admin":        "admin panel, admin.py, admin templates",
    "knowledge":    "Iyveris knowledge docs, global ChromaDB collection",
    "schema":       "PostgreSQL schema, tables, migrations, db.py",
    "tools":        "Daedalus tools, markers, NEED_* system",
    "navigator":    "Navigator, Haiku, briefing system",
    "stock":        "StockArgos, stock data, tickers, financial data",
    "indicators":   "technical indicators, TA, knowledge endpoint",
}

BUDGETS = {
    "short":  "Reply in 2-4 sentences. No code blocks unless essential.",
    "normal": "Normal Daedalus response length.",
    "deep":   "Thorough: full code, detailed explanation, step-by-step.",
}

TOOLS_AVAILABLE = [
    "NEED_ENDPOINTS", "NEED_MODULES", "NEED_DB_SCHEMA", "NEED_CODE_SEARCH",
    "NEED_PG", "NEED_PROPOSE", "NEED_WEB", "NEED_GITHUB",
    "NEED_READ_FILE", "NEED_LOGS",
]

# ---------------------------------------------------------------------------
# Briefing dataclass
# ---------------------------------------------------------------------------

@dataclass
class Briefing:
    """Structured briefing from Navigator to Main."""
    intent: str = "chat"                  # one of INTENTS keys
    contexts: list[str] = field(default_factory=list)   # subset of CONTEXTS keys
    mem_refs: list[str] = field(default_factory=list)   # lesson/episode IDs or indices
    tools: list[str] = field(default_factory=list)      # NEED_* markers to prepare
    budget: str = "normal"                # one of BUDGETS keys
    hint: str = ""                        # free-text hint ≤ 300 chars
    fast_path: bool = False               # True = Navigator used rules, not Haiku
    raw: str = ""                         # raw Navigator output for debug

    def to_prompt_block(self) -> str:
        """Format briefing as a compact block to prepend to Sonnet's context."""
        lines = ["━━━ NAVIGATOR BRIEFING ━━━"]
        lines.append(f"INTENT : {self.intent}  ({INTENTS.get(self.intent, '?')})")
        if self.contexts:
            lines.append(f"CONTEXT: {', '.join(self.contexts)}")
        if self.tools:
            lines.append(f"TOOLS  : focus on {', '.join(self.tools)}")
        if self.budget != "normal":
            lines.append(f"BUDGET : {self.budget}  — {BUDGETS.get(self.budget, '')}")
        if self.hint:
            lines.append(f"HINT   : {self.hint}")
        if self.mem_refs:
            lines.append(f"MEM    : {', '.join(self.mem_refs)}")
        if self.fast_path:
            lines.append("(fast-path: rule-based, no Haiku call)")
        lines.append("━━━ END BRIEFING ━━━")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "contexts": self.contexts,
            "mem_refs": self.mem_refs,
            "tools": self.tools,
            "budget": self.budget,
            "hint": self.hint,
            "fast_path": self.fast_path,
        }


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"\[([A-Z_]+):([^\]]{0,400})\]")


def parse_briefing(text: str) -> Briefing:
    """Parse a Navigator output string into a Briefing.

    Lenient: unknown tags are ignored, missing tags get defaults.
    Works even if Haiku wraps tags in prose.

    Examples
    --------
    >>> b = parse_briefing("[INTENT:db_query] [CTX:proposals] [HINT:use status='applied']")
    >>> b.intent
    'db_query'
    >>> b.contexts
    ['proposals']
    """
    b = Briefing()
    b.raw = text
    for tag, value in _TAG_RE.findall(text):
        value = value.strip()
        if tag == "INTENT":
            v = value.lower().replace("-", "_")
            if v in INTENTS:
                b.intent = v
        elif tag in ("CTX", "CONTEXT"):
            b.contexts = [c.strip().lower() for c in value.split(",") if c.strip()]
        elif tag == "MEM":
            b.mem_refs = [r.strip() for r in value.split(",") if r.strip()]
        elif tag == "TOOLS":
            b.tools = [t.strip().upper() for t in value.split(",") if t.strip()]
        elif tag == "BUDGET":
            v = value.lower()
            if v in BUDGETS:
                b.budget = v
        elif tag == "HINT":
            b.hint = value[:300]
    return b


def format_briefing(b: Briefing) -> str:
    """Serialize a Briefing back to the wire format (for logging / display)."""
    parts = [f"[INTENT:{b.intent}]"]
    if b.contexts:
        parts.append(f"[CTX:{','.join(b.contexts)}]")
    if b.mem_refs:
        parts.append(f"[MEM:{','.join(b.mem_refs)}]")
    if b.tools:
        parts.append(f"[TOOLS:{','.join(b.tools)}]")
    if b.budget != "normal":
        parts.append(f"[BUDGET:{b.budget}]")
    if b.hint:
        parts.append(f"[HINT:{b.hint}]")
    return " ".join(parts)
