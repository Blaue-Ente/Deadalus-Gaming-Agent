"""Parse → authorize → execute → observe → next-turn.

Leaf module. Do not import agent.py. Cheap-path ROUTING, leftover NEED_*
if-arms, NEED_PROPOSE, and RunFinalizer stay on Daedalus. The verify spine
façade is a sibling leaf. ToolLoop is the round driver. Execute stays in
native_tools / leftover callback.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

MAX_TOOL_ITERATIONS = 30

_PROMISE_RE = re.compile(
    r"\b("
    r"ще\s+(?:провер|потърс|намер|погледн|изчисл|анализир)|"
    r"нека\s+(?:намер|провер|видим|потърс)|"
    r"мога\s+да\s+(?:провер|намер|потърс|прочет)|"
    r"трябва\s+да\s+(?:провер|намер|прочет|използ)|"
    r"агентът\s+трябва|"
    r"let\s+me\s+(?:check|find|search|look|read)|"
    r"i(?:'ll| will)\s+(?:check|search|find|look|read)|"
    r"should\s+(?:check|read|use|emit)|"
    r"i\s+(?:need|must|have\s+to)\s+(?:check|read|find|look)|"
    r"first\s+(?:i\s+(?:need|should|will|must)|let\s+me)|"
    r"NEED_BATCH|NEED_VERIFY"
    r")",
    re.IGNORECASE,
)

_INTENT_PHRASES = (
    "ще подготвя", "сега ще", "следващата стъпка", "ще създам",
    "ще генерирам", "ще вмъкна", "ще добавя", "next i'll", "i will now",
    "now i'll", "i'll now", "let me now", "i'll prepare", "i'll generate",
    "i'll create", "i'll insert", "i'll add", "i'll write",
    "preparing", "will prepare", "will generate", "will create",
)
_ACTION_MARKERS = (
    "NEED_DATA_PROPOSAL", "NEED_PG", "NEED_PROPOSE_START",
    "NEED_INBOX_MSG", "NEED_BLOCKED", "NEED_WORKER",
    "NEED_STAGE", "NEED_BUNDLE_START", "NEED_BUNDLE_COMMIT",
)
_DUMP_SKIP = frozenset({"postgres_query", "mcp_call", "spawn_subagent"})


@dataclass
class NativeRoundRequest:
    system: str
    user_content: str
    call_with_tools_fn: Callable[..., Awaitable[dict[str, Any]]]
    memory: Any
    session_id: str
    read_file_fn: Callable[..., dict]
    max_rounds: int = 8
    tools: list[dict[str, Any]] | None = None
    loop: Any = None


@dataclass
class NativeRoundResult:
    text: str | None
    tool_log: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class LeftoverExecResult:
    parts: list[str] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    vision_blocks: list[dict[str, Any]] = field(default_factory=list)
    data_prop_truncated: bool = False


@dataclass
class LeftoverRoundRequest:
    raw: str
    user_content: str
    persona: str
    session_id: str
    user_message: str
    expect_json: bool
    propose_result_text: str
    complete_fn: Callable[..., Awaitable[str]]
    execute_leftover_fn: Callable[..., Awaitable[LeftoverExecResult]]
    keepalive: Any
    set_live: Callable[..., Any]
    memory: Any
    read_file_fn: Callable[..., dict]
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    for_self: bool = False
    max_iterations: int = MAX_TOOL_ITERATIONS
    after_reroll_fn: Callable[[str], Awaitable[tuple[str, str, dict[str, Any] | None]]] | None = None


@dataclass
class LeftoverRoundResult:
    raw: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    broke_with_fallback: bool = False


def _leftover_live_tool(hit: Any, propose_result_text: str) -> str:
    if hit.pg_q:
        return "NEED_PG"
    if hit.search_q:
        return "NEED_SEARCH"
    if hit.web_q:
        return "NEED_WEB"
    if hit.fetch_url:
        return "NEED_WEB_FETCH"
    if hit.screenshot_u:
        return "NEED_WEB_SCREENSHOT"
    if hit.worker_calls:
        return "NEED_WORKER"
    if hit.data_prop_m:
        return "NEED_DATA_PROPOSAL"
    if propose_result_text:
        return "NEED_PROPOSE_START"
    if hit.progress_m:
        return "NEED_PROGRESS"
    if hit.check_perm_m:
        return "NEED_CHECK_PERM"
    if hit.project_plan_m:
        return "NEED_PROJECT_PLAN"
    if hit.skill_q:
        return "NEED_SKILL"
    if hit.gh_q:
        return "NEED_GITHUB"
    if hit.mesh_marker:
        return "NEED_MESH"
    if hit.endpoints_q or hit.modules_q or hit.db_schema_q:
        return "NEED_READ_FILE"
    if hit.inbox_msg_m:
        return "NEED_INBOX_MSG"
    if hit.lint_q:
        return "NEED_LINT"
    if hit.tdd_m:
        return "NEED_TDD"
    return "thinking"


def _promise_followup(guard_count: int) -> str:
    if guard_count == 1:
        return (
            "[GUARD] Обеща да провериш/намериш нещо, но НЕ емитира NEED_* маркер.\n\n"
            "СЕГА emit ДИРЕКТНО маркер:\n"
            "  • Един файл → <NEED_READ_FILE: path/to/file.py>\n"
            "  • PostgreSQL → <NEED_PG: SELECT …>\n"
            "  • Уеб → <NEED_WEB: заявка>\n"
            "  • Множество неща наведнъж → NEED_BATCH блок:\n"
            "<NEED_BATCH>\n"
            "read: argos_data_nexus/main.py|1-80\n"
            "pg: SELECT count(*) FROM endpoint_registry\n"
            "capability: health check\n"
            "</NEED_BATCH>\n\n"
            "Никога не обещавай без да емитираш маркер."
        )
    if guard_count == 2:
        return (
            "[GUARD #2] Отново без маркер. ПОСЛЕДНО ПРЕДУПРЕЖДЕНИЕ.\n\n"
            "Отговори САМО с NEED_BATCH блок — без проза, без обяснение:\n"
            "<NEED_BATCH>\n"
            "tool: argument\n"
            "</NEED_BATCH>\n\n"
            "Ако нямаш нужда от инструменти, отговори САМО: DIRECT_ANSWER"
        )
    return (
        "[GUARD #3 — FINAL] Отговори с ЕДНО от двете:\n\n"
        "Вариант A (ако трябват инструменти):\n"
        "<NEED_BATCH>\n"
        "read: argos_data_nexus/main.py|1-50\n"
        "</NEED_BATCH>\n\n"
        "Вариант B (ако можеш да отговориш директно):\n"
        "DIRECT_ANSWER\n\n"
        "НИЩО ДРУГО. БЕЗ ПРОЗА."
    )


def leftover_followup_pieces(
    *,
    raw: str,
    tool_iter: int,
    max_iterations: int,
    data_prop_truncated: bool,
) -> tuple[str, str]:
    _raw_lower = (raw or "").lower()
    _has_intent = any(p in _raw_lower for p in _INTENT_PHRASES)
    _has_action = any(m in (raw or "") for m in _ACTION_MARKERS)
    _force_action_nudge = _has_intent and not _has_action
    _is_final_iter = tool_iter == max_iterations - 1
    _cutoff_notice = ""
    if data_prop_truncated and not _is_final_iter:
        _cutoff_notice = (
            "━━━ CUT-OFF DETECTED ━━━\n"
            "Твоето NEED_DATA_PROPOSAL беше прекъснато на средата на JSON-а "
            "(max_tokens лимит). Никакъв запис не е извършен.\n\n"
            "ДЕЙСТВАЙ СЕГА — раздели на ПО-МАЛКИ batches:\n"
            "  • Максимум 3 концепта/criteria в ЕДИН marker\n"
            "  • Емитирай НЯКОЛКО NEED_DATA_PROPOSAL marker-а в един отговор\n"
            "  • Всеки batch е независим — ако един фейлне, другите минават\n\n"
            "Пример (3 batch-а в един отговор):\n"
            "  <NEED_DATA_PROPOSAL: trading_concepts | Batch 1/3 | [{...},{...},{...}]>\n"
            "  <NEED_DATA_PROPOSAL: trading_concepts | Batch 2/3 | [{...},{...},{...}]>\n"
            "  <NEED_DATA_PROPOSAL: trading_concepts | Batch 3/3 | [{...},{...}]>\n\n"
            "НЕ описвай отново структурата — направо емитирай marker-ите."
        )
        log.info("data_proposal cut-off detected at iter=%d, injecting split prompt", tool_iter)
    if _is_final_iter:
        tail = (
            "⚠️  ФИНАЛНА ИТЕРАЦИЯ — НЕ ОЩЕ ЕДИН ROUND OF TOOLS.\n"
            "Трябва да ДЕЙСТВАШ сега — избери ЕДНО от следните:\n\n"
            "• За trading данни (concepts/criteria/patterns): емитирай\n"
            "  <NEED_DATA_PROPOSAL: category | Title | [json_array]>\n"
            "  Може да емитираш МНОГО такива маркера в един отговор.\n\n"
            "• За код: емитирай <NEED_PROPOSE_START> ... </NEED_PROPOSE_START>\n\n"
            "• За блокер: емитирай <NEED_BLOCKED: причина | опитано | следващи стъпки>\n\n"
            "• Ако вече свърши: пиши финалния отговор БЕЗ маркери.\n\n"
            "ЗАБРАНЕНО: описания на това, което 'ще направиш'. Само действие."
        )
    elif _force_action_nudge:
        tail = (
            "⚠️  ЗАСЕЧЕН INTENT БЕЗ ДЕЙСТВИЕ — спри да описваш, ДЕЙСТВАЙ.\n"
            "Написа какво 'ще направиш', но не емитира никакъв маркер.\n\n"
            "Емитирай СЕГА (в същия отговор — много маркера са ОК):\n"
            "  <NEED_DATA_PROPOSAL: category | Title | [json_array]>  — за trading данни\n"
            "  <NEED_PG: INSERT INTO ...>                              — за директен SQL (ако perms granted)\n"
            "  <NEED_PROPOSE_START> ... </NEED_PROPOSE_START>          — за код\n"
            "  <NEED_CHECK_PERM: insert_trading_concepts>              — за проверка на права\n\n"
            "Оставащи итерации: "
            f"{max_iterations - tool_iter - 1}. "
            "Използвай ги за ДЕЙСТВИЕ, не за повече reconnaissance.\n\n"
            "DATA CURATION: <NEED_DATA_PROPOSAL: trading_concepts | SMC Batch 1 | [{...},{...}]>\n"
            "Можеш да пратиш всички концепти в ЕДИН маркер (json array). "
            "Report progress: <NEED_PROGRESS: task | pct | msg>"
        )
        log.debug("action-forcing nudge injected at iter=%d", tool_iter)
    else:
        tail = (
            "You MAY emit more NEED_* markers (NEED_READ_FILE, NEED_CODE_SEARCH, "
            "NEED_PG, NEED_DB_SCHEMA, NEED_PROPOSE_START, NEED_DATA_PROPOSAL, "
            "NEED_PROGRESS, NEED_CHECK_PERM, NEED_WORKER etc.) if you need more "
            "info or to write code now that you have it. When you have everything "
            "needed to fully complete the task, reply with your final answer "
            "WITHOUT any more markers.\n\n"
            "PROJECT MODE: If the task is too big for this loop (10+ DB records, "
            "multi-domain analysis, multi-module refactor), emit:\n"
            "  <NEED_PROJECT_PLAN: detailed goal description>\n"
            "Дедал ще разбие целта на стъпки, всяка ще се изпълни в собствена сесия "
            "с пълни 20 итерации. Не се опитвай да побереш всичко в текущия loop.\n\n"
            "WORKER DELEGATION: To parallelize work, emit one or more NEED_WORKER markers:\n"
            "  <NEED_WORKER: fast | task description>   — cheap/fast model (Haiku/Groq)\n"
            "  <NEED_WORKER: balanced | task>           — same model as master\n"
            "  <NEED_WORKER: deep | task>               — powerful model (Opus)\n"
            "Multiple NEED_WORKER markers in one response run in parallel.\n"
            "For a bounded file slice WITH tools, emit:\n"
            "  <NEED_SUBAGENT: slug | task>\n"
            "  <NEED_SUBAGENT: slug | task | explorer>  — read-only map/verify.\n"
            "Explorer cannot write. Pass one slice, not the whole chat.\n"
            "Workers are stateless — pass full context in the task description. "
            "Wait for their results in the next tool iteration before synthesizing.\n\n"
            "DATA CURATION REMINDER: For inserting trading_concepts / signal_criteria / "
            "asset_behavior_patterns into the database, ALWAYS use NEED_DATA_PROPOSAL "
            "instead of NEED_PROPOSE_START (code sandbox). "
            "Format: <NEED_DATA_PROPOSAL: category | Title | [json_array]>\n"
            "First check permission with NEED_CHECK_PERM: insert_<category> — "
            "if GRANTED you can insert directly with NEED_PG; if DENIED use NEED_DATA_PROPOSAL. "
            "Report progress with NEED_PROGRESS: task_name | pct | message (pct 0.0-1.0).\n\n"
            f"Remaining tool iterations: {max_iterations - tool_iter - 1}. "
            "Reconnaissance should be DONE by now — move to ACTION (emit markers)."
        )
    cutoff_prefix = (
        f"{_cutoff_notice}\n\n" if data_prop_truncated and not _is_final_iter else ""
    )
    return cutoff_prefix, tail


class ToolLoop:
    """Stateless façade. Native rounds + leftover driver. No leftover if-arms."""

    async def run_native(self, req: NativeRoundRequest) -> NativeRoundResult:
        text, tool_log = await _run_native_rounds(req)
        return NativeRoundResult(text=text, tool_log=tool_log)

    async def run_leftover(self, req: LeftoverRoundRequest) -> LeftoverRoundResult:
        return await _run_leftover_rounds(req)


async def _run_native_rounds(req: NativeRoundRequest) -> tuple[str | None, list[dict[str, Any]]]:
    from .native_tools import (
        NATIVE_TOOL_SPECS,
        _kernel_play_bot,
        _kernel_visual_proof,
        _mentor_record_outcome,
        _user_turn,
        execute_native_tool,
    )
    from .tool_dumps import extract_tool_dumps

    system = req.system
    user_content = req.user_content
    call_with_tools_fn = req.call_with_tools_fn
    memory = req.memory
    session_id = req.session_id
    read_file_fn = req.read_file_fn
    max_rounds = req.max_rounds
    tools = req.tools
    loop = req.loop

    try:
        from . import writer_assist as _writer_assist
        _writer_assist.set_ask(user_content)
    except Exception:
        pass
    specs = tools or NATIVE_TOOL_SPECS
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_content}]
    tool_log: list[dict[str, Any]] = []
    all_results: list[str] = []

    budget = max_rounds
    _round = -1
    while True:
        if _round + 1 >= budget:
            if loop is not None and not getattr(loop, "stop_reason", ""):
                if getattr(loop, "wants_exhaust_proof", lambda: False)():
                    try:
                        proof_block = await _kernel_visual_proof(loop)
                        if proof_block:
                            all_results.append(proof_block)
                        if loop.observations:
                            tool_log.append(loop.observations[-1].as_call())
                    except Exception as exc:
                        log.warning("kernel visual proof on exhaust failed: %s", exc)
                if loop is not None and getattr(loop, "wants_play_bot", lambda: False)():
                    try:
                        play_block = await _kernel_play_bot(loop)
                        if play_block:
                            all_results.append(play_block)
                        if loop.observations:
                            tool_log.append(loop.observations[-1].as_call())
                    except Exception as exc:
                        log.warning("kernel play bot on exhaust failed: %s", exc)
                extra = 0
                try:
                    from . import mentor as _mentor
                    await _mentor.refresh_helm(loop)
                    extra = int(_mentor.extra_round_budget(loop) or 0)
                except Exception:
                    extra = 0
                if extra > 0:
                    loop.mentor_grants = int(getattr(loop, "mentor_grants", 0) or 0) + 1
                    budget += extra
                    try:
                        from . import mentor as _mentor
                        _mentor.maybe_capture_lesson(loop, memory)
                    except Exception:
                        pass
                    nudge = loop.progress_block()
                    vision = list(getattr(loop, "proof_vision", None) or [])
                    messages.append(_user_turn(nudge, vision or None))
                    continue
                loop.stop_reason = "round budget exhausted"
                loop.verdict("")
            break
        _round += 1
        resp = await call_with_tools_fn(system, messages, specs)
        if resp.get("budget_stopped"):
            final = ""
            for block in resp.get("content") or []:
                if block.get("type") == "text":
                    final = str(block.get("text") or "")
                    break
            if loop is not None:
                loop.stop_reason = "run budget exhausted"
                loop.verdict(final)
            _mentor_record_outcome(loop)
            return (final or None), tool_log
        content_blocks = resp.get("content", [])

        assistant_content: list[dict[str, Any]] = []
        tool_uses: list[dict[str, Any]] = []
        text_parts: list[str] = []

        for block in content_blocks:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
                assistant_content.append(block)
            elif block.get("type") == "tool_use":
                tool_uses.append(block)
                assistant_content.append(block)

        if not tool_uses:
            for i, dump in enumerate(extract_tool_dumps("\n".join(text_parts))):
                tool_uses.append({
                    "type": "tool_use",
                    "id": f"dump_{_round}_{i}",
                    "name": dump.name,
                    "input": dump.args,
                })
                assistant_content.append(tool_uses[-1])

        if not tool_uses:
            final = "\n".join(text_parts).strip()
            proof_block = ""
            if loop is not None and getattr(loop, "wants_kernel_proof", lambda: False)():
                try:
                    proof_block = await _kernel_visual_proof(loop)
                    if loop.observations:
                        tool_log.append(loop.observations[-1].as_call())
                except Exception as exc:
                    log.warning("kernel visual proof failed: %s", exc)
                    proof_block = f"━━━ VISUAL PROOF (kernel) ━━━\nProof crashed: {exc}\n"
            play_block = ""
            if loop is not None and getattr(loop, "wants_play_bot", lambda: False)():
                try:
                    play_block = await _kernel_play_bot(loop)
                    if loop.observations:
                        tool_log.append(loop.observations[-1].as_call())
                except Exception as exc:
                    log.warning("kernel play bot failed: %s", exc)
                    play_block = f"━━━ PLAYABLE (kernel computer) ━━━\nFAILED: {exc}\n"
            nudge = None
            if loop is not None:
                try:
                    from . import mentor as _mentor
                    await _mentor.refresh_helm(loop)
                except Exception:
                    pass
                nudge = loop.on_quiet(final)
            if nudge:
                extra = "\n\n".join(p for p in (proof_block, play_block, nudge) if p)
                messages.append({
                    "role": "assistant",
                    "content": assistant_content or [{"type": "text", "text": final or "(no reply)"}],
                })
                vision = list(getattr(loop, "proof_vision", None) or []) if loop is not None else []
                messages.append(_user_turn(extra, vision or None))
                budget += loop.rounds_per_fix
                continue
            for block in (proof_block, play_block):
                if block:
                    all_results.append(block)
            _mentor_record_outcome(loop)
            if all_results:
                combined = (
                    "━━━ NATIVE TOOL RESULTS ━━━\n"
                    + "\n\n".join(all_results)
                    + ("\n\n━━━ MODEL FOLLOW-UP ━━━\n" + final if final else "")
                )
                return combined, tool_log
            return None, []

        messages.append({"role": "assistant", "content": assistant_content})

        tool_result_blocks: list[dict[str, Any]] = []
        for tu in tool_uses:
            tid = tu.get("id", "")
            tname = tu.get("name", "")
            tinput = tu.get("input") or {}
            if isinstance(tinput, str):
                try:
                    tinput = json.loads(tinput)
                except json.JSONDecodeError:
                    tinput = {"raw": tinput}

            result_text = await execute_native_tool(
                tname, tinput,
                memory=memory, session_id=session_id,
                read_file_fn=read_file_fn,
            )
            all_results.append(result_text)
            blocked = "BLOCKED" in result_text
            ok = blocked or ("FAILED" not in result_text and "ERROR" not in result_text)
            tool_log.append({
                "tool": tname,
                "input": {k: v for k, v in tinput.items() if k != "content"} if isinstance(tinput, dict) else {},
                "ok": ok,
                "blocked": blocked,
            })
            if loop is not None:
                loop.observe(
                    tname, tinput if isinstance(tinput, dict) else {}, result_text,
                    ok=ok, blocked=blocked,
                )
                from .tool_catalog import canonical as _can
                tcan = _can(tname).lower()
                if tcan in {"browser", "engine", "visual_proof", "run_command"} and not ok:
                    try:
                        from . import mentor as _mentor
                        if _mentor.enabled():
                            ret = _mentor.progress_next(loop)
                            if ret:
                                result_text = result_text + "\n" + ret
                                all_results[-1] = result_text
                    except Exception:
                        pass
            tool_result_blocks.append({
                "type": "tool_result",
                "tool_use_id": tid,
                "content": result_text,
            })

        messages.append({"role": "user", "content": tool_result_blocks})

    _mentor_record_outcome(loop)
    if all_results:
        return "━━━ NATIVE TOOL RESULTS ━━━\n" + "\n\n".join(all_results), tool_log
    return None, []


async def _run_leftover_rounds(req: LeftoverRoundRequest) -> LeftoverRoundResult:
    from . import scratch_journal as _journal
    from .improvements import get_error_tracker, mark_schema_read, record_tool_error_for_promotion
    from .need_scan import scan_need_markers
    from .native_tools import execute_native_tool
    from .tool_dumps import extract_tool_dumps, strip_tool_dumps

    raw = req.raw
    user_content = req.user_content
    session_id = req.session_id
    _call_persona = req.persona
    _call_claude = req.complete_fn
    _keepalive = req.keepalive
    _set_live = req.set_live
    propose_result_text = req.propose_result_text
    tool_calls_used = list(req.tool_calls)
    max_iterations = req.max_iterations
    _tool_loop_broke_with_fallback = False
    _promise_guard_count = 0
    _tool_iter = 0

    for _tool_iter in range(max_iterations):
        hit = scan_need_markers(raw)
        if hit.parsed_calls and _tool_iter == 0:
            log.debug(
                "tool_parser: %d markers at positions %s",
                len(hit.parsed_calls),
                [c.name for c in hit.parsed_calls[:8]],
            )
        if hit.db_schema_q:
            _tables = [t.strip() for t in re.split(r"[,\s]+", hit.db_schema_q) if t.strip()]
            try:
                mark_schema_read(session_id, _tables)
            except Exception:
                pass
        _json_dumps = extract_tool_dumps(raw)
        has_markers = bool(propose_result_text) or hit.has_markers or bool(_json_dumps)
        if not has_markers:
            _clean = re.sub(r"```.*?```", " ", raw, flags=re.DOTALL)
            _clean = re.sub(r"`[^`\n]*`", " ", _clean)
            _clean = re.sub(r"(?m)^\s*>.*$", " ", _clean)
            _clean = re.sub(r'[\u201c\u201d\u00ab\u00bb]', " ", _clean)
            if raw and _promise_guard_count < 3 and _PROMISE_RE.search(_clean):
                _promise_guard_count += 1
                log.info(
                    "promise-guard #%d: triggered session=%s iter=%d",
                    _promise_guard_count, (session_id or "")[:10], _tool_iter,
                )
                try:
                    _set_live(
                        session_id, "reasoning", _tool_iter,
                        f"Guard #{_promise_guard_count} — карам модела да emit-не маркер…",
                    )
                except Exception:
                    pass
                followup = _promise_followup(_promise_guard_count)
                try:
                    async with _keepalive(
                        session_id, "reasoning", _tool_iter,
                        f"Re-roll #{_promise_guard_count} след promise-guard…",
                    ):
                        raw = await _call_claude(
                            _call_persona,
                            [
                                {"role": "user", "content": user_content},
                                {"role": "assistant", "content": raw},
                                {"role": "user", "content": followup},
                            ],
                            use_cache_override=False,
                        )
                except Exception as _g_exc:
                    log.debug(
                        "promise-guard re-roll #%d failed: %s",
                        _promise_guard_count, _g_exc,
                    )
                    break
                continue
            break

        try:
            _set_live(
                session_id, _leftover_live_tool(hit, propose_result_text), _tool_iter,
                f"итерация {_tool_iter + 1}/20",
            )
        except Exception:
            pass

        tool_results_text_parts: list[str] = []
        vision_blocks: list[dict] = []
        if _json_dumps:
            dump_bits: list[str] = []
            for dump in _json_dumps:
                if dump.name in _DUMP_SKIP:
                    continue
                result_text = await execute_native_tool(
                    dump.name, dump.args,
                    memory=req.memory, session_id=session_id or "",
                    read_file_fn=req.read_file_fn,
                )
                blocked = "BLOCKED" in result_text
                ok = blocked or ("FAILED" not in result_text and "ERROR" not in result_text)
                tool_calls_used.append({
                    "tool": dump.name,
                    "ok": ok,
                    "blocked": blocked,
                    "source": "dump",
                })
                dump_bits.append(result_text)
            if dump_bits:
                tool_results_text_parts.append(
                    "━━━ TOOL RESULTS — DUMPED CALLS ━━━\n" + "\n\n".join(dump_bits)
                )
            raw = strip_tool_dumps(raw, _json_dumps)
        if propose_result_text:
            tool_results_text_parts.append(propose_result_text)

        leftover_exec = await req.execute_leftover_fn(
            raw,
            hit,
            session_id=session_id,
            user_message=req.user_message,
            user_content=user_content,
            for_self=req.for_self,
            tool_iter=_tool_iter,
            set_live=_set_live,
        )
        tool_results_text_parts.extend(leftover_exec.parts)
        tool_calls_used.extend(leftover_exec.calls)
        vision_blocks.extend(leftover_exec.vision_blocks)
        _data_prop_truncated = bool(hit.data_prop_truncated or leftover_exec.data_prop_truncated)

        tool_block = "\n\n".join(tool_results_text_parts)

        try:
            _err_tracker = get_error_tracker()
            for _tc in tool_calls_used[-len(tool_results_text_parts):]:
                _err_tracker.track_call(session_id, _tc.get("tool", "unknown"))
            _failed_now: list[tuple[str, str]] = []
            for _tc in tool_calls_used[-len(tool_results_text_parts):]:
                if _tc.get("ok") is False:
                    _tool_name = _tc.get("tool", "unknown")
                    _err_sample = ""
                    for _ln in tool_block.split("\n"):
                        if "FAILED" in _ln or "Error" in _ln or "error" in _ln:
                            _err_sample = _ln[:200]
                            break
                    _failed_now.append((_tool_name, _err_sample))
                    try:
                        record_tool_error_for_promotion(
                            req.memory, _tool_name, _err_sample,
                            query=str(_tc.get("query", ""))[:200],
                        )
                    except Exception:
                        pass
            _should_break_repeat = False
            _repeat_tool = ""
            for _tn, _es in _failed_now:
                if _err_tracker.should_break(session_id, _tn, _es, threshold=2):
                    _should_break_repeat = True
                    _repeat_tool = _tn
                    break
            if _err_tracker.tool_call_count(session_id, "postgres_query") > 4:
                _should_break_repeat = True
                _repeat_tool = "postgres_query (budget exceeded)"
            if _should_break_repeat:
                log.warning(
                    "repeated-error breaker: session=%s tool=%s — breaking loop",
                    session_id, _repeat_tool,
                )
                tool_block += (
                    f"\n\n━━━ BREAKER ENGAGED ━━━\n"
                    f"Същата грешка от [{_repeat_tool}] се появи повторно. "
                    f"Спирам опитите. Премини към NEED_BLOCKED или различен подход — "
                    f"НЕ повтаряй същия маркер."
                )
                messages.append({"role": "user", "content": tool_block})
                raw = (
                    f"Спирам да опитвам — същата грешка от [{_repeat_tool}] "
                    f"се повтори. Премини към друг подход или ми кажи конкретно "
                    f"какво искаш да направя."
                )
                break
        except Exception as _bk_exc:
            log.debug("breaker check failed (non-fatal): %s", _bk_exc)

        _cutoff_prefix, followup_tail = leftover_followup_pieces(
            raw=raw,
            tool_iter=_tool_iter,
            max_iterations=max_iterations,
            data_prop_truncated=_data_prop_truncated,
        )
        followup = (
            _cutoff_prefix
            + f"━━━ TOOL RESULTS (your request) ━━━\n{tool_block}\n\n"
            + followup_tail
        )
        if vision_blocks:
            followup_content: object = [{"type": "text", "text": followup}] + vision_blocks
        else:
            followup_content = followup
        try:
            if _tool_iter == 0:
                reroll_messages = [
                    {"role": "user", "content": user_content},
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": followup_content},
                ]
            else:
                reroll_messages = [
                    {"role": "user", "content": "(context already provided in prior turns)"},
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": followup_content},
                ]
            try:
                async with _keepalive(
                    session_id, "generating", _tool_iter,
                    f"Генерирам следващ отговор… (итер. {_tool_iter + 1}/{max_iterations})",
                ):
                    raw = await _call_claude(
                        _call_persona, reroll_messages, use_cache_override=False,
                    )
            except NameError:
                raw = await _call_claude(
                    _call_persona, reroll_messages, use_cache_override=False,
                )
            try:
                _extra = _journal.estimate_tokens(raw) + _journal.estimate_tokens(followup)
                _do_pause, _pr = _journal.should_pause(session_id or "", _extra)
                if _do_pause:
                    _journal.write_step(
                        session_id or "",
                        "auto_pause",
                        f"Paused след итер. {_tool_iter + 1}/{max_iterations} "
                        f"({_pr}). Дедал ще resume-не при 'продължи'.",
                        data=(raw or "")[:4000],
                    )
                    _journal.mark_paused(session_id or "", _pr)
                    log.info(
                        "journal: auto-pause session=%s reason=%s iter=%d",
                        (session_id or "")[:10], _pr, _tool_iter + 1,
                    )
                    _stripped_pause = re.sub(r"<NEED_[A-Z_]+(?:[^>]*)>", "", raw or "").strip()
                    raw = (
                        f"{_stripped_pause}\n\n"
                        f"⏸ ПАУЗА — стигнах ~{int(_journal.PAUSE_THRESHOLD*100)}% от token budget "
                        f"({_pr}). Запазих прогреса в journal-а (стъпки: "
                        f"{len((_journal.get_journal(session_id or '') or {}).get('steps', []))}). "
                        f"Напиши 'продължи' за да възстановя от където спрях."
                    ).strip()
                    _tool_loop_broke_with_fallback = True
                    break
            except Exception as _pe:
                log.debug("auto-pause check skipped: %s", _pe)
        except Exception as exc:
            log.warning("tool re-roll failed: %s", exc)
            stripped = re.sub(r"<NEED_[A-Z_]+(?:[^>]*)>", "", raw or "")
            stripped = re.sub(
                r"<NEED_(?:PROPOSE_START|SANDBOX)>.*?</NEED_(?:PROPOSE_START|SANDBOX)>",
                "", stripped, flags=re.DOTALL,
            ).strip()
            tool_summary = "; ".join(
                f"{c['tool']}({c['query']!r}): {c['result_count']} results"
                for c in tool_calls_used
            )
            if req.expect_json:
                raw = json.dumps({
                    "language": "text",
                    "filename": "fallback.txt",
                    "requirements": [],
                    "install_command": "",
                    "run_command": "",
                    "code": "",
                    "explanation": (
                        f"Tool re-roll failed ({exc}). Tools attempted: {tool_summary}. "
                        "Owner — please retry the request."
                    ),
                    "key_decisions": [],
                    "usage_example": "",
                    "warnings": ["LLM re-roll failure after tool call"],
                    "memory_notes": "tool re-roll failed; do not store as success",
                })
            else:
                raw = (
                    f"{stripped}\n\n"
                    f"(I needed to consult external sources [{tool_summary}] but the "
                    f"follow-up call failed: {exc}. Please retry.)"
                ).strip()
            _tool_loop_broke_with_fallback = True
            break

        propose_result_text = ""
        if req.after_reroll_fn is not None:
            raw, propose_result_text, extra_call = await req.after_reroll_fn(raw)
            if extra_call:
                tool_calls_used.append(extra_call)

    return LeftoverRoundResult(
        raw=raw,
        tool_calls=tool_calls_used,
        iterations=_tool_iter,
        broke_with_fallback=_tool_loop_broke_with_fallback,
    )
