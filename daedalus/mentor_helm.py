"""Mentor director LLM — owner-chosen model, tight token budget.

`daedalus.mentor.steer()` stays the cheap deterministic helm (tests, unambiguous
cases, LLM off / over budget). This module is the *director*: a strong model
uses its own training plus a tiny inspect digest, not the harness RAG.

Token control (defaults, owner-overridable):
- skip LLM when rules already know the next move (Vite #app scaffold, ship/stop)
- max ~320 output tokens, truncated digest (~1.8k input tokens)
- daily ledger (in+out estimate)
- at most two LLM calls per native turn, cached on loop.mentor_steer
- 10s timeout (``DAEDALUS_MENTOR_TIMEOUT``, 3–60s); JSON only; no chain-of-thought
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
MENTOR_TIMEOUT_MIN_S = 3.0
MENTOR_TIMEOUT_MAX_S = 60.0

SYSTEM_PROMPT = (
    "You are Daedalus Mentor: an external evaluator with your own game-craft "
    "knowledge, not a dispatcher. You do not write files. You do not assign "
    "workers. Judge: is this slice playable? Painted Menu is not playable. "
    "Play.js stub is not playable. <canvas id=game>+WEBGL is a black screen. "
    "The kernel computer (playtest/scenario.json) is the playable standard. "
    "The inspect digest includes a tool graph (tools, flow, edges, standards, "
    "recovery). Pick next_tool only from graph.tools. If graph.recovery.next "
    "is set, use it. proof_failed → write_file, never browser. "
    "play_stub / play_incomplete / canvas_game_trap / play_bot_failed → "
    "write_file, never ship. "
    "kernel_proof_ran and play_bot_ok and not proof_failed → action ship. "
    "Otherwise return a verdict to the writer: continue + next_tool. "
    "Output JSON only, no markdown, no chain-of-thought. Schema: "
    '{"action":"call_tool|continue|stop|ship","next_tool":"string|null",'
    '"next_args":{},"reason":"<=140 chars","forbid_tools":[],"blockers":[]}. '
    "Never invent tools. Spend few tokens."
)

FRAMES_ADDENDUM = (
    " Real frames of the running game are attached as images, newest last. Look at them and judge playability "
    "from what is visible, not from file names. Add \"observations\":[up to 3 short strings of what is visibly on "
    "screen] to the JSON. Never claim to see anything that is not in the images."
)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_THINKING_MARKERS = ("thinking", "r1", "qwq", "reasoner", "o1-", "o3-", "o4-")
_COST_ORDER = {"none": 0, "low": 1, "mid": 2, "high": 3, "varies": 2}
_EXTRA_PROVIDERS = frozenset({
    "openrouter", "alltoken", "novita", "openai", "nvidia", "anthropic",
})


def _row(
    cid: str,
    label: str,
    *,
    group: str,
    provider: str = "",
    model: str = "",
    why: str = "",
    cost: str = "mid",
    recommended: bool = False,
    auto: bool = False,
    rank: int = 100,
    needs_key: bool = True,
) -> dict[str, Any]:
    return {
        "id": cid,
        "label": label,
        "group": group,
        "provider": provider,
        "model": model,
        "why": why,
        "cost": cost,
        "recommended": recommended,
        "auto": auto,
        "rank": rank,
        "needs_key": needs_key,
    }


# Pinned director list. `recommended` = shows in the Recommended optgroup.
# `auto` = eligible for Auto (cheaper frontier first; flagships stay a manual pick).
CATALOG: list[dict[str, Any]] = [
    _row("auto", "Auto (best available)", group="choice",
         why="Picks a strong mid-cost director whose API key is set.", cost="varies", needs_key=False),
    _row("or-fable-51", "Claude Fable 5.1", group="recommended", recommended=True,
         provider="openrouter", model="anthropic/claude-fable-5.1",
         why="Best coding-agent director. Tiny Mentor JSON keeps the bill small.",
         cost="high", rank=5),
    _row("or-astra", "GPT-6 Astra", group="recommended", recommended=True,
         provider="openrouter", model="openai/gpt-6-astra",
         why="Newest OpenAI flagship. Expensive — Mentor still caps tokens.",
         cost="high", rank=6),
    _row("or-gpt-56-sol", "GPT-5.6 Sol", group="recommended", recommended=True,
         provider="openrouter", model="openai/gpt-5.6-sol",
         why="Strong GPT-5.6. Prefer Terra if you want the cheaper 5.6.",
         cost="high", rank=8),
    _row("or-opus-48", "Claude Opus 4.8", group="recommended", recommended=True,
         provider="openrouter", model="anthropic/claude-opus-4.8",
         why="Opus 4.8 — previous Claude flagship, still excellent at helm.",
         cost="high", rank=9),
    _row("or-gpt-55", "GPT-5.5", group="recommended", recommended=True,
         provider="openrouter", model="openai/gpt-5.5",
         why="GPT-5.5 via OpenRouter.", cost="high", rank=10),
    _row("or-kimi-k3", "Kimi K3", group="recommended", recommended=True, auto=True,
         provider="openrouter", model="moonshotai/kimi-k3",
         why="Open-weight agent model. Strong director, mid price.",
         cost="mid", rank=20),
    _row("or-glm-53", "GLM 5.3", group="recommended", recommended=True,
         provider="openrouter", model="z-ai/glm-5.3",
         why="Z.ai coding/agent flagship. Hidden reasoning stays on — Flash is cheaper.",
         cost="mid", rank=22),
    _row("or-grok-46", "Grok 4.6", group="recommended", recommended=True, auto=True,
         provider="openrouter", model="x-ai/grok-4.6",
         why="Frontier quality at mid price. Default Auto pick on OpenRouter.",
         cost="mid", rank=12),
    _row("or-gpt-56-terra", "GPT-5.6 Terra", group="recommended", recommended=True, auto=True,
         provider="openrouter", model="openai/gpt-5.6-terra",
         why="Cheaper GPT-5.6. Good Auto alternative to Grok.",
         cost="mid", rank=18),
    _row("or-glm-53-flash", "GLM 5.3 Flash", group="also", auto=True,
         provider="openrouter", model="z-ai/glm-5.3-flash",
         why="Same family, much cheaper. Prefer this if GLM reasoning burns the cap.",
         cost="mid", rank=15),
    _row("or-qwen38-max", "Qwen 3.8 Max", group="also", auto=True,
         provider="openrouter", model="qwen/qwen3.8-max-0902",
         why="Alibaba coding flagship on OpenRouter.", cost="mid", rank=28),
    _row("or-sonnet-46", "Claude Sonnet 4.6", group="also", auto=True,
         provider="openrouter", model="anthropic/claude-sonnet-4.6",
         why="Solid director, lower than Fable/Opus.", cost="mid", rank=30),
    _row("or-claude-sonnet", "Claude Sonnet 4.5", group="also",
         provider="openrouter", model="anthropic/claude-sonnet-4-5",
         why="Previous default. Still fine.", cost="mid", rank=40),
    _row("or-opus-5", "Claude Opus 5", group="also",
         provider="openrouter", model="anthropic/claude-opus-5",
         why="Opus 5 on OpenRouter.", cost="high", rank=11),
    _row("or-gpt-4o", "GPT-4o", group="also",
         provider="openrouter", model="openai/gpt-4o",
         why="Older GPT. Kept for keys that only have this id.", cost="mid", rank=60),
    _row("or-claude-opus", "Claude Opus 4", group="also",
         provider="openrouter", model="anthropic/claude-opus-4",
         why="Legacy Opus 4 slug.", cost="high", rank=70),
    _row("at-gpt-55", "GPT-5.5 (AllToken)", group="also", auto=True,
         provider="alltoken", model="gpt-5.5",
         why="Flagship GPT on AllToken.", cost="high", rank=25),
    _row("at-opus-48", "Claude Opus 4.8 (AllToken)", group="also", auto=True,
         provider="alltoken", model="claude-opus-4-8",
         why="Opus 4.8 on AllToken.", cost="high", rank=26),
    _row("at-kimi-k26", "Kimi K2.6 (AllToken)", group="also", auto=True,
         provider="alltoken", model="kimi-k2.6",
         why="If AllToken has no K3 slug yet.", cost="mid", rank=35),
    _row("novita-qwen-coder", "Qwen3 Coder 480B", group="also", auto=True,
         provider="novita", model="qwen/qwen3-coder-480b-a35b-instruct",
         why="Huge coding model when only NOVITA_API_KEY is set.", cost="mid", rank=50),
    _row("novita-qwen-235", "Qwen3 235B", group="also",
         provider="novita", model="qwen/qwen3-235b-a22b-instruct-2507",
         why="Large general model on Novita.", cost="mid", rank=55),
    _row("novita-kimi-k2", "Kimi K2 (Novita)", group="also", auto=True,
         provider="novita", model="moonshotai/kimi-k2-instruct",
         why="Agent/coding Kimi on Novita.", cost="mid", rank=45),
    _row("openai-gpt-4o", "GPT-4o (OpenAI)", group="also",
         provider="openai", model="gpt-4o",
         why="Direct OpenAI key, not OpenRouter.", cost="mid", rank=65),
    _row("nvidia-mistral-large", "Mistral Large (NVIDIA)", group="also", auto=True,
         provider="nvidia", model="mistralai/mistral-large-3-675b-instruct-2512",
         why="If you already have NVIDIA_API_KEY.", cost="mid", rank=80),
    _row("brain", "Studio Brain", group="choice",
         why="Use whatever model is selected in Studio → Brain.", cost="varies", needs_key=False),
    _row("rules", "Rules only (no LLM)", group="choice",
         why="Deterministic inspect helm. Zero LLM spend.", cost="none", needs_key=False),
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


def llm_timeout_s() -> float:
    raw = (os.environ.get("DAEDALUS_MENTOR_TIMEOUT") or "").strip()
    if raw:
        try:
            return max(MENTOR_TIMEOUT_MIN_S, min(MENTOR_TIMEOUT_MAX_S, float(raw)))
        except ValueError:
            pass
    return LLM_TIMEOUT_S


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


def day_cap_env_locked() -> bool:
    raw = (os.environ.get("DAEDALUS_MENTOR_DAY_CAP") or "").strip()
    if not raw:
        return False
    try:
        int(raw)
    except ValueError:
        return False
    return True


def choice_env_locked() -> bool:
    return bool((os.environ.get("DAEDALUS_MENTOR_MODEL") or "").strip())


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


def _publish(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["available"] = _entry_available(row)
    warn = thinking_warn(str(row.get("model") or ""))
    if warn:
        item["warn"] = warn
    return item


def preset_extras() -> list[dict[str, Any]]:
    """Brain preset list minus ids already pinned — for the custom picker."""
    seen: set[tuple[str, str]] = set()
    for row in CATALOG:
        if row.get("provider") and row.get("model"):
            seen.add((str(row["provider"]), str(row["model"])))
    out: list[dict[str, Any]] = []
    try:
        presets = llm_config.list_preset_models()
    except Exception:
        return []
    for m in presets:
        provider = str(m.get("provider") or "").strip()
        model = str(m.get("id") or "").strip()
        if not provider or not model:
            continue
        if (provider, model) in seen:
            continue
        if m.get("tier") == "cheap":
            continue
        if provider not in _EXTRA_PROVIDERS:
            continue
        if ":batch" in model or model.startswith("~"):
            continue
        row = _row(
            f"p:{provider}:{model}",
            str(m.get("label") or model),
            group="catalog",
            provider=provider,
            model=model,
            why="From the Studio Brain model list.",
            cost="high" if m.get("tier") == "power" else "mid",
            rank=800,
        )
        out.append(row)
        seen.add((provider, model))
    return out


def all_choice_rows() -> list[dict[str, Any]]:
    return list(CATALOG) + preset_extras()


def find_row(cid: str) -> dict[str, Any] | None:
    if not cid:
        return None
    for row in all_choice_rows():
        if row["id"] == cid:
            return row
    if cid.startswith("p:") and cid.count(":") >= 2:
        provider, model = cid[2:].split(":", 1)
        if provider and model:
            return _row(cid, f"{provider}/{model}", group="catalog",
                        provider=provider, model=model, why="Custom from Brain list.")
    return None


def catalog_public() -> list[dict[str, Any]]:
    rows = sorted(
        CATALOG,
        key=lambda r: (0 if r.get("group") == "recommended" else 1, r.get("rank", 100), r["id"]),
    )
    return [_publish(r) for r in rows]


def recommend() -> dict[str, Any] | None:
    """Strong mid-cost director whose key is set. Flagships stay a manual pick."""
    cands = [r for r in CATALOG if r.get("auto") and _entry_available(r)]
    if not cands:
        cands = [r for r in CATALOG if r.get("recommended") and r.get("needs_key") and _entry_available(r)]
    cands.sort(key=lambda r: (_COST_ORDER.get(str(r.get("cost") or "mid"), 2), int(r.get("rank") or 100)))
    return cands[0] if cands else None


def thinking_warn(model: str) -> str | None:
    ml = (model or "").lower()
    if "glm-5.3" in ml and "flash" not in ml:
        return "GLM-5.3 keeps hidden reasoning on; Flash is cheaper for Mentor JSON."
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
    try:
        from .run_budget import applied_mentor
        bud = applied_mentor()
    except Exception:
        bud = None
    if isinstance(bud, dict) and bud.get("choice_id"):
        cid = str(bud.get("choice_id") or "")
        if cid == "rules":
            return {
                "id": "rules",
                "label": "Rules only (no LLM)",
                "provider": "",
                "model": "",
                "source": "run-budget",
            }
        row = find_row(cid)
        if row:
            out = {
                "id": row["id"],
                "label": row["label"],
                "provider": row.get("provider") or bud.get("provider") or "",
                "model": row.get("model") or bud.get("model") or "",
                "source": "run-budget",
            }
            warn = thinking_warn(str(out.get("model") or ""))
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
        row = find_row(cid)
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
    if flags.get("needs_npm") and nxt == "run_command":
        return True
    if flags.get("mount_mismatch") and nxt == "write_file":
        return True
    if flags.get("missing_scenes") and nxt == "write_file":
        return True
    if flags.get("missing_imports") and nxt == "write_file":
        return True
    if flags.get("phaser_no_import") and nxt == "write_file":
        return True
    if flags.get("phaser_auto_canvas") and nxt == "write_file":
        return True
    if flags.get("play_stub") and nxt == "write_file":
        return True
    if flags.get("play_incomplete") and nxt == "write_file":
        return True
    if flags.get("canvas_game_trap") and nxt == "write_file":
        return True
    if flags.get("play_bot_failed") and nxt == "write_file":
        return True
    if flags.get("scenario_missing") and nxt == "write_file":
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
        "playbook": inspect.get("playbook"),
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
    graph = inspect.get("graph")
    if isinstance(graph, dict) and graph:
        body["graph"] = graph
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
    obs_raw = data.get("observations")
    observations = [str(x).strip()[:160] for x in obs_raw if str(x).strip()][:3] if isinstance(obs_raw, list) else []
    return {
        "ok": True,
        "action": action,
        "next_tool": nxt,
        "next_args": args,
        "reason": reason,
        "forbid_tools": forbid,
        "blockers": blockers,
        "observations": observations,
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
    try:
        from .run_budget import would_block
        if would_block():
            return "run_budget"
    except Exception:
        pass
    if turn_calls >= TURN_LLM_CAP:
        return "turn_cap"
    if rules_unambiguous(inspect, rules_steer):
        return "rules_unambiguous"
    if _resolve_endpoint() is None:
        return "no_key"
    return None


def _director_route(provider: str, model: str) -> Any:
    from .harness import gateway as gw
    from .llm_config import PROVIDER_DEFAULTS, resolve_provider_api_key
    from .local_llm import dummy_api_key, is_local_provider

    # "brain" means the Studio brain: use its configured endpoint (the old code used the provider default
    # URL even then). Any other choice is an explicit provider/model with that provider's default endpoint.
    if get_choice().get("id") == "brain":
        cfg = dict(llm_config.get_config())
        cfg.update({"provider": provider, "model": model})
        return cfg
    pd = PROVIDER_DEFAULTS.get(provider) or PROVIDER_DEFAULTS["openai"]
    key = resolve_provider_api_key(provider) or ""
    if not key and is_local_provider(provider):
        key = dummy_api_key(provider)
    return gw.route_for(provider, model, key, str(pd.get("base_url") or ""), str(pd.get("sdk") or "openai"))


async def _complete_director_gateway(provider: str, model: str, user: str,
                                     frames: list[dict[str, Any]] | None = None) -> str:
    from .harness import gateway as gw

    route = _director_route(provider, model)
    content: Any = user
    if frames:  # real image parts for the mentor (A5c); never summarised into text
        content = [gw.text_part(user), *frames]
    result = await gw.call("mentor", [{"role": "user", "content": content}],
                           system=SYSTEM_PROMPT + (FRAMES_ADDENDUM if frames else ""),
                           max_tokens=max_out_tokens(), temperature=0.1, json_mode=True,
                           route=route, timeout=llm_timeout_s(), use_cache=False, client_id="daedalus/mentor")
    if not result.ok:
        raise gw.GatewayCallError(result.error or gw.GatewayError("provider_error", "mentor call failed"))
    return result.text


async def _complete_director(provider: str, model: str, user: str,
                             frames: list[dict[str, Any]] | None = None) -> str:
    """One-shot JSON call on the chosen provider — does not mutate Studio Brain.

    Goes through the harness gateway; ``DAEDALUS_GATEWAY_MENTOR=0`` restores the original code below.
    """
    from .harness import gateway as _gw
    if _gw.path_enabled("mentor"):
        return await _complete_director_gateway(provider, model, user, frames)
    from .llm_config import (
        PROVIDER_DEFAULTS,
        openai_compat_extra_body,
        openai_compat_headers,
        resolve_provider_api_key,
    )
    from .local_llm import dummy_api_key, is_local_provider

    pd = PROVIDER_DEFAULTS.get(provider) or PROVIDER_DEFAULTS["openai"]
    api_key = resolve_provider_api_key(provider) or ""
    if not api_key and is_local_provider(provider):
        api_key = dummy_api_key(provider)
    if not api_key:
        raise RuntimeError(f"no API key for {provider}")
    base_url = str(pd.get("base_url") or "")
    sdk = str(pd.get("sdk") or "openai")
    timeout = llm_timeout_s()

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
        text = "".join(getattr(b, "text", "") or "" for b in (resp.content or []))
        try:
            from .llm_cost import parse_usage
            from . import run_budget
            run_budget.note_usage(
                parse_usage(getattr(resp, "usage", None), resp),
                model=model,
                role="mentor",
            )
        except Exception:
            log.debug("mentor helm spend meter skipped", exc_info=True)
        return text

    from openai import AsyncOpenAI

    extra = openai_compat_headers(provider)
    extra_body = openai_compat_extra_body(provider, with_tools=False)
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
    if extra_body:
        body["extra_body"] = extra_body
    try:
        body["response_format"] = {"type": "json_object"}
        resp = await client.chat.completions.create(**body)
    except Exception:
        body.pop("response_format", None)
        resp = await client.chat.completions.create(**body)
    try:
        from .llm_cost import parse_usage
        from . import run_budget
        run_budget.note_usage(
            parse_usage(getattr(resp, "usage", None), resp),
            model=model,
            role="mentor",
        )
    except Exception:
        log.debug("mentor helm spend meter skipped", exc_info=True)
    return (resp.choices[0].message.content if resp.choices else "") or ""


async def probe_vision(frame_path: Any, label: str) -> dict[str, Any]:
    """Ask the CURRENT mentor model to read a hidden label from a frame we rendered; stores the verdict.

    This is the only thing that moves a model from "frames sent" to "vision verified" (spec L5)."""
    from .harness import gateway as gw
    from .harness import perception

    endpoint = _resolve_endpoint()
    if endpoint is None:
        return {"ok": False, "perceived": False, "error": "no_mentor_model"}
    provider, model = endpoint
    route = _director_route(provider, model)

    async def ask(prompt: str, parts: list[dict[str, Any]]) -> str:
        res = await gw.call("mentor", [{"role": "user", "content": [gw.text_part(prompt), *parts]}],
                            max_tokens=60, temperature=0.0, json_mode=True, route=route,
                            timeout=llm_timeout_s(), use_cache=False, client_id="daedalus/mentor-probe")
        if not res.ok:
            raise gw.GatewayCallError(res.error or gw.GatewayError("provider_error", "probe call failed"))
        return res.text

    out = await perception.vision_probe(ask, frame_path, label)
    out.update(provider=provider, model=model)
    out["recorded"] = perception.record_probe(provider, model, out)
    return out


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
    frames: Any = None,
) -> dict[str, Any]:
    """Ask the director. Always returns a steer dict; `source` is llm|rules.

    ``frames`` is a ``harness.perception.FrameSet``: when given, its frames go to the model as real image parts and
    the steer carries a ``perception`` block saying exactly what happened (image | text-only and why).
    """
    why = skip_reason(inspect, rules_steer, turn_calls=turn_calls)
    if why:
        log.info("mentor helm skip: %s", why)
        from .mentor_graph import guard_steer
        return guard_steer(_rules_fallback(rules_steer, why), inspect, rules_steer)
    endpoint = _resolve_endpoint()
    if endpoint is None:
        from .mentor_graph import guard_steer
        return guard_steer(_rules_fallback(rules_steer, "no_key"), inspect, rules_steer)
    provider, model = endpoint
    user = build_user_prompt(inspect)
    in_tok = estimate_tokens(SYSTEM_PROMPT) + estimate_tokens(user)
    from .harness import gateway as _gw0
    from .harness import perception as _perc
    if callable(frames):                           # collected only now (the LLM will run), off the event loop
        try:
            frames = await asyncio.to_thread(frames)
        except Exception:
            log.debug("mentor frames unavailable", exc_info=True)
            frames = None
    parts: list[dict[str, Any]] = []
    p_mode, p_reason = "text-only", ("no_frames" if not frames else "")
    if frames and _perc.known_no_vision(provider, model):
        p_reason = "model_has_no_vision"           # it refused images before: do not pay for the failed attempt
    elif frames:
        if not _gw0.path_enabled("mentor"):
            p_reason = "gateway_disabled"          # the legacy path cannot carry images
        else:
            parts = frames.parts()
            p_mode, p_reason = ("image", "") if parts else ("text-only", "frames_unavailable")
    t0 = time.monotonic()
    try:
        try:
            director = (_complete_director(provider, model, user, parts) if parts
                        else _complete_director(provider, model, user))   # frame-less call keeps the old signature
            text = await asyncio.wait_for(director, timeout=llm_timeout_s() + 1.0)
        except Exception as exc:
            err = getattr(exc, "error", None)
            if parts and getattr(err, "type", "") == "unsupported_modality":
                # The model cannot take images: ask again as text and SAY so (never pretend it looked).
                log.info("mentor helm: %s/%s has no vision; retrying text-only", provider, model)
                parts, p_mode, p_reason = [], "text-only", "model_has_no_vision"
                _perc.note_no_vision(provider, model)
                text = await asyncio.wait_for(
                    _complete_director(provider, model, user), timeout=llm_timeout_s() + 1.0)
            else:
                raise
    except Exception as exc:
        log.info("mentor helm LLM failed: %s: %s", type(exc).__name__, exc or "(empty)")
        from .mentor_graph import guard_steer
        return guard_steer(_rules_fallback(rules_steer, "llm_error"), inspect, rules_steer)
    in_tok += 800 * len(parts)                     # rough image cost for the daily ledger
    parsed = parse_director_json(text or "")
    out_tok = estimate_tokens(text or "")
    record_spend(in_tok + out_tok)
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    if not parsed:
        log.info("mentor helm LLM bad_json model=%s ms=%s", model, elapsed_ms)
        from .mentor_graph import guard_steer
        return guard_steer(
            _rules_fallback(rules_steer, "bad_json", llm_ms=elapsed_ms),
            inspect,
            rules_steer,
        )
    merged_forbid = list(dict.fromkeys(
        list(parsed.get("forbid_tools") or []) + list(rules_steer.get("forbid_tools") or [])
    ))
    parsed["forbid_tools"] = merged_forbid
    parsed["llm_ms"] = elapsed_ms
    parsed["llm_model"] = model
    parsed["llm_provider"] = provider
    parsed["llm_tokens"] = in_tok + out_tok
    parsed["perception"] = _perc.describe(frames, mode=p_mode, reason=p_reason,
                                          provider=provider, model=model)
    if p_mode != "image":
        parsed["observations"] = []                # no picture was sent: anything it "observed" is not observation
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
    from .mentor_graph import guard_steer
    parsed = guard_steer(parsed, inspect, rules_steer)
    log.info(
        "mentor helm LLM ok model=%s action=%s next=%s ms=%s tokens=%s",
        model,
        parsed.get("action"),
        parsed.get("next_tool"),
        elapsed_ms,
        parsed.get("llm_tokens"),
    )
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
        "presets": [_publish(r) for r in preset_extras()],
        "spend": {**spend, "cap": day_token_cap()},
        "max_out": max_out_tokens(),
        "max_in": max_in_tokens(),
        "turn_calls_cap": TURN_LLM_CAP,
        "timeout_s": llm_timeout_s(),
    }
