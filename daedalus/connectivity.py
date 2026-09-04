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

# group_id → section shown in Connectors
GROUPS: list[dict[str, Any]] = [
    {
        "id": "llm",
        "title": "Brain (LLM)",
        "blurb": (
            "One brain for all of Daedalus — Studio, Command Center, workers, autopilot. "
            "Ollama or a cloud key. Daedalus calls an OpenAI-compatible API."
        ),
        "pick": (
            "Ollama — no internet, no key; the wardrobe above applies to every mode. "
            "OpenRouter — many models (incl. Grok via x-ai/…). "
            "AllToken — one key for several families. "
            "NVIDIA — free preview."
        ),
    },
    {
        "id": "git",
        "title": "Code (GitHub / GitLab)",
        "blurb": (
            "Full file control is local in the playground. "
            "Tokens are for clone, new repo, push, Pull Request and Merge Request."
        ),
        "pick": (
            "GitLab — if you publish at Blaue-Ente / self-hosted. "
            "GitHub — classic PAT with repo scope for 1-click new repo + PR. "
            "Auto push: GitLab if a token exists, otherwise GitHub."
        ),
    },
    {
        "id": "publish",
        "title": "Publish",
        "blurb": "Studio → Live: local /apps/, then public https.",
        "pick": (
            "Netlify token — lasting *.netlify.app in seconds. "
            "GITLAB_TOKEN — GitLab Pages. "
            "No key: 1-hour Netlify preview (needs Node/npx)."
        ),
    },
    {
        "id": "mcp",
        "title": "MCP servers",
        "blurb": (
            "Daedalus is an MCP client: native tools cover most games. "
            "Enable a server only when native does not cover the job. "
            "Server keys are written here, not in git."
        ),
        "pick": (
            "Start without MCP. GitHub/GitLab MCP — if you want PR/issues via MCP "
            "(Daedalus already has native git_forge). Blender MCP — only with local Blender. "
            "Fetch MCP is redundant next to native web_read."
        ),
    },
    {
        "id": "mesh",
        "title": "Assistants / mesh",
        "blurb": (
            "An external agent (another process, future Grok/Cursor MCP) calls "
            "POST /api/iyveris with this key. Loopback Studio does not need it."
        ),
        "pick": "Change the default key before a public bind. It is not a substitute for DAEDALUS_TOKEN.",
    },
    {
        "id": "lab",
        "title": "Lab / engines",
        "blurb": "Play Lab is the virtual computer. Local HTTP always; optional SmolVM guest browser to watch.",
        "pick": (
            "Local HTTP is enough for HTML5. SmolVM (optional, pip install smolvm) "
            "gives a guest browser you can watch. Docker lab — only if the image is already local. "
            "Windows guests are not started."
        ),
    },
    {
        "id": "assets3d",
        "title": "3D / sidecar",
        "blurb": "Hero GLB only with a key. Without a key — a greybox cube, not a fake hero.",
        "pick": "fal / Tripo / Meshy — optional. MiroFish is a separate AGPL docker, not vendored.",
    },
]

FIELDS: list[dict[str, Any]] = [
    # LLM
    {
        "key": "OPENROUTER_API_KEY", "group": "llm", "secret": True,
        "label": "OpenRouter",
        "why": "One gateway to Claude, GPT, Gemini, DeepSeek, Grok (x-ai/…) and the :free pool.",
        "recommend": True,
        "docs": "https://openrouter.ai/keys",
        "placeholder": "sk-or-…",
    },
    {
        "key": "ALLTOKEN_API_KEY", "group": "llm", "secret": True,
        "label": "AllToken",
        "why": "One paid key for several model families.",
        "recommend": True,
        "docs": "https://api.alltoken.ai",
    },
    {
        "key": "NOVITA_API_KEY", "group": "llm", "secret": True,
        "label": "Novita",
        "why": "Open-source models (DeepSeek, Qwen) and sprites. Pay-per-token.",
        "docs": "https://novita.ai",
    },
    {
        "key": "NVIDIA_API_KEY", "group": "llm", "secret": True,
        "label": "NVIDIA NIM",
        "why": "Free preview of hosted open models.",
        "docs": "https://build.nvidia.com",
    },
    {
        "key": "OPENAI_API_KEY", "group": "llm", "secret": True,
        "label": "OpenAI (direct)",
        "why": "Direct api.openai.com. Only needed if you are not using a gateway.",
    },
    {
        "key": "ANTHROPIC_API_KEY", "group": "llm", "secret": True,
        "label": "Anthropic (direct)",
        "why": "Direct Claude. OpenRouter already covers most cases.",
    },
    {
        "key": "GROQ_API_KEY", "group": "llm", "secret": True,
        "label": "Groq",
        "why": "Fast Llama. Not xAI Grok.",
    },
    {
        "key": "GEMINI_API_KEY", "group": "llm", "secret": True,
        "label": "Gemini (direct)",
        "why": "Google Generative Language. Optional next to OpenRouter.",
    },
    {
        "key": "DAEDALUS_PROVIDER", "group": "llm", "secret": False,
        "label": "Provider",
        "why": "openrouter / alltoken / novita / nvidia / ollama / custom. Empty = auto. Ollama is switched from the wardrobe above.",
        "placeholder": "openrouter",
        "advanced": True,
    },
    {
        "key": "DAEDALUS_MODEL", "group": "llm", "secret": False,
        "label": "Model",
        "why": "Concrete id. For $0 on OpenRouter: poolside/laguna-s-2.1:free (preset or_free).",
        "placeholder": "poolside/laguna-s-2.1:free",
        "advanced": True,
    },
    {
        "key": "DAEDALUS_BASE_URL", "group": "llm", "secret": False,
        "label": "Custom base URL",
        "why": "For xAI Grok: https://api.x.ai/v1 plus a key as OPENAI_API_KEY or DAEDALUS_API_KEY. For Ollama you usually leave this.",
        "placeholder": "https://api.x.ai/v1",
        "advanced": True,
    },
    {
        "key": "DAEDALUS_API_KEY", "group": "llm", "secret": True,
        "label": "DAEDALUS_API_KEY",
        "why": "Fallback key when the provider is custom (e.g. xAI) and there is no separate env.",
        "advanced": True,
    },
    {
        "key": "OLLAMA_HOST", "group": "llm", "secret": False,
        "label": "Ollama host",
        "why": "Default 127.0.0.1:11434. Set if Ollama runs on another machine or port. Same brain for the whole app.",
        "placeholder": "127.0.0.1:11434",
        "advanced": True,
    },
    # Git
    {
        "key": "GITHUB_TOKEN", "group": "git", "secret": True,
        "label": "GitHub PAT",
        "why": "Clone, new repo, push, issues, Pull Request. Classic token: repo scope. Fine-grained: Contents + Pull requests + Administration Read/Write.",
        "recommend": True,
        "docs": "https://github.com/settings/tokens",
        "placeholder": "ghp_…",
    },
    {
        "key": "GITLAB_TOKEN", "group": "git", "secret": True,
        "label": "GitLab token",
        "why": "Clone, new project, push, Merge Request, Pages. Scope: api + write_repository.",
        "recommend": True,
        "docs": "https://gitlab.com/-/user_settings/personal_access_tokens",
        "placeholder": "glpat-…",
    },
    {
        "key": "GITLAB_HOST", "group": "git", "secret": False,
        "label": "GitLab host",
        "why": "gitlab.com by default. For self-hosted: git.example.com without https://.",
        "placeholder": "gitlab.com",
    },
    {
        "key": "GITLAB_NAMESPACE", "group": "git", "secret": False,
        "label": "GitLab namespace",
        "why": "Group/user under which new projects are created. Empty = your user.",
    },
    # Publish
    {
        "key": "NETLIFY_AUTH_TOKEN", "group": "publish", "secret": True,
        "label": "Netlify",
        "why": "Lasting public URL for static / vite / expo-web.",
        "docs": "https://app.netlify.com/user/applications",
    },
    # Mesh
    {
        "key": "IYVERIS_MESH_KEY", "group": "mesh", "secret": True,
        "label": "Mesh key",
        "why": "X-Iyveris-Mesh-Key for POST /api/iyveris (chat/capabilities from another agent).",
    },
    # Lab / engines
    {
        "key": "DAEDALUS_LAB_BACKEND", "group": "lab", "secret": False,
        "label": "Lab computer backend",
        "why": "auto (default) | local | smolvm | docker. SmolVM is optional and not vendored.",
        "placeholder": "auto",
    },
    {
        "key": "DAEDALUS_SMOLVM", "group": "lab", "secret": False,
        "label": "SmolVM guest",
        "why": "0 = never boot SmolVM even if installed. Default: allow when DAEDALUS_LAB_BACKEND is auto or smolvm.",
        "placeholder": "1",
    },
    {
        "key": "DAEDALUS_LAB_IMAGE", "group": "lab", "secret": False,
        "label": "Docker lab image",
        "why": "Default python:3.12-alpine. HTTP server for the specimen only.",
        "placeholder": "python:3.12-alpine",
    },
    {
        "key": "DAEDALUS_LAB_PULL", "group": "lab", "secret": False,
        "label": "Allow docker pull",
        "why": "1 = allow pulling the lab image. Off by default.",
        "placeholder": "0",
    },
    {
        "key": "DAEDALUS_LAB_DOCKER", "group": "lab", "secret": False,
        "label": "Docker lab",
        "why": "0 = never start Docker lab (local HTTP only).",
        "placeholder": "1",
    },
    {
        "key": "GODOT_BIN", "group": "lab", "secret": False,
        "label": "Godot 4 binary",
        "why": "Path to Godot 4.3. Missing = godot kind fail-closed.",
    },
    {
        "key": "DAEDALUS_GODOT_MCP", "group": "lab", "secret": False,
        "label": "Godot MCP name",
        "why": "Server name in mcp.json. Headless TCP bridge is the default; MCP is an adapter, not a second Editor.",
    },
    {
        "key": "UNITY_PATH", "group": "lab", "secret": False,
        "label": "Unity Editor",
        "why": "Path to Unity. Missing = unity kind fail-closed (batchmode).",
    },
    {
        "key": "DAEDALUS_UNITY_MCP", "group": "lab", "secret": False,
        "label": "Unity MCP name",
        "why": "Local Editor MCP in mcp.json. The cloud does not vendor Unity Editor.",
    },
    # 3D / sidecar
    {
        "key": "FAL_KEY", "group": "assets3d", "secret": True,
        "label": "fal.ai",
        "why": "Hunyuan / Trellis queue for hero GLB.",
    },
    {
        "key": "TRIPO_API_KEY", "group": "assets3d", "secret": True,
        "label": "Tripo",
        "why": "Text-to-3D. Without a key assets({action:mesh}) returns need_key.",
    },
    {
        "key": "MESHY_API_KEY", "group": "assets3d", "secret": True,
        "label": "Meshy",
        "why": "Text-to-3D alternative to Tripo/fal.",
    },
    {
        "key": "MIROFISH_URL", "group": "assets3d", "secret": False,
        "label": "MiroFish API",
        "why": "Only if you run nikmcfly/MiroFish-Offline yourself. Default http://127.0.0.1:5001",
        "placeholder": "http://127.0.0.1:5001",
    },
    {
        "key": "MIROFISH_UI", "group": "assets3d", "secret": False,
        "label": "MiroFish UI",
        "why": "Sidecar UI. Default http://127.0.0.1:3000",
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
        "# Daedalus owner secrets — not in git. Written by Studio → Connectors.",
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
    if updates and str(updates.get("DAEDALUS_PROVIDER") or "").strip():
        try:
            from .llm_wardrobe import apply_provider_hint
            apply_provider_hint(str(updates["DAEDALUS_PROVIDER"]))
        except Exception as extra:
            log.debug("wardrobe sync from provider: %s", extra)
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
        "advanced": bool(field.get("advanced")),
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
    if provider in {"ollama", "lmstudio", "llamacpp", "vllm"}:
        return True
    try:
        from .llm_wardrobe import wants_local
        if wants_local():
            return True
    except Exception:
        pass
    return False


def recommendations() -> list[dict[str, Any]]:
    """Next actions for the owner — highest first."""
    out: list[dict[str, Any]] = []
    if not _has_llm():
        out.append({
            "id": "need-llm",
            "level": "high",
            "title": "Set an LLM key or a local provider",
            "body": (
                "Without a brain Daedalus does not write code. Most flexible: Ollama "
                "(wardrobe above) or OpenRouter (many models, incl. Grok). Alternative: AllToken / NVIDIA."
            ),
            "group": "llm",
        })
    or_key = current_value("OPENROUTER_API_KEY")
    model = current_value("DAEDALUS_MODEL")
    provider = (current_value("DAEDALUS_PROVIDER") or "").strip().lower()
    try:
        from .local_llm import default_base_url, local_requested, probe_endpoint
        if local_requested():
            probed = probe_endpoint(default_base_url("ollama"))
            if not probed.get("ok"):
                out.insert(0, {
                    "id": "ollama-down",
                    "level": "high",
                    "title": "Ollama is not responding",
                    "body": (
                        "The brain is local for all of Daedalus (chat, workers, autopilot), "
                        "but nothing answers on port 11434. Start Ollama, then pull a model from the wardrobe."
                    ),
                    "group": "llm",
                })
    except Exception:
        pass
    if or_key and not model and provider in ("", "openrouter"):
        try:
            from .local_llm import local_requested as _loc
            cloudish = not _loc()
        except Exception:
            cloudish = True
        if cloudish:
            out.append({
                "id": "or-model",
                "level": "medium",
                "title": "OpenRouter without a chosen model uses paid Claude",
                "body": "For $0 set DAEDALUS_MODEL=poolside/laguna-s-2.1:free or preset or_free in Connectors.",
                "group": "llm",
            })
    gh = bool(current_value("GITHUB_TOKEN"))
    gl = bool(current_value("GITLAB_TOKEN"))
    if not gh and not gl:
        out.append({
            "id": "need-git",
            "level": "medium",
            "title": "No Git token",
            "body": (
                "Games are written locally without one. For a new repo, clone, PR/MR set "
                "GITLAB_TOKEN (if you publish on GitLab) and/or a GitHub PAT with repo scope."
            ),
            "group": "git",
        })
    if not current_value("NETLIFY_AUTH_TOKEN") and not gl:
        out.append({
            "id": "need-publish",
            "level": "low",
            "title": "Public https is optional",
            "body": "/apps/ on this machine always works. Netlify or GitLab Pages give a lasting link.",
            "group": "publish",
        })
    if not out:
        out.append({
            "id": "ready",
            "level": "ok",
            "title": "Connectors are enough to build",
            "body": "MCP is not required. Enable a server only for a specific gap in native tools.",
            "group": "mcp",
        })
    return out


def _public_llm(cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": cfg.get("provider") or "",
        "model": cfg.get("model") or "",
        "source": cfg.get("source") or "",
        "local": bool(cfg.get("local")),
        "reachable": cfg.get("reachable"),
        "warning": str(cfg.get("warning") or ""),
        "base_url": cfg.get("base_url") or "",
        "has_key": bool(cfg.get("api_key")),
    }


def brain_overview() -> dict[str, Any]:
    """Sanitized LLM + wardrobe + worker routing for Connectors. Never includes keys."""
    from .llm_config import get_config
    from .llm_wardrobe import status as wardrobe_status
    from .local_llm import default_base_url, local_requested, probe_endpoint
    from .workers import get_worker_config

    try:
        cfg = get_config()
    except Exception as extra:
        cfg = {"warning": str(extra)[:240]}
    try:
        w = wardrobe_status()
    except Exception as extra:
        w = {"ok": False, "error": str(extra)[:240], "wardrobe": {}, "catalog": [], "profile": {}}
    try:
        workers = get_worker_config()
    except Exception:
        workers = {}
    ollama = probe_endpoint(default_base_url("ollama"))
    wardrobe = w.get("wardrobe") if isinstance(w.get("wardrobe"), dict) else {}
    profile = w.get("profile") if isinstance(w.get("profile"), dict) else {}
    local = bool(cfg.get("local") or local_requested())
    slots = {}
    for slot, sc in (workers or {}).items():
        if not isinstance(sc, dict):
            continue
        slots[slot] = {
            "provider": sc.get("provider") or "",
            "model": sc.get("model") or "",
            "source": sc.get("source") or "",
            "enabled": sc.get("enabled", True),
        }
    catalog = []
    for row in w.get("catalog") or []:
        catalog.append({
            "id": row.get("id"),
            "label": row.get("label"),
            "role": row.get("role"),
            "ollama": row.get("ollama"),
            "blurb": row.get("blurb") or "",
            "ram_gb": row.get("ram_gb"),
            "fits": row.get("fits"),
            "installed": row.get("installed"),
            "recommended": row.get("recommended"),
        })
    return {
        "mode": "local" if local else "cloud",
        "effective": _public_llm(cfg if isinstance(cfg, dict) else {}),
        "wardrobe": {
            "backend": wardrobe.get("backend") or "auto",
            "active": wardrobe.get("active"),
            "offline": bool(wardrobe.get("offline")),
        },
        "ollama": {
            "bin": bool(w.get("ollama")),
            "ok": bool(ollama.get("ok")),
            "base_url": ollama.get("base_url") or "",
            "models": (ollama.get("models") or [])[:24],
            "error": ollama.get("error") or "",
        },
        "profile": {
            "class": profile.get("class") or "",
            "ram_gb": profile.get("ram_gb"),
            "note": profile.get("note") or "",
        },
        "catalog": catalog,
        "workers": slots,
    }


def overview() -> dict[str, Any]:
    from .addons import catalog as addon_catalog
    from .mcp_client import status as mcp_status

    mcp = mcp_status()
    addons = addon_catalog()
    return {
        "ok": True,
        "groups": catalog_status(),
        "recommendations": recommendations(),
        "brain": brain_overview(),
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
