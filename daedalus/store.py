"""SQLite playground registry for standalone Daedalus.

Mimics the asyncpg pool surface used by ``playground.py`` (``acquire``,
``execute``, ``fetch``, ``fetchrow`` with ``$1`` placeholders) so the same
SQL works next to ADN Postgres and on a laptop with zero Postgres.

ADN is never redirected here: ``host.get_pool()`` only opens SQLite when
standalone mode is on or ADN's pool cannot be imported.
"""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Sequence

from .paths import data_root

_SCHEMA = """
CREATE TABLE IF NOT EXISTS playground_projects (
    id            TEXT PRIMARY KEY,
    slug          TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL DEFAULT '',
    description   TEXT DEFAULT '',
    kind          TEXT NOT NULL DEFAULT 'static',
    source        TEXT NOT NULL DEFAULT 'built',
    root_dir      TEXT NOT NULL DEFAULT '',
    run_cmd       TEXT DEFAULT '',
    entry         TEXT DEFAULT 'index.html',
    github_repo   TEXT DEFAULT '',
    gitlab_repo   TEXT DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'idle',
    coding_mode   TEXT NOT NULL DEFAULT 'lite',
    autopilot_plan TEXT DEFAULT NULL,
    upstream_url  TEXT DEFAULT '',
    cloned_branch TEXT DEFAULT '',
    cloned_ref    TEXT DEFAULT '',
    upstream_sha  TEXT DEFAULT '',
    compat_score  REAL DEFAULT 0,
    compat_detail TEXT DEFAULT '{}',
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_pgp_status  ON playground_projects (status);
CREATE INDEX IF NOT EXISTS ix_pgp_created ON playground_projects (created_at DESC);

CREATE TABLE IF NOT EXISTS playground_published_apps (
    slug         TEXT PRIMARY KEY,
    name         TEXT NOT NULL DEFAULT '',
    description  TEXT DEFAULT '',
    kind         TEXT NOT NULL DEFAULT 'static',
    entry        TEXT NOT NULL DEFAULT 'index.html',
    object_path  TEXT DEFAULT '',
    icon         TEXT DEFAULT '🎮',
    menu_order   INT DEFAULT 100,
    enabled      INT DEFAULT 1,
    project_id   TEXT DEFAULT '',
    size_bytes   INTEGER DEFAULT 0,
    published_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS daedalus_learning_records (
    id               TEXT PRIMARY KEY,
    slug             TEXT NOT NULL,
    description      TEXT DEFAULT '',
    kind             TEXT NOT NULL DEFAULT 'static',
    coding_mode      TEXT NOT NULL DEFAULT 'lite',
    build_model      TEXT DEFAULT '',
    success          INT NOT NULL DEFAULT 0,
    stage_count      INT DEFAULT 0,
    total_attempts   INT DEFAULT 0,
    errors           TEXT DEFAULT '[]',
    assets_generated INT DEFAULT 0,
    files_count      INT DEFAULT 0,
    acceptance_met   TEXT DEFAULT '[]',
    acceptance_unmet TEXT DEFAULT '[]',
    runtime_verified INT DEFAULT 0,
    browser_verified INT DEFAULT 0,
    review_notes     TEXT DEFAULT '',
    duration_sec     REAL DEFAULT 0,
    source           TEXT NOT NULL DEFAULT 'autopilot',
    created_at       TEXT DEFAULT (datetime('now')),
    quality_score    REAL DEFAULT 0,
    quality_detail   TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS daedalus_plans (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_dpl_status ON daedalus_plans (status);

CREATE TABLE IF NOT EXISTS daedalus_plan_steps (
    id               TEXT PRIMARY KEY,
    plan_id          TEXT NOT NULL REFERENCES daedalus_plans(id) ON DELETE CASCADE,
    step_order       INT  NOT NULL DEFAULT 0,
    title            TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'pending',
    proposal_id      TEXT,
    error_msg        TEXT,
    created_at       TEXT DEFAULT (datetime('now')),
    completed_at     TEXT,
    started_at       TEXT,
    retry_count      INT  DEFAULT 0,
    max_retries      INT  DEFAULT 3,
    retry_at         TEXT,
    depends_on       TEXT DEFAULT '',
    checkpoints      TEXT DEFAULT '{}',
    progress_note    TEXT,
    diagnostics      TEXT,
    repair_attempts  INT  DEFAULT 0,
    manual_override  INT  DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_dps_plan_id ON daedalus_plan_steps (plan_id);
CREATE INDEX IF NOT EXISTS ix_dps_status  ON daedalus_plan_steps (status);

CREATE TABLE IF NOT EXISTS daedalus_agent_inbox (
    id          TEXT PRIMARY KEY,
    from_who    TEXT NOT NULL DEFAULT 'daedalus',
    subject     TEXT NOT NULL DEFAULT '',
    body        TEXT NOT NULL DEFAULT '',
    priority    INT  NOT NULL DEFAULT 2,
    read_at     TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS daedalus_bundles (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL DEFAULT '',
    summary      TEXT DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'staging',
    session_id   TEXT DEFAULT '',
    created_at   TEXT DEFAULT (datetime('now')),
    committed_at TEXT,
    applied_at   TEXT
);
CREATE INDEX IF NOT EXISTS ix_db_status  ON daedalus_bundles (status);
CREATE INDEX IF NOT EXISTS ix_db_created ON daedalus_bundles (created_at DESC);

CREATE TABLE IF NOT EXISTS daedalus_bundle_files (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    bundle_id        TEXT NOT NULL REFERENCES daedalus_bundles(id) ON DELETE CASCADE,
    file_path        TEXT NOT NULL,
    action           TEXT NOT NULL DEFAULT 'replace',
    reason           TEXT DEFAULT '',
    new_content      TEXT DEFAULT '',
    original_content TEXT DEFAULT '',
    staged_at        TEXT DEFAULT (datetime('now')),
    UNIQUE (bundle_id, file_path)
);
CREATE INDEX IF NOT EXISTS ix_dbf_bundle ON daedalus_bundle_files (bundle_id);

CREATE TABLE IF NOT EXISTS daedalus_clarifications (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id       TEXT NOT NULL,
    user_message     TEXT NOT NULL,
    agent_reply      TEXT NOT NULL DEFAULT '',
    questions_asked  TEXT DEFAULT '[]',
    lesson_generated INT  NOT NULL DEFAULT 0,
    created_at       TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_dclr_session ON daedalus_clarifications (session_id);
"""

_DOLLAR = re.compile(r"\$(\d+)")
_PG_CAST = re.compile(
    r"::(?:jsonb|timestamptz|timestamp|int|integer|bool|boolean|text|float|numeric)(?:\[\])?",
    re.I,
)
_PG_ANY = re.compile(r"=\s*ANY\(\$(\d+)(?:\s*::\s*[a-zA-Z0-9_\[\]]+)?\)", re.I)
_JSON_COLS = {
    "checkpoints", "compat_detail", "autopilot_plan", "acceptance_met",
    "acceptance_unmet", "quality_detail", "errors", "questions_asked",
}
_POOL: "SqlitePool | None" = None


def db_path() -> Path:
    path = data_root() / "daedalus.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _bind(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _decode_cell(column: str, value: Any) -> Any:
    if column in _JSON_COLS and isinstance(value, str) and value[:1] in "{[":
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def _pg_to_sqlite(sql: str, args: Sequence[Any]) -> tuple[str, tuple[Any, ...]]:
    """Translate asyncpg ``$n`` / ``NOW()`` / ``::jsonb`` into sqlite3."""
    translated = re.sub(
        r"\bNOW\s*\(\s*\)\s*-\s*INTERVAL\s+'(\d+)\s+days?'",
        r"datetime('now', '-\1 days')",
        sql,
        flags=re.I,
    )
    translated = re.sub(r"\bNOW\s*\(\s*\)", "datetime('now')", translated, flags=re.I)
    translated = re.sub(
        r"to_timestamp\s*\(\s*\$(\d+)\s*\)",
        r"datetime($\1, 'unixepoch')",
        translated,
        flags=re.I,
    )
    translated = translated.replace("E'\\n---\\n'", "char(10) || '---' || char(10)")
    translated = translated.replace("E'\n---\n'", "'\n---\n'")

    args_list = list(args)
    for m in reversed(list(_PG_ANY.finditer(translated))):
        idx = int(m.group(1)) - 1
        seq = args_list[idx] if 0 <= idx < len(args_list) else []
        if not isinstance(seq, (list, tuple)):
            seq = [seq]
        start = len(args_list) + 1
        args_list.extend(seq)
        placeholders = ",".join(f"${start + i}" for i in range(len(seq)))
        inn = f" IN ({placeholders})" if seq else " IN (NULL)"
        translated = translated[: m.start()] + inn + translated[m.end():]

    translated = _PG_CAST.sub("", translated)
    translated = re.sub(r"\bTRUE\b", "1", translated, flags=re.I)
    translated = re.sub(r"\bFALSE\b", "0", translated, flags=re.I)

    params: list[Any] = []

    def repl(match: re.Match[str]) -> str:
        idx = int(match.group(1)) - 1
        if idx < 0 or idx >= len(args_list):
            raise IndexError(f"SQL placeholder ${idx + 1} with {len(args_list)} bound args")
        params.append(args_list[idx])
        return "?"

    return _DOLLAR.sub(repl, translated), tuple(params)


class _Row(dict):
    """dict that also supports asyncpg-style ``row['col']`` (already dict)."""

    def __getitem__(self, key: Any) -> Any:  # type: ignore[override]
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class SqliteConn:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._txn_depth = 0

    def transaction(self) -> "_Transaction":
        return _Transaction(self)

    async def execute(self, sql: str, *args: Any) -> str:
        q, params = _pg_to_sqlite(sql, args)
        bound = tuple(_bind(p) for p in params)
        cur = self._conn.execute(q, bound)
        if self._txn_depth == 0:
            self._conn.commit()
        verb = (q.strip().split() or ["OK"])[0].upper()
        return f"{verb} {cur.rowcount}"

    async def fetch(self, sql: str, *args: Any) -> list[_Row]:
        q, params = _pg_to_sqlite(sql, args)
        bound = tuple(_bind(p) for p in params)
        cur = self._conn.execute(q, bound)
        cols = [d[0] for d in cur.description] if cur.description else []
        out: list[_Row] = []
        for row in cur.fetchall():
            out.append(_Row({c: _decode_cell(c, v) for c, v in zip(cols, row)}))
        return out

    async def fetchrow(self, sql: str, *args: Any) -> _Row | None:
        rows = await self.fetch(sql, *args)
        return rows[0] if rows else None

    async def fetchval(self, sql: str, *args: Any) -> Any:
        row = await self.fetchrow(sql, *args)
        if not row:
            return None
        return next(iter(row.values()))


class _Transaction:
    def __init__(self, conn: SqliteConn):
        self._conn = conn

    async def __aenter__(self) -> SqliteConn:
        if self._conn._txn_depth == 0:
            self._conn._conn.execute("BEGIN")
        self._conn._txn_depth += 1
        return self._conn

    async def __aexit__(self, exc_type: Any, *_exc: Any) -> None:
        self._conn._txn_depth = max(0, self._conn._txn_depth - 1)
        if self._conn._txn_depth > 0:
            return
        if exc_type:
            self._conn._conn.rollback()
        else:
            self._conn._conn.commit()


class _Acquire:
    def __init__(self, pool: "SqlitePool"):
        self._pool = pool

    async def __aenter__(self) -> SqliteConn:
        await self._pool._lock.acquire()
        return SqliteConn(self._pool._conn)

    async def __aexit__(self, *_exc: Any) -> None:
        self._pool._lock.release()


class SqlitePool:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._lock = asyncio.Lock()

    def acquire(self) -> _Acquire:
        return _Acquire(self)

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass


def reset_sqlite_pool() -> None:
    global _POOL
    if _POOL is not None:
        _POOL.close()
        _POOL = None


async def get_sqlite_pool() -> SqlitePool:
    global _POOL
    if _POOL is not None:
        return _POOL
    conn = sqlite3.connect(str(db_path()), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    conn.commit()
    _POOL = SqlitePool(conn)
    return _POOL
