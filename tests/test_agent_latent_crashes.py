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


def test_a_finished_task_leaves_no_resume_note(a0_tmp):  # noqa: F811
    """Review 2026-11-12: at 100% the note stayed for 72 h on every later message of the session."""
    from daedalus.agent import Daedalus

    Daedalus._phase_save("sess-2", 0.5, "level 3", "half")
    Daedalus._phase_save("sess-2", 1.0, "level 3", "done")
    assert Daedalus._phase_load("sess-2") is None


def test_the_breaker_counts_one_turn_and_the_tracker_stays_small():
    from daedalus.improvements import ErrorTracker

    t = ErrorTracker()
    assert t.should_break("turn-a", "read_file", "missing x", threshold=2) is False
    assert t.should_break("turn-b", "read_file", "missing x", threshold=2) is False   # another turn: no carry-over
    for i in range(ErrorTracker.MAX_KEYS + 50):
        t.track_call(f"turn-{i}", "t")
    assert len(t._tool_counts) <= ErrorTracker.MAX_KEYS + 1
    src = AGENT.read_text(encoding="utf-8")
    assert "tool_calls_used[-len(tool_results_text_parts):]" not in src and "_breaker_key" in src


def test_reflection_is_opt_in():
    """Revived by the asyncio fix, it is one more paid model call per turn and writes memory: the owner switches it on."""
    src = AGENT.read_text(encoding="utf-8")
    assert 'os.environ.get("DAEDALUS_REFLECTION")' in src and "elif not _reflect:" in src


def test_a_missing_project_is_never_a_clean_scan(a0_tmp):  # noqa: F811
    from daedalus.game_engineer import scan_project

    out = scan_project("no-such-project")
    assert out["ok"] is False and out["scanned"] == 0 and "not found" in out["error"]
