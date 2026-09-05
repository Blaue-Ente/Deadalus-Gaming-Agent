"""Turn-level LLM cost ledger.

OpenRouter can return ``usage.cost`` when the request asks for it. Anthropic
and other gateways report tokens; we price those from the catalog. A missing
figure is estimated, never invented as zero when tokens exist.
"""
from __future__ import annotations

import contextvars
from dataclasses import asdict, dataclass, field
from typing import Any

_TURN: contextvars.ContextVar["CostLedger | None"] = contextvars.ContextVar("llm_cost_turn", default=None)


@dataclass
class UsageRow:
    role: str
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float | None = None
    source: str = "estimate"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CostLedger:
    rows: list[UsageRow] = field(default_factory=list)

    def record(
        self,
        *,
        role: str,
        provider: str,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float | None = None,
    ) -> UsageRow:
        source = "provider" if cost_usd is not None else "estimate"
        if cost_usd is None and (prompt_tokens or completion_tokens):
            cost_usd = _estimate(model, provider, prompt_tokens, completion_tokens)
        row = UsageRow(
            role=role or "chat",
            provider=provider,
            model=model,
            prompt_tokens=int(prompt_tokens or 0),
            completion_tokens=int(completion_tokens or 0),
            cost_usd=None if cost_usd is None else round(float(cost_usd), 6),
            source=source if cost_usd is not None else "unknown",
        )
        self.rows.append(row)
        return row

    def total_usd(self) -> float:
        return round(sum(r.cost_usd or 0.0 for r in self.rows), 6)

    def total_tokens(self) -> tuple[int, int]:
        return (
            sum(r.prompt_tokens for r in self.rows),
            sum(r.completion_tokens for r in self.rows),
        )

    def below_mentor_upgrade(self) -> bool:
        from .model_catalog import upgrade_below_usd
        return self.total_usd() < upgrade_below_usd()

    def summary(self) -> str:
        usd = self.total_usd()
        pin, pout = self.total_tokens()
        if not self.rows:
            return ""
        return f"LLM spend this turn: ${usd:.3f} ({pin} in / {pout} out tokens, {len(self.rows)} calls)."

    def as_dict(self) -> dict[str, Any]:
        pin, pout = self.total_tokens()
        return {
            "usd": self.total_usd(),
            "prompt_tokens": pin,
            "completion_tokens": pout,
            "calls": [r.as_dict() for r in self.rows],
        }


def _estimate(model: str, provider: str, n_in: int, n_out: int) -> float | None:
    try:
        from .model_catalog import find
        m = find(model, provider) or find(model)
    except Exception:
        return None
    if m is None or not (m.prompt_per_m or m.completion_per_m):
        return None
    return m.estimate_build_usd(n_in, n_out)


def begin_turn() -> CostLedger:
    ledger = CostLedger()
    _TURN.set(ledger)
    return ledger


def current() -> CostLedger | None:
    return _TURN.get()


def end_turn() -> CostLedger | None:
    ledger = _TURN.get()
    _TURN.set(None)
    return ledger


def record_from_response(
    resp: Any,
    *,
    role: str,
    provider: str,
    model: str,
) -> UsageRow | None:
    ledger = current()
    if ledger is None:
        return None
    usage = getattr(resp, "usage", None)
    if usage is None and isinstance(resp, dict):
        usage = resp.get("usage")
    prompt = completion = 0
    cost: float | None = None
    if usage is None:
        return None
    if isinstance(usage, dict):
        prompt = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        completion = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
        if usage.get("cost") is not None:
            try:
                cost = float(usage["cost"])
            except (TypeError, ValueError):
                cost = None
    else:
        prompt = int(getattr(usage, "prompt_tokens", 0) or getattr(usage, "input_tokens", 0) or 0)
        completion = int(getattr(usage, "completion_tokens", 0) or getattr(usage, "output_tokens", 0) or 0)
        raw_cost = getattr(usage, "cost", None)
        if raw_cost is None:
            extra = getattr(usage, "model_extra", None) or {}
            if isinstance(extra, dict):
                raw_cost = extra.get("cost")
        if raw_cost is not None:
            try:
                cost = float(raw_cost)
            except (TypeError, ValueError):
                cost = None
    if not prompt and not completion and cost is None:
        return None
    return ledger.record(
        role=role, provider=provider, model=model,
        prompt_tokens=prompt, completion_tokens=completion, cost_usd=cost,
    )
