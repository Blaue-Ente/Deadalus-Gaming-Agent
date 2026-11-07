"""Ralph-inspired loop guards for Playground autopilot (Tier 1).

Patterns ported from https://github.com/frankbria/ralph-claude-code:
  1. Dual exit gate — completion_indicators >= 2 AND EXIT_SIGNAL: true
  2. Test-only loop detector — during Phase 5+ only: 3 consecutive verify
     retries without file writes → halt (build retries use _AUTOPILOT_MAX_ATTEMPTS)
  3. Circuit breaker — same normalized error hash 5× → halt

Used by ``autopilot_jobs._run_job`` to observe SSE events and gate ``done``.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import Any

# ── Tunables (env override) ───────────────────────────────────────────────────

def _max_test_only() -> int:
    try:
        return max(2, int(os.environ.get("DAEDALUS_AP_MAX_TEST_LOOPS", "3")))
    except (ValueError, TypeError):
        return 3


def _max_same_error() -> int:
    try:
        return max(3, int(os.environ.get("DAEDALUS_AP_MAX_SAME_ERROR", "5")))
    except (ValueError, TypeError):
        return 5


def _min_completion_indicators() -> int:
    try:
        return max(1, int(os.environ.get("DAEDALUS_AP_MIN_COMPLETION", "2")))
    except (ValueError, TypeError):
        return 2


_ERROR_NOISE_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}|\d{2}:\d{2}:\d{2}|опит\s+\d+/\d+|attempt\s+\d+",
    re.IGNORECASE,
)

# Only retry / re-test steps count toward saturation — not the first verify pass
# or success logs (those fire many events without file writes on a healthy build).
# Verify-phase retries only — build loop uses "Поправка (опит N/15)" which must not
# count here (build has its own _AUTOPILOT_MAX_ATTEMPTS cap).
_VERIFY_RETRY_RE = re.compile(
    r"тествам пак|рендерирам пак|verify…|"
    r"Runtime грешка \(опит|"
    r"Polish pass\s+\d+/|Polish pass \d+|"
    r"възстановявам по-добрия|Re-score не успя",
    re.IGNORECASE,
)

_WRITE_LOG_RE = re.compile(
    r"🔧|записа|write_file|поправих \d+ файла|files_written|===FILE:|"
    r"📄\s+\d+\s+файла|✓\s+\S+:\s+\d+\s+chars",
    re.IGNORECASE,
)


@dataclass
class ExitVerdict:
    allow: bool
    reason: str = ""
    code: str = "ok"  # ok | exit_blocked | test_saturation | circuit_open


@dataclass
class RalphLoopGuard:
    """Stateful observer for one autopilot job."""

    completion_indicators: set[str] = field(default_factory=set)
    consecutive_test_only: int = 0
    error_counts: dict[str, int] = field(default_factory=dict)
    circuit_open: bool = False
    circuit_reason: str = ""
    event_index: int = 0
    _saw_file_write_since_test: bool = True
    in_verify_phase: bool = False

    def observe_event(self, event: str, data: dict[str, Any]) -> ExitVerdict | None:
        """Track event; return halt verdict if loop must stop now."""
        if self.circuit_open:
            return ExitVerdict(
                allow=False,
                reason=self.circuit_reason,
                code="circuit_open",
            )

        self.event_index += 1
        data = data or {}

        if event == "error":
            msg = str(data.get("message") or data.get("error") or "unknown error")
            halt = self._record_error(msg)
            if halt:
                return halt

        if event == "log":
            line = str(data.get("line") or "")
            level = str(data.get("level") or "")
            if level in ("warn", "err", "error") and (
                "❌" in line or "грешка" in line.lower() or "error" in line.lower()
            ):
                halt = self._record_error(line)
                if halt:
                    return halt
            if _WRITE_LOG_RE.search(line):
                self._note_file_write()
            elif _VERIFY_RETRY_RE.search(line):
                self._note_test_only()

        if event == "stage":
            status = str(data.get("status") or "")
            if status == "done" and data.get("ok"):
                self.completion_indicators.add("stage_done")
                files = data.get("files") or []
                if files:
                    self.completion_indicators.add("files_written")
                    self._note_file_write()
                else:
                    self._note_test_only()
            elif status == "error":
                halt = self._record_error(str(data.get("message") or "stage error"))
                if halt:
                    return halt

        if event == "phase":
            phase_n = int(data.get("n") or 0)
            if phase_n >= 5:
                self.in_verify_phase = True
                self.completion_indicators.add("verify_phase")
                # Fresh verify counter — build-phase retries must not carry over.
                self.consecutive_test_only = 0
                self._saw_file_write_since_test = True

        if event == "asset" and data.get("status") == "ok":
            self._note_file_write()
            self.completion_indicators.add("asset_written")

        if self.in_verify_phase and self.consecutive_test_only >= _max_test_only():
            self.circuit_open = True
            self.circuit_reason = (
                f"Test-only loop: {self.consecutive_test_only} verify/test стъпки "
                f"без file write (max {_max_test_only()}). Автопилотът е спрян."
            )
            return ExitVerdict(
                allow=False,
                reason=self.circuit_reason,
                code="test_saturation",
            )

        return None

    def evaluate_done(self, data: dict[str, Any]) -> ExitVerdict:
        """Dual exit gate before marking job done."""
        if self.circuit_open:
            return ExitVerdict(
                allow=False,
                reason=self.circuit_reason,
                code="circuit_open",
            )

        data = data or {}
        payload_inds = data.get("completion_indicators") or []
        indicators = set(self.completion_indicators) | set(payload_inds)
        exit_signal = bool(data.get("exit_signal"))
        min_ind = _min_completion_indicators()

        if len(indicators) >= min_ind and exit_signal:
            return ExitVerdict(allow=True, reason="dual exit gate passed", code="ok")

        if len(indicators) >= min_ind and not exit_signal:
            return ExitVerdict(
                allow=False,
                reason=(
                    f"EXIT_SIGNAL липсва — {len(indicators)} completion indicator(s), "
                    f"но exit_signal=false. Ralph gate блокира premature done."
                ),
                code="exit_blocked",
            )

        if exit_signal and len(indicators) < min_ind:
            return ExitVerdict(
                allow=False,
                reason=(
                    f"Completion threshold не е достигнат ({len(indicators)}/{min_ind} "
                    f"indicators). EXIT_SIGNAL игнориран."
                ),
                code="exit_blocked",
            )

        return ExitVerdict(
            allow=False,
            reason=(
                f"Done блокиран: {len(indicators)}/{min_ind} indicators, "
                f"exit_signal={exit_signal}"
            ),
            code="exit_blocked",
        )

    def _note_file_write(self) -> None:
        self._saw_file_write_since_test = True
        self.consecutive_test_only = 0

    def _note_test_only(self) -> None:
        if self._saw_file_write_since_test:
            self.consecutive_test_only = 1
            self._saw_file_write_since_test = False
        else:
            self.consecutive_test_only += 1

    def _record_error(self, message: str) -> ExitVerdict | None:
        norm = _ERROR_NOISE_RE.sub("", message.lower()).strip()[:400]
        if len(norm) < 8:
            return None
        h = hashlib.sha256(norm.encode()).hexdigest()[:16]
        self.error_counts[h] = self.error_counts.get(h, 0) + 1
        if self.error_counts[h] >= _max_same_error():
            self.circuit_open = True
            self.circuit_reason = (
                f"Circuit breaker: същата грешка {self.error_counts[h]}× "
                f"(max {_max_same_error()}). Автопилот спрян."
            )
            return ExitVerdict(
                allow=False,
                reason=self.circuit_reason,
                code="circuit_open",
            )
        return None


def build_completion_indicators(
    *,
    stages_done: int = 0,
    runtime_verified: bool = False,
    browser_verified: bool = False,
    verified: bool = False,
    files_count: int = 0,
    deliverable: bool = False,
    assets_count: int = 0,
) -> list[str]:
    """Build indicator list from pipeline state (admin.py before yield done)."""
    indicators: list[str] = []
    if stages_done > 0:
        indicators.append("stages_completed")
    if files_count > 0:
        indicators.append("files_written")
    if assets_count > 0:
        indicators.append("assets_written")
    if runtime_verified:
        indicators.append("runtime_verified")
    if browser_verified:
        indicators.append("browser_verified")
    if verified:
        indicators.append("smoke_verified")
    if deliverable:
        indicators.append("deliverable_ready")
    return indicators


def enrich_done_payload(data: dict[str, Any], indicators: list[str]) -> dict[str, Any]:
    """Add Ralph exit metadata. Sets exit_signal when indicator threshold is met."""
    out = dict(data)
    merged = list(dict.fromkeys(indicators))
    out["completion_indicators"] = merged
    out["exit_signal"] = len(merged) >= _min_completion_indicators()
    return out
