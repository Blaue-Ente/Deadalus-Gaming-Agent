"""Single control point for live LLM profile (master + worker slots).

Writes to adn:daedalus_llm_config / worker config / active_mix go through
apply_profile() while router_write_context() is active. Keys, Ollama wardrobe,
and mode live in Свързаност (`/admin/daedalus/connectors`). The old LLM Router
page is a status view of the same brain.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_LLM_WRITE_ALLOWED: ContextVar[bool] = ContextVar("llm_write_allowed", default=False)

# HTTP paths that may mutate the live LLM profile (middleware enables write context).
ROUTER_WRITE_PATH_PREFIXES = (
    "/admin/llm-router",
    "/admin/daedalus/connectors",
    "/api/v1/llm",
)
ROUTER_WRITE_PATH_EXACT = frozenset({
    "/admin/daedalus/mix/apply",
    "/admin/daedalus/mix/test",
})
ROUTER_WRITE_PATH_PATTERNS = (
    re.compile(r"^/admin/daedalus/llm-presets/[^/]+/apply$"),
    re.compile(r"^/admin/daedalus/worker-named-configs/[^/]+/apply$"),
)

CORE_PROFILE_SLOTS = ("master", "fast", "balanced", "deep")


class LlmConfigWriteForbidden(RuntimeError):
    """Raised when a write targets live LLM config outside LLM Router."""


def is_router_write_path(path: str, method: str) -> bool:
    if method.upper() not in ("POST", "PUT", "DELETE", "PATCH"):
        return False
    if path in ROUTER_WRITE_PATH_EXACT:
        return True
    if any(path.startswith(prefix) for prefix in ROUTER_WRITE_PATH_PREFIXES):
        return True
    return any(pat.match(path) for pat in ROUTER_WRITE_PATH_PATTERNS)


@contextmanager
def router_write_context() -> Iterator[None]:
    token = _LLM_WRITE_ALLOWED.set(True)
    try:
        yield
    finally:
        _LLM_WRITE_ALLOWED.reset(token)


def llm_writes_allowed() -> bool:
    return _LLM_WRITE_ALLOWED.get()


def require_llm_write_permission() -> None:
    if not llm_writes_allowed():
        raise LlmConfigWriteForbidden(
            "Live LLM конфигурацията се променя от Свързаност "
            "(/admin/daedalus/connectors#brain) — Ollama, ключове и облачен provider."
        )


def get_llm_profile() -> dict:
    """Current effective master + core worker slots + active mix metadata."""
    from .llm_config import describe_effective_config, get_config, persona_via_master_enabled
    from .workers import WORKER_SLOTS, get_worker_config
    from . import model_registry as mr

    try:
        from argos_data_nexus.modules.mesh_nvidia_free import free_only_mode_get
        nvidia_free_only = free_only_mode_get()
    except Exception:
        nvidia_free_only = False

    try:
        master = get_config()
    except Exception as exc:
        master = {"error": str(exc)}
    workers = get_worker_config()
    slots: dict[str, dict] = {
        "master": {
            "provider": master.get("provider", ""),
            "model": master.get("model", ""),
            "source": master.get("source", ""),
        },
    }
    for slot in CORE_PROFILE_SLOTS:
        if slot == "master":
            continue
        sc = workers.get(slot) or {}
        if sc.get("model"):
            slots[slot] = {
                "provider": sc.get("provider", ""),
                "model": sc.get("model", ""),
                "enabled": sc.get("enabled", True),
                "source": sc.get("source", ""),
            }
    extra_workers = {}
    for slot, meta in WORKER_SLOTS.items():
        if slot in slots or slot in ("auto",):
            continue
        sc = workers.get(slot) or {}
        if sc.get("model"):
            extra_workers[slot] = {
                "provider": sc.get("provider", ""),
                "model": sc.get("model", ""),
                "label": meta.get("label", slot),
            }
    diag = describe_effective_config()
    return {
        "slots": slots,
        "extra_workers": extra_workers,
        "active_mix": mr.get_active_mix(),
        "auto_mode": mr.get_auto_mode(),
        "nvidia_free_only": nvidia_free_only,
        "persona_via_master": persona_via_master_enabled(),
        "diagnostics": diag,
        "router_only_writes": True,
    }


def apply_profile(
    slots: dict[str, dict],
    *,
    source_id: str = "",
    source_label: str = "",
    source_kind: str = "mix",
    master_api_key: str = "",
    persona_via_master: bool | None = None,
) -> dict:
    """Atomically apply master + worker slots. Must run inside router_write_context."""
    require_llm_write_permission()
    from .llm_presets import apply_mix_config

    return apply_mix_config(
        slots,
        source_id=source_id,
        source_label=source_label,
        source_kind=source_kind,
        master_api_key=master_api_key,
        persona_via_master=persona_via_master,
    )


def clear_profile() -> None:
    """Reset master + workers + active mix. Router-only."""
    require_llm_write_permission()
    from .llm_config import clear_config
    from .workers import clear_worker_config
    from . import model_registry as mr

    clear_config()
    clear_worker_config()
    mr.clear_active_mix()
