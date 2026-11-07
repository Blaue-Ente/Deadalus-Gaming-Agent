"""TDD Loop — Затворен test-driven development цикъл за Дедал.

Workflow:
  1. Дедал emit-ва <NEED_TDD: file_path | test_code | initial_code>
  2. tdd_loop() се стартира БЕЗ нужда от разрешения
  3. Итерира (до MAX_ITER): run тест → ако fail → LLM fix → lint → повтори
  4. При успех → auto-извиква propose_patch_validated() → proposal за owner
  5. При провал → връща подробен failure report на Дедал

Дедал НЕ се спира между итерациите — loop е напълно автономен.
Owner вижда само финалния proposal (ако loop мине) или error report.
"""
from __future__ import annotations

import asyncio
import logging
import re
import textwrap
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

MAX_ITER = 5          # максимален брой опити за fix
MAX_CODE_CHARS = 30_000
MAX_TEST_CHARS = 8_000
_SANDBOX_TIMEOUT = 20  # секунди за sandbox run


# ── Prompt за LLM fixer ──────────────────────────────────────────────────────

_FIX_SYSTEM = """\
You are a senior Python engineer. Your task: fix a Python function so a given
test suite passes. Return ONLY the fixed Python source code — no explanations,
no markdown fences, no comments beyond what was already there.
If the fix is impossible, return the original code unchanged and add a comment
# TDD_UNFIXABLE: <reason> at the very top of the file.
"""

def _build_fix_prompt(
    file_path: str,
    test_code: str,
    current_code: str,
    error_msg: str,
    iteration: int,
) -> str:
    return textwrap.dedent(f"""\
        File: {file_path}
        Iteration: {iteration}/{MAX_ITER}

        === TEST CODE (must pass) ===
        {test_code[:MAX_TEST_CHARS]}

        === CURRENT CODE (failing) ===
        {current_code[:MAX_CODE_CHARS]}

        === FAILURE OUTPUT ===
        {error_msg[:3000]}

        Fix the code so the tests pass. Return ONLY the fixed Python source.
    """)


# ── Code extraction from LLM response ────────────────────────────────────────

def _extract_code(response: str) -> str:
    """Pull Python code from LLM response — handles fenced blocks or raw code."""
    # Try ```python ... ``` first
    m = re.search(r"```(?:python)?\n(.*?)```", response, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Try ``` ... ``` (no language)
    m = re.search(r"```\n(.*?)```", response, re.DOTALL)
    if m:
        return m.group(1).strip()
    # Raw response — assume it's all code
    return response.strip()


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class TDDResult:
    ok: bool
    file_path: str
    final_code: str = ""
    iterations_used: int = 0
    iterations: list[dict] = field(default_factory=list)
    proposal_id: str = ""
    error: str = ""
    unfixable: bool = False

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "file_path": self.file_path,
            "iterations_used": self.iterations_used,
            "proposal_id": self.proposal_id,
            "error": self.error,
            "unfixable": self.unfixable,
            "iterations": self.iterations,
        }

    def format_for_daedalus(self) -> str:
        lines: list[str] = []
        fp = self.file_path
        if self.ok:
            lines.append(
                f"✅ TDD LOOP УСПЕШЕН — {fp}\n"
                f"   Итерации: {self.iterations_used}/{MAX_ITER}\n"
                f"   Proposal ID: {self.proposal_id or '—'}\n"
                f"   Тестовете минаха — proposal чака одобрение от Николай."
            )
        elif self.unfixable:
            lines.append(
                f"🛑 TDD LOOP — UNFIXABLE — {fp}\n"
                f"   LLM отбеляза TDD_UNFIXABLE след {self.iterations_used} итерации.\n"
                f"   Причина: {self.error}\n"
                f"   Действие: преформулирай теста или промени изискванията."
            )
        else:
            lines.append(
                f"❌ TDD LOOP ПРОВАЛЕНО — {fp}\n"
                f"   {self.iterations_used}/{MAX_ITER} итерации без успех.\n"
                f"   Последна грешка: {self.error[:400]}\n"
                f"   Действие: разгледай теста/кода, или разбий задачата."
            )
        for i, it in enumerate(self.iterations, 1):
            status = "✓" if it.get("passed") else "✗"
            lines.append(
                f"   [{status}] Итерация {i}: {it.get('summary','')[:120]}"
            )
        return "\n".join(lines)


# ── LLM fixer call ───────────────────────────────────────────────────────────

async def _llm_fix(
    file_path: str,
    test_code: str,
    current_code: str,
    error_msg: str,
    iteration: int,
) -> str:
    """Call the 'coder' worker slot to fix the code. Returns fixed source."""
    from .workers import call_workers_parallel, _resolve_slot_cfg

    prompt = _build_fix_prompt(file_path, test_code, current_code, error_msg, iteration)
    slot_cfg = _resolve_slot_cfg("coder")
    results = await call_workers_parallel([{"slot": "coder", "task": prompt}])
    r = results[0] if results else {}
    if not r.get("ok"):
        return current_code  # LLM failed — keep current, loop will hit max_iter
    raw = r.get("result", "")
    return _extract_code(raw) or current_code


# ── Sandbox runner ────────────────────────────────────────────────────────────

def _run_test(test_code: str, impl_code: str) -> dict:
    """Combine impl + test in sandbox, return sandbox result dict."""
    from .sandbox import run_snippet
    combined = impl_code + "\n\n" + test_code
    return run_snippet(combined, timeout=_SANDBOX_TIMEOUT)


# ── Main loop ─────────────────────────────────────────────────────────────────

async def tdd_loop(
    file_path: str,
    test_code: str,
    initial_code: str = "",
    reason: str = "TDD loop auto-fix",
    proposed_by: str = "daedalus-tdd",
    max_iterations: int = MAX_ITER,
    auto_propose: bool = True,
) -> TDDResult:
    """Run the TDD loop autonomously.

    Args:
        file_path:     Целевият файл (за propose_patch_validated)
        test_code:     Тест-кодът, който ТРЯБВА да мине (pytest или прост assert)
        initial_code:  Начален код (или се чете от файла ако празен)
        reason:        Причина за proposal-а
        proposed_by:   Автор (default 'daedalus-tdd')
        max_iterations: Макс итерации (default 5)
        auto_propose:  Ако True → при успех auto-създава proposal

    Returns:
        TDDResult с ok=True ако тестовете минаха
    """
    from pathlib import Path
    from .lint_gate import lint_content, format_for_daedalus as fmt_lint
    from .self_improve import propose_patch_validated

    t_start = time.monotonic()
    iterations_log: list[dict] = []

    # Load initial code from disk if not provided
    if not initial_code:
        p = Path(file_path)
        initial_code = p.read_text("utf-8") if p.exists() else ""

    current_code = initial_code

    log.info("TDD loop START: %s, max_iter=%d", file_path, max_iterations)

    for i in range(1, max_iterations + 1):
        iter_t0 = time.monotonic()

        # ── Step A: Lint check ────────────────────────────────────────────────
        lint_r = lint_content(file_path, current_code)
        if not lint_r.ok:
            lint_msg = fmt_lint(lint_r)
            log.info("TDD iter %d: lint FAILED %s", i, lint_msg[:200])
            # Feed lint errors to LLM fixer as the error context
            error_for_llm = f"Lint gate blocked the code:\n{lint_msg}"
        else:
            # ── Step B: Run tests ─────────────────────────────────────────────
            sandbox_r = _run_test(test_code, current_code)
            elapsed_ms = int((time.monotonic() - iter_t0) * 1000)
            passed = sandbox_r.get("ok", False)

            # Build summary for this iteration
            stdout = (sandbox_r.get("stdout") or "").strip()
            stderr = (sandbox_r.get("stderr") or "").strip()
            tb = (sandbox_r.get("traceback") or "").strip()
            error_for_llm = (
                sandbox_r.get("error", "") + "\n" +
                (tb or stderr or stdout)
            ).strip()

            iter_summary = (
                f"{'PASS' if passed else 'FAIL'} "
                f"({elapsed_ms}ms) "
                f"{error_for_llm[:150] if not passed else '✓ all assertions passed'}"
            )
            iterations_log.append({
                "iteration": i,
                "passed": passed,
                "summary": iter_summary,
                "elapsed_ms": elapsed_ms,
            })

            log.info("TDD iter %d: %s", i, iter_summary)

            if passed:
                # ── SUCCESS ─────────────────────────────────────────────────
                proposal_id = ""
                if auto_propose and current_code != initial_code:
                    try:
                        pr = propose_patch_validated(
                            file_path=file_path,
                            new_content=current_code,
                            reason=f"{reason} (TDD: {i} iterations, tests passed)",
                            proposed_by=proposed_by,
                            test_code=test_code,
                        )
                        proposal_id = pr.get("proposal_id", "")
                        if not pr.get("ok"):
                            log.warning("TDD propose_patch failed: %s", pr.get("error"))
                    except Exception as exc:
                        log.warning("TDD auto-propose error: %s", exc)

                return TDDResult(
                    ok=True,
                    file_path=file_path,
                    final_code=current_code,
                    iterations_used=i,
                    iterations=iterations_log,
                    proposal_id=proposal_id,
                )

        # ── Step C: LLM Fix ──────────────────────────────────────────────────
        if i < max_iterations:
            log.info("TDD iter %d: calling LLM fixer...", i)
            try:
                fixed = await _llm_fix(
                    file_path, test_code, current_code, error_for_llm, i
                )
            except Exception as exc:
                log.warning("TDD LLM fixer error: %s", exc)
                fixed = current_code

            # Detect TDD_UNFIXABLE signal from LLM
            if "TDD_UNFIXABLE:" in fixed:
                m = re.search(r"#\s*TDD_UNFIXABLE:\s*(.+)", fixed)
                reason_str = m.group(1).strip() if m else "LLM marked unfixable"
                return TDDResult(
                    ok=False,
                    file_path=file_path,
                    final_code=fixed,
                    iterations_used=i,
                    iterations=iterations_log,
                    error=reason_str,
                    unfixable=True,
                )

            if fixed and fixed != current_code:
                current_code = fixed
                log.info("TDD iter %d: code updated (%d chars)", i, len(current_code))

    # ── Max iterations reached — failed ──────────────────────────────────────
    last_error = iterations_log[-1]["summary"] if iterations_log else "no iterations completed"
    return TDDResult(
        ok=False,
        file_path=file_path,
        final_code=current_code,
        iterations_used=max_iterations,
        iterations=iterations_log,
        error=f"Max {max_iterations} iterations reached. Last: {last_error}",
    )


# ── Parse NEED_TDD marker ────────────────────────────────────────────────────

def parse_tdd_marker(raw: str) -> dict | None:
    """Extract NEED_TDD marker from Дедал output.

    Format: <NEED_TDD: file_path | test_code | initial_code>
    initial_code is optional — if missing, reads from disk.
    """
    if not raw or "NEED_TDD" not in raw:
        return None
    m = re.search(
        r"<NEED_TDD:\s*([^|>]{1,200})\|([^|>]{10,30000})(?:\|([^>]{0,30000}))?\s*>",
        raw, re.DOTALL
    )
    if not m:
        return None
    return {
        "file_path":    m.group(1).strip(),
        "test_code":    m.group(2).strip(),
        "initial_code": (m.group(3) or "").strip(),
    }
