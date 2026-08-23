"""
Централизиран parser за Daedalus NEED_* tool маркери.

Подобрява разпръснатите if-блокове в agent.py:
- Всички маркери дефинирани на едно място (PATTERNS / INLINE_MARKERS)
- extract_all() сортира по позиция → маркерите се изпълняват в реда
  на появяване в текста, не в хардкоднат if-ред
- Graceful degradation за невалиден JSON (NEED_MESH)
- Multiline поддръжка за блокови маркери (NEED_SANDBOX, NEED_PROPOSE)
- Специализирани parse helpers за всеки сложен маркер

Предложен от: Дедал (agent self-improvement research task, 2026-05-19)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# ToolCall dataclass
# ---------------------------------------------------------------------------

@dataclass
class ToolCall:
    name: str    # "NEED_PG", "NEED_READ_FILE", "NEED_MESH" …
    payload: str # основен payload (group(1) на match-а)
    raw: str     # пълен match string (<NEED_FOO: ...>)
    start: int   # позиция в оригиналния текст
    end: int     # край на match-а в оригиналния текст


# ---------------------------------------------------------------------------
# ToolCallParser
# ---------------------------------------------------------------------------

class ToolCallParser:
    """Извлича и сортира всички NEED_* маркери по позиция в LLM отговора."""

    # Прости inline маркери: <NEED_FOO: payload>
    # Добавянето на нов маркер = добавяне на един string тук.
    # Block markers scanned separately (not inline)
    BLOCK_MARKERS: list[str] = [
        "NEED_BATCH",
        "NEED_VERIFY",
        "NEED_EDIT",
    ]

    INLINE_MARKERS: list[str] = [
        "NEED_PG",
        "NEED_WEB",
        "NEED_GITHUB",
        "NEED_GH_REPO",
        "NEED_GH_ISSUE",
        "NEED_SKILL",
        "NEED_DOCUMENT",
        "NEED_BACKUP",
        "NEED_PYPI",
        "NEED_NPM",
        "NEED_DOCS",
        "NEED_DEP",
        "NEED_GIT_LOG",
        "NEED_GIT_DIFF",
        "NEED_GITHUB_SEARCH",
        "NEED_ENDPOINTS",
        "NEED_MODULES",
        "NEED_DB_SCHEMA",
        "NEED_CODE_SEARCH",
        "NEED_CAPABILITY",
    ]

    # Специализирани regex за маркери с по-сложна структура
    SPECIAL_PATTERNS: dict[str, re.Pattern[str]] = {
        "NEED_READ_FILE": re.compile(
            r"<NEED_READ_FILE:\s*([^>|]{1,300})(?:\|\s*(\d+)-(\d+))?\s*>",
            re.DOTALL,
        ),
        "NEED_LOGS": re.compile(
            r"<NEED_LOGS:\s*(\d+)(?:\s*\|\s*([^>]{1,200}))?\s*>",
            re.DOTALL,
        ),
        "NEED_MESH": re.compile(
            r"<NEED_MESH:\s*([\w-]+)\s*:\s*([\w.]+)\s*:\s*(\{[^<>]*?\})\s*>",
            re.DOTALL,
        ),
        "NEED_INBOX_MSG": re.compile(
            r"<NEED_INBOX_MSG:\s*([^|>]{1,200})\|([^|>]{1,2000})(?:\|(\d))?\s*>",
            re.DOTALL,
        ),
        "NEED_PLAN_STEP": re.compile(
            r"<NEED_PLAN_STEP:\s*([^|>]{1,100})\|([^|>]{1,200})(?:\|([^>]{1,500}))?\s*>",
            re.DOTALL,
        ),
        "NEED_SANDBOX": re.compile(
            r"<NEED_SANDBOX>\s*\n(.*?)\n\s*</NEED_SANDBOX>",
            re.DOTALL,
        ),
        "NEED_PROPOSE": re.compile(
            r"<NEED_PROPOSE_START>.*?</NEED_PROPOSE_START>",
            re.DOTALL,
        ),
        "NEED_EDIT": re.compile(
            r"<NEED_EDIT:\s*([^|\n]{1,300})\s*\|\s*([\s\S]*?)\|\|\|([\s\S]*?)>>>",
            re.DOTALL,
        ),
        "NEED_BATCH": re.compile(
            r"<NEED_BATCH>\s*(.*?)\s*</NEED_BATCH>",
            re.DOTALL,
        ),
        "NEED_VERIFY": re.compile(
            r"<NEED_VERIFY>\s*\n(.*?)\n?\s*</NEED_VERIFY>",
            re.DOTALL,
        ),
    }

    # ── Public API ──────────────────────────────────────────────────────────

    def extract_all(self, text: str) -> list[ToolCall]:
        """
        Извлича ВСИЧКИ маркери, сортирани по позиция в текста.

        Резултатът трябва да се изпълнява в реда, в който agent.py
        ги получава — не в произволен if-ред.
        """
        if not text:
            return []
        return sorted(self._scan(text), key=lambda c: c.start)

    def extract_first(self, text: str, name: str) -> ToolCall | None:
        """Извлича само първото срещане на конкретен маркер."""
        if not text or name not in text:
            return None
        hits = [c for c in self._scan(text) if c.name == name]
        return min(hits, key=lambda c: c.start) if hits else None

    def has_any(self, text: str) -> bool:
        """Бърза проверка — има ли изобщо NEED_* маркер в текста."""
        if not text:
            return False
        for name in self.BLOCK_MARKERS:
            if name in text:
                return True
        for name in self.INLINE_MARKERS:
            if name in text:
                return True
        for name in self.SPECIAL_PATTERNS:
            if name in text:
                return True
        return False

    # ── Специализирани parse helpers ────────────────────────────────────────

    def parse_read_file(self, call: ToolCall) -> tuple[str, int | None, int | None]:
        """NEED_READ_FILE → (path, start_line | None, end_line | None)."""
        m = self.SPECIAL_PATTERNS["NEED_READ_FILE"].search(call.raw)
        if not m:
            return call.payload, None, None
        path  = m.group(1).strip()
        start = int(m.group(2)) if m.lastindex and m.lastindex >= 2 and m.group(2) else None
        end   = int(m.group(3)) if m.lastindex and m.lastindex >= 3 and m.group(3) else None
        return path, start, end

    def parse_mesh(self, call: ToolCall) -> tuple[str, str, dict[str, Any]]:
        """NEED_MESH → (target, action, payload_dict). JSON errors → empty dict."""
        m = self.SPECIAL_PATTERNS["NEED_MESH"].search(call.raw)
        if not m:
            return "", "ping", {}
        target = m.group(1).strip()
        action = m.group(2).strip()
        try:
            payload: dict[str, Any] = json.loads(m.group(3))
        except (json.JSONDecodeError, IndexError):
            payload = {}
        return target, action, payload

    def parse_logs(self, call: ToolCall) -> tuple[int, str | None]:
        """NEED_LOGS → (n_lines, grep_pattern | None)."""
        m = self.SPECIAL_PATTERNS["NEED_LOGS"].search(call.raw)
        if not m:
            return 50, None
        n    = int(m.group(1))
        grep = m.group(2).strip() if m.lastindex and m.lastindex >= 2 and m.group(2) else None
        return n, grep

    def parse_inbox_msg(self, call: ToolCall) -> tuple[str, str, int]:
        """NEED_INBOX_MSG → (subject, body, priority)."""
        m = self.SPECIAL_PATTERNS["NEED_INBOX_MSG"].search(call.raw)
        if not m:
            return call.payload, "", 2
        subject  = m.group(1).strip()
        body     = m.group(2).strip()
        priority = int(m.group(3)) if m.lastindex and m.lastindex >= 3 and m.group(3) else 2
        return subject, body, priority

    def parse_plan_step(self, call: ToolCall) -> tuple[str, str, str]:
        """NEED_PLAN_STEP → (plan_id, title, description)."""
        m = self.SPECIAL_PATTERNS["NEED_PLAN_STEP"].search(call.raw)
        if not m:
            return "", call.payload, ""
        plan_id = m.group(1).strip()
        title   = m.group(2).strip()
        desc    = m.group(3).strip() if m.lastindex and m.lastindex >= 3 and m.group(3) else ""
        return plan_id, title, desc

    # ── Private ─────────────────────────────────────────────────────────────

    def _scan(self, text: str) -> list[ToolCall]:
        calls: list[ToolCall] = []

        # Inline маркери
        for name in self.INLINE_MARKERS:
            if name not in text:
                continue
            for m in re.finditer(
                rf"<{re.escape(name)}:\s*([^>]{{1,300}})>", text, re.DOTALL
            ):
                payload = m.group(1).strip()
                if payload:
                    calls.append(
                        ToolCall(
                            name=name,
                            payload=payload,
                            raw=m.group(0),
                            start=m.start(),
                            end=m.end(),
                        )
                    )

        # Специализирани маркери
        for name, pattern in self.SPECIAL_PATTERNS.items():
            if name not in text:
                continue
            for m in pattern.finditer(text):
                # group(1) е основният payload за всички специализирани маркери
                payload = m.group(1).strip() if m.lastindex and m.lastindex >= 1 else m.group(0)
                calls.append(
                    ToolCall(
                        name=name,
                        payload=payload,
                        raw=m.group(0),
                        start=m.start(),
                        end=m.end(),
                    )
                )

        return calls


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_parser = ToolCallParser()


def get_parser() -> ToolCallParser:
    """Връща споделения singleton ToolCallParser."""
    return _parser
