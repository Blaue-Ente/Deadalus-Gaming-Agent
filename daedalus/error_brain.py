"""Daedalus Error Brain — Phase 17B.

Classifies errors into 12 types and returns a concrete repair strategy.
Plugs into the sandbox failure feedback and the repeated-error breaker.

Usage:
    from .error_brain import classify_error, format_for_prompt

    cls = classify_error(sandbox_result.get("errors", []))
    feedback += "\\n\\n" + format_for_prompt(cls)
"""
from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Error taxonomy — 12 types with detection patterns + strategies
# ---------------------------------------------------------------------------

_TAXONOMY: list[dict[str, Any]] = [
    {
        "code": "SYNTAX",
        "label": "Syntax Error",
        "patterns": [r"SyntaxError", r"IndentationError", r"unexpected.*indent",
                     r"invalid syntax", r"EOL while scanning"],
        "strategy": [
            "Намери точния ред и символ от error message-а.",
            "Поправи само синтаксиса — не рефакторирай.",
            "Провери indentation (tabs vs spaces).",
        ],
        "hint": "Syntax грешки са механични — погледни точния ред, не цялата функция.",
    },
    {
        "code": "TYPE",
        "label": "Type Error",
        "patterns": [r"TypeError", r"type.*mismatch", r"unsupported operand",
                     r"argument.*must be.*type", r"expected.*type.*got"],
        "strategy": [
            "Провери типовете на аргументите — str vs bytes vs int.",
            "Добави explicit cast ако е необходимо.",
            "Провери дали функцията съществува за този тип.",
        ],
        "hint": "TypeErrors се решават с print(type(x)) мислено — какъв тип се подава?",
    },
    {
        "code": "IMPORT",
        "label": "Import / Path Error",
        "patterns": [r"ImportError", r"ModuleNotFoundError", r"cannot import name",
                     r"No module named", r"from .* import"],
        "strategy": [
            "Провери точното import path с search_code.",
            "Провери дали модулът е инсталиран (requirements.txt / pyproject.toml).",
            "Използвай relative import ако е вътрешен модул.",
        ],
        "hint": "Import грешки: провери __init__.py и точния module path с search_code.",
    },
    {
        "code": "RUNTIME",
        "label": "Runtime Error",
        "patterns": [r"RuntimeError", r"RecursionError", r"StopIteration",
                     r"KeyError", r"IndexError", r"AttributeError"],
        "strategy": [
            "Провери дали обектът/ключът съществува преди достъп.",
            "Добави guard (if key in dict / if attr exists).",
            "Намали дълбочината на рекурсия ако е RecursionError.",
        ],
        "hint": "Runtime грешки: defensive coding — провери преди да достъпваш.",
    },
    {
        "code": "API_PROVIDER",
        "label": "API / Provider Error",
        "patterns": [r"anthropic", r"openai", r"APIError", r"API.*error",
                     r"provider.*fail", r"model.*unavailable", r"overloaded"],
        "strategy": [
            "Провери дали API key-ът е валиден (VAULT_TOKEN / env var).",
            "Намали max_tokens и/или context size.",
            "Опитай с по-бърз/евтин модел (Haiku вместо Sonnet).",
            "Изчакай 30с и retry веднъж.",
        ],
        "hint": "API грешки: намали контекста, провери key-а, опитай Haiku.",
    },
    {
        "code": "TIMEOUT",
        "label": "Timeout",
        "patterns": [r"timeout", r"Timeout", r"timed out", r"ReadTimeout",
                     r"ConnectTimeout", r"deadline exceeded"],
        "strategy": [
            "Намали context до <5k tokens.",
            "Намали max_output_tokens наполовина.",
            "Запази task state с NEED_TASK_STATE и продължи от checkpoint.",
            "Превключи на по-бърз модел (Haiku/Groq).",
            "Раздели задачата на по-малки стъпки.",
        ],
        "hint": "Timeout: checkpoint (NEED_TASK_STATE), намали контекст, по-бърз модел. Никога не рестартирай от нула.",
    },
    {
        "code": "RATE_LIMIT",
        "label": "Rate Limit",
        "patterns": [r"rate.?limit", r"RateLimitError", r"429", r"Too Many Requests",
                     r"quota exceeded", r"requests per minute"],
        "strategy": [
            "Изчакай 60с преди retry.",
            "Превключи на друг provider (Groq/OpenAI) от fallback chain.",
            "Намали честотата на tool calls.",
        ],
        "hint": "Rate limit: системата има fallback chain — тя ще превключи автоматично.",
    },
    {
        "code": "AUTH",
        "label": "Auth / API Key Error",
        "patterns": [r"AuthenticationError", r"401", r"403", r"Unauthorized",
                     r"invalid.*key", r"api.?key.*invalid", r"permission denied"],
        "strategy": [
            "Провери дали env var-ът е зареден (не го print-вай).",
            "Провери Vault token scopes.",
            "Докладвай на Николай — не пробвай сам да четеш secrets.",
        ],
        "hint": "Auth грешки: не пробвай сам да четеш secrets — докладвай на Николай.",
    },
    {
        "code": "TEST_FAILURE",
        "label": "Test Failure",
        "patterns": [r"AssertionError", r"FAILED.*test", r"test.*FAILED",
                     r"pytest.*failed", r"assert.*==", r"Expected.*Got"],
        "strategy": [
            "Прочети точното assert — какво се очаква vs. какво е реалното.",
            "Поправи само кода, не теста (освен ако тестът е грешен).",
            "Изолирай минималния failing case.",
        ],
        "hint": "Test failure: прочети assertion message внимателно — не гадай.",
    },
    {
        "code": "LOGIC",
        "label": "Logic Mismatch",
        "patterns": [r"wrong.*result", r"incorrect.*output", r"unexpected.*value",
                     r"logic.*error", r"не работи", r"wrong.*value"],
        "strategy": [
            "Добави print/log за intermediate стойности.",
            "Провери edge cases (empty list, None, 0).",
            "Сравни с очаквания output стъпка по стъпка.",
        ],
        "hint": "Logic грешки: трейс execution мислено — накъде отива данните?",
    },
    {
        "code": "MISSING_CONTEXT",
        "label": "Missing File / Context",
        "patterns": [r"FileNotFoundError", r"No such file", r"file.*not found",
                     r"cannot.*open", r"missing.*file", r"not.*exist"],
        "strategy": [
            "Провери пътя с probe/read_file.",
            "Провери дали файлът е в ALLOWED_PREFIXES.",
            "Ако файлът трябва да се създаде — направи го с NEED_PROPOSE_START.",
        ],
        "hint": "Missing file: провери пътя с probe преди да пишеш нов код.",
    },
    {
        "code": "ENV_CONFIG",
        "label": "Environment / Config Error",
        "patterns": [r"env.*not set", r"environment.*variable", r"config.*missing",
                     r"DATABASE_URL", r"variable.*not found", r"getenv.*None",
                     r"KeyError.*env"],
        "strategy": [
            "Провери дали env var-ът е в `.env` или Studio → Свързаност.",
            "Провери дали `.env` се зарежда правилно.",
            "Докладвай ако ключът липсва след запис в Свързаност.",
        ],
        "hint": "Config грешки: env var в `.env` / Свързаност ли е?",
    },
]

# Fallback за неразпознати грешки
_UNKNOWN: dict[str, Any] = {
    "code": "UNKNOWN",
    "label": "Unknown Error",
    "patterns": [],
    "strategy": [
        "Прочети error message-а дума по дума.",
        "search_code за ключовия символ/функция от stack trace-а.",
        "Ако 2 различни подхода са се провалили → NEED_BLOCKED.",
    ],
    "hint": "Непозната грешка: прочети stack trace, не гадай.",
}


def classify_error(errors: list[str] | str | Exception) -> dict[str, Any]:
    """Classify one or more error messages into one of 12 types.

    Args:
        errors: list of error strings, single string, or Exception.

    Returns:
        Dict with keys: code, label, strategy (list), hint (str).
    """
    if isinstance(errors, Exception):
        text = str(errors)
    elif isinstance(errors, list):
        text = " | ".join(str(e) for e in errors[:5])
    else:
        text = str(errors)

    text_lower = text.lower()

    for entry in _TAXONOMY:
        for pat in entry["patterns"]:
            if re.search(pat, text, re.IGNORECASE):
                return {
                    "code": entry["code"],
                    "label": entry["label"],
                    "strategy": entry["strategy"],
                    "hint": entry["hint"],
                    "raw_error": text[:300],
                }

    return {**_UNKNOWN, "raw_error": text[:300]}


def format_for_prompt(cls: dict[str, Any]) -> str:
    """Format classification result for injection into sandbox/tool failure prompt."""
    code = cls.get("code", "UNKNOWN")
    label = cls.get("label", "Unknown")
    strategy = cls.get("strategy", [])
    hint = cls.get("hint", "")

    steps = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(strategy))
    return (
        f"━━━ ERROR BRAIN — {code}: {label} ━━━\n"
        f"Стратегия:\n{steps}\n"
        f"Подсказка: {hint}"
    )


def classify_from_sandbox(sandbox_result: dict[str, Any]) -> dict[str, Any] | None:
    """Extract and classify errors from a sandbox result dict."""
    errors = sandbox_result.get("errors") or []
    if not errors:
        # Try to extract from layers
        for layer in sandbox_result.get("layers", []):
            if not layer.get("ok"):
                errs = layer.get("errors") or layer.get("output") or []
                if errs:
                    errors = errs if isinstance(errs, list) else [str(errs)]
                    break
    if not errors:
        return None
    return classify_error(errors)
