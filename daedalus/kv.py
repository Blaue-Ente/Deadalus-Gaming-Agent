"""Tiny key-value store: Replit DB when present, else JSON files.

Standalone Docker has no Replit DB. Command Center features that used to
write only to Replit (tasks, blockers, outbox, playground session state)
persist under ``DAEDALUS_DATA``. ADN is unchanged when Replit DB is
available — that path is tried first.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_INDEX_NAME = "_index.json"


def _kv_dir() -> Path:
    from .paths import data_root
    path = data_root() / "kv"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _path(key: str) -> Path:
    name = _SAFE.sub("_", key).strip("_") or "key"
    return _kv_dir() / f"{name}.json"


def _index_path() -> Path:
    return _kv_dir() / _INDEX_NAME


def _load_index() -> dict[str, str]:
    path = _index_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_index(index: dict[str, str]) -> None:
    _index_path().write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")


def _remember_key(key: str) -> None:
    stem = _path(key).stem
    index = _load_index()
    if index.get(stem) != key:
        index[stem] = key
        _save_index(index)


def _forget_key(key: str) -> None:
    stem = _path(key).stem
    index = _load_index()
    if stem in index:
        index.pop(stem, None)
        _save_index(index)


def get(key: str) -> Any:
    try:
        from replit import db  # type: ignore
        raw = db.get(key)
        if raw is not None:
            if isinstance(raw, str):
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return raw
            return raw
    except Exception:
        pass
    path = _path(key)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.debug("kv get %s: %s", key, exc)
        return None


def set(key: str, value: Any) -> None:
    payload = json.dumps(value, ensure_ascii=False, default=str)
    try:
        from replit import db  # type: ignore
        db[key] = payload
        return
    except Exception:
        pass
    path = _path(key)
    path.write_text(payload, encoding="utf-8")
    _remember_key(key)


def delete(key: str) -> None:
    try:
        from replit import db  # type: ignore
        del db[key]
    except Exception:
        pass
    path = _path(key)
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass
    _forget_key(key)


def list_keys(prefix: str) -> list[str]:
    """Keys starting with ``prefix``. Replit prefix-scan first, then JSON files."""
    found: list[str] = []
    try:
        from replit import db  # type: ignore
        found.extend(str(k) for k in db.prefix(prefix))
    except Exception:
        pass
    try:
        for original in _load_index().values():
            if str(original).startswith(prefix) and original not in found:
                found.append(str(original))
        safe_prefix = _SAFE.sub("_", prefix)
        for path in _kv_dir().glob("*.json"):
            if path.name == _INDEX_NAME:
                continue
            stem = path.stem
            if safe_prefix and not stem.startswith(safe_prefix):
                continue
            reconstructed = prefix + stem[len(safe_prefix):] if prefix else stem
            if reconstructed not in found:
                found.append(reconstructed)
    except Exception as exc:
        log.debug("kv list_keys: %s", exc)
    return found


class _DbFacade:
    """Replit-DB-shaped surface so callers can keep ``db[k] = v`` / ``db.prefix``."""

    def get(self, key: str, default: Any = None) -> Any:
        val = get(key)
        return default if val is None else val

    def __getitem__(self, key: str) -> Any:
        return get(key)

    def __setitem__(self, key: str, value: Any) -> None:
        set(key, value)

    def __delitem__(self, key: str) -> None:
        delete(key)

    def prefix(self, prefix: str) -> list[str]:
        return list_keys(prefix)

    def keys(self) -> list[str]:
        found: list[str] = []
        try:
            from replit import db as rdb  # type: ignore
            found.extend(str(k) for k in rdb.keys())
        except Exception:
            pass
        for key in list_keys(""):
            if key not in found:
                found.append(key)
        return found


db = _DbFacade()
