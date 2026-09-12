"""Per-prompt context window accounting.

Leaf module. Do not import agent.py, native_tools, job_ledger, run_engine,
scratch_journal, context_assembler, model_registry, or model_gateway.
Do not write studio/*.json. skip ≠ ok. Run schema v1 is unchanged.

``account()`` is observational. ``pack()`` trims only when ``trim=True``.
Owner is never dropped. System is never dropped entirely (truncated last).
Optional persist under ``$DAEDALUS_DATA/context/{run_id}.json``.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from daedalus.contracts.context import ContextBudgetReport, ContextSlice, ContextWindow
from daedalus.paths import data_root

log = logging.getLogger(__name__)

DEFAULT_RESERVE_OUTPUT = 8192
HEADROOM_RATIO = 0.10
HEADROOM_MIN = 4000
WINDOW_ENV = "DAEDALUS_CONTEXT_WINDOW"

# First matching needle in model.lower() wins. Do not import model_registry.
_WINDOW_TABLE: tuple[tuple[tuple[str, ...], int], ...] = (
    (
        (
            "gemini-2.5",
            "gemini-1.5",
            "gemini-2.0",
            "google/gemini",
            "gemini-pro",
            "gemini-flash",
        ),
        1_000_000,
    ),
    (
        (
            "claude-opus",
            "claude-sonnet",
            "claude-3-5",
            "claude-3.5",
            "claude-3-7",
            "claude-4",
            "claude-fable",
            "anthropic/claude",
        ),
        200_000,
    ),
    (
        (
            "gpt-4o",
            "gpt-4.1",
            "gpt-5",
            "gpt-4-turbo",
            "gpt-4-1106",
            "o3",
            "o4",
        ),
        128_000,
    ),
    (
        (
            "llama-3.1",
            "qwen2.5",
            "qwen3",
            "ollama",
            "lmstudio",
            "llamacpp",
            "llama.cpp",
            "vllm",
        ),
        32_000,
    ),
)

# name → (kind, priority). Higher priority is kept longer.
_SLICE_SPEC: dict[str, tuple[str, int]] = {
    "owner": ("owner", 100),
    "system": ("system", 90),
    "persona": ("system", 90),
    "briefing": ("briefing", 80),
    "dod": ("briefing", 80),
    "skills": ("skills", 70),
    "task": ("other", 60),
    "job": ("other", 60),
    "tools": ("tools", 50),
    "memory": ("memory", 40),
    "lessons": ("memory", 40),
    "episodes": ("memory", 40),
    "history": ("history", 30),
    "compaction": ("history", 30),
    "mesh": ("other", 20),
    "inbox": ("other", 20),
    "awareness": ("other", 20),
    "studio": ("other", 20),
}
_DEFAULT_SPEC = ("other", 10)
_PROTECTED_KINDS = frozenset({"owner", "system", "reserve"})


@dataclass
class BudgetRequest:
    run_id: str = ""
    session_id: str = ""
    model: str = ""
    window_tokens: int | None = None
    reserve_output: int | None = None
    parts: dict[str, str] = field(default_factory=dict)
    trim: bool = False
    persist: bool = False


def estimate(text: str | None) -> int:
    """Char/4 heuristic. Empty is 0 — same rule as the journal, copied locally."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def _spec(name: str) -> tuple[str, int]:
    return _SLICE_SPEC.get(str(name).strip().lower(), _DEFAULT_SPEC)


def _env_window() -> int | None:
    raw = (os.environ.get(WINDOW_ENV) or "").strip()
    if not raw:
        return None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _table_window(model: str) -> int | None:
    blob = (model or "").strip().lower()
    if not blob:
        return None
    for needles, window in _WINDOW_TABLE:
        if any(needle in blob for needle in needles):
            return window
    return None


def _reserve_output(req: BudgetRequest) -> int:
    if req.reserve_output is None:
        return DEFAULT_RESERVE_OUTPUT
    try:
        n = int(req.reserve_output)
    except (TypeError, ValueError):
        return DEFAULT_RESERVE_OUTPUT
    return n if n >= 0 else DEFAULT_RESERVE_OUTPUT


def _safe_run_id(run_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", (run_id or "").strip())[:80]


def _uniq(rows: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for row in rows:
        text = str(row or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _persist(run_id: str, report: ContextBudgetReport) -> str:
    safe = _safe_run_id(run_id)
    if not safe:
        return ""
    dest = data_root() / "context"
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{safe}.json"
    path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return str(path)


def _with_persist(req: BudgetRequest, report: ContextBudgetReport) -> ContextBudgetReport:
    if not req.persist:
        return report
    try:
        persisted = _persist(req.run_id, report)
    except Exception as exc:
        log.debug("context budget persist skipped: %s", exc)
        return report
    if not persisted:
        return report
    return report.model_copy(update={"persisted": persisted})


class ContextBudget:
    """Stateless façade. Accounts named prompt parts against a model window."""

    def estimate(self, text: str | None) -> int:
        return estimate(text)

    def resolve_window(self, req: BudgetRequest) -> ContextWindow:
        reserve_output = _reserve_output(req)
        source = "unknown"
        window_tokens = 0
        if req.window_tokens is not None:
            try:
                requested = int(req.window_tokens)
            except (TypeError, ValueError):
                requested = 0
            if requested > 0:
                window_tokens = requested
                source = "request"
        if source == "unknown":
            env_n = _env_window()
            if env_n is not None:
                window_tokens = env_n
                source = "env"
        if source == "unknown":
            table_n = _table_window(req.model or "")
            if table_n is not None:
                window_tokens = table_n
                source = "table"
        if source == "unknown":
            return ContextWindow(
                model=req.model or "",
                window_tokens=0,
                reserve_output=reserve_output,
                reserve_headroom=0,
                usable_tokens=0,
                source="unknown",
                skipped=True,
                ok=False,
            )
        headroom = max(HEADROOM_MIN, int(window_tokens * HEADROOM_RATIO))
        usable = window_tokens - reserve_output - headroom
        if usable <= 0:
            return ContextWindow(
                model=req.model or "",
                window_tokens=window_tokens,
                reserve_output=reserve_output,
                reserve_headroom=headroom,
                usable_tokens=0,
                source=source,
                skipped=True,
                ok=False,
            )
        return ContextWindow(
            model=req.model or "",
            window_tokens=window_tokens,
            reserve_output=reserve_output,
            reserve_headroom=headroom,
            usable_tokens=usable,
            source=source,
            skipped=False,
            ok=True,
        )

    def account(self, req: BudgetRequest) -> ContextBudgetReport:
        window = self.resolve_window(req)
        parts = dict(req.parts or {})
        content: list[ContextSlice] = []
        tokens_est = 0
        for name, text in parts.items():
            kind, priority = _spec(str(name))
            body = text if isinstance(text, str) else str(text or "")
            tok = estimate(body)
            content.append(
                ContextSlice(
                    name=str(name),
                    kind=kind,
                    tokens_est=tok,
                    chars=len(body),
                    kept=True,
                    trimmed=False,
                    skipped=False,
                    ok=True,
                    priority=priority,
                )
            )
            tokens_est += tok
        reserve = ContextSlice(
            name="reserve",
            kind="reserve",
            tokens_est=window.reserve_output + window.reserve_headroom,
            chars=0,
            kept=True,
            trimmed=False,
            skipped=False,
            ok=True,
            priority=1000,
        )
        empty = not any((value or "") for value in parts.values())
        blockers: list[str] = []
        skipped = False
        if window.skipped:
            skipped = True
            blockers.append("unknown_window" if window.source == "unknown" else "unusable_window")
        if empty:
            skipped = True
            blockers.append("empty_pack")
        usable = window.usable_tokens
        if empty:
            overflow = 0
        elif usable <= 0:
            overflow = tokens_est
        else:
            overflow = max(0, tokens_est - usable)
        if overflow and not skipped:
            blockers.append("overflow")
        report = ContextBudgetReport(
            run_id=req.run_id,
            session_id=req.session_id,
            window=window,
            slices=[*content, reserve],
            tokens_est=tokens_est,
            tokens_kept=tokens_est,
            overflow=overflow,
            trimmed=False,
            ok=(not skipped) and overflow == 0,
            skipped=skipped,
            blockers=_uniq(blockers),
            persisted="",
        )
        return _with_persist(req, report)

    def pack(self, req: BudgetRequest) -> tuple[dict[str, str], ContextBudgetReport]:
        parts = dict(req.parts or {})
        observed = self.account(
            BudgetRequest(
                run_id=req.run_id,
                session_id=req.session_id,
                model=req.model,
                window_tokens=req.window_tokens,
                reserve_output=req.reserve_output,
                parts=parts,
                trim=False,
                persist=False,
            )
        )
        if not req.trim or observed.skipped or observed.overflow <= 0:
            report = _with_persist(req, observed)
            return parts, report
        texts = dict(parts)
        working: dict[str, ContextSlice] = {
            row.name: row for row in observed.slices if row.kind != "reserve"
        }
        usable = observed.window.usable_tokens

        def kept_tokens() -> int:
            return sum(row.tokens_est for row in working.values() if row.kept)

        while kept_tokens() > usable:
            droppable = [
                row for row in working.values()
                if row.kept and row.kind not in _PROTECTED_KINDS
            ]
            if not droppable:
                break
            victim = min(droppable, key=lambda row: (row.priority, row.name))
            working[victim.name] = victim.model_copy(
                update={"kept": False, "trimmed": True, "ok": True, "skipped": False}
            )
            texts.pop(victim.name, None)

        if kept_tokens() > usable:
            systems = [
                row for row in working.values()
                if row.kept and row.kind == "system"
            ]
            systems.sort(key=lambda row: (-row.tokens_est, row.name))
            owner_tok = sum(
                row.tokens_est for row in working.values()
                if row.kept and row.kind == "owner"
            )
            budget = max(0, usable - owner_tok)
            for row in systems:
                if kept_tokens() <= usable:
                    break
                body = texts.get(row.name, "")
                if budget <= 0:
                    stub = body[:1] if body else ""
                    texts[row.name] = stub
                    working[row.name] = row.model_copy(
                        update={
                            "tokens_est": estimate(stub),
                            "chars": len(stub),
                            "trimmed": True,
                            "kept": True,
                        }
                    )
                    continue
                new_chars = budget * 4
                new_text = body[:new_chars]
                texts[row.name] = new_text
                working[row.name] = row.model_copy(
                    update={
                        "tokens_est": estimate(new_text),
                        "chars": len(new_text),
                        "trimmed": True,
                        "kept": True,
                    }
                )

        tokens_kept = kept_tokens()
        overflow = max(0, tokens_kept - usable)
        blockers = [row for row in observed.blockers if row != "overflow"]
        if overflow:
            blockers.append("overflow")
        reserve_rows = [row for row in observed.slices if row.kind == "reserve"]
        order = [row.name for row in observed.slices]
        slices = [*working.values(), *reserve_rows]
        slices.sort(key=lambda row: order.index(row.name) if row.name in order else 999)
        report = ContextBudgetReport(
            run_id=req.run_id,
            session_id=req.session_id,
            window=observed.window,
            slices=slices,
            tokens_est=observed.tokens_est,
            tokens_kept=tokens_kept,
            overflow=overflow,
            trimmed=True,
            ok=overflow == 0,
            skipped=False,
            blockers=_uniq(blockers),
            persisted="",
        )
        return texts, _with_persist(req, report)


def context_dir() -> Path:
    path = data_root() / "context"
    path.mkdir(parents=True, exist_ok=True)
    return path


def account(req: BudgetRequest) -> ContextBudgetReport:
    return ContextBudget().account(req)


def pack(req: BudgetRequest) -> tuple[dict[str, str], ContextBudgetReport]:
    return ContextBudget().pack(req)
