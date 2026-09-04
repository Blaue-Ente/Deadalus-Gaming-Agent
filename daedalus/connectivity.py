"""Owner-facing connectivity: secrets, MCP, and recommendations.

One catalog for inbound and outbound links (LLM, git forges, publish, MCP,
mesh, engines). Values live in ``$DAEDALUS_DATA/secrets.env`` (not the git
checkout). Process env is updated immediately so GitHub/GitLab/LLM pick them
up without a restart.

The UI never receives raw secrets back — only ``set`` + a short mask.
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .paths import data_root

log = logging.getLogger(__name__)

_SECRETS_NAME = "secrets.env"
_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")

# Never writable through this module — owner token and process identity stay elsewhere.
FORBIDDEN_KEYS = frozenset({
    "PATH", "HOME", "USER", "SHELL", "PWD", "TMPDIR",
    "DAEDALUS_TOKEN", "DAEDALUS_STANDALONE", "DAEDALUS_BIND_HOST",
    "HOST", "PORT", "PYTHONPATH", "VIRTUAL_ENV",
})

# group_id → section shown in Свързаност
GROUPS: list[dict[str, Any]] = [
    {
        "id": "llm",
        "title": "Мозък (LLM)",
        "blurb": (
            "Кой модел пише кода. Достатъчен е един облачен ключ — или локален Ollama. "
            "Дедал вика OpenAI-съвместим API."
        ),
        "pick": (
            "OpenRouter — ако искаш много модели (вкл. Grok през x-ai/…). "
            "AllToken — един ключ за няколко семейства. "
            "NVIDIA — безплатен преглед. "
            "Ollama — без интернет, без ключ (Studio → Гардероб)."
        ),
    },
    {
        "id": "git",
        "title": "Код (GitHub / GitLab)",
        "blurb": (
            "Пълен контрол върху файловете е локално в playground. "
            "Токените са за clone, нов repo, push, Pull Request и Merge Request."
        ),
        "pick": (
            "GitLab — ако публикуваш при Blaue-Ente / self-hosted. "
            "GitHub — classic PAT със scope repo за 1-клик нов repo + PR. "
            "Auto push: GitLab ако има токен, иначе GitHub."
        ),
    },
    {
        "id": "publish",
        "title": "Публикуване",
        "blurb": "Studio → Наживо: локално /apps/, после публичен https.",
        "pick": (
            "Netlify токен — траен *.netlify.app за секунди. "
            "GITLAB_TOKEN — GitLab Pages. "
            "Без ключ: 1-часова Netlify preview (трябва Node/npx)."
        ),
    },
    {
        "id": "mcp",
        "title": "MCP сървъри",
        "blurb": (
            "Дедал е MCP клиент: native инструменти стигат за повечето игри. "
            "Включи сървър само когато native не покрива задачата. "
            "Ключовете на сървъра се пишат тук, не в git."
        ),
        "pick": (
            "Започни без MCP. GitHub/GitLab MCP — ако искаш PR/issues през MCP "
            "(Дедал вече има native git_forge). Blender MCP — само с локален Blender. "
            "Fetch MCP е излишен до native web_read."
        ),
    },
    {
        "id": "mesh",
        "title": "Асистенти / mesh",
        "blurb": (
            "Външен агент (друг процес, бъдещ Grok/Cursor MCP) вика "
            "POST /api/iyveris с този ключ. Loopback Studio не го изисква."
        ),
        "pick": "Смени default ключа преди публичен bind. Не е заместител на DAEDALUS_TOKEN.",
    },
    {
        "id": "lab",
        "title": "Лаборатория / двигатели",
        "blurb": "Play Lab е виртуалният компютър за мостри. Godot/Unity са на тази машина, не в облака.",
        "pick": (
            "Local HTTP стига за HTML5 мостри. Docker lab — само ако образът вече е локален "
            "или включиш pull. QEMU/Wine се засичат, не се пускат."
        ),
    },
    {
        "id": "assets3d",
        "title": "3D / sidecar",
        "blurb": "Hero GLB само с ключ. Без ключ — greybox куб, не фалшив герой.",
        "pick": "fal / Tripo / Meshy — по избор. MiroFish е отделен AGPL docker, не се вендорира.",
    },
]

FIELDS: list[dict[str, Any]] = [
    # LLM
    {
        "key": "OPENROUTER_API_KEY", "group": "llm", "secret": True,
        "label": "OpenRouter",
        "why": "Един вход към Claude, GPT, Gemini, DeepSeek, Grok (x-ai/…) и :free пул.",
        "recommend": True,
        "docs": "https://openrouter.ai/keys",
        "placeholder": "sk-or-…",
    },
    {
        "key": "ALLTOKEN_API_KEY", "group": "llm", "secret": True,
        "label": "AllToken",
        "why": "Един платен ключ за няколко семейства модели.",
        "recommend": True,
        "docs": "https://api.alltoken.ai",
    },
    {
        "key": "NOVITA_API_KEY", "group": "llm", "secret": True,
        "label": "Novita",
        "why": "Open-source модели (DeepSeek, Qwen) и спрайтове. Pay-per-token.",
        "docs": "https://novita.ai",
    },
    {
        "key": "NVIDIA_API_KEY", "group": "llm", "secret": True,
        "label": "NVIDIA NIM",
        "why": "Безплатен преглед на хоствани open модели.",
        "docs": "https://build.nvidia.com",
    },
    {
        "key": "OPENAI_API_KEY", "group": "llm", "secret": True,
        "label": "OpenAI (direct)",
        "why": "Пряк api.openai.com. Нужен само ако не ползваш gateway.",
    },
    {
        "key": "ANTHROPIC_API_KEY", "group": "llm", "secret": True,
        "label": "Anthropic (direct)",
        "why": "Пряк Claude. OpenRouter вече го покрива за повечето случаи.",
    },
    {
        "key": "GROQ_API_KEY", "group": "llm", "secret": True,
        "label": "Groq",
        "why": "Бърз Llama. Не е xAI Grok.",
    },
    {
        "key": "GEMINI_API_KEY", "group": "llm", "secret": True,
        "label": "Gemini (direct)",
        "why": "Google Generative Language. Опционално до OpenRouter.",
    },
    {
        "key": "DAEDALUS_PROVIDER", "group": "llm", "secret": False,
        "label": "Provider",
        "why": "openrouter / alltoken / novita / nvidia / ollama / custom. Празно = авто.",
        "placeholder": "openrouter",
    },
    {
        "key": "DAEDALUS_MODEL", "group": "llm", "secret": False,
        "label": "Модел",
        "why": "Конкретен id. За $0 при OpenRouter: poolside/laguna-s-2.1:free (preset or_free).",
        "placeholder": "poolside/laguna-s-2.1:free",
    },
    {
        "key": "DAEDALUS_BASE_URL", "group": "llm", "secret": False,
        "label": "Custom base URL",
        "why": "За xAI Grok: https://api.x.ai/v1 плюс ключ като OPENAI_API_KEY или DAEDALUS_API_KEY. За Ollama обикновено не пипай.",
        "placeholder": "https://api.x.ai/v1",
    },
    {
        "key": "DAEDALUS_API_KEY", "group": "llm", "secret": True,
        "label": "DAEDALUS_API_KEY",
        "why": "Резервен ключ, когато provider е custom (напр. xAI) и няма отделен env.",
    },
    # Git
    {
        "key": "GITHUB_TOKEN", "group": "git", "secret": True,
        "label": "GitHub PAT",
        "why": "Clone, нов repo, push, issues, Pull Request. Classic token: scope repo. Fine-grained: Contents + Pull requests + Administration Read/Write.",
        "recommend": True,
        "docs": "https://github.com/settings/tokens",
        "placeholder": "ghp_…",
    },
    {
        "key": "GITLAB_TOKEN", "group": "git", "secret": True,
        "label": "GitLab token",
        "why": "Clone, нов project, push, Merge Request, Pages. Scope: api + write_repository.",
        "recommend": True,
        "docs": "https://gitlab.com/-/user_settings/personal_access_tokens",
        "placeholder": "glpat-…",
    },
    {
        "key": "GITLAB_HOST", "group": "git", "secret": False,
        "label": "GitLab host",
        "why": "gitlab.com по подразбиране. За self-hosted: git.example.com без https://.",
        "placeholder": "gitlab.com",
    },
    {
        "key": "GITLAB_NAMESPACE", "group": "git", "secret": False,
        "label": "GitLab namespace",
        "why": "Group/user, под който се създават нови проекти. Празно = твоят user.",
    },
    # Publish
    {
        "key": "NETLIFY_AUTH_TOKEN", "group": "publish", "secret": True,
        "label": "Netlify",
        "why": "Траен публичен URL за static / vite / expo-web.",
        "docs": "https://app.netlify.com/user/applications",
    },
    # Mesh
    {
        "key": "IYVERIS_MESH_KEY", "group": "mesh", "secret": True,
        "label": "Mesh ключ",
        "why": "X-Iyveris-Mesh-Key за POST /api/iyveris (chat/capabilities от друг агент).",
    },
    # Lab / engines
    {
        "key": "DAEDALUS_LAB_IMAGE", "group": "lab", "secret": False,
        "label": "Docker lab image",
        "why": "По подразбиране python:3.12-alpine. Само HTTP сървър за мострата.",
        "placeholder": "python:3.12-alpine",
    },
    {
        "key": "DAEDALUS_LAB_PULL", "group": "lab", "secret": False,
        "label": "Разреши docker pull",
        "why": "1 = позволи дърпане на lab образа. Изключено по подразбиране.",
        "placeholder": "0",
    },
    {
        "key": "DAEDALUS_LAB_DOCKER", "group": "lab", "secret": False,
        "label": "Docker lab",
        "why": "0 = никога не стартирай Docker lab (само локален HTTP).",
        "placeholder": "1",
    },
    {
        "key": "GODOT_BIN", "group": "lab", "secret": False,
        "label": "Godot 4 binary",
        "why": "Път до Godot 4.3. Липса = godot kind fail-closed.",
    },
    {
        "key": "DAEDALUS_GODOT_MCP", "group": "lab", "secret": False,
        "label": "Godot MCP име",
        "why": "Име на сървър в mcp.json. Headless TCP bridge е default; MCP е адаптер, не втори Editor.",
    },
    {
        "key": "UNITY_PATH", "group": "lab", "secret": False,
        "label": "Unity Editor",
        "why": "Път до Unity. Липса = unity kind fail-closed (batchmode).",
    },
    {
        "key": "DAEDALUS_UNITY_MCP", "group": "lab", "secret": False,
        "label": "Unity MCP име",
        "why": "Локален Editor MCP в mcp.json. Облакът не вендорира Unity Editor.",
    },
    # 3D / sidecar
    {
        "key": "FAL_KEY", "group": "assets3d", "secret": True,
        "label": "fal.ai",
        "why": "Hunyuan / Trellis опашка за hero GLB.",
    },
    {
        "key": "TRIPO_API_KEY", "group": "assets3d", "secret": True,
        "label": "Tripo",
        "why": "Text-to-3D. Без ключ assets({action:mesh}) връща need_key.",
    },
    {
        "key": "MESHY_API_KEY", "group": "assets3d", "secret": True,
        "label": "Meshy",
        "why": "Text-to-3D алтернатива на Tripo/fal.",
    },
    {
        "key": "MIROFISH_URL", "group": "assets3d", "secret": False,
        "label": "MiroFish API",
        "why": "Само ако въртиш nikmcfly/MiroFish-Offline сам. Default http://127.0.0.1:5001",
        "placeholder": "http://127.0.0.1:5001",
    },
    {
        "key": "MIROFISH_UI", "group": "assets3d", "secret": False,
        "label": "MiroFish UI",
        "why": "UI на sidecar. Default http://127.0.0.1:3000",
        "placeholder": "http://127.0.0.1:3000",
    },
]

FIELD_BY_KEY = {str(f["key"]): f for f in FIELDS}
ALLOWED_KEYS = frozenset(FIELD_BY_KEY)


def secrets_path() -> Path:
    return data_root() / _SECRETS_NAME


def mask_secret(value: str, *, secret: bool = True) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if not secret:
        return raw if len(raw) <= 80 else raw[:40] + "…"
    if len(raw) <= 8:
        return "••••"
    return raw[:2] + "••••" + raw[-4:]


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("secrets.env read failed: %s", exc)
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip("'").strip('"')
        if _KEY_RE.match(key):
            out[key] = val
    return out


def load_secrets_file() -> dict[str, str]:
    return _parse_env_file(secrets_path())


def _quote_env_value(value: str) -> str:
    if all(ch.isalnum() or ch in "-_./:@+=" for ch in value) and value != "":
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def write_secrets_file(data: dict[str, str]) -> None:
    path = secrets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Daedalus owner secrets — not in git. Written by Studio → Свързаност.",
        "# Do not commit this file.",
        "",
    ]
    for key in sorted(data):
        if key in FORBIDDEN_KEYS or not _KEY_RE.match(key):
            continue
        lines.append(f"{key}={_quote_env_value(data[key])}")
    payload = "\n".join(lines) + "\n"
    fd, tmp = tempfile.mkstemp(prefix="secrets.", suffix=".env", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def apply_secrets(data: dict[str, str] | None = None) -> list[str]:
    """Overwrite process env with secrets.env. Returns applied keys."""
    blob = data if data is not None else load_secrets_file()
    applied: list[str] = []
    for key, val in blob.items():
        if key in FORBIDDEN_KEYS or not _KEY_RE.match(key):
            continue
        os.environ[key] = val
        applied.append(key)
    return applied


def current_value(key: str) -> str:
    stored = load_secrets_file()
    if key in stored:
        return stored[key]
    return (os.environ.get(key) or "").strip()


def value_source(key: str) -> str:
    stored = load_secrets_file()
    if key in stored and stored[key] != "":
        return "ui"
    if (os.environ.get(key) or "").strip():
        return "env"
    return "none"


def set_secrets(
    updates: dict[str, str] | None = None,
    *,
    clear: list[str] | None = None,
) -> dict[str, str]:
    """Merge UI updates into secrets.env and apply to os.environ.

    Empty string in *updates* means leave unchanged. *clear* removes keys
    from the file and from os.environ (so a process-level leftover cannot
    shadow the deletion until restart).
    """
    stored = load_secrets_file()
    for key, raw in (updates or {}).items():
        key = str(key).strip()
        if key in FORBIDDEN_KEYS or key not in ALLOWED_KEYS:
            continue
        val = str(raw or "").strip()
        if val == "":
            continue
        stored[key] = val
    for key in clear or []:
        key = str(key).strip()
        if key in FORBIDDEN_KEYS or not _KEY_RE.match(key):
            continue
        stored.pop(key, None)
        os.environ.pop(key, None)
    write_secrets_file(stored)
    apply_secrets(stored)
    return stored


def field_status(field: dict[str, Any]) -> dict[str, Any]:
    key = str(field["key"])
    secret = bool(field.get("secret"))
    raw = current_value(key)
    return {
        "key": key,
        "group": field["group"],
        "label": field["label"],
        "why": field["why"],
        "recommend": bool(field.get("recommend")),
        "docs": field.get("docs") or "",
        "placeholder": field.get("placeholder") or "",
        "secret": secret,
        "set": bool(raw),
        "source": value_source(key),
        "mask": mask_secret(raw, secret=secret) if raw else "",
    }


def catalog_status() -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    by_id = {g["id"]: {**g, "fields": []} for g in GROUPS}
    for field in FIELDS:
        gid = field["group"]
        if gid in by_id:
            by_id[gid]["fields"].append(field_status(field))
    for g in GROUPS:
        groups.append(by_id[g["id"]])
    return groups


def _has_llm() -> bool:
    for key in (
        "OPENROUTER_API_KEY", "ALLTOKEN_API_KEY", "NOVITA_API_KEY",
        "NVIDIA_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
        "DAEDALUS_API_KEY",
    ):
        if current_value(key):
            return True
    provider = (current_value("DAEDALUS_PROVIDER") or os.environ.get("DAEDALUS_PROVIDER") or "").strip().lower()
    return provider in {"ollama", "lmstudio", "llamacpp", "vllm"}


def recommendations() -> list[dict[str, Any]]:
    """Next actions for the owner — highest first."""
    out: list[dict[str, Any]] = []
    if not _has_llm():
        out.append({
            "id": "need-llm",
            "level": "high",
            "title": "Задай LLM ключ или локален provider",
            "body": (
                "Без мозък Дедал не пише код. Най-гъвкаво: OpenRouter "
                "(много модели, вкл. Grok). Алтернатива: AllToken, NVIDIA, или Ollama в Гардероба."
            ),
            "group": "llm",
        })
    or_key = current_value("OPENROUTER_API_KEY")
    model = current_value("DAEDALUS_MODEL")
    provider = (current_value("DAEDALUS_PROVIDER") or "").strip().lower()
    if or_key and not model and provider in ("", "openrouter"):
        out.append({
            "id": "or-model",
            "level": "medium",
            "title": "OpenRouter без избран модел ползва платен Claude",
            "body": "За $0 сложи DAEDALUS_MODEL=poolside/laguna-s-2.1:free или Studio preset or_free.",
            "group": "llm",
        })
    gh = bool(current_value("GITHUB_TOKEN"))
    gl = bool(current_value("GITLAB_TOKEN"))
    if not gh and not gl:
        out.append({
            "id": "need-git",
            "level": "medium",
            "title": "Няма Git токен",
            "body": (
                "Игрите се пишат локално и без това. За нов repo, clone, PR/MR сложи "
                "GITLAB_TOKEN (ако публикуваш в GitLab) и/или GitHub PAT със scope repo."
            ),
            "group": "git",
        })
    if not current_value("NETLIFY_AUTH_TOKEN") and not gl:
        out.append({
            "id": "need-publish",
            "level": "low",
            "title": "Публичен https е опционален",
            "body": "/apps/ на тази машина винаги работи. Netlify или GitLab Pages дават траен линк.",
            "group": "publish",
        })
    if not out:
        out.append({
            "id": "ready",
            "level": "ok",
            "title": "Свързаността е достатъчна за строеж",
            "body": "MCP не е задължителен. Включи сървър само за конкретна дупка в native tools.",
            "group": "mcp",
        })
    return out


def overview() -> dict[str, Any]:
    from .addons import catalog as addon_catalog
    from .mcp_client import status as mcp_status

    mcp = mcp_status()
    addons = addon_catalog()
    return {
        "ok": True,
        "groups": catalog_status(),
        "recommendations": recommendations(),
        "mcp": mcp,
        "addons": [
            {
                "id": a["id"],
                "kind": a.get("kind"),
                "label": a.get("label"),
                "blurb": a.get("blurb"),
                "enabled": a.get("enabled"),
                "offline_ok": a.get("offline_ok"),
                "blocked_offline": a.get("blocked_offline"),
                "env_keys": a.get("env_keys") or [],
                "docs": a.get("docs") or "",
                "recommend": a.get("recommend") or "",
            }
            for a in addons
        ],
        "secrets_file": str(secrets_path()),
    }
