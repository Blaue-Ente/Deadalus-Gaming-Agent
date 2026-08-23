"""Daedalus Definition of Done — Phase 17A.

Генерира DoD checklist за coding задачи и принуждава Дедал да го верифицира
преди да казва "готово".

Как работи:
1. is_coding_task() засича дали заявката е coding work
2. generate_dod_block() → инжектира се в user_content ПРЕДИ main Sonnet call
3. format_dod_check() → добавя се към result_text след NEED_PROPOSE_START success
4. Дедал трябва да провери всеки item преди да докладва завършване
"""

from __future__ import annotations

import re

_CODING_INTENTS = frozenset({
    "code", "bugfix", "feature", "refactor", "database", "api", "test",
    "config", "deployment", "ui", "edit", "fix", "implement",
})

_CODING_KW = re.compile(
    r"\b("
    r"добав[иа]|добавяш|напиши|измен[и]|поправ[и]|създай|направи|имплементирай"
    r"|fix|add|implement|create|update|refactor|edit|write|build|patch|change"
    r"|промен[и]|дебъг[уи]|debug|интегрирай|инсталирай"
    r")\b",
    re.IGNORECASE,
)

_PROPOSE_KW = re.compile(
    r"\b(NEED_PROPOSE_START|propose_patch|код|code|файл|file|route|endpoint|function|class)\b",
    re.IGNORECASE,
)


def is_coding_task(user_msg: str, intent: str = "") -> bool:
    """Засича дали заявката е coding/implementation задача.

    Не се активира за curator/data задачи (NEED_DATA_PROPOSAL flow).
    """
    if intent in _CODING_INTENTS:
        return True
    msg_lower = user_msg.lower()
    # Curator задачи — не инжектираме DoD
    if any(kw in msg_lower for kw in ("data_proposal", "trading_concepts", "signal_criteria",
                                       "curator", "куратор", "knowledge", "знание")):
        return False
    return bool(_CODING_KW.search(user_msg))


def generate_dod_block(user_msg: str = "", intent: str = "") -> str:
    """Връща DoD prompt block за инжектиране преди main Sonnet call.

    Кратък и конкретен — не натоварва контекста.
    """
    return (
        "━━━ DEFINITION OF DONE (задължително за тази задача) ━━━\n"
        "Преди да кажеш 'готово', провери ВСЕКИ пункт:\n"
        "  □ DoD-1: Sandbox/синтаксис мина без грешки\n"
        "  □ DoD-2: Proposal е създаден с реален proposal_id\n"
        "  □ DoD-3: Решава ТОЧНО това което Николай поиска (не partial)\n"
        "  □ DoD-4: Пипнат е само 1 файл (или изрично одобрените)\n"
        "  □ DoD-5: Няма останали TODO-та без дата и причина\n"
        "В края на отговора добави 1 ред: ✅ DoD [1✓/✗ 2✓/✗ 3✓/✗ 4✓/✗ 5✓/✗]\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )


def format_dod_check() -> str:
    """Добавя се към result_text след успешен proposal — принуждава self-check.

    Кратко и директно, без излишен шум.
    """
    return (
        "\n\n━━━ DoD SELF-CHECK (отговори преди да докладваш) ━━━\n"
        "DoD-1 Sandbox: виж резултата горе\n"
        "DoD-2 Proposal ID: реален ли е?\n"
        "DoD-3 Задача: решена точно?\n"
        "DoD-4 Scope: само 1 файл?\n"
        "DoD-5 TODO-та: няма ли?\n"
        "→ Отговори: ✅ DoD [1✓ 2✓ 3✓ 4✓ 5✓] или ❌ DoD [посочи кое не минава и защо]"
    )


def format_confidence_warning(file_path: str, confidence: float, evidence_count: int) -> str:
    """Форматира warning при нисък confidence на target file."""
    return (
        f"━━━ ⚠ CONFIDENCE WARNING ━━━\n"
        f"File       : {file_path}\n"
        f"Confidence : {confidence:.2f} (препоръчително ≥ 0.75)\n"
        f"Evidence   : {evidence_count} piece(s) (препоръчително ≥ 2)\n\n"
        f"Продължавам въпреки ниския confidence — но ако sandbox-ът се провали,\n"
        f"провери дали файлът е правилният target:\n"
        f"  1. search_code за ключовата функция/symbol\n"
        f"  2. NEED_ENDPOINTS / NEED_MODULES за потвърждение\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )
