"""Playground Skill Creator — guided SKILL.md authoring for any agent platform.

Workflow:
  1. start   — research + plan + clarifying questions (Master LLM + tools)
  2. refine  — iterative Q&A until ready for approval
  3. generate — final SKILL.md (+ optional references/) after user approval
  4. export  — download bundle or push to GitHub gist/repo

Sessions are ephemeral (in-memory, 24h TTL). No playground project slug required.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any

log = logging.getLogger(__name__)

SESSION_TTL_SEC = 86_400  # 24h
_MAX_HISTORY = 40

VALID_PLATFORMS = frozenset({
    "cursor",
    "claude",
    "codex",
    "generic",
    "harness",
})

_PLATFORM_SPECS: dict[str, dict[str, str]] = {
    "cursor": {
        "label": "Cursor IDE",
        "path_hint": ".cursor/skills/{slug}/SKILL.md",
        "frontmatter": "name + description (kebab-case folder, name matches dir)",
        "sections": "Instructions, Examples, optional references/ and scripts/",
    },
    "claude": {
        "label": "Claude Code / Claude Desktop",
        "path_hint": ".claude/skills/{slug}/SKILL.md",
        "frontmatter": "name + description (+ optional metadata, argument-hint)",
        "sections": "Workflow, Important Rules, Examples, optional references/",
    },
    "codex": {
        "label": "OpenAI Codex / .agents",
        "path_hint": ".agents/skills/{slug}/SKILL.md",
        "frontmatter": "name + description",
        "sections": "Instructions, Examples, domain docs beside SKILL.md",
    },
    "generic": {
        "label": "Universal Agent Skills (open format)",
        "path_hint": "skills/{slug}/SKILL.md",
        "frontmatter": "name + description",
        "sections": "When to use, Workflow, Examples, Troubleshooting",
    },
    "harness": {
        "label": "Harness Cursor Plugin",
        "path_hint": "skills/{slug}/SKILL.md",
        "frontmatter": "name + description (name matches directory)",
        "sections": "Instructions, Examples, Performance Notes, Troubleshooting (required)",
    },
}

_sessions: dict[str, dict[str, Any]] = {}


def platform_label(platform: str) -> str:
    key = (platform or "generic").strip().lower()
    return _PLATFORM_SPECS.get(key, _PLATFORM_SPECS["generic"])["label"]


def platform_spec(platform: str) -> dict[str, str]:
    key = (platform or "generic").strip().lower()
    if key not in VALID_PLATFORMS:
        key = "generic"
    return _PLATFORM_SPECS[key]


def _now() -> float:
    return time.time()


def _purge_stale() -> None:
    cutoff = _now() - SESSION_TTL_SEC
    stale = [sid for sid, s in _sessions.items() if (s.get("updated_at") or 0) < cutoff]
    for sid in stale:
        _sessions.pop(sid, None)


def _session_key(session_id: str) -> str:
    return f"skill-creator:{session_id}"


def _persist_session(session: dict[str, Any]) -> None:
    """Best-effort persist for multi-instance / Docker restart resilience."""
    from . import kv
    kv.set(_session_key(session["id"]), session)


def _load_persisted(session_id: str) -> dict[str, Any] | None:
    from . import kv
    raw = kv.get(_session_key(session_id))
    return raw if isinstance(raw, dict) else None


def get_session(session_id: str) -> dict[str, Any] | None:
    _purge_stale()
    sid = (session_id or "").strip()
    if not sid:
        return None
    if sid in _sessions:
        return _sessions[sid]
    loaded = _load_persisted(sid)
    if loaded:
        _sessions[sid] = loaded
    return loaded


def save_session(session: dict[str, Any]) -> None:
    session["updated_at"] = _now()
    _sessions[session["id"]] = session
    _persist_session(session)


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]


def slugify_skill_name(name: str) -> str:
    s = (name or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or f"skill-{uuid.uuid4().hex[:6]}"


def build_research_prompt(*, idea: str, platform: str, extra_context: str = "") -> str:
    spec = platform_spec(platform)
    ctx = f"\nДОПЪЛНИТЕЛЕН КОНТЕКСТ:\n{extra_context}\n" if extra_context.strip() else ""
    return (
        f"[📝 SKILL CREATOR — ФАЗА 1: ПРОУЧВАНЕ]\n"
        f"Целева платформа: {spec['label']} ({spec['path_hint']})\n\n"
        f"ИДЕЯ ЗА SKILL:\n{idea.strip()}\n"
        f"{ctx}\n"
        f"Проучи ПРЕДИ да планираш skill-а:\n"
        f"• Ползвай <NEED_SKILL_CATALOG: релевантна техника/домейн> за сходни skills и best practices\n"
        f"• Ползвай <NEED_WEB: open agent skills format / {spec['label']} skills> при нужда\n"
        f"• Ако идеята е за конкретен инструмент/API — проучи официалната документация\n\n"
        f"НЕ пиши финален SKILL.md. НЕ пиши код.\n"
        f"Отговори на български с кратко резюме (5–8 bullets):\n"
        f"- Какво намери (референции, patterns)\n"
        f"- Препоръчан scope на skill-а\n"
        f"- Рискове/граници (кога НЕ да се ползва)\n"
        f"- Ключови секции за {spec['label']}\n"
    )


def build_plan_prompt(
    *,
    idea: str,
    platform: str,
    research_notes: str,
) -> str:
    spec = platform_spec(platform)
    return (
        f"Ти си експерт по Agent Skills за {spec['label']}.\n\n"
        f"ИДЕЯ:\n{idea.strip()}\n\n"
        f"ПРОУЧВАНЕ:\n{research_notes or '(няма)'}\n\n"
        f"ФОРМАТ НА SKILL за {spec['label']}:\n"
        f"- Път: {spec['path_hint']}\n"
        f"- Frontmatter: {spec['frontmatter']}\n"
        f"- Секции: {spec['sections']}\n\n"
        f"Върни САМО валиден JSON (без markdown fence):\n"
        f'{{"skill_name":"kebab-case-slug","display_title":"Human Title",'
        f'"summary":"1-2 изречения за trigger description",'
        f'"plan":{{"objective":"...","audience":"...","workflow_steps":["..."],'
        f'"anti_patterns":["..."],"optional_assets":["references/...","scripts/..."]}},'
        f'"clarifying_questions":[{{"id":"q1","question":"...","why":"..."}}],'
        f'"improvement_tips":["съвет 1","съвет 2"],'
        f'"ready_for_generation":false}}\n\n'
        f"Правила:\n"
        f"- skill_name: kebab-case, 2–4 думи, без vc: prefix освен ако platform=claude и user иска namespace\n"
        f"- 3–6 clarifying_questions — конкретни, не общи\n"
        f"- improvement_tips: 2–4 actionable съвета\n"
        f"- ready_for_generation: true САМО ако идеята е кристално ясна (рядко при start)\n"
        f"- Пиши стойностите на български\n"
    )


def build_refine_prompt(session: dict[str, Any], user_message: str) -> str:
    spec = platform_spec(session.get("platform", "generic"))
    plan_json = json.dumps(session.get("plan") or {}, ensure_ascii=False, indent=2)
    history_block = _format_history(session.get("history") or [])
    return (
        f"[📝 SKILL CREATOR — ФАЗА 2: УТОЧНЯВАНЕ]\n"
        f"Платформа: {spec['label']}\n"
        f"Идея: {session.get('idea', '')}\n\n"
        f"ТЕКУЩ ПЛАН:\n{plan_json}\n\n"
        f"ПРОУЧВАНЕ:\n{session.get('research_notes', '')}\n\n"
        f"ИСТОРИЯ:\n{history_block}\n\n"
        f"НОВО ОТ ПОТРЕБИТЕЛЯ:\n{user_message.strip()}\n\n"
        f"Отговори на български. Обнови плана ако е нужно.\n"
        f"Върни САМО валиден JSON:\n"
        f'{{"reply":"разговорен отговор към потребителя (2-6 параграфа)",'
        f'"plan":{{"objective":"...","audience":"...","workflow_steps":["..."],'
        f'"anti_patterns":["..."],"optional_assets":["..."]}},'
        f'"clarifying_questions":[{{"id":"q1","question":"...","why":"..."}}],'
        f'"improvement_tips":["..."],'
        f'"skill_name":"kebab-slug","display_title":"...","summary":"...",'
        f'"ready_for_generation":false}}\n\n'
        f"- ready_for_generation=true когато всички критични въпроси са отговорени\n"
        f"- clarifying_questions: само отворените въпроси (премахни отговорените)\n"
        f"- reply: включи съвети как да подобри skill-а\n"
    )


def build_generate_prompt(session: dict[str, Any]) -> str:
    spec = platform_spec(session.get("platform", "generic"))
    plan_json = json.dumps(session.get("plan") or {}, ensure_ascii=False, indent=2)
    slug = session.get("skill_name") or slugify_skill_name(session.get("display_title", "skill"))
    return (
        f"[📝 SKILL CREATOR — ФАЗА 3: ГЕНЕРИРАНЕ]\n"
        f"Платформа: {spec['label']}\n"
        f"Skill slug: {slug}\n"
        f"Път: {spec['path_hint'].format(slug=slug)}\n\n"
        f"ИДЕЯ:\n{session.get('idea', '')}\n\n"
        f"ОДОБРЕН ПЛАН:\n{plan_json}\n\n"
        f"ПРОУЧВАНЕ:\n{session.get('research_notes', '')}\n\n"
        f"Frontmatter: {spec['frontmatter']}\n"
        f"Задължителни секции: {spec['sections']}\n\n"
        f"Генерирай ГОТОВ за публикуване skill пакет.\n"
        f"Формат на отговора — за всеки файл:\n"
        f"===FILE: relative/path===\n<съдържание>\n===END===\n\n"
        f"Минимум:\n"
        f"- SKILL.md с пълен YAML frontmatter (name: {slug}, description: …)\n"
        f"- Ако platform=harness: задължително Instructions, Examples, Performance Notes, Troubleshooting\n"
        f"- По избор: references/*.md за детайлни контракти\n"
        f"- По избор: scripts/ за validation\n\n"
        f"Правила:\n"
        f"- Пиши на български (освен code/commands/API names)\n"
        f"- Конкретни стъпки, не общи фрази\n"
        f"- description в frontmatter = trigger кога agent-ът да зареди skill-а\n"
        f"- НЕ пиши нищо извън ===FILE=== блоковете\n"
    )


def _format_history(history: list[dict[str, str]]) -> str:
    lines: list[str] = []
    for item in history[-12:]:
        role = item.get("role", "?")
        content = (item.get("content") or "").strip()
        if content:
            lines.append(f"[{role}]: {content[:2000]}")
    return "\n".join(lines) or "(празна)"


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Best-effort JSON extraction from LLM output."""
    raw = (text or "").strip()
    if not raw:
        return None
    # Strip markdown fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    # Find first { ... } block
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(raw[start : end + 1])
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def parse_skill_files(text: str) -> dict[str, str]:
    """Parse ===FILE: path=== blocks."""
    files: dict[str, str] = {}
    pattern = re.compile(
        r"===FILE:\s*(.+?)\s*===\r?\n(.*?)\r?\n?===END===",
        re.DOTALL,
    )
    for m in pattern.finditer(text or ""):
        path = m.group(1).strip().lstrip("/")
        content = m.group(2)
        if path:
            files[path] = content
    return files


def validate_skill_md(content: str, *, platform: str) -> list[str]:
    """Return list of validation warnings (empty = OK)."""
    warnings: list[str] = []
    text = content or ""
    if not text.strip():
        return ["SKILL.md е празен"]
    if not text.startswith("---"):
        warnings.append("Липсва YAML frontmatter (---)")
    else:
        end = text.find("\n---", 3)
        if end == -1:
            warnings.append("Frontmatter не е затворен (---)")
        else:
            fm = text[3:end]
            if "name:" not in fm:
                warnings.append("Frontmatter: липсва name")
            if "description:" not in fm:
                warnings.append("Frontmatter: липсва description")
    body = text.split("---", 2)[-1] if text.startswith("---") else text
    if platform == "harness":
        for sec in ("## Instructions", "## Examples", "## Performance Notes", "## Troubleshooting"):
            if sec.lower() not in body.lower():
                warnings.append(f"Harness skill: липсва секция {sec}")
    if len(body.strip()) < 200:
        warnings.append("Тялото на SKILL.md е твърде кратко (<200 символа)")
    return warnings


def normalize_plan_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Sanitize plan JSON from LLM."""
    skill_name = slugify_skill_name(str(data.get("skill_name") or data.get("display_title") or "skill"))
    questions = data.get("clarifying_questions") or []
    if not isinstance(questions, list):
        questions = []
    tips = data.get("improvement_tips") or []
    if not isinstance(tips, list):
        tips = []
    plan = data.get("plan") or {}
    if not isinstance(plan, dict):
        plan = {}
    return {
        "skill_name": skill_name,
        "display_title": str(data.get("display_title") or skill_name.replace("-", " ").title()),
        "summary": str(data.get("summary") or "").strip(),
        "plan": plan,
        "clarifying_questions": questions,
        "improvement_tips": [str(t) for t in tips if str(t).strip()],
        "ready_for_generation": bool(data.get("ready_for_generation")),
        "reply": str(data.get("reply") or "").strip(),
    }


def init_session(*, idea: str, platform: str, extra_context: str = "") -> dict[str, Any]:
    plat = (platform or "generic").strip().lower()
    if plat not in VALID_PLATFORMS:
        plat = "generic"
    sid = new_session_id()
    session: dict[str, Any] = {
        "id": sid,
        "created_at": _now(),
        "updated_at": _now(),
        "idea": idea.strip(),
        "platform": plat,
        "extra_context": (extra_context or "").strip(),
        "phase": "research",
        "research_notes": "",
        "tool_steps": [],
        "history": [],
        "plan": {},
        "skill_name": "",
        "display_title": "",
        "summary": "",
        "clarifying_questions": [],
        "improvement_tips": [],
        "ready_for_generation": False,
        "generated_files": {},
        "validation_warnings": [],
    }
    save_session(session)
    return session


def apply_start_result(session: dict[str, Any], *, research_notes: str, tool_steps: list[dict], plan_data: dict[str, Any]) -> dict[str, Any]:
    norm = normalize_plan_payload(plan_data)
    session["research_notes"] = research_notes
    session["tool_steps"] = tool_steps
    session["plan"] = norm["plan"]
    session["skill_name"] = norm["skill_name"]
    session["display_title"] = norm["display_title"]
    session["summary"] = norm["summary"]
    session["clarifying_questions"] = norm["clarifying_questions"]
    session["improvement_tips"] = norm["improvement_tips"]
    session["ready_for_generation"] = norm["ready_for_generation"]
    session["phase"] = "refining" if norm["clarifying_questions"] else "ready"
    if norm["ready_for_generation"]:
        session["phase"] = "ready"
    session["history"].append({"role": "assistant", "content": norm.get("reply") or _default_start_reply(norm)})
    save_session(session)
    return session


def apply_refine_result(session: dict[str, Any], *, user_message: str, plan_data: dict[str, Any]) -> dict[str, Any]:
    norm = normalize_plan_payload(plan_data)
    session["history"].append({"role": "user", "content": user_message.strip()})
    reply = norm.get("reply") or "Планът е обновен."
    session["history"].append({"role": "assistant", "content": reply})
    if len(session["history"]) > _MAX_HISTORY:
        session["history"] = session["history"][-_MAX_HISTORY:]
    session["plan"] = norm["plan"] or session.get("plan") or {}
    if norm["skill_name"]:
        session["skill_name"] = norm["skill_name"]
    if norm["display_title"]:
        session["display_title"] = norm["display_title"]
    if norm["summary"]:
        session["summary"] = norm["summary"]
    session["clarifying_questions"] = norm["clarifying_questions"]
    session["improvement_tips"] = norm["improvement_tips"]
    session["ready_for_generation"] = norm["ready_for_generation"]
    session["phase"] = "ready" if norm["ready_for_generation"] else "refining"
    save_session(session)
    return session


def apply_generate_result(session: dict[str, Any], *, raw_reply: str) -> dict[str, Any]:
    files = parse_skill_files(raw_reply)
    if not files:
        # Fallback: treat entire reply as SKILL.md if it looks like markdown skill
        stripped = (raw_reply or "").strip()
        if stripped.startswith("---") and "description:" in stripped.lower():
            slug = session.get("skill_name") or "skill"
            files = {f"{slug}/SKILL.md": stripped}
    session["generated_files"] = files
    skill_md = ""
    for path, content in files.items():
        if path.endswith("SKILL.md") or path == "SKILL.md":
            skill_md = content
            break
    if not skill_md and files:
        skill_md = next(iter(files.values()))
    session["validation_warnings"] = validate_skill_md(skill_md, platform=session.get("platform", "generic"))
    session["phase"] = "generated"
    save_session(session)
    return session


def _default_start_reply(norm: dict[str, Any]) -> str:
    parts = [
        f"План за skill **{norm.get('display_title', norm.get('skill_name', 'skill'))}**.",
        f"Обобщение: {norm.get('summary', '')}",
    ]
    qs = norm.get("clarifying_questions") or []
    if qs:
        parts.append("Имам няколко уточняващи въпроса — отговори на тях, за да финализираме skill-а.")
    tips = norm.get("improvement_tips") or []
    if tips:
        parts.append("Съвети: " + "; ".join(tips[:3]))
    return "\n\n".join(p for p in parts if p)


def session_public_view(session: dict[str, Any]) -> dict[str, Any]:
    """API-safe session snapshot (no huge file bodies in list views)."""
    files = session.get("generated_files") or {}
    preview = ""
    for path, content in files.items():
        if path.endswith("SKILL.md") or path == "SKILL.md":
            preview = (content or "")[:4000]
            break
    return {
        "session_id": session["id"],
        "phase": session.get("phase"),
        "platform": session.get("platform"),
        "platform_label": platform_label(session.get("platform", "generic")),
        "idea": session.get("idea"),
        "research_notes": session.get("research_notes"),
        "tool_steps": session.get("tool_steps") or [],
        "plan": session.get("plan") or {},
        "skill_name": session.get("skill_name"),
        "display_title": session.get("display_title"),
        "summary": session.get("summary"),
        "clarifying_questions": session.get("clarifying_questions") or [],
        "improvement_tips": session.get("improvement_tips") or [],
        "ready_for_generation": session.get("ready_for_generation"),
        "generated_files": list(files.keys()),
        "skill_md_preview": preview,
        "validation_warnings": session.get("validation_warnings") or [],
        "history": session.get("history") or [],
    }


def export_bundle(session: dict[str, Any]) -> dict[str, str]:
    """Return {relative_path: content} for download."""
    files = dict(session.get("generated_files") or {})
    if not files:
        return {}
    slug = session.get("skill_name") or "skill"
    plat = session.get("platform", "generic")
    prefix_map = {
        "cursor": f".cursor/skills/{slug}",
        "claude": f".claude/skills/{slug}",
        "codex": f".agents/skills/{slug}",
        "harness": f"skills/{slug}",
        "generic": f"skills/{slug}",
    }
    prefix = prefix_map.get(plat, f"skills/{slug}")
    bundled: dict[str, str] = {}
    for path, content in files.items():
        clean = path.lstrip("/")
        if clean.startswith(prefix):
            bundled[clean] = content
        elif clean.startswith(f"{slug}/"):
            bundled[f"{prefix}/{clean.split('/', 1)[-1]}"] = content
        elif clean == "SKILL.md":
            bundled[f"{prefix}/SKILL.md"] = content
        else:
            bundled[f"{prefix}/{clean}"] = content
    return bundled
