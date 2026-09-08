"""Playground / Navigator must not strip native game tools from build turns."""
from __future__ import annotations

from daedalus.navigator import _fast_path, _unavailable_briefing
from daedalus.playground_agent_routing import (
    is_playground_work_session,
    treat_as_conversational,
)


SNAKE = (
    "Направи нова Playground игра snake: HTML canvas, стрелки, расте при ябълка, "
    "game over при стена/опашка. Една завършена среза."
)
SLERP = (
    "Направи нова Playground 3D demo: камера с quaternion slerp към цел и look-at."
)


def test_playground_session_is_work():
    assert is_playground_work_session("playground:eval-snake", SNAKE)
    assert not is_playground_work_session("owner-chat", "здравей")


def test_build_or_playground_keeps_native_tools_even_if_nav_says_chat():
    assert treat_as_conversational(
        SNAKE, nav_intent="chat", block_chat=True
    ) is False
    assert treat_as_conversational(
        "здравей, как си днес?", nav_intent="chat", block_chat=False
    ) is True


def test_fast_path_classifies_napravi_playground_as_code_change():
    b = _fast_path(SNAKE)
    assert b is not None
    assert b.intent == "code_change"
    assert "NEED_PROPOSE" not in (b.tools or [])


def test_unavailable_navigator_does_not_mark_game_build_as_chat():
    b = _unavailable_briefing(SLERP)
    assert b.intent == "code_change"
    hi = _unavailable_briefing("здравей")
    assert hi.intent == "chat"


def test_playground_build_specs_exclude_harness_search():
    from daedalus.native_tools import playground_build_specs

    names = {t["name"] for t in playground_build_specs()}
    assert "playground_new" in names
    assert "math3d" in names
    assert "spatial" in names
    assert "game_canon" in names
    assert "write_file" in names
    assert "code_search" not in names
    assert "postgres_query" not in names


def test_looks_like_game_covers_playground_demo():
    from daedalus.game_engineer import looks_like_game, prompt_block

    assert looks_like_game(SLERP)
    assert looks_like_game(SNAKE)
    assert "math3d" in prompt_block(SLERP)


def test_json_tool_dumps_execute_shape():
    from daedalus.tool_dumps import extract_tool_dumps, strip_tool_dumps

    raw = (
        'Ще викна math3d.\n'
        '{"tool": "math3d", "arguments": {"op": "quat_slerp", "t": 0.5, "claimed": [0, 0.38, 0, 0.92]}}\n'
        '{"tool": "playground_new", "arguments": {"name": "slerp-cam", "type": "static"}}\n'
    )
    dumps = extract_tool_dumps(raw)
    assert [d.name for d in dumps] == ["math3d", "playground_new"]
    assert dumps[0].args["op"] == "quat_slerp"
    assert dumps[1].args["kind"] == "static"
    stripped = strip_tool_dumps(raw, dumps)
    assert "math3d" not in stripped or "викна" in stripped


def test_xml_tool_dump():
    from daedalus.tool_dumps import extract_tool_dumps

    raw = "<tool_call>run_command<arg_key>command</arg_key><arg_value>ls</arg_value></tool_call>"
    dumps = extract_tool_dumps(raw)
    assert dumps[0].name == "run_command"
    assert dumps[0].args["command"] == "ls"


def test_job_contract_static_write_without_run():
    from daedalus.job_contract import evaluate

    row = evaluate(
        SNAKE,
        "Готово.",
        tool_calls=[{"tool": "write_file", "ok": True}],
        kind="static",
    )
    assert row.checks["wrote_files"] is True
    assert row.checks["verified"] is True
    assert row.complete is True


def test_dumped_math3d_executes():
    import asyncio
    from daedalus.native_tools import execute_native_tool
    from daedalus.tool_dumps import extract_tool_dumps

    raw = '{"tool": "math3d", "arguments": {"op": "catalog"}}'
    dump = extract_tool_dumps(raw)[0]

    async def _run():
        return await execute_native_tool(
            dump.name, dump.args,
            memory=None, session_id="playground:eval",
            read_file_fn=lambda *a, **k: {"ok": False, "error": "n/a"},
        )

    text = asyncio.run(_run())
    assert "math3d" in text.lower()
    assert "FAILED" not in text and "ERROR" not in text


def test_job_contract_policy_blocked_run_is_not_failure():
    from daedalus.job_contract import evaluate

    row = evaluate(
        SNAKE,
        "Написах index.html.",
        tool_calls=[
            {"tool": "write_file", "ok": True},
            {"tool": "playground_run", "ok": True, "blocked": True},
        ],
        kind="static",
    )
    assert row.checks["no_failed_tools"] is True
    failed = evaluate(
        SNAKE,
        "oops",
        tool_calls=[{"tool": "write_file", "ok": False}],
        kind="static",
    )
    assert failed.checks["no_failed_tools"] is False
