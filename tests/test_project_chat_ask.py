"""Project chat: a question is answered, not built.

Found live (2026-11-11): every project-chat message went through the build loop, whose review gate wants a
"completed slice", so a read-only question ("what does the game do?") still rewrote index.html. Owner decision
(2026-11-11): when the message does not ask for a change, skip the build and refuse every write for that turn.
"""
from __future__ import annotations

import asyncio

import pytest
from a0_support import a0_tmp  # noqa: F401


@pytest.mark.parametrize("message", [
    "make it red",
    "Add a pause menu",
    "fix the jump, it is too floaty",
    "Можеш ли да добавиш звук?",
    "Направи врага по-бърз",
    "оправи колизията",
    "please continue",
    "продължи",
    "Could you change the colours to blue?",
    "Пренапиши играта с Phaser",
])
def test_change_requests_build(message):
    from daedalus import project_chat_intent as pci

    assert pci.wants_change(message) is True


@pytest.mark.parametrize("message", [
    "What does the game do?",
    "Какво прави тази игра?",
    "Защо екранът е черен?",
    "how do I add sound?",
    "Как да добавя звук?",
    "Кой файл стартира играта?",
    "Look at the project and tell me what you think",
    "Разгледай проекта и ми кажи какво мислиш",
    "здравей",
    "",
])
def test_questions_do_not_build(message):
    from daedalus import project_chat_intent as pci

    assert pci.wants_change(message) is False


@pytest.fixture
def project(a0_tmp):  # noqa: F811
    from daedalus import playground as pg

    slug = asyncio.run(pg.create_project("Ask Me", kind="static"))["slug"]
    pg.write_file(slug, "index.html", "<h1>old</h1>\n")
    return slug


def test_every_playground_write_is_refused_while_answering(project):
    from daedalus import playground as pg
    from daedalus import project_chat_intent as pci

    with pci.answering(project):
        for write in (
            lambda: pg.write_file(project, "index.html", "<h1>new</h1>\n"),
            lambda: pg.write_files(project, {"x.js": "1"}),
            lambda: pg.write_bytes(project, "a.png", b"png"),
            lambda: pg.edit_file(project, "index.html", "old", "new"),
        ):
            with pytest.raises(pci.ReadOnlyTurn):
                write()
    assert pg.read_file(project, "index.html") == "<h1>old</h1>\n"
    pg.write_file(project, "index.html", "<h1>after</h1>\n")          # outside the turn writes work again
    assert pg.read_file(project, "index.html") == "<h1>after</h1>\n"


def test_only_read_tools_run_while_answering(project):
    from daedalus import native_tools as nt
    from daedalus import project_chat_intent as pci

    with pci.answering(project):
        refused = [(n, {}) for n in ("write_file", "edit_file", "playground_new", "run_command", "generate_image",
                                     "assets", "ship_request", "play", "spawn_subagent")]
        refused += [("preview", {"action": "run"}), ("preview", {"action": "stop"}),
                    ("milestones", {"action": "advance"}), ("milestones", {})]
        for name, args in refused:
            ok, why = nt.before_tool_call(name, args=args)
            assert ok is False and "question" in why.lower(), (name, args)
        for name, args in (("read_file", {}), ("list_dir", {}), ("milestones", {"action": "state"}),
                           ("runs", {"action": "list"}), ("preview", {"action": "status"})):
            assert nt.before_tool_call(name, args=args)[0] is True, (name, args)
    specs = {s["name"] for s in pci.read_only_specs()}
    assert specs and specs <= pci.READ_TOOLS and "write_file" not in specs


class Agent:
    def __init__(self, write=None):
        self.write, self.calls, self.mode = write, [], None

    async def chat(self, message, session_id="", **kw):
        from daedalus import project_chat_intent as pci

        self.calls.append((message, session_id))
        self.mode = pci.current()
        if self.write:
            try:
                self.write()
            except pci.ReadOnlyTurn:
                pass
        return {"reply": "answer"}


def _run(slug, agent, monkeypatch, message):
    from daedalus import chat_jobs

    monkeypatch.setattr("daedalus.agent.get_daedalus", lambda: agent)

    async def go():
        started = await chat_jobs.start_chat(message=message, session_id="studio-x", project=slug)
        await chat_jobs._jobs[started["job_id"]]["task"]
        return chat_jobs.get_job(started["job_id"])

    return asyncio.run(go())


def test_a_question_in_project_chat_changes_nothing(project, monkeypatch):
    from daedalus import playground as pg

    agent = Agent(write=lambda: pg.write_file(project, "index.html", "<h1>rewritten</h1>\n"))
    job = _run(project, agent, monkeypatch, "What does the game do?")
    assert agent.mode == project and job["mode"] == "ask" and job["changes"] == []
    assert pg.read_file(project, "index.html") == "<h1>old</h1>\n"
    assert "ANSWER ONLY" in agent.calls[0][0]


def test_a_change_request_in_project_chat_still_builds(project, monkeypatch):
    from daedalus import playground as pg

    agent = Agent(write=lambda: pg.write_file(project, "index.html", "<h1>red</h1>\n"))
    job = _run(project, agent, monkeypatch, "make it red")
    assert agent.mode == "" and job["mode"] == "build"
    assert [c["path"] for c in job["changes"]] == ["index.html"]


def test_agent_skips_the_build_loop_for_a_question(project, monkeypatch):
    """The agent's build branch is chosen by one helper; in an answering turn it says no."""
    from daedalus import project_chat_intent as pci

    assert pci.build_allowed() is True
    with pci.answering(project):
        assert pci.build_allowed() is False
