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


def test_unavailable_navigator_does_not_mark_game_build_as_chat():
    b = _unavailable_briefing(SLERP)
    assert b.intent == "code_change"
    hi = _unavailable_briefing("здравей")
    assert hi.intent == "chat"
