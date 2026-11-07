"""Daedalus Playbooks — Phase 17C.

RIPER / process playbooks requested by the LLM:

  <NEED_PLAYBOOK: name_or_query>

Kernel task graphs (arcade, SLERP, Godot, …) live in
``daedalus.task_playbooks`` and are selected by ``daedalus.tool_router``
*before* the model talks — do not duplicate those here.

The system returns the matching playbook steps which are then injected
into the tool results so Дедал follows the correct process.

Built-in playbooks cover the most frequent ADN development patterns.
Custom playbooks can be added via data/daedalus/playbooks.json.
"""
from __future__ import annotations
from .paths import data_root

import json
import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

def _custom_file():
    return data_root() / "playbooks.json"


from .harness.context import legacy_paths as _legacy_paths  # noqa: E402

_legacy_paths(globals(), {"_DATA_ROOT": data_root, "_CUSTOM_FILE": _custom_file})

# ---------------------------------------------------------------------------
# Built-in playbook definitions
# ---------------------------------------------------------------------------

_BUILTIN_PLAYBOOKS: list[dict[str, Any]] = [
    {
        "name": "add-api-endpoint",
        "aliases": ["нов endpoint", "add endpoint", "нов route", "add route"],
        "description": "Добавяне на нов FastAPI endpoint в ADN",
        "steps": [
            "NEED_MODULES → намери правилния router файл (main.py, admin.py или отделен модул)",
            "search_code → провери дали подобен endpoint вече съществува",
            "NEED_DB_SCHEMA → ако endpoint-ът чете от DB, провери схемата",
            "Дефинирай Pydantic модели за request/response ако е необходимо",
            "Имплементирай endpoint с правилния HTTP method + path",
            "Добави error handling (try/except, HTTPException)",
            "NEED_PROPOSE_START с CONFIDENCE: 0.90+ (след горните стъпки)",
            "NEED_LOGS → провери стартирането след промяната",
        ],
    },
    {
        "name": "add-db-column",
        "aliases": ["нова колона", "add column", "alter table", "нова схема"],
        "description": "Добавяне на нова DB колона/таблица чрез ensure_schema()",
        "steps": [
            "NEED_READ_FILE: argos_data_nexus/db.py → прочети ensure_schema()",
            "Намери правилния блок (CREATE TABLE или ALTER TABLE секцията)",
            "Добави колоната с ALTER TABLE ... ADD COLUMN IF NOT EXISTS",
            "Ако е нова таблица — добави CREATE TABLE IF NOT EXISTS блок",
            "NEED_PROPOSE_START → само в db.py, никога отделна migrations папка",
            "NEED_PG: SELECT column_name FROM information_schema.columns WHERE table_name='...' → верифицирай",
        ],
    },
    {
        "name": "debug-500-error",
        "aliases": ["500 error", "грешка 500", "server error", "traceback", "дебъг грешка"],
        "description": "Дебъгване на 500 Internal Server Error",
        "steps": [
            "NEED_LOGS: 50 | ERROR → вземи последните error логове",
            "Прочети stack trace дума по дума — намери точния файл и ред",
            "NEED_READ_FILE: <файл от traceback-а> → прочети контекста",
            "Идентифицирай root cause (не symptom-а)",
            "Провери дали е edge case (None, empty list, missing key)",
            "NEED_PROPOSE_START с минимална промяна (само root cause-а)",
            "NEED_LOGS: 20 → потвърди че грешката изчезна",
        ],
    },
    {
        "name": "add-need-marker",
        "aliases": ["нов маркер", "add marker", "need marker", "нов NEED"],
        "description": "Имплементиране на нов NEED_* маркер в Дедал",
        "steps": [
            "NEED_READ_FILE: argos_data_nexus/modules/daedalus/agent.py (линии 1260-1320) → виж pattern на съществуващ маркер",
            "Дефинирай regex pattern: <NEED_XXX: field1 | field2>",
            "Добави regex extraction след останалите маркери (около ред 1275)",
            "Добави маркера в has_markers list (около ред 1316)",
            "Имплементирай handler след NEED_TASK_STATE handler (около ред 1950)",
            "Документирай в identity.py в TOOLS раздела",
            "NEED_PROPOSE_START → всичко в agent.py за regex+has_markers+handler",
            "Тест: NEED_LOGS след рестарт за чист старт",
        ],
    },
    {
        "name": "refactor-module",
        "aliases": ["рефактор", "refactor", "преструктуриране", "rename"],
        "description": "Безопасен рефактор на модул",
        "steps": [
            "search_code → намери ВСИЧКИ места където модулът се импортира/ползва",
            "NEED_READ_FILE: <target module> → прочети целия файл преди промяна",
            "Провери .daedalusignore — файлът не трябва да е игнориран",
            "Планирай промените — запиши в NEED_TASK_STATE",
            "NEED_PROPOSE_START → само таргетирания модул (не всички файлове наведнъж)",
            "search_code → верифицирай че старите imports са обновени навсякъде",
            "NEED_LOGS → провери чист старт след промяната",
        ],
    },
    {
        "name": "implement-feature",
        "aliases": ["нова функционалност", "implement", "feature", "нова фийчър"],
        "description": "Имплементиране на нова функционалност от нула",
        "steps": [
            "Уточни scope — 1 изречение какво точно прави функционалността",
            "NEED_MODULES → провери кои файлове са свързани",
            "NEED_DB_SCHEMA → ако трябват нови таблици/колони (→ добави в db.py)",
            "NEED_TASK_STATE: <описание> | planning | <стъпки> → запази плана",
            "Имплементирай от долу нагоре: helpers → логика → routes → UI",
            "NEED_PROPOSE_START за всяка логическа единица (не всичко наведнъж)",
            "NEED_LOGS след всяка стъпка → провери за грешки",
            "NEED_TASK_STATE: update | done | → изчисти при завършване",
        ],
    },
    {
        "name": "mesh-integration",
        "aliases": ["mesh", "iyveris mesh", "inter-app", "сервис интеграция"],
        "description": "Интеграция с Iyveris Mesh (извикване на друга услуга)",
        "steps": [
            "NEED_MESH: health → провери LIVE STATE на target сервиса ПЪРВО",
            "search_code → намери съществуващи mesh calls за образец",
            "Провери IYVERIS_MESH_KEY e наличен (env var, не го print-вай)",
            "Имплементирай с timeout + fallback (mesh може да е down)",
            "Добави error handling за 503/timeout отговори",
            "NEED_PROPOSE_START с CONFIDENCE: 0.85+",
            "NEED_LOGS → потвърди успешен mesh call в логовете",
        ],
    },
    {
        "name": "write-llm-prompt",
        "aliases": [
            "напиши промпт", "write prompt", "worker prompt", "system prompt",
            "подобри промпт", "improve prompt", "prompt engineering",
            "NEED_WORKER prompt", "промпт за worker", "prompt за LLM",
        ],
        "description": "Писане или подобряване на LLM промпт (worker, system prompt, template)",
        "steps": [
            "Извлечи 9-те dimension мислено: [1]TASK [2]INPUT [3]OUTPUT [4]CONSTRAINTS [5]CONTEXT [6]AUDIENCE [7]MEMORY [8]SUCCESS [9]EXAMPLES",
            "Ако [1],[3],[8] са неясни → задай MAX 3 прецизни въпроса, после действай",
            "Избери правилната роля за промпта (role assignment): кой е агентът, какво знае, какво НЕ знае",
            "Добави few-shot примери АКО форматът на output-а е нетривиален (JSON schema, специален текст)",
            "Структурирай с XML тагове ако промптът е сложен (<task>, <context>, <constraints>, <format>)",
            "За worker промпти: добави 'respond ONLY with [format]' — workers са stateless, нямат памет",
            "Приложи Token Efficiency Audit: премахни всяка дума която не променя output-а",
            "Тествай мислено: ако дам този промпт на DeepSeek V3.2, ще върне ли точно [3]OUTPUT?",
            "Ако промптът е за NEED_WORKER: include slot (fast/balanced/deep) в зависимост от сложността",
        ],
    },
    {
        "name": "data-proposal",
        "aliases": ["вмъкни данни", "insert data", "нови данни", "data proposal", "seed data"],
        "description": "Вмъкване на данни в DB чрез NEED_DATA_PROPOSAL",
        "steps": [
            "NEED_DB_SCHEMA: <target table> → провери структурата",
            "NEED_PG: SELECT * FROM <table> LIMIT 5 → виж съществуващите записи",
            "Провери за дублиращи се записи ПРЕДИ предложението",
            "NEED_CHECK_PERM: data_write → провери разрешение",
            "NEED_DATA_PROPOSAL: <category> | <Title> | [json_array] → изпрати batch",
            "Не ползвай NEED_PG INSERT директно — само NEED_DATA_PROPOSAL за данни",
        ],
    },
    # ── Resilience playbook ──────────────────────────────────────────────────
    {
        "name": "riper-stuck",
        "aliases": ["stuck", "блокиран", "не мога", "cannot proceed", "help stuck", "какво да правя", "заседнал"],
        "description": "Протокол при блокиране — 5 стъпки преди да кажеш 'не мога'",
        "steps": [
            "СТОП. Преди NEED_BLOCKED задължително изпробвай всичките 5 изхода по ред.",
            "ИЗХОД 1 — Раздели задачата: Може ли да реши само ЧАСТТА на задачата? Емитирай NEED_STAGE за частичното решение. Partial progress > no progress.",
            "ИЗХОД 2 — Делегирай подпроблема: <NEED_WORKER: deep | [конкретен под-въпрос]> — deep worker може да намери решение което ти не виждаш.",
            "ИЗХОД 3 — Потърси в кода: search_code за подобни patterns. Някой вече е решил подобен проблем в ADN — намери го и адаптирай.",
            "ИЗХОД 4 — Потърси online: <NEED_WEB: [конкретния технически въпрос]> — Stack Overflow/docs/GitHub issues.",
            "ИЗХОД 5 — Опрости радикално: Има ли версия на решението с 1/3 от сложността? Направи нея сега, документирай ограниченията.",
            "АКО ВСИЧКО FAIL: <NEED_BLOCKED: [точна причина] | [изхода 1-5 опитани] | [точно какво трябва от owner]>",
            "ПРАВИЛО: NEED_BLOCKED е последна опция, не първа. Owner трябва да вижда чатта без NEED_BLOCKED повече от 80% от времето.",
        ],
    },
    # ── RIPER-5 методологии (vibecode-pro-max-kit) ──────────────────────────
    {
        "name": "riper-research",
        "aliases": ["research mode", "само проучване", "gather facts", "информация", "проучи", "riper research"],
        "description": "RIPER-5 RESEARCH режим — само събиране на факти, нула предложения",
        "steps": [
            "ПРАВИЛО: В RESEARCH режим НЕ предлагаш решения, не правиш промени, не давен препоръки.",
            "NEED_MODULES → намери всички засегнати файлове и техните импорти",
            "search_code → провери съществуващи patterns, функции, route-ове свързани с темата",
            "NEED_LOGS: 30 → събери последните runtime сигнали (ако задачата е за debug/behavior)",
            "Прочети 2-3 ключови файла → извлечи само факти ('Файлът прави X', 'Функцията Y очаква Z')",
            "Провери от поне 2 независими источника (код + лог, или 2 файла) преди да приемеш факт за верен",
            "Разграничи: STABLE FACT (доказано от код) vs HYPOTHESIS (нужда от проверка) vs UNKNOWN",
            "Output: структуриран доклад — само наблюдения. Без 'Трябва да...', без 'Предлагам...'",
            "Ако owner пита 'какво следва?' → отговори, но маркирай изхода от RESEARCH режим",
        ],
    },
    {
        "name": "riper-plan",
        "aliases": ["plan mode", "планиране", "specification", "spec", "архитектура", "riper plan", "техническа спецификация"],
        "description": "RIPER-5 PLAN режим — изчерпателна спецификация преди ред код",
        "steps": [
            "ПРАВИЛО: В PLAN режим НЕ пишеш код и не модифицираш файлове.",
            "Ако не е правен RESEARCH → направи го първо (NEED_PLAYBOOK: riper-research)",
            "Дефинирай точно: [1]ЗАДАЧА [2]ВХОДНИ ДАННИ [3]ИЗХОДЕН РЕЗУЛТАТ [4]ОГРАНИЧЕНИЯ [5]ЗАСЕГНАТИ ФАЙЛОВЕ",
            "Data flow: опиши как данните текат от вход към изход — всяка трансформация",
            "Dependencies: кои функции/модули/таблици се засягат директно и индиректно",
            "Failure modes: кои edge cases могат да счупят нещото (None, empty, race condition, timeout)",
            "Test matrix: за всеки failure mode — как ще се провери че е fixed",
            "Rollback plan: как се връща назад ако нещо се счупи",
            "Размер: ако планът изисква > 120 реда код → раздели на фази с NEED_TASK_STATE",
            "Финал: Изложи план като numbered checklist. Изчакай потвърждение от owner преди EXECUTE.",
        ],
    },
    {
        "name": "riper-execute",
        "aliases": ["execute mode", "изпълнение", "implement plan", "riper execute", "приложи плана"],
        "description": "RIPER-5 EXECUTE режим — имплементира ТОЧНО одобрения план, без допълнения",
        "steps": [
            "ПРАВИЛО: Имплементирай САМО това, което е в одобрения план. Нула 'подобрения' без одобрение.",
            "Потвърди план файл или numbered checklist от PLAN режим — ако няма → СТОП, върни се към PLAN",
            "NEED_MODULES → финална проверка на засегнатите файлове (може да са се сменили)",
            "За всяка стъпка от плана: CONFIDENCE ≥ 0.75 преди edit (виж CODING PROTOCOL)",
            "Един файл на proposal — ако трябват повече → последователно, с потвърждение между тях",
            "След всеки edit: провери DoD-1 до DoD-5 (от CODING PROTOCOL в персоната)",
            "NEED_LOGS: 20 → след последния edit провери runtime (нови грешки?)",
            "Ако намериш bug извън плана → ДОКУМЕНТИРАЙ го (NEED_INBOX_MSG), не го оправяй сега",
            "Финал: DoD ✅ [1✓ 2✓ 3✓ 4✓ 5✓] + кратко summary на направеното",
        ],
    },
    {
        "name": "riper-debug",
        "aliases": ["debug mode", "дебъг", "root cause", "разследване", "riper debug", "incident", "500 грешка", "crash"],
        "description": "RIPER-5 DEBUG режим — evidence-first разследване, никакво гадаене",
        "steps": [
            "ПРАВИЛО: Никога не гадаеш root cause. Всяко заключение е подкрепено от доказателство.",
            "NEED_LOGS: 50 | ERROR → събери всички error логове от последния инцидент",
            "Прочети stack trace дума по дума — намери точния файл, ред и функция",
            "Формулирай 2-3 конкурентни хипотези (НЕ само първата правдоподобна)",
            "За всяка хипотеза: дефинирай тест → изпълни → потвърди или елиминирай с доказателство",
            "Конструирай timeline: кога точно е започнало, какво се е случило преди, корелирани events",
            "Провери environmental факти: скорошни промени в код, env vars, deploy, dependency update",
            "Root cause statement: 'Причината е X, доказано от Y и Z.' — не 'вероятно е X'",
            "Минимална fix: NEED_PROPOSE_START само за root cause-а — не за симптомите",
            "Верификация: NEED_LOGS: 20 след fix → потвърди липсата на грешката",
            "Recurrence prevention: какво трябва да се добави (log, guard, test) за да не се повтори",
        ],
    },
]


def _score_match(playbook: dict[str, Any], query: str) -> int:
    """Simple relevance score for a query against a playbook."""
    q = query.lower()
    score = 0
    name = playbook["name"].lower()
    desc = playbook["description"].lower()
    aliases = [a.lower() for a in playbook.get("aliases", [])]

    if q == name:
        score += 100
    elif q in name or name in q:
        score += 60
    for alias in aliases:
        if alias in q or q in alias:
            score += 40
    if any(word in desc for word in q.split()):
        score += 10
    return score


def search(query: str, top_k: int = 2) -> list[dict[str, Any]]:
    """Return top-k matching playbooks for a query."""
    custom = _load_custom()
    all_books = _BUILTIN_PLAYBOOKS + custom
    scored = [(pb, _score_match(pb, query)) for pb in all_books]
    scored.sort(key=lambda x: x[1], reverse=True)
    results = [pb for pb, score in scored if score > 0]
    if not results:
        results = all_books  # fallback: show all
    return results[:top_k]


def get(name: str) -> dict[str, Any] | None:
    """Get a specific playbook by exact name."""
    all_books = _BUILTIN_PLAYBOOKS + _load_custom()
    for pb in all_books:
        if pb["name"].lower() == name.lower():
            return pb
    return None


def list_all() -> list[dict[str, str]]:
    """Return name + description for all playbooks."""
    all_books = _BUILTIN_PLAYBOOKS + _load_custom()
    return [{"name": pb["name"], "description": pb["description"]} for pb in all_books]


def format_for_prompt(playbooks: list[dict[str, Any]]) -> str:
    """Format matched playbooks for injection into tool results."""
    parts: list[str] = []
    for pb in playbooks:
        steps = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(pb["steps"]))
        parts.append(
            f"📋 PLAYBOOK: {pb['name']}\n"
            f"{pb['description']}\n"
            f"Стъпки:\n{steps}"
        )
    return "\n\n".join(parts) if parts else "Не намерих подходящ playbook за това запитване."


def _load_custom() -> list[dict[str, Any]]:
    """Load custom playbooks from data/daedalus/playbooks.json if it exists."""
    try:
        if _custom_file().exists():
            data = json.loads(_custom_file().read_text("utf-8"))
            if isinstance(data, list):
                return data
    except Exception as exc:
        log.debug("custom playbooks load failed (non-fatal): %s", exc)
    return []
