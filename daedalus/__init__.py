"""Daedalus — independent coding agent harness (games-first).

Canonical package for the Daedalus product. ArgosDataNexus may still mount
the admin UI and LLM gateway; this package is the agent itself.

Eager imports of ``agent`` are avoided so lightweight modules (paths, kernel,
loop guard) can load without Chroma/LLM.
"""
from __future__ import annotations

from typing import Any

__all__ = ["Daedalus", "get_daedalus", "DaedalusMemory", "DaedalusKnowledge"]


def __getattr__(name: str) -> Any:
    if name in ("Daedalus", "get_daedalus"):
        from .agent import Daedalus, get_daedalus
        return Daedalus if name == "Daedalus" else get_daedalus
    if name == "DaedalusMemory":
        from .memory import DaedalusMemory
        return DaedalusMemory
    if name == "DaedalusKnowledge":
        from .knowledge import DaedalusKnowledge
        return DaedalusKnowledge
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
