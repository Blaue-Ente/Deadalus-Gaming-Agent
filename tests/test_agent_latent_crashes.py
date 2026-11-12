"""Four features in agent.py never ran: an undefined name raised inside a broad except (found 2026-11-12 with ruff
F821/F823; agent.py was excluded from the CI lint). These keep them alive."""
from __future__ import annotations

import symtable
from pathlib import Path

from a0_support import a0_tmp  # noqa: F401

AGENT = Path(__file__).resolve().parents[1] / "daedalus" / "agent.py"


def _function(table: symtable.SymbolTable, name: str) -> symtable.SymbolTable | None:
    for child in table.get_children():
        if child.get_name() == name:
            return child
        if child.get_type() == "class":
            found = _function(child, name)
            if found is not None:
                return found
    return None


def test_asyncio_is_the_module_not_a_local_of_the_big_turn_function():
    """A local "import asyncio" near the end made asyncio local everywhere in it: the daily digest task at its top
    always raised UnboundLocalError."""
    table = symtable.symtable(AGENT.read_text(encoding="utf-8"), str(AGENT), "exec")
    fn = _function(table, "_build_context_and_call")
    assert fn is not None and not fn.lookup("asyncio").is_local()


def test_phase_resume_saves_and_loads(a0_tmp):  # noqa: F811
    """_phase_save referred to a class "DaedalusAgent" that does not exist: nothing was ever saved."""
    from daedalus.agent import Daedalus

    Daedalus._phase_save("sess-1", 0.4, "level 2", "map done")
    got = Daedalus._phase_load("sess-1")
    assert got and got["task_name"] == "level 2" and got["pct"] == 0.4
    Daedalus._phase_clear("sess-1")
    assert Daedalus._phase_load("sess-1") is None


def test_agent_py_has_no_undefined_names():
    import shutil
    import subprocess

    import pytest

    ruff = shutil.which("ruff")
    if not ruff:
        pytest.skip("ruff not installed")
    out = subprocess.run([ruff, "check", "--select", "F821,F823", "--output-format", "concise", str(AGENT)],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stdout
