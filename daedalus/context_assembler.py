"""Retrieve + compose front matter for Daedalus._build_context_and_call.

Leaf module. Do not import agent.py. Native tools, leftover NEED_* if-arms,
and cheap-path routing stay on Daedalus. ToolLoop is the round driver.
ContextAssembler is retrieve then compose — cheap chat returns between
those two steps.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from . import continuity as _continuity
from . import dod as _dod
from . import mode as _mode
from . import task_state as _task_state
from .context_compaction import maybe_compact_session_context
from .improvements import select_skills
from .mesh_tools import format_mesh_for_prompt, get_mesh_snapshot
from .session_branching import build_branch_context_block
from .steering_queue import pop_steering_messages

log = logging.getLogger(__name__)

FUSED_TOP_K = 14


@dataclass
class AssembleRequest:
    user_message: str
    session_id: str
    retrieval_query: str | None = None
    expect_json: bool = False
    for_self: bool = False
    persona: str = ""


@dataclass
class RetrievedContext:
    user_message: str
    query: str
    knowledge: list[dict[str, Any]] = field(default_factory=list)
    past_memory: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    session_recent: list[dict[str, Any]] = field(default_factory=list)
    lessons: list[dict[str, Any]] = field(default_factory=list)
    episodes: list[dict[str, Any]] = field(default_factory=list)
    mesh_block: str = ""
    injected: str = ""
    inbox_block: str = ""
    compaction_block: str | None = None
    briefing_block: str = ""
    briefing_dict: dict[str, Any] | None = None
    conv_mode: str = "auto"

    def retrieval_counts(self) -> dict[str, Any]:
        return {
            "knowledge_used": len(self.knowledge),
            "past_memory_used": len(self.past_memory),
            "similar_artifacts": len(self.artifacts),
            "session_turns_in_context": len(self.session_recent),
            "lessons_used": len(self.lessons),
            "episodes_used": len(self.episodes),
            "mesh_aware": bool(self.mesh_block),
        }


@dataclass
class AssembledPrompt:
    user_content: str
    system_prompt: str
    retrieval_counts: dict[str, Any]


def format_injected_context(
    knowledge: list[dict[str, Any]],
    memory: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    session_recent: list[dict[str, Any]],
    lessons: list[dict[str, Any]] | None = None,
    episodes: list[dict[str, Any]] | None = None,
    mesh_block: str = "",
) -> str:
    parts: list[str] = []

    if mesh_block:
        parts.append(mesh_block)

    if lessons:
        parts.append("\n━━━ LESSONS YOU LEARNED (self-reflection) ━━━")
        for L in lessons:
            meta = L.get("metadata", {})
            parts.append(
                f"  • [{meta.get('kind','general')}] {L.get('content','')[:240]}"
            )

    if episodes:
        parts.append("\n━━━ EPISODIC MEMORIES (past session summaries) ━━━")
        for ep in episodes:
            meta = ep.get("metadata", {})
            parts.append(
                f"\n[session={meta.get('session_id','?')[:10]} "
                f"@ {meta.get('ended_at','?')[:19]} turns={meta.get('turn_count','?')}]"
            )
            parts.append(ep.get("content", "")[:1500])

    if knowledge:
        parts.append("\n━━━ RELEVANT IYVERIS KNOWLEDGE (auto-retrieved) ━━━")
        for k in knowledge:
            meta = k.get("metadata", {})
            title = meta.get("title", "?")
            src = meta.get("source", "?")
            parts.append(f"\n[{meta.get('category','?')}] {title}  ({src})")
            parts.append(k.get("content", "")[:1200])

    if memory:
        parts.append("\n━━━ RELEVANT PAST MEMORY (cross-session) ━━━")
        for m in memory:
            meta = m.get("metadata", {})
            parts.append(
                f"\n[{meta.get('role','?')} @ {meta.get('timestamp','?')[:19]} "
                f"session={meta.get('session_id','?')[:8]}]"
            )
            parts.append(m.get("content", "")[:800])

    if artifacts:
        parts.append("\n━━━ SIMILAR PAST ARTIFACTS YOU GENERATED ━━━")
        for a in artifacts:
            meta = a.get("metadata", {})
            parts.append(
                f"\n• {meta.get('filename','?')} ({meta.get('language','?')}) — "
                f"{meta.get('summary','')[:200]}"
            )

    if session_recent:
        parts.append("\n━━━ RECENT TURNS IN THIS SESSION (chronological) ━━━")
        for t in session_recent:
            meta = t.get("metadata", {})
            parts.append(
                f"\n[{meta.get('role','?')} @ {meta.get('timestamp','?')[:19]}]"
            )
            parts.append(t.get("content", "")[:800])

    if not parts:
        return ""
    return "\n".join(parts)


def partition_fused(fused: list[Any]) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """Split mem0 fused entries into formatter buckets. Episodic is separate."""
    knowledge: list[dict[str, Any]] = []
    past_memory: list[dict[str, Any]] = []
    lessons: list[dict[str, Any]] = []
    episodic_fused: list[dict[str, Any]] = []
    for e in fused:
        row = {"id": e.id, "content": e.content, "metadata": e.metadata, "distance": None}
        if e.source == "global":
            knowledge.append(row)
        elif e.source == "lesson":
            lessons.append(row)
        elif e.source == "episodic":
            episodic_fused.append(row)
        else:  # semantic | working | hybrid
            past_memory.append(row)
    return knowledge, past_memory, lessons, episodic_fused


class ContextAssembler:
    """Memory / skill / fragment front matter. Not the chat loop."""

    def __init__(self, memory: Any, mem0: Any, *, knowledge_ready: bool = True) -> None:
        self.memory = memory
        self.mem0 = mem0
        self.knowledge_ready = knowledge_ready

    async def retrieve(self, req: AssembleRequest) -> RetrievedContext:
        """Steering → fused memory → format → prefs → inbox → navigator → mode."""
        user_message = req.user_message
        session_id = req.session_id

        _steering_block = pop_steering_messages(session_id or "")
        if _steering_block:
            user_message = _steering_block + "\n\n" + user_message

        q = req.retrieval_query or user_message
        fused = self.mem0.fused_context(session_id, q, top_k=FUSED_TOP_K)

        knowledge, past_memory, lessons, episodic_fused = partition_fused(fused)

        artifacts = self.memory.find_similar_artifacts(q, top_k=3)
        session_recent = self.memory.session_history(session_id, limit=6)
        _compaction_block: str | None = None
        try:
            session_recent, _compaction_block = maybe_compact_session_context(
                session_id, session_recent,
            )
        except Exception as _cmp_exc:
            log.debug("context compaction skipped: %s", _cmp_exc)
        episodes = self.memory.retrieve_episodes(q, top_k=2)
        if episodic_fused:
            lessons = lessons + episodic_fused

        mesh_block = ""
        try:
            mesh_block = format_mesh_for_prompt(get_mesh_snapshot())
        except Exception as exc:
            log.debug("mesh snapshot unavailable: %s", exc)

        injected = format_injected_context(
            knowledge, past_memory, artifacts, session_recent,
            lessons=lessons, episodes=episodes, mesh_block=mesh_block,
        )
        try:
            from .preferences import format_block as _pref_block
            _prefs = _pref_block()
            if _prefs:
                injected = _prefs + ("\n\n" + injected if injected else "")
        except Exception as _mem_exc:
            log.debug("preference inject skipped: %s", _mem_exc)

        inbox_block = ""
        try:
            from .planner import get_unread_inbox_for_prompt
            inbox_block = await get_unread_inbox_for_prompt()
        except Exception as exc:
            log.debug("inbox inject failed (non-fatal): %s", exc)

        briefing_block = ""
        briefing_dict: dict[str, Any] | None = None
        try:
            try:
                from .stream import set_live as _nav_set_live
                _nav_set_live(session_id, "navigator", 0,
                              "Haiku подготвя маршрут…")
            except Exception:
                pass
            from .navigator import navigate, preflight_feasibility
            briefing = await navigate(
                user_message,
                lessons=lessons,
                episodes=episodes,
                session_recent=session_recent,
            )
            try:
                _preflight_hint = preflight_feasibility(user_message, briefing)
                if _preflight_hint:
                    _merged = (
                        (briefing.hint or "").strip() + " · " + _preflight_hint
                    ).strip(" ·")
                    briefing.hint = _merged[:300]
                    log.info("preflight: session=%s flags=%s",
                             session_id[:10], _preflight_hint[:160])
            except Exception as _pf_exc:
                log.debug("preflight skipped: %s", _pf_exc)
            briefing_block = briefing.to_prompt_block()
            briefing_dict = briefing.to_dict()
        except Exception as exc:
            log.debug("navigator failed (non-fatal): %s", exc)

        try:
            _conv_mode = _mode.get_mode()
        except Exception:
            _conv_mode = "auto"

        return RetrievedContext(
            user_message=user_message,
            query=q,
            knowledge=knowledge,
            past_memory=past_memory,
            artifacts=artifacts,
            session_recent=session_recent,
            lessons=lessons,
            episodes=episodes,
            mesh_block=mesh_block,
            injected=injected,
            inbox_block=inbox_block,
            compaction_block=_compaction_block,
            briefing_block=briefing_block,
            briefing_dict=briefing_dict,
            conv_mode=_conv_mode,
        )

    async def compose(
        self,
        req: AssembleRequest,
        retrieved: RetrievedContext,
    ) -> AssembledPrompt:
        """Skills / MCP / parts / awareness / DoD / eng / task / job / system prompt."""
        user_message = retrieved.user_message
        session_id = req.session_id
        briefing_block = retrieved.briefing_block
        briefing_dict = retrieved.briefing_dict
        injected = retrieved.injected
        inbox_block = retrieved.inbox_block
        _compaction_block = retrieved.compaction_block
        _conv_mode = retrieved.conv_mode

        parts: list[str] = []
        if briefing_block:
            parts.append(briefing_block)
        try:
            from .learned_skills import disclose as _disclose
            _intent_for_skills = (briefing_dict or {}).get("intent", "") if briefing_dict else ""
            _hint_for_skills = (briefing_dict or {}).get("hint", "") if briefing_dict else ""
            _skill_names = select_skills(user_message, _intent_for_skills, _hint_for_skills)
            try:
                from .game_engineer import extra_skill_ids, prefer_skill_id
                _skill_names = list(dict.fromkeys(
                    list(_skill_names) + extra_skill_ids(
                        user_message, _intent_for_skills, _hint_for_skills,
                    )
                ))
                _prefer = prefer_skill_id(user_message, intent=_intent_for_skills)
            except Exception:
                _prefer = None
            _pin_ids: list[str] = []
            try:
                from .session_context import current as _turn_now
                _pack = _turn_now()
                if _pack:
                    _pin_ids = list(_pack.skill_ids)
            except Exception:
                _pack = None
            _skill_block = _disclose(
                user_message, extra_ids=_skill_names, prefer_id=_prefer, pin_ids=_pin_ids,
            )
            if _skill_block:
                parts.append(_skill_block)
                log.info("skills disclosed: %s pins=%s (session=%s)", _skill_names, _pin_ids, session_id[:10])
            if _pack and _pack.mcp_ids:
                from .session_context import mcp_prompt_block as _mcp_block
                _mb = _mcp_block(_pack.mcp_ids)
                if _mb:
                    parts.append(_mb)
        except Exception as _sk_exc:
            log.debug("skill injection skipped: %s", _sk_exc)
        if injected:
            parts.append(injected)
        if inbox_block:
            parts.append(inbox_block)
        if _compaction_block:
            parts.append(_compaction_block)
        try:
            _branch_block = build_branch_context_block(session_id)
            if _branch_block:
                parts.append(_branch_block)
        except Exception as _br_exc:
            log.debug("branch context skipped: %s", _br_exc)

        try:
            from .awareness import build_project_awareness as _bpa
            _awareness = await _bpa(session_id=session_id)
            if _awareness:
                parts.append(_awareness)
        except Exception as _aw_exc:
            log.debug("awareness injection skipped: %s", _aw_exc)

        if parts:
            user_content = "\n\n".join(parts) + f"\n\n━━━ NEW REQUEST FROM OWNER ━━━\n{user_message}"
        else:
            user_content = user_message

        try:
            _dod_intent = (briefing_dict or {}).get("intent", "") if briefing_dict else ""
            if _dod.is_coding_task(user_message, _dod_intent):
                user_content = user_content + "\n\n" + _dod.generate_dod_block(user_message, _dod_intent)
        except Exception as _dod_exc:
            log.debug("DoD injection skipped: %s", _dod_exc)

        try:
            from .game_engineer import prompt_block as _eng_block
            _eng_intent = (briefing_dict or {}).get("intent", "") if briefing_dict else ""
            _eng = _eng_block(user_message, intent=_eng_intent)
            if _eng:
                user_content = user_content + "\n\n" + _eng
        except Exception as _eng_exc:
            log.debug("game engineer contract skipped: %s", _eng_exc)

        try:
            _ts_block = _task_state.get_context_block()
            if _ts_block:
                user_content = _ts_block + "\n\n" + user_content
        except Exception as _ts_exc:
            log.debug("task-state injection skipped: %s", _ts_exc)

        try:
            from .job_ledger import format_session_block as _job_block
            _jb = _job_block(session_id or "")
            if _jb:
                user_content = _jb + "\n\n" + user_content
        except Exception as _jb_exc:
            log.debug("job ledger injection skipped: %s", _jb_exc)

        _continuity_block = _continuity.build_continuity_context(session_id=session_id)
        _call_persona = (req.persona + "\n\n" + _continuity_block) if _continuity_block else req.persona
        try:
            _mode_block = _mode.build_mode_context(_conv_mode)
            if _mode_block:
                _call_persona = _call_persona + "\n\n" + _mode_block
        except Exception:
            pass

        return AssembledPrompt(
            user_content=user_content,
            system_prompt=_call_persona,
            retrieval_counts=retrieved.retrieval_counts(),
        )
