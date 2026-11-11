"""Playbook keywords match whole words, not substrings. Found in a live project-chat turn (2026-11-11): "Please ...,
look at its runs" routed to the 3D-math and mobile BUILD playbooks ("eas" in "please", "look at" in plain English),
and the model then built a new camera demo project instead of reading the logs it was asked for."""
from __future__ import annotations

import pytest


@pytest.mark.parametrize("text, banned", [
    ("Please check this", "playground.mobile"),             # "eas" inside "please"
    ("show me the scenarios", "playground.mobile"),         # "ios" inside "scenarios"
    ("what is the purpose of this?", "playground.unity"),   # "urp" inside "purpose"
    ("I have a request about the logs", "playground.epic"),  # "quest" inside "request"
    ("solve this equation for me", "playground.math3d"),     # "quat" inside "equation"
    ("look at its runs, please", "playground.math3d"),       # plain English "look at"
])
def test_a_word_inside_another_word_does_not_pick_a_build_playbook(text, banned):
    from daedalus.tool_router import match_playbook

    assert banned not in match_playbook(text).id.split("+"), text


@pytest.mark.parametrize("text, wanted", [
    ("build an iOS game with Expo", "playground.mobile"),
    ("ship it with EAS build", "playground.mobile"),
    ("make the camera look-at the player with slerp", "playground.math3d"),
    ("use quaternion rotation", "playground.math3d"),
    ("камерата да следи играча", "playground.math3d"),      # a Cyrillic stem still matches the start of a word
    ("Unity URP project", "playground.unity"),
    ("an epic quest with dialogue", "playground.epic"),
    ("write it in levels/ folder", "playground.epic"),        # a keyword that ends in a symbol
    ("a godot .tscn scene", "playground.godot"),
])
def test_real_keywords_still_pick_their_playbook(text, wanted):
    from daedalus.tool_router import match_playbook

    assert wanted in match_playbook(text).id.split("+"), (text, match_playbook(text).id)
