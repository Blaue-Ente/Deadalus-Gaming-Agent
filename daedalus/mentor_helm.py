"""Mentor director LLM — owner-chosen model, tight token budget.

`daedalus.mentor.steer()` stays the cheap deterministic helm (tests, unambiguous
cases, LLM off / over budget). This module is the *director*: a strong model
uses its own training plus a tiny inspect digest, not the harness RAG.

Token control (defaults, owner-overridable):
- skip LLM when rules already know the next move (Vite #app scaffold, ship/stop)
- max ~320 output tokens, truncated digest (~1.8k input tokens)
- daily ledger (in+out estimate)
- at most two LLM calls per native turn, cached on loop.mentor_steer
- 10s timeout; JSON only; no chain-of-thought
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any

from . import kv, llm_config
from .scratch_journal import estimate_tokens

log = logging.getLogger("daedalus.mentor_helm")

KV_CHOICE = "adn:daedalus_mentor_choice"
KV_SPEND = "adn:daedalus_mentor_spend"
KV_DAY_CAP = "adn:daedalus_mentor_day_cap"

DEFAULT_MAX_OUT = 320
DEFAULT_MAX_IN = 1800
DEFAULT_DAY_CAP = 40_000
TURN_LLM_CAP = 2
LLM_TIMEOUT_S = 10.0

SYSTEM_PROMPT = (
    "You are Daedalus Mentor: a terse professional game director. "
    "You do not write files. You pick the next native tool using your own "
    "game-craft knowledge plus the inspect digest. Output JSON only, no markdown, "
    "no chain-of-thought. Schema: "
    '{"action":"call_tool|continue|stop","next_tool":"string|null",'
    '"next_args":{},"reason":"<=140 chars","forbid_tools":[],"blockers":[]}. '
    "Prefer existing Vite/Phaser loops, generate_image for missing art, "
    "run_command for npm run build/dev. Never invent tools. Spend few tokens."
)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_THINKING_MARKERS = ("thinking", "r1", "qwq", "reasoner", "o1-", "o3-", "o4-")

# Owner-facing catalog. `recommended` is the default auto pick when a key exists.
CATALOG: list[dict[str, Any]] = [
    {
        "id": "auto",
        "label": "Auto (best available)",
        "group": "choice",
        "recommended": False,
        "provider": "",
        "model": "",
        "why": "Picks the first recommended model whose API key is set.",
        "cost": "varies",
        "needs_key": False,
    },
    {
        "id": "or-claude-sonnet",
        "label": "Claude Sonnet 4.5",
        "group": "recommended",
        "recommended": True,
        "provider": "openrouter",
        "model": "anthropic/claude-sonnet-4-5",
        "why": "Strong director, good quality/price. Default auto pick.",
        "cost": "mid",
        "needs_key": True,
    },
    {
        "id": "or-gpt-4o",
        "label": "GPT-4o",
        "group": "recommended",
        "recommended": True,
        "provider": "openrouter",
        "model": "openai/gpt-4o",
        "why": "Broad game/product knowledge via OpenRouter.",
        "cost": "mid",
        "needs_key": True,
    },
    {
        "id": "or-claude-opus",
        "label": "Claude Opus 4",
        "group": "also",
        "recommended": False,
        "provider": "openrouter",
        "model": "anthropic/claude-opus-4",
        "why": "Strongest Claude. Expensive — use when stuck.",
        "cost": "high",
        "needs_key": True,
    },
    {
        "id": "novita-qwen-coder",
        "label": "Qwen3 Coder 480B",
        "group": "recommended",
        "recommended": True,
        "provider": "novita",
        "model": "qwen/qwen3-coder-480b-a35b-instruct",
        "why": "Huge coding model on Novita. Good when OpenRouter is unset.",
        "cost": "mid",
        "needs_key": True,
    },
    {
        "id": "novita-qwen-235",
        "label": "Qwen3 235B",
        "group": "also",
        "recommended": False,
        "provider": "novita",
        "model": "qwen/qwen3-235b-a22b-instruct-2507",
        "why": "Large general model on Novita.",
        "cost": "mid",
        "needs_key": True,
    },
    {
        "id": "openai-gpt-4o",
        "label": "GPT-4o (OpenAI)",
        "group": "also",
        "recommended": False,
        "provider": "openai",
        "model": "gpt-4o",
        "why": "Direct OpenAI key, not OpenRouter.",
        "cost": "mid",
        "needs_key": True,
    },
    {
        "id": "nvidia-mistral-large",
        "label": "Mistral Large (NVIDIA)",
        "group": "also",
        "recommended": False,
        "provider": "nvidia",
        "model": "mistralai/mistral-large-3-675b-instruct-2512",
        "why": "If you already have NVIDIA_API_KEY.",
        "cost": "mid",
        "needs_key": True,
    },
    {
        "id": "brain",
        "label": "Studio Brain",
        "group": "choice",
        "recommended": False,
        "provider": "",
        "model": "",
        "why": "Use whatever model is selected in Studio → Brain. May be cheap.",
        "cost": "varies",
        "needs_key": False,
    },
    {
        "id": "rules",
        "label": "Rules only (no LLM)",
        "group": "choice",
        "recommended": False,
        "provider": "",
        "model": "",
        "why": "Deterministic inspect helm. Zero LLM spend.",
        "cost": "none",
        "needs_key": False,
    },
]


def llm_enabled() -> bool:
    raw = (os.environ.get("DAEDALUS_MENTOR_LLM") or "1").strip().lower()
    return raw not in ("0", "false", "off", "no")


def max_out_tokens() -> int:
    try:
        return max(64, min(800, int(os.environ.get("DAEDALUS_MENTOR_MAX_OUT") or DEFAULT_MAX_OUT)))
    except ValueError:
        return DEFAULT_MAX_OUT


def max_in_tokens() -> int:
    try:
        return max(400, min(4000, int(os.environ.get("DAEDALUS_MENTOR_MAX_IN") or DEFAULT_MAX_IN)))
    except ValueError:
        return DEFAULT_MAX_IN


def _kv_json(key: str, default: Any = None) -> Any:
    raw = kv.get(key)
    if raw is None:
        return default
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return default
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return default
    return raw


def day_token_cap() -> int:
    raw = (os.environ.get("DAEDALUS_MENTOR_DAY_CAP") or "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    stored = _kv_json(KV_DAY_CAP, None)
    if isinstance(stored, dict):
        try:
            return max(0, int(stored.get("cap") or DEFAULT_DAY_CAP))
        except (TypeError, ValueError):
            pass
    if isinstance(stored, (int, float)):
        return max(0, int(stored))
    return DEFAULT_DAY_CAP


def set_day_token_cap(cap: int) -> int:
    cap = max(0, min(2_000_000, int(cap)))
    kv.set(KV_DAY_CAP, {"cap": cap})
    return cap


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def spend_today() -> dict[str, Any]:
    raw = _kv_json(KV_SPEND, {}) or {}
    if not isinstance(raw, dict):
        raw = {}
    day = str(raw.get("day") or "")
    if day != _today():
        return {"day": _today(), "tokens": 0, "calls": 0}
    return {
        "day": day,
        "tokens": int(raw.get("tokens") or 0),
        "calls": int(raw.get("calls") or 0),
    }


def record_spend(tokens: int) -> dict[str, Any]:
    cur = spend_today()
    cur["tokens"] = int(cur["tokens"]) + max(0, int(tokens))
    cur["calls"] = int(cur["calls"]) + 1
    cur["day"] = _today()
    kv.set(KV_SPEND, cur)
    return cur


def over_day_cap() -> bool:
    cap = day_token_cap()
    if cap <= 0:
        return True
    return int(spend_today().get("tokens") or 0) >= cap


def _provider_ready(provider: str) -> bool:
    p = (provider or "").strip()
    if not p:
        return False
    if llm_config.resolve_provider_api_key(p):
        return True
    try:
        from .local_llm import is_local_provider
        return is_local_provider(p)
    except Exception:
        return False


def _brain_ready() -> bool:
    cfg = llm_config.get_config()
    if cfg.get("api_key"):
        return True
    return _provider_ready(str(cfg.get("provider") or ""))


def _entry_available(row: dict[str, Any]) -> bool:
    cid = row.get("id") or ""
    if cid in ("auto", "rules"):
        return True
    if cid == "brain":
        return _brain_ready()
    return _provider_ready(str(row.get("provider") or ""))


def catalog_public() -> list[dict[str, Any]]:
    out = []
    for row in CATALOG:
        item = dict(row)
        item["available"] = _entry_available(row)
        warn = thinking_warn(str(row.get("model") or ""))
        if warn:
            item["warn"] = warn
        out.append(item)
    return out


def recommend() -> dict[str, Any] | None:
    """First recommended catalog row whose key is present."""
    for row in CATALOG:
        if row.get("recommended") and _entry_available(row):
            return row
    return None


def thinking_warn(model: str) -> str | None:
    ml = (model or "").lower()
    if any(m in ml for m in _THINKING_MARKERS):
        return "Hidden-thinking models burn tokens; Mentor already forces tiny JSON."
    return None


def get_choice() -> dict[str, Any]:
    env_model = (os.environ.get("DAEDALUS_MENTOR_MODEL") or "").strip()
    env_provider = (os.environ.get("DAEDALUS_MENTOR_PROVIDER") or "").strip()
    if env_model:
        provider = env_provider or (llm_config.get_config().get("provider") or "openrouter")
        out = {
            "id": "custom",
            "label": f"{provider}/{env_model}",
            "provider": provider,
            "model": env_model,
            "source": "env",
        }
        warn = thinking_warn(env_model)
        if warn:
            out["warn"] = warn
        return out
    stored = _kv_json(KV_CHOICE, None)
    if isinstance(stored, dict) and (stored.get("id") or stored.get("model")):
        cid = str(stored.get("id") or "")
        if cid == "auto":
            rec = recommend()
            return {
                "id": "auto",
                "label": "Auto (best available)",
                "provider": (rec or {}).get("provider") or "",
                "model": (rec or {}).get("model") or "",
                "resolved_id": (rec or {}).get("id") or "rules",
                "source": "kv",
            }
        if cid == "brain":
            cfg = llm_config.get_config()
            return {
                "id": "brain",
                "label": "Studio Brain",
                "provider": cfg.get("provider") or "",
                "model": cfg.get("model") or "",
                "source": "kv",
            }
        out = {**stored, "source": stored.get("source") or "kv"}
        warn = thinking_warn(str(out.get("model") or ""))
        if warn:
            out["warn"] = warn
        return out
    rec = recommend()
    if rec:
        return {
            "id": rec["id"],
            "label": rec["label"],
            "provider": rec["provider"],
            "model": rec["model"],
            "source": "auto",
        }
    return {
        "id": "rules",
        "label": "Rules only (no LLM)",
        "provider": "",
        "model": "",
        "source": "fallback",
    }


def set_choice(body: dict[str, Any] | None) -> dict[str, Any]:
    body = body or {}
    cid = str(body.get("choice_id") or body.get("id") or "").strip()
    provider = str(body.get("provider") or "").strip()
    model = str(body.get("model") or "").strip()
    if cid == "auto":
        rec = recommend()
        payload = {
            "id": "auto",
            "label": "Auto (best available)",
            "provider": (rec or {}).get("provider") or "",
            "model": (rec or {}).get("model") or "",
            "resolved_id": (rec or {}).get("id") or "rules",
        }
    elif cid == "brain":
        cfg = llm_config.get_config()
        payload = {
            "id": "brain",
            "label": "Studio Brain",
            "provider": cfg.get("provider") or "",
            "model": cfg.get("model") or "",
        }
    elif cid == "rules":
        payload = {"id": "rules", "label": "Rules only (no LLM)", "provider": "", "model": ""}
    elif cid:
        row = next((r for r in CATALOG if r["id"] == cid), None)
        if not row:
            raise ValueError(f"unknown mentor choice_id: {cid}")
        payload = {
            "id": row["id"],
            "label": row["label"],
            "provider": row.get("provider") or provider,
            "model": row.get("model") or model,
        }
    elif provider and model:
        payload = {
            "id": "custom",
            "label": f"{provider}/{model}",
            "provider": provider,
            "model": model,
        }
    else:
        raise ValueError("choice_id or provider+model required")
    payload["source"] = "kv"
    kv.set(KV_CHOICE, payload)
    return get_choice()


def rules_unambiguous(inspect: dict[str, Any], steer: dict[str, Any]) -> bool:
    """Skip the expensive model when inspect already dictates the next tool."""
    flags = inspect.get("flags") or {}
    nxt = steer.get("next_tool")
    action = str(steer.get("action") or "")
    if action in {"stop", "ship"}:
        return True
    if flags.get("scaffold_vite_app_div") and nxt in {"write_file", "write_in_tree"}:
        return True
    return False


def digest_fingerprint(inspect: dict[str, Any]) -> str:
    flags = inspect.get("flags") or {}
    keys = (
        inspect.get("kind"),
        inspect.get("scaffold"),
        inspect.get("loop_ok"),
        inspect.get("has_src_main"),
        inspect.get("has_index_html"),
        inspect.get("proof_blank"),
        inspect.get("play_url"),
        tuple(sorted(str(k) for k, v in flags.items() if v)),
        tuple((inspect.get("last_fails") or [])[:3]),
    )
    return json.dumps(keys, ensure_ascii=False, separators=(",", ":"))


def _truncate(text: str, max_tok: int) -> str:
    budget = max_tok * 4
    text = (text or "").strip()
    if len(text) <= budget:
        return text
    return text[: budget - 1] + "…"


def build_user_prompt(inspect: dict[str, Any]) -> str:
    flags = inspect.get("flags") or {}
    compact_flags = {k: v for k, v in flags.items() if v}
    body = {
        "kind": inspect.get("kind"),
        "scaffold": inspect.get("scaffold"),
        "loop_ok": inspect.get("loop_ok"),
        "has_src_main": inspect.get("has_src_main"),
        "has_index_html": inspect.get("has_index_html"),
        "play_url": inspect.get("play_url"),
        "proof_blank": inspect.get("proof_blank"),
        "flags": compact_flags,
        "last_fails": (inspect.get("last_fails") or [])[:4],
        "native_hint": inspect.get("native_hint") or "",
    }
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    return _truncate(raw, max_in_tokens())


def parse_director_json(text: str) -> dict[str, Any] | None:
    blob = (text or "").strip()
    if not blob:
        return None
    if blob.startswith("```"):
        blob = re.sub(r"^```(?:json)?\s*", "", blob)
        blob = re.sub(r"\s*```$", "", blob)
    try:
        data = json.loads(blob)
    except json.JSONDecodeError:
        m = _JSON_RE.search(blob)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    action = str(data.get("action") or "continue").strip().lower()
    if action not in ("call_tool", "continue", "stop", "ship"):
        action = "continue"
    nxt = data.get("next_tool")
    nxt = str(nxt).strip() if nxt else None
    args = data.get("next_args") if isinstance(data.get("next_args"), dict) else {}
    reason = str(data.get("reason") or "")[:140]
    forbid = [str(x) for x in (data.get("forbid_tools") or []) if x][:12]
    blockers = [str(x) for x in (data.get("blockers") or []) if x][:8]
    return {
        "ok": True,
        "action": action,
        "next_tool": nxt,
        "next_args": args,
        "reason": reason,
        "forbid_tools": forbid,
        "blockers": blockers,
        "source": "llm",
    }


def _resolve_endpoint() -> tuple[str, str] | None:
    choice = get_choice()
    cid = choice.get("id") or ""
    if cid == "rules":
        return None
    if cid == "auto":
        rec = recommend()
        if not rec:
            return None
        provider, model = rec["provider"], rec["model"]
    elif cid == "brain":
        cfg = llm_config.get_config()
        provider = (cfg.get("provider") or "").strip()
        model = (cfg.get("model") or "").strip()
    else:
        provider = (choice.get("provider") or "").strip()
        model = (choice.get("model") or "").strip()
    if not provider or not model:
        return None
    if not _provider_ready(provider):
        return None
    return provider, model


def skip_reason(inspect: dict[str, Any], rules_steer: dict[str, Any], *, turn_calls: int) -> str | None:
    if not llm_enabled():
        return "llm_disabled"
    if get_choice().get("id") == "rules":
        return "rules_only"
    if over_day_cap():
        return "day_cap"
    if turn_calls >= TURN_LLM_CAP:
        return "turn_cap"
    if rules_unambiguous(inspect, rules_steer):
        return "rules_unambiguous"
    if _resolve_endpoint() is None:
        return "no_key"
    return None


async def _complete_director(provider: str, model: str, user: str) -> str:
    """One-shot JSON call on the chosen provider — does not mutate Studio Brain."""
    from .llm_config import PROVIDER_DEFAULTS, openai_compat_headers, resolve_provider_api_key
    from .local_llm import dummy_api_key, is_local_provider

    pd = PROVIDER_DEFAULTS.get(provider) or PROVIDER_DEFAULTS["openai"]
    api_key = resolve_provider_api_key(provider) or ""
    if not api_key and is_local_provider(provider):
        api_key = dummy_api_key(provider)
    if not api_key:
        raise RuntimeError(f"no API key for {provider}")
    base_url = str(pd.get("base_url") or "")
    sdk = str(pd.get("sdk") or "openai")
    timeout = LLM_TIMEOUT_S

    if sdk == "anthropic":
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(
            api_key=api_key,
            base_url=base_url or None,
            max_retries=0,
            timeout=timeout,
        )
        resp = await client.messages.create(
            model=model,
            max_tokens=max_out_tokens(),
            temperature=0.1,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(getattr(b, "text", "") or "" for b in (resp.content or []))

    from openai import AsyncOpenAI

    extra = openai_compat_headers(provider)
    client = AsyncOpenAI(
        api_key=api_key,
        base_url=base_url or None,
        max_retries=0,
        timeout=timeout,
        default_headers=extra or None,
    )
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_out_tokens(),
        "temperature": 0.1,
    }
    try:
        body["response_format"] = {"type": "json_object"}
        resp = await client.chat.completions.create(**body)
    except Exception:
        body.pop("response_format", None)
        resp = await client.chat.completions.create(**body)
    return (resp.choices[0].message.content if resp.choices else "") or ""


def _rules_fallback(rules_steer: dict[str, Any], why: str, **extra: Any) -> dict[str, Any]:
    out = dict(rules_steer)
    out.setdefault("ok", True)
    out["source"] = "rules"
    out["llm_skip"] = why
    out.update(extra)
    return out


async def consult(
    inspect: dict[str, Any],
    rules_steer: dict[str, Any],
    *,
    turn_calls: int = 0,
) -> dict[str, Any]:
    """Ask the director. Always returns a steer dict; `source` is llm|rules."""
    why = skip_reason(inspect, rules_steer, turn_calls=turn_calls)
    if why:
        return _rules_fallback(rules_steer, why)
    endpoint = _resolve_endpoint()
    if endpoint is None:
        return _rules_fallback(rules_steer, "no_key")
    provider, model = endpoint
    user = build_user_prompt(inspect)
    in_tok = estimate_tokens(SYSTEM_PROMPT) + estimate_tokens(user)
    t0 = time.monotonic()
    try:
        text = await asyncio.wait_for(
            _complete_director(provider, model, user),
            timeout=LLM_TIMEOUT_S + 1.0,
        )
    except Exception as exc:
        log.info("mentor helm LLM failed: %s", exc)
        return _rules_fallback(rules_steer, "llm_error")
    parsed = parse_director_json(text or "")
    out_tok = estimate_tokens(text or "")
    record_spend(in_tok + out_tok)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    if not parsed:
        return _rules_fallback(rules_steer, "bad_json", llm_ms=elapsed_ms)
    merged_forbid = list(dict.fromkeys(
        list(parsed.get("forbid_tools") or []) + list(rules_steer.get("forbid_tools") or [])
    ))
    parsed["forbid_tools"] = merged_forbid
    parsed["llm_ms"] = elapsed_ms
    parsed["llm_model"] = model
    parsed["llm_provider"] = provider
    parsed["llm_tokens"] = in_tok + out_tok
    nxt = parsed.get("next_tool")
    if nxt and nxt in merged_forbid:
        parsed["next_tool"] = rules_steer.get("next_tool")
        parsed["next_args"] = rules_steer.get("next_args") or {}
        parsed["action"] = rules_steer.get("action") or "continue"
        parsed["reason"] = (parsed.get("reason") or "") or (rules_steer.get("reason") or "")
    if parsed.get("action") == "call_tool" and not parsed.get("next_tool"):
        parsed["action"] = rules_steer.get("action") or "continue"
        parsed["next_tool"] = rules_steer.get("next_tool")
        parsed["next_args"] = rules_steer.get("next_args") or {}
        parsed["reason"] = (parsed.get("reason") or "") or (rules_steer.get("reason") or "")
    return parsed


def status_llm() -> dict[str, Any]:
    rec = recommend()
    choice = get_choice()
    spend = spend_today()
    return {
        "enabled": llm_enabled(),
        "choice": choice,
        "choice_locked": choice.get("source") == "env",
        "recommended": (
            {
                "id": rec["id"],
                "label": rec["label"],
                "provider": rec["provider"],
                "model": rec["model"],
                "why": rec.get("why") or "",
            }
            if rec else None
        ),
        "catalog": catalog_public(),
        "spend": {**spend, "cap": day_token_cap()},
        "max_out": max_out_tokens(),
        "max_in": max_in_tokens(),
        "turn_calls_cap": TURN_LLM_CAP,
        "timeout_s": LLM_TIMEOUT_S,
    }
