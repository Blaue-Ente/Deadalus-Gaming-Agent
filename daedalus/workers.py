"""Worker agents — stateless, parallel LLM calls delegated by master Дедал.

Usage (from agent tool loop):
    from .workers import parse_worker_markers, call_workers_parallel
    calls = parse_worker_markers(raw)
    if calls:
        results = await call_workers_parallel(calls)

NEED_WORKER format emitted by master:
    <NEED_WORKER: slot | task description (can be multi-line)>

slot is one of: fast / balanced / deep / analysis  (or a literal model id)
  fast     — Gemini 2.5 Flash (OpenRouter): класификация, JSON, резюмиране, извличане,
             превод, бърз отговор. Компактни изходи (5-10 реда). ~1-3s
  balanced — Llama 3.3 70B (OpenRouter): финансов/технически/research/writer/planner
             — средно-сложен анализ + изводи. ~5-15s
  deep     — DeepSeek V3.2 (OpenRouter): code writing/review, сложен синтез,
             стратегически документи, тежки reasoning задачи. ~10-30s
  analysis — DeepSeek R1-0528 (OpenRouter, DISABLED by default): САМО за задачи
             изискващи chain-of-thought reasoning — Wyckoff, позиционна математика,
             multi-step market analysis. max_tokens=5000. ~60-100s

Each slot uses a distinct WORKER_PERSONA. Personas са рамки за качество, не domain-locks —
адаптивна роля се избира от модела според конкретната задача.
Literal model-id slots (e.g. "deepseek/...") fall back to the balanced persona.
"""
from __future__ import annotations

from .model_defaults import OPENROUTER_DEFAULT_MODEL

import asyncio
import json
import logging
import re

log = logging.getLogger(__name__)


def _is_rate_limited(exc: BaseException) -> bool:
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if code == 429:
        return True
    msg = str(exc).lower()
    return (
        "429" in msg
        or "rate-limited" in msg
        or "rate_limit" in msg
        or "too many requests" in msg
    )

_DB_KEY = "adn:daedalus_worker_config"

# Per-slot system prompts — specialized roles that significantly improve output
# quality by giving each worker a concrete identity and output contract.
# Common rules (no NEED_* markers, no clarifications, language match) are
# repeated in each so the prompt is self-contained.

_COMMON_RULES = (
    "ОБЩИ ПРАВИЛА (валидни за всички):\n"
    "• Никога не emit NEED_* маркери — нямаш инструменти.\n"
    "• Никога не задавай уточняващи въпроси — работи с дадения контекст.\n"
    "• Никога не започвай с 'Ето резултата:', 'Разбира се!', 'Готово!' и подобни запълнители.\n"
    "• Отговаряй на езика на задачата (BG ако задачата е на BG, EN ако е на EN).\n"
)

WORKER_PERSONAS: dict[str, str] = {

    "fast": (
        "Ти си БЪРЗ ИЗПЪЛНИТЕЛ в системата Daedalus.\n"
        "Извикан от master агент Дедал за кратка подзадача с компактен изход.\n"
        "\n"
        "АДАПТИВНА РОЛЯ — избери според задачата:\n"
        "• Класификация / тагване → списък или JSON със категории\n"
        "• Кратко резюме → 3-5 bullet точки с ключови моменти\n"
        "• Извличане на данни → структурирана таблица или JSON\n"
        "• Бърз отговор на въпрос → 1-3 изречения по същество\n"
        "• Превод / реформатиране → директен резултат, нищо повече\n"
        "• Генериране на варианти (имена, идеи) → номериран списък\n"
        "\n"
        "ПРИНЦИПИ:\n"
        "• Само резултат — без уводи, обяснения, или заключения освен ако се иска.\n"
        "• Компактност > красноречие. Целиш максимум 5-10 реда.\n"
        "• Машинно-четим формат когато е приложим (JSON, Markdown таблица).\n"
        "• Ако нещо не е ясно от контекста — предположи разумно и продължи.\n"
        "\n"
        + _COMMON_RULES
    ),

    "balanced": (
        "Ти си УНИВЕРСАЛЕН АНАЛИЗАТОР в системата Daedalus.\n"
        "Извикан от master агент Дедал за задача изискваща изследване, сравнение,\n"
        "анализ или средно-сложен синтез — НЕЗАВИСИМО от domain-а.\n"
        "\n"
        "АДАПТИВНА РОЛЯ — приеми правилния персонаж според задачата:\n"
        "• Финансов / market въпрос → финансов анализатор (числа, тенденции, рискове)\n"
        "• Технически / архитектурен → senior engineer (trade-offs, реализуемост)\n"
        "• Research / факт-проверка → researcher (източници, контекст, нюанси)\n"
        "• Сравнение на варианти → product analyst (таблица + препоръка)\n"
        "• Бизнес / стратегия → бизнес консултант (impact, ROI, рискове)\n"
        "• Писане на текст / комуникация → writer (тон, структура, аудитория)\n"
        "• Планиране на задача → project planner (стъпки, зависимости, време)\n"
        "• Обяснение на концепция → учител (от просто към сложно, примери)\n"
        "\n"
        "ПРИНЦИПИ ЗА ВСЯКА РОЛЯ:\n"
        "• Започни директно с резултата/заключението — без въведение.\n"
        "• Markdown структура: ### секции, таблици при сравнения, bold за ключови думи.\n"
        "• Всяко твърдение подкрепено с логика — не голи мнения.\n"
        "• Конкретика > общи приказки ('подобрява с 30%' > 'значително подобрява').\n"
        "• Неутрален тон — избягвай 'страхотно', 'перфектно', маркетингов език.\n"
        "• Завърши с 'Извод:' или 'Препоръка:' (1-3 изречения) когато е приложимо.\n"
        "\n"
        + _COMMON_RULES
    ),

    "deep": (
        "Ти си ГЛАВЕН СИНТЕЗАТОР И КРИТИЧЕН МИСЛИТЕЛ в системата Daedalus.\n"
        "Извикан от master агент Дедал за най-сложните, критични или финални подзадачи.\n"
        "Качеството на твоя изход е по-важно от компактността.\n"
        "\n"
        "АДАПТИВНА РОЛЯ — приеми правилния персонаж според задачата:\n"
        "• CODE задачи (писане) → senior engineer: production-quality код с error\n"
        "  handling, docstring, type hints; 2-3 изречения за KEY дизайн решения.\n"
        "• CODE REVIEW → architect: посочи проблеми с конкретна референция (файл/ред/функция),\n"
        "  структурирай по: 🔴 Критично | 🟡 Важно | 🟢 Препоръка.\n"
        "• ФИНАЛЕН СИНТЕЗ → integrate всички данни в кохерентен наратив:\n"
        "  Заключение → Обосновка → Следващи стъпки.\n"
        "• СЛОЖЕН REASONING → разсъждавай стъпка по стъпка, маркирай предположенията,\n"
        "  посочи къде доказателствата са слаби.\n"
        "• СТРАТЕГИЧЕСКИ ДОКУМЕНТ → consultant: контекст → опции → препоръка с обосновка.\n"
        "• ДЪЛБОК АНАЛИЗ → expert: едновременно широта (всички аспекти) и дълбочина\n"
        "  (детайли там, където са важни).\n"
        "• ДЕБАТ / OPPOSING VIEWS → devil's advocate: представи и силните, и слабите страни.\n"
        "\n"
        "ОБЩИ ПРИНЦИПИ ЗА ВСЯКА РОЛЯ:\n"
        "• Не спирай при 'достатъчно добро' — довърши мисълта напълно.\n"
        "• Посочвай edge cases, рискове, алтернативи когато са релевантни.\n"
        "• Ако нещо в задачата е грешно, противоречиво или непълно — кажи го директно.\n"
        "• Когато имаш мнение — заяви го с обосновка, не седи на оградата.\n"
        "\n"
        + _COMMON_RULES
    ),

    "analysis": (
        "You are a DEEP REASONING ANALYST powered by DeepSeek R1.\n"
        "You are invoked for tasks that require genuine multi-step chain-of-thought reasoning.\n"
        "Think step by step before answering. Show your reasoning process clearly.\n"
        "\n"
        "BEST USE CASES (this slot excels here):\n"
        "• Technical analysis pattern recognition (Wyckoff phases, Elliott waves, SMC)\n"
        "• Position sizing and risk/reward mathematics with all intermediate steps\n"
        "• Multi-factor scoring and stock/asset ranking with explicit calculations\n"
        "• Options strategy construction with Greeks and P&L calculations\n"
        "• Market structure analysis requiring multi-variable reasoning\n"
        "• Logical debugging of quantitative strategies (find ALL bugs + root causes)\n"
        "\n"
        "OUTPUT FORMAT:\n"
        "• Show reasoning steps clearly — numbered or structured\n"
        "• State assumptions explicitly\n"
        "• Highlight conclusions with ### headers\n"
        "• For math: show every calculation step, not just the final answer\n"
        "• End with a crisp Summary/Conclusion section\n"
        "\n"
        "NOTE: This slot has a large token budget (5000 tokens) and higher latency (60-100s).\n"
        "Do NOT use for: simple questions, code generation, JSON extraction — use fast/deep instead.\n"
        "\n"
        + _COMMON_RULES
    ),

    "claude": (
        "Ти си CLAUDE — висококачествен анализатор и оркестратор в системата Daedalus.\n"
        "Извикан от master агент Дедал само когато задачата изисква най-добрата налична преценка,\n"
        "нюансирано разбиране или финален авторитетен синтез.\n"
        "\n"
        "КОГА СЕ ИЗПОЛЗВА ТОЗИ СЛОТ (само при реална нужда):\n"
        "• Сложен мулти-стъпков анализ където R1 не успява да завърши (code+reasoning комбо)\n"
        "• Финансово становище изискващо едновременно количествен анализ И качествена оценка\n"
        "• Оркестрация на план с много зависимости — кой worker прави какво и в какъв ред\n"
        "• Критична code review където грешката би имала сериозни последствия\n"
        "• Синтез от множество несвързани данни в кохерентен executive-level доклад\n"
        "• Дебат/devil's advocate на инвестиционна теза с реална аргументация и от двете страни\n"
        "\n"
        "СТАНДАРТИ ЗА КАЧЕСТВО:\n"
        "• Давай конкретни изводи — не оставяй Николай да гадае какво препоръчваш.\n"
        "• Ако виждаш риск или грешка в предпоставките — кажи го директно, не го заобикаляй.\n"
        "• Структурирай с ### секции: Анализ → Рискове → Препоръка → Следващи стъпки.\n"
        "• Посочвай конкретни числа, проценти, файлове, редове — не общи твърдения.\n"
        "• Финалното мнение е твое — заяви го с обосновка, не седи на оградата.\n"
        "\n"
        "NOTE: Скъп слот (~$3-5m$ на call). Използвай само когато deep/analysis не са достатъчни.\n"
        "\n"
        + _COMMON_RULES
    ),

    "planner": (
        "Ти си СТРАТЕГИЧЕСКИ ПЛАНОВИК в системата Daedalus.\n"
        "Имаш deep reasoning способности. Задачата ти е САМО да създадеш структуриран план —\n"
        "НЕ го изпълнявай, НЕ пиши код, НЕ давай финален резултат.\n"
        "\n"
        "ИЗХОД — задължителна структура:\n"
        "## ПЛАН\n"
        "**Цел:** (1 изречение)\n"
        "**Подход:** (2-3 изречения — защо тази стратегия)\n"
        "\n"
        "**Стъпки:**\n"
        "1. [Стъпка] — [файл/компонент] — [очакван изход]\n"
        "2. ...\n"
        "\n"
        "**Зависимости:** (кои стъпки блокират кои)\n"
        "**Рискове:** (top 2-3 неща, които могат да се объркат)\n"
        "**Критерий за завършване:** (как ще знаем че е готово)\n"
        "\n"
        "ПРИНЦИПИ:\n"
        "• Декомпозирай до атомарни стъпки, изпълними от един следващ worker без допълнителен контекст.\n"
        "• Посочвай конкретни файлове, функции, таблици — не абстрактни описания.\n"
        "• При неясноти в задачата — предположи разумно и продължи (без да питаш).\n"
        "• Планът трябва да е достатъчен за CODER worker да го изпълни директно.\n"
        "\n"
        + _COMMON_RULES
    ),

    "coder": (
        "Ти си CODER АГЕНТ в системата Daedalus.\n"
        "Получаваш конкретна задача с TASK CONTRACT и пишеш production-ready код.\n"
        "\n"
        "АКО получиш TASK CONTRACT (структуриран като [GLOBAL PLAN] / [YOUR FILE] / [INTERFACE] / [FORBIDDEN]):\n"
        "  • Прочети GLOBAL PLAN за да разбереш архитектурата\n"
        "  • Пиши САМО файла посочен в [YOUR FILE]\n"
        "  • Спазвай ТОЧНО интерфейсите в [INTERFACE] — не ги преименувай\n"
        "  • НЕ пипай файловете в [FORBIDDEN]\n"
        "  • Завърши с код, отговарящ на [ACCEPTANCE CRITERIA]\n"
        "  • OUTPUT: САМО пълния код в ```lang filename``` блок — нищо друго\n"
        "\n"
        "АКО получиш свободна задача (без contract):\n"
        "• Нова функционалност → чист, типизиран код с error handling\n"
        "• Bug fix → идентифицирай root cause, поправи минимално\n"
        "• Refactor → подобри без да чупиш API\n"
        "• HTML/JS/CSS → работещ, responsive, без external deps освен ако се иска\n"
        "\n"
        "СТАНДАРТИ (ВИНАГИ):\n"
        "• Типизация: Python type hints, TS interfaces — задължителни.\n"
        "• Форматирай с ```lang filename``` за всеки файл.\n"
        "• Не трункирай — давай ПЪЛНИЯ код. Никога '// ... rest of code ...'.\n"
        "• Нямаш достъп до playground — пишеш само текст. Дедал ще запише файловете.\n"
        "• Ако задачата е невъзможна или противоречива — кажи защо, предложи алтернатива.\n"
        "\n"
        + _COMMON_RULES
    ),

    "reviewer": (
        "Ти си ARCHITECT REVIEWER в системата Daedalus.\n"
        "Задачата ти е да прегледаш предоставения код/резултат и да дадеш авторитетна оценка.\n"
        "\n"
        "АКО получиш PLAYGROUND PROJECT REVIEW (project_manifest + файлове + изискване):\n"
        "  Провери за ВСЕКИ файл:\n"
        "  □ Спазва ли публичния API от project_manifest?\n"
        "  □ Модифицира ли само разрешените файлове (не пипа чужди модули)?\n"
        "  □ Дублира ли съществуваща логика от друг файл?\n"
        "  □ Въвежда ли hidden coupling (директен import на конкретна имплементация)?\n"
        "  □ Покрива ли acceptance criteria от task contract-а?\n"
        "  □ Има ли syntax error / очевидни runtime грешки?\n"
        "  □ Може ли тази промяна да счупи друг модул?\n"
        "\n"
        "СТРУКТУРА НА REVIEW:\n"
        "## VERDICT: APPROVED | NEEDS_FIX | BLOCKED\n"
        "(APPROVED=готово за publish; NEEDS_FIX=конкретни файлове/редове; BLOCKED=фундаментален проблем)\n"
        "\n"
        "🔴 **Критично** (блокира publish):\n"
        "- [файл: конкретен проблем + ред (ако е известен) + точна поправка]\n"
        "\n"
        "🟡 **Важно** (поправи преди publish):\n"
        "- ...\n"
        "\n"
        "🟢 **Препоръка** (nice-to-have за v2):\n"
        "- ...\n"
        "\n"
        "**Файлове за поправка:** [списък само на файловете с NEEDS_FIX проблеми]\n"
        "**Извод:** [1-2 изречения финална оценка]\n"
        "\n"
        "ПРИНЦИПИ:\n"
        "• Бъди конкретен — 'game.js ред 47: липсва null check за canvas context' > 'добави error handling'.\n"
        "• При NEEDS_FIX: посочи ТОЧНО кой файл, каква промяна — coder агентът ще го поправи.\n"
        "• Не хвали без причина. Ако е добро — кажи APPROVED и приключи.\n"
        "• Гледай: security, корректност, performance bottlenecks, API breaking changes, missing error paths.\n"
        "\n"
        + _COMMON_RULES
    ),
}

# Fallback за непознати слотове (literal model id като slot name)
_DEFAULT_WORKER_PERSONA = WORKER_PERSONAS["balanced"]

WORKER_SLOTS: dict[str, dict] = {
    # ── Класически слотове ───────────────────────────────────────────────────
    "fast":     {"label": "⚡ Бърз изпълнител (Gemini 2.5 Flash)",         "default_model": "google/gemini-2.5-flash",              "default_provider": "openrouter"},
    "balanced": {"label": "📊 Универсален анализатор (Llama 3.3 70B)",     "default_model": "meta-llama/llama-3.3-70b-instruct",    "default_provider": "openrouter"},
    "deep":     {"label": "🧠 Главен синтезатор / Мислител (DS V3.2)",     "default_model": "deepseek/deepseek-v3.2",               "default_provider": "openrouter"},
    "analysis": {"label": "🔬 Reasoning анализатор (DS R1-0528, бавен)",   "default_model": "deepseek/deepseek-r1-0528",            "default_provider": "openrouter"},
    "claude":   {"label": "✨ Claude — хибриден оркестратор (при нужда)",  "default_model": OPENROUTER_DEFAULT_MODEL,          "default_provider": "openrouter"},
    # ── Pipeline слотове (роли, не конкретни модели) ─────────────────────────
    "planner":  {"label": "🗺 Плановик — план + декомпозиция (DS R1)",     "default_model": "deepseek/deepseek-r1-0528",            "default_provider": "openrouter"},
    "coder":    {"label": "💻 Coder — пише/поправя код (Qwen3 / DS V3)",   "default_model": "qwen/qwen3-coder-30b-a3b-instruct",    "default_provider": "novita"},
    "reviewer": {"label": "🔍 Reviewer — одобрява/блокира (Claude Sonnet)","default_model": OPENROUTER_DEFAULT_MODEL,          "default_provider": "openrouter"},
    # ── Auto слот — избира от Model Registry ─────────────────────────────────
    "auto":     {"label": "🤖 Auto — registry-driven избор",               "default_model": "_auto_",                              "default_provider": "_auto_"},
}

_WORKER_RE = re.compile(
    r"<NEED_WORKER:\s*([a-zA-Z0-9_.\-]{1,60})\s*\|\s*(.+?)\s*>",
    re.DOTALL,
)

MAX_PARALLEL_WORKERS = 5


def parse_worker_markers(raw: str) -> list[dict]:
    """Extract all <NEED_WORKER: slot | task> from raw text. Returns [{slot, task}]."""
    if not raw or "NEED_WORKER" not in raw:
        return []
    matches = _WORKER_RE.findall(raw)
    return [{"slot": s.strip(), "task": t.strip()} for s, t in matches][:MAX_PARALLEL_WORKERS]


def _worker_cfg_path():
    from .paths import data_root
    return data_root() / "worker_config.json"


def _read_worker_db() -> dict:
    try:
        from .kv import replit_required as _replit_required
        db = _replit_required()
        raw = db.get(_DB_KEY)
        if raw:
            return json.loads(raw) if isinstance(raw, str) else dict(raw)
    except Exception:
        pass
    try:
        path = _worker_cfg_path()
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _write_worker_db(cfg: dict) -> None:
    try:
        from .kv import replit_required as _replit_required
        db = _replit_required()
        db[_DB_KEY] = json.dumps(cfg)
        return
    except Exception:
        pass
    try:
        path = _worker_cfg_path()
        path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as extra:
        log.warning("workers: cannot persist config: %s", extra)


def _resolve_replit_gemini_creds() -> tuple[str, str]:
    """Stub — Replit AI integrations are disabled. Returns empty creds."""
    return "", ""


def _resolve_replit_openai_creds() -> tuple[str, str]:
    """Stub — Replit AI integrations are disabled. Returns empty creds."""
    return "", ""


def get_worker_config() -> dict:
    """Return resolved worker slot configs: {fast: {...}, balanced: {...}, deep: {...}}"""
    from .llm_config import (
        PROVIDER_DEFAULTS,
        get_brain_config,
        is_nvidia_free_only,
        resolve_provider_api_key,
    )

    master = get_brain_config()
    db_cfg = _read_worker_db()
    nv_free = is_nvidia_free_only()

    # NVIDIA free-only slot defaults (orchestrator master delegates to these workers)
    _nv_free_slots: dict[str, tuple[str, str]] = {
        "fast": ("nvidia", "openai/gpt-oss-20b"),
        "balanced": ("nvidia", "deepseek-ai/deepseek-v4-flash"),
        "deep": ("nvidia", "mistralai/mistral-large-3-675b-instruct-2512"),
        "coder": ("nvidia", "deepseek-ai/deepseek-v4-flash"),
        "planner": ("nvidia", "moonshotai/kimi-k2.6"),
        "reviewer": ("nvidia", "mistralai/mistral-large-3-675b-instruct-2512"),
        "analysis": ("nvidia", "deepseek-ai/deepseek-v4-pro"),
        "claude": ("nvidia", "mistralai/mistral-large-3-675b-instruct-2512"),
    }

    result: dict[str, dict] = {}
    for slot, meta in WORKER_SLOTS.items():
        saved = db_cfg.get(slot, {})
        if nv_free and slot in _nv_free_slots:
            provider, model = _nv_free_slots[slot]
            pd = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["openai"])
            api_key = resolve_provider_api_key(provider)
            if api_key:
                result[slot] = {
                    "model": model,
                    "provider": provider,
                    "api_key": api_key,
                    "base_url": pd["base_url"],
                    "sdk": pd["sdk"],
                    "enabled": saved.get("enabled", True),
                    "source": "nvidia_free_only",
                }
                continue
        if saved.get("model") and saved.get("provider"):
            provider = saved["provider"]
            pd = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["anthropic"])
            # Special case: Replit AI Integration for Gemini — resolve creds from env
            if provider == "replit_gemini":
                rg_key, rg_url = _resolve_replit_gemini_creds()
                api_key  = saved.get("api_key") or rg_key
                base_url = saved.get("base_url") or rg_url
            # Special case: Replit AI Integration for OpenAI — resolve creds from env
            elif provider == "replit_openai":
                ro_key, ro_url = _resolve_replit_openai_creds()
                api_key  = saved.get("api_key") or ro_key
                base_url = saved.get("base_url") or ro_url
            else:
                api_key  = (
                    saved.get("api_key")
                    or resolve_provider_api_key(provider)
                    or master.get("api_key", "")
                )
                base_url = saved.get("base_url") or pd["base_url"]
            result[slot] = {
                "model":    saved["model"],
                "provider": provider,
                "api_key":  api_key,
                "base_url": base_url,
                "sdk":      pd["sdk"],
                "enabled":  saved.get("enabled", True),
                "source":   "db",
            }
        else:
            default_model    = meta["default_model"]
            default_provider = meta["default_provider"]
            pd = PROVIDER_DEFAULTS.get(default_provider, PROVIDER_DEFAULTS["anthropic"])
            # Resolve managed integration creds if needed
            if default_provider == "replit_openai":
                ro_key, ro_url = _resolve_replit_openai_creds()
                resolved_key = ro_key
                resolved_url = ro_url
            elif default_provider == "replit_gemini":
                rg_key, rg_url = _resolve_replit_gemini_creds()
                resolved_key = rg_key
                resolved_url = rg_url
            else:
                resolved_key = (
                    resolve_provider_api_key(default_provider)
                    or master.get("api_key", "")
                )
                resolved_url = master.get("base_url") or pd["base_url"]
            result[slot] = {
                "model":    default_model,
                "provider": default_provider,
                "api_key":  resolved_key,
                "base_url": resolved_url or pd["base_url"],
                "sdk":      pd["sdk"],
                "enabled":  True,
                "source":   "default",
            }
    return _inherit_master_local(result, master)


def _inherit_master_local(workers: dict, master: dict) -> dict:
    """When the owner picked a local brain, every slot uses it.

    Saved OpenRouter/NVIDIA worker defaults must not keep calling the cloud
    while Studio chat, CLI, and autopilot are on Ollama.
    """
    from .llm_config import PROVIDER_DEFAULTS, is_loopback_gateway
    from .local_llm import is_local_provider, local_requested

    master_provider = str(master.get("provider") or "")
    if not (
        local_requested()
        or master.get("local")
        or is_local_provider(master_provider)
        or is_loopback_gateway(master_provider)
    ):
        return workers
    provider = master.get("provider") or "ollama"
    pd = PROVIDER_DEFAULTS.get(provider) or PROVIDER_DEFAULTS.get("ollama") or {}
    out: dict[str, dict] = {}
    for slot, w in workers.items():
        slot_provider = str(w.get("provider") or "")
        if is_local_provider(slot_provider) or is_loopback_gateway(slot_provider):
            out[slot] = w
            continue
        out[slot] = {
            **w,
            "model": master.get("model") or w.get("model"),
            "provider": provider,
            "api_key": master.get("api_key") or w.get("api_key") or "",
            "base_url": master.get("base_url") or pd.get("base_url") or "",
            "sdk": master.get("sdk") or pd.get("sdk") or "openai",
            "source": "master_local",
        }
    return out


def set_worker_slot(slot: str, *, model: str, provider: str,
                    api_key: str = "", base_url: str = "",
                    enabled: bool = True) -> None:
    """Persist a single worker slot config to Replit DB."""
    from .llm_router_config import require_llm_write_permission
    from .llm_config import PROVIDER_DEFAULTS

    require_llm_write_permission()
    cfg = _read_worker_db()
    pd = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["anthropic"])
    cfg[slot] = {
        "model":    model,
        "provider": provider,
        "api_key":  api_key,
        "base_url": base_url or pd["base_url"],
        "enabled":  enabled,
    }
    _write_worker_db(cfg)


def clear_worker_config() -> None:
    """Remove all worker DB overrides."""
    from .llm_router_config import require_llm_write_permission

    require_llm_write_permission()
    try:
        from .kv import replit_required as _replit_required
        db = _replit_required()
        del db[_DB_KEY]
    except Exception:
        pass
    try:
        _worker_cfg_path().unlink(missing_ok=True)
    except Exception:
        pass


async def _disabled_slot(slot: str) -> dict:
    return {
        "slot":         slot,
        "model":        "disabled",
        "task_preview": "—",
        "result":       f"⚠ Worker slot '{slot}' е изключен.",
        "ok":           False,
    }


# Per-slot max_tokens defaults (can be overridden by DB config per slot)
_SLOT_MAX_TOKENS: dict[str, int] = {
    "fast":     2048,
    "balanced": 4096,
    "deep":     6144,
    "analysis": 5000,   # R1 needs large budget — reasoning chain consumes most tokens
    "claude":   8192,   # Claude handles long outputs well — give full budget
    "planner":  5000,   # R1 — reasoning chain needs budget
    "coder":    8192,   # Coder needs full budget for complete implementations
    "reviewer": 4096,   # Review output is structured and moderate length
    "auto":     4096,   # Will be overridden by selected model's tier defaults
}


def _update_registry_stats(registry_id: str | None, success: bool, latency_ms: int) -> None:
    """Async-safe fire-and-forget stats update — never raises."""
    if not registry_id:
        return
    try:
        from . import model_registry as mr
        mr.update_stats(registry_id, success=success, latency_ms=latency_ms)
    except Exception:
        pass


async def _call_worker_single(slot: str, task: str, slot_cfg: dict) -> dict:
    """Execute one worker call. Returns {slot, model, task_preview, result, ok}."""
    _t0      = asyncio.get_event_loop().time()
    model    = slot_cfg.get("model", "deepseek/deepseek-v3.2")
    sdk      = slot_cfg.get("sdk", "openai")
    provider = slot_cfg.get("provider", "openrouter")
    api_key  = slot_cfg.get("api_key", "")
    base_url = slot_cfg.get("base_url", "")
    # DB config max_tokens takes priority over slot defaults
    max_tokens = slot_cfg.get("max_tokens") or _SLOT_MAX_TOKENS.get(slot, 4096)

    # Pick the specialized persona for this slot; fall back to balanced for
    # literal model-id slots (e.g. <NEED_WORKER: deepseek/... | task>)
    persona = WORKER_PERSONAS.get(slot, _DEFAULT_WORKER_PERSONA)

    try:
        from . import daedalus_mesh as dm
        if dm.should_route_worker_via_mesh(provider, model):
            _is_reasoning = (model.startswith("gpt-5") or model.startswith("o4") or
                             model.startswith("o3") or "r1" in model.lower() or
                             "qwq" in model.lower() or slot == "analysis")
            _mesh_max = max(max_tokens * 2, 2000) if _is_reasoning else max_tokens
            mesh_res = await dm.worker_complete(
                slot=slot,
                system=persona,
                user=task,
                provider=provider,
                model=model,
                max_tokens=_mesh_max,
            )
            result_text = dm.extract_text(mesh_res)
            model_used = mesh_res.get("model") or model
            # A budget/route stop is not worker output: report it as a failure, keep the text.
            mesh_stopped = bool(mesh_res.get("budget_stopped")) or mesh_res.get("ok") is False
            mesh_ok = bool(str(result_text or "").strip()) and not mesh_stopped
            mesh_error = (
                "" if mesh_ok
                else ("budget_stopped" if mesh_res.get("budget_stopped")
                      else str(mesh_res.get("error") or "empty_or_failed_reply")[:200])
            )
            elapsed_ms = int((asyncio.get_event_loop().time() - _t0) * 1000)
            log.info(
                "Worker [%s/%s] mesh_router done (%d chars, %dms, $%.6f, %s)",
                slot, model_used, len(result_text), elapsed_ms,
                float(mesh_res.get("cost_usd") or 0.0),
                mesh_res.get("provider") or "?",
            )
            _update_registry_stats(slot_cfg.get("_registry_id"), mesh_ok, elapsed_ms)
            return {
                "slot":         slot,
                "model":        model_used,
                "task_preview": task[:100],
                "result":       result_text,
                "ok":           mesh_ok,
                **({"error": mesh_error} if not mesh_ok else {}),
                "mesh_router":  True,
                "cost_usd":     mesh_res.get("cost_usd"),
                "provider":     mesh_res.get("provider"),
            }
        result_text = ""
        from .harness import gateway as _gw
        if _gw.path_enabled("workers"):
            from .llm_config import PROVIDER_DEFAULTS
            _pd = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["openai"])
            _route = _gw.route_for(provider, model, api_key, base_url or _pd["base_url"], sdk)
            _reasoning = (model.startswith("gpt-5") or model.startswith("o4") or model.startswith("o3")
                          or "r1" in model.lower() or "qwq" in model.lower() or slot == "analysis")
            _budget = max(max_tokens * 2, 2000) if _reasoning else max_tokens
            for attempt in range(4):
                _gr = await _gw.call("worker", [{"role": "user", "content": task}], system=persona,
                                     max_tokens=_budget, route=_route, reasoning=_reasoning,
                                     client_id=f"daedalus/worker/{slot}")
                if _gr.ok:
                    result_text = _gr.text
                    break
                if attempt < 3 and _gr.error and _gr.error.type == "quota" and _is_rate_limited(Exception(_gr.error.message)):
                    wait = 8 * (2 ** attempt)
                    log.warning("Worker [%s/%s] rate-limited (try %d/4), sleep %ss: %s",
                                slot, model, attempt + 1, wait, _gr.error.message)
                    await asyncio.sleep(wait)
                    continue
                raise _gw.GatewayCallError(_gr.error or _gw.GatewayError("provider_error", "worker call failed"))
        for attempt in range(4 if not _gw.path_enabled("workers") else 0):
            try:
                if sdk == "anthropic":
                    from anthropic import AsyncAnthropic
                    client = AsyncAnthropic(api_key=api_key, base_url=base_url or None, max_retries=1)
                    response = await client.messages.create(
                        model=model,
                        max_tokens=max_tokens,
                        system=persona,
                        messages=[{"role": "user", "content": task}],
                    )
                    result_text = response.content[0].text if response.content else ""
                else:
                    from openai import AsyncOpenAI
                    from .llm_config import PROVIDER_DEFAULTS, openai_compat_headers
                    pd = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["openai"])
                    extra = openai_compat_headers(provider)
                    client = AsyncOpenAI(
                        api_key=api_key,
                        base_url=base_url or pd["base_url"],
                        default_headers=extra or None,
                    )
                    # Reasoning models need extra budget for chain-of-thought before final answer
                    _is_reasoning = (model.startswith("gpt-5") or model.startswith("o4") or
                                     model.startswith("o3") or "r1" in model.lower() or
                                     "qwq" in model.lower() or slot == "analysis")
                    kwargs: dict = {
                        "model":    model,
                        "messages": [
                            {"role": "system", "content": persona},
                            {"role": "user",   "content": task},
                        ],
                    }
                    if _is_reasoning:
                        # double budget — reasoning models consume hidden thinking tokens
                        kwargs["max_completion_tokens"] = max(max_tokens * 2, 2000)
                    else:
                        kwargs["max_tokens"] = max_tokens
                    response = await client.chat.completions.create(**kwargs)
                    result_text = response.choices[0].message.content or ""
                break
            except Exception as exc:
                if attempt < 3 and _is_rate_limited(exc):
                    wait = 8 * (2 ** attempt)
                    log.warning(
                        "Worker [%s/%s] rate-limited (try %d/4), sleep %ss: %s",
                        slot, model, attempt + 1, wait, exc,
                    )
                    await asyncio.sleep(wait)
                    continue
                raise

        elapsed_ms = int((asyncio.get_event_loop().time() - _t0) * 1000)
        log.info("Worker [%s/%s] task done (%d chars, %dms)", slot, model, len(result_text), elapsed_ms)
        _update_registry_stats(slot_cfg.get("_registry_id"), True, elapsed_ms)
        return {
            "slot":         slot,
            "model":        model,
            "task_preview": task[:100],
            "result":       result_text,
            "ok":           True,
        }
    except Exception as exc:
        elapsed_ms = int((asyncio.get_event_loop().time() - _t0) * 1000)
        log.warning("Worker [%s/%s] failed: %s", slot, model, exc)
        _update_registry_stats(slot_cfg.get("_registry_id"), False, elapsed_ms)
        return {
            "slot":         slot,
            "model":        model,
            "task_preview": task[:100],
            "result":       f"⚠ Worker грешка: {exc}",
            "ok":           False,
        }


def _resolve_auto_slot_cfg(task: str) -> dict | None:
    """For 'auto' slot: pick best model from registry and build a slot_cfg dict."""
    try:
        from . import model_registry as mr
        from .llm_config import PROVIDER_DEFAULTS, get_brain_config
        entry = mr.select_for_task(task)
        if not entry:
            return None
        provider = entry.get("provider", "openrouter")
        pd = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS.get("openrouter", {}))
        master = get_brain_config()
        return {
            "model":        entry.get("model_id", "deepseek/deepseek-v3.2"),
            "provider":     provider,
            "api_key":      master.get("api_key", ""),
            "base_url":     pd.get("base_url", ""),
            "sdk":          pd.get("sdk", "openai"),
            "enabled":      True,
            "_registry_id": entry.get("id"),
            "_auto":        True,
            "_auto_reason": entry.get("display_name", ""),
        }
    except Exception as exc:
        log.warning("auto slot resolution failed: %s", exc)
        return None


def _resolve_slot_cfg(slot: str) -> dict:
    """Resolve slot name → slot_cfg dict (used by tdd_loop and other internal callers)."""
    worker_cfg = get_worker_config()
    if slot in worker_cfg:
        return worker_cfg[slot]
    from .llm_config import get_brain_config
    master = get_brain_config()
    return {**master, "model": slot, "enabled": True}


async def call_workers_parallel(calls: list[dict]) -> list[dict]:
    """Execute multiple worker calls in parallel.

    calls: [{slot, task}, ...]  (from parse_worker_markers)
    Returns [{slot, model, task_preview, result, ok}, ...]
    All workers run concurrently via asyncio.gather().

    Special slots:
      'auto'     → picks best model from Model Registry based on task description
      'planner'  → planning persona + reasoning model
      'coder'    → coder persona + code-specialized model
      'reviewer' → reviewer persona + Claude Sonnet
    """
    if not calls:
        return []

    # Check global auto mode — if ON, all unrecognized/basic slots → auto
    try:
        from . import model_registry as mr
        _auto_mode_on = mr.get_auto_mode()
    except Exception:
        _auto_mode_on = False

    worker_cfg = get_worker_config()
    coros = []
    for c in calls:
        slot = c.get("slot", "fast")
        task = c.get("task", "")

        # ── Resolve slot config ────────────────────────────────────────────
        if slot == "auto" or (_auto_mode_on and slot not in ("deep", "claude", "reviewer")):
            # Auto: pick from registry
            slot_cfg = _resolve_auto_slot_cfg(task)
            if slot_cfg:
                effective_slot = "auto"
            else:
                # Fallback: use balanced
                slot_cfg = worker_cfg.get("balanced", {})
                effective_slot = "balanced"
        elif slot in worker_cfg:
            slot_cfg = worker_cfg[slot]
            effective_slot = slot
        else:
            from .llm_config import get_brain_config
            master = get_brain_config()
            slot_cfg = {**master, "model": slot, "enabled": True}
            effective_slot = slot

        # ── Guard: disabled slot ───────────────────────────────────────────
        if not slot_cfg.get("enabled", True):
            coros.append(_disabled_slot(slot))
            continue

        # ── Guard: premium worker slots require permission ───────────────
        from .outbox import (
            WORKER_SLOT_PERMISSIONS,
            check_permission,
        )
        _slot_perm = WORKER_SLOT_PERMISSIONS.get(effective_slot)
        if _slot_perm and not check_permission(_slot_perm):
            async def _worker_blocked(
                t=task, slot=effective_slot, perm=_slot_perm,
            ) -> dict:
                return {
                    "slot":         slot,
                    "model":        "(blocked)",
                    "task_preview": t[:100],
                    "result": (
                        f"⛔ {slot} worker изисква разрешение. "
                        f"Emit <NEED_CHECK_PERM: {perm}> преди да извикаш {slot} слота."
                    ),
                    "ok": False,
                }
            coros.append(_worker_blocked())
            continue

        # ── Auto mode annotation in persona hint ──────────────────────────
        actual_slot = effective_slot
        if slot_cfg.get("_auto") and effective_slot == "auto":
            reason = slot_cfg.get("_auto_reason", "auto")
            log.info("auto worker: selected %s for task %r", reason, task[:60])
            # Use balanced persona as base for auto — task context drives behaviour
            actual_slot = "balanced"

        coros.append(_call_worker_single(actual_slot, task, slot_cfg))

    results = await asyncio.gather(*coros, return_exceptions=False)
    return list(results)


def format_worker_results(results: list[dict]) -> str:
    """Format worker results into a tool result block for the master agent."""
    parts = []
    for r in results:
        slot  = r.get("slot", "?")
        model = r.get("model", "?")
        ok    = r.get("ok", False)
        prev  = r.get("task_preview", "")
        res   = r.get("result", "")
        status = "✓" if ok else "✗"
        parts.append(
            f"━━━ WORKER [{slot}/{model}] {status} — Task: {prev!r}\n{res}"
        )
    return "\n\n".join(parts)
