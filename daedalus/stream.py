"""Live-status tracker за Дедал.

Лесен in-memory dict per session_id — без SSE/WebSocket сложност.
Frontend поллва /admin/daedalus/live на всеки 1s и показва текущия инструмент.
"""
from __future__ import annotations

import asyncio
import contextlib
import time

_live: dict[str, dict] = {}

_TOOL_LABELS: dict[str, str] = {
    "thinking":           "🧠 Анализирам задачата",
    "navigator":          "🧭 Navigator избира маршрут",
    "reasoning":          "💭 Дедал мисли",
    "finalize":           "📦 Финализирам отговора",
    "NEED_PG":            "🔍 SQL заявка към базата",
    "NEED_WEB":           "🌐 Търся в мрежата",
    "NEED_GITHUB":        "🐙 Търся в GitHub",
    "NEED_GH_REPO":       "🐙 GitHub репо",
    "NEED_GH_ISSUE":      "🐙 GitHub Issues",
    "NEED_WORKER":        "👷 Worker агент",
    "NEED_DATA_PROPOSAL": "📋 Записвам данни",
    "NEED_PROPOSE_START": "✏️ Пиша/редактирам код",
    "NEED_PROGRESS":      "⚡ Прогрес",
    "NEED_CHECK_PERM":    "🔐 Проверявам разрешение",
    "NEED_PROJECT_PLAN":  "📐 Планирам задачата",
    "NEED_SKILL":         "📚 Анализирам знания",
    "NEED_DOCS":          "📖 Четя документация",
    "NEED_READ_FILE":     "📂 Четя файл",
    "NEED_MESH":          "🔗 Mesh заявка",
    "NEED_INBOX_MSG":     "📬 Изпращам съобщение",
    "NEED_PYPI":          "📦 Търся PyPI",
    "NEED_NPM":           "📦 Търся NPM",
    "NEED_DEP":           "📦 Анализирам зависимости",
    "NEED_BACKUP":        "💾 Backup",
    "done":               "✅ Готово",
    "generating":         "🔄 Генерирам следващ отговор",
}

# Добавяме и динамичен label за "Генерирам следващ отговор"
# (използва се при LLM re-call между tool iterations)

_TIMEOUT_SECS = 300  # след 5 минути без update считаме за неактивен


def set_live(session_id: str, tool: str, iteration: int, preview: str = "") -> None:
    """Актуализира live статуса за дадена сесия."""
    _live[session_id] = {
        "active": True,
        "tool": tool,
        "label": _TOOL_LABELS.get(tool, f"⚙️ {tool}"),
        "iteration": iteration,
        "preview": preview[:120] if preview else "",
        "ts": time.time(),
    }


def get_live(session_id: str | None = None) -> dict:
    """Връща live статуса за сесия или за всички активни сесии."""
    now = time.time()
    if session_id:
        v = _live.get(session_id)
        if not v:
            return {}
        if now - v.get("ts", 0) > _TIMEOUT_SECS:
            _live.pop(session_id, None)
            return {}
        return v
    return {
        sid: v for sid, v in list(_live.items())
        if now - v.get("ts", 0) < _TIMEOUT_SECS
    }


def clear_live(session_id: str) -> None:
    """Изчиства live статуса след приключване."""
    _live.pop(session_id, None)


def bump_live(session_id: str) -> None:
    """Обновява само `ts` за съществуваща сесия — keepalive heartbeat.

    Използва се по време на дълги LLM извиквания, за да не изглежда UI-ът
    замръзнал когато няма промяна във фазата.
    """
    v = _live.get(session_id)
    if v:
        v["ts"] = time.time()
        v["elapsed"] = int(v["ts"] - v.get("started_at", v["ts"]))


@contextlib.asynccontextmanager
async def keepalive(session_id: str, phase: str, iteration: int = 0,
                    preview: str = "", interval: float = 2.0):
    """Async context manager — поддържа жив heartbeat по време на блокираща операция.

    Сеттва фазата на старт, после във фонова задача обновява `ts` на всеки
    `interval` секунди докато операцията тече. Така UI вижда нарастващ
    elapsed таймер дори когато Claude генерира 30-60 секунди без друг сигнал.

    Пример:
        async with keepalive(sid, "reasoning"):
            raw = await _call_claude(...)
    """
    started = time.time()
    set_live(session_id, phase, iteration, preview)
    v = _live.get(session_id)
    if v is not None:
        v["started_at"] = started
        v["elapsed"] = 0

    stop_event = asyncio.Event()

    async def _beat():
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                bump_live(session_id)

    beat_task = asyncio.create_task(_beat())
    try:
        yield
    finally:
        stop_event.set()
        try:
            await asyncio.wait_for(beat_task, timeout=1.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            beat_task.cancel()
