"""Дедал Playground — project registry & workspace management.

Standalone apps, които Дедал строи / клонира от GitHub, се пазят в
``playground/projects/{slug}/``. Регистърът е в PostgreSQL
(``playground_projects``). Публикуването → object storage +
``playground_published_apps`` (виж ``playground_publish.py``).

Видове проекти (``kind``):
  static    — HTML/CSS/JS (вкл. canvas игри). Serve-ва се in-process → dev+prod.
  fastapi   — Python ASGI app (uvicorn). Subprocess test (само dev).
  streamlit — Streamlit app. Subprocess test (само dev).
  vite      — Frontend build tool. Test чрез dev server (dev) / build→static (publish).
              iOS+Android: Capacitor wrap of dist/ (not a new kind; see playground_capacitor).
  vite-react — Vite + React 18. Test чрез dev server (dev) / build→static (publish).
  node      — Generic Node app. Subprocess test (само dev).
  expo      — React Native / Expo app. Dev test чрез Expo Web (браузър preview).
              Publish = ``npx expo export --platform web`` → static web app.
              QR → Expo Go на телефон (само dev, изисква тунел).

Източник (``source``): built (Дедал) | cloned (GitHub) | imported (upload) | revived (Import & Evolve).
"""
from __future__ import annotations

import html
import json as _json_pg
import logging
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from .paths import playground_projects_dir, repo_root

log = logging.getLogger(__name__)

ADN_ROOT = repo_root()
PROJECTS_DIR = playground_projects_dir()

VALID_KINDS = {"static", "fastapi", "streamlit", "vite", "vite-react", "node", "expo", "unity", "godot", "auto"}
VALID_SOURCES = {"built", "cloned", "imported", "revived"}

_SLUG_RE = re.compile(r"[^a-z0-9-]+")
_CYR = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l",
    "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s",
    "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch",
    "ш": "sh", "щ": "sht", "ъ": "a", "ь": "", "ю": "yu", "я": "ya",
}


# ---------------------------------------------------------------------------
# DB helper (shared pool)
# ---------------------------------------------------------------------------

async def _pool():
    from .host import get_pool
    return await get_pool()


# ---------------------------------------------------------------------------
# Slug & path helpers
# ---------------------------------------------------------------------------

def slugify(name: str) -> str:
    """Convert a human name to a safe url/dir slug.

    Bulgarian letters become Latin so ``Северна сага`` is ``severna-saga``,
    not ``app-ab12cd``.
    """
    s = (name or "").strip().lower()
    s = "".join(_CYR.get(ch, ch) for ch in s)
    s = s.replace(" ", "-").replace("_", "-")
    s = _SLUG_RE.sub("", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or f"app-{uuid.uuid4().hex[:6]}"


def project_dir(slug: str) -> Path:
    """Absolute path to a project's workspace dir (safe — no traversal).

    Reads ``DAEDALUS_PLAYGROUND`` live so tests and standalone boot cannot
    leave a stale module-level folder behind.
    """
    safe = slugify(slug)
    return playground_projects_dir() / safe


def _safe_join(root: Path, rel: str) -> Path:
    """Join rel onto root, refusing path traversal outside root."""
    target = (root / rel).resolve()
    root_resolved = root.resolve()
    if root_resolved not in target.parents and target != root_resolved:
        raise ValueError(f"path traversal blocked: {rel!r}")
    return target


# ---------------------------------------------------------------------------
# Kind detection
# ---------------------------------------------------------------------------

def detect_kind(root: Path) -> tuple[str, str, str]:
    """Inspect a project dir → (kind, run_cmd, entry).

    Best-effort heuristics. run_cmd is the dev-test command; entry is the
    static entrypoint (for static/vite-built apps).
    """
    root = Path(root)
    has = lambda p: (root / p).exists()  # noqa: E731

    # Python web frameworks
    if has("streamlit_app.py") or has("app.py") and _file_mentions(root, "streamlit"):
        main = "streamlit_app.py" if has("streamlit_app.py") else "app.py"
        return "streamlit", f"streamlit run {main} --server.headless true", "index.html"
    if _any_py_imports(root, ("fastapi", "uvicorn")):
        main = _guess_python_main(root)
        return "fastapi", f"uvicorn {main}:app --host 0.0.0.0", "index.html"

    # Unity
    if has("Packages/manifest.json") and (root / "Assets").is_dir():
        return "unity", "", "studio/preview/index.html"
    if has("ProjectSettings/ProjectVersion.txt"):
        return "unity", "", "studio/preview/index.html"

    # Godot 4
    if has("project.godot"):
        return "godot", "", "studio/preview/index.html"

    # Expo / React Native
    if has("app.json") or has("app.config.js") or has("app.config.ts"):
        import json as _json
        app_cfg = root / "app.json"
        if app_cfg.exists():
            try:
                cfg = _json.loads(app_cfg.read_text("utf-8"))
                if "expo" in cfg:
                    return "expo", "npx expo start --web --non-interactive", "dist/index.html"
            except Exception:
                pass
        if has("App.tsx") or has("App.js") or (root / "src/app").is_dir():
            return "expo", "npx expo start --web --non-interactive", "dist/index.html"

    # Node / Vite
    pkg = root / "package.json"
    if pkg.exists():
        txt = _read_text(pkg)
        if '"expo"' in txt or "'expo'" in txt:
            return "expo", "npx expo start --web --non-interactive", "dist/index.html"
        if "expo-router" in txt:
            return "expo", "npx expo start --web --non-interactive", "dist/index.html"
        if "vite" in txt:
            if '"react"' in txt or "'react'" in txt:
                return "vite-react", "npm run dev", "dist/index.html"
            return "vite", "npm run dev", "dist/index.html"
        return "node", "npm start", "index.html"

    # Plain static
    if has("index.html") or _has_any_html(root):
        entry = "index.html" if has("index.html") else _first_html(root)
        return "static", "", entry

    return "static", "", "index.html"


def _read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _file_mentions(root: Path, needle: str) -> bool:
    for name in ("app.py", "requirements.txt", "pyproject.toml"):
        if needle in _read_text(root / name):
            return True
    return False


def _any_py_imports(root: Path, needles: tuple[str, ...]) -> bool:
    for py in list(root.glob("*.py")) + list(root.glob("*/*.py")):
        txt = _read_text(py)
        if any(n in txt for n in needles):
            return True
    return any(n in _read_text(root / "requirements.txt") for n in needles)


def _guess_python_main(root: Path) -> str:
    for cand in ("main", "app", "server"):
        if (root / f"{cand}.py").exists():
            return cand
    py = sorted(root.glob("*.py"))
    return py[0].stem if py else "main"


def _has_any_html(root: Path) -> bool:
    return bool(list(root.glob("*.html")) or list(root.glob("**/*.html")))


def _first_html(root: Path) -> str:
    for h in sorted(root.glob("*.html")) + sorted(root.glob("**/*.html")):
        return str(h.relative_to(root))
    return "index.html"


# ---------------------------------------------------------------------------
# Registry CRUD
# ---------------------------------------------------------------------------

_SCAFFOLD: dict[str, dict[str, str]] = {
    "static": {
        "index.html": """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{name}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: #0f172a; color: #e2e8f0; font-family: system-ui, sans-serif;
           display: flex; align-items: center; justify-content: center; min-height: 100vh; }}
    .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 12px;
             padding: 2rem 2.5rem; text-align: center; max-width: 420px; }}
    h1 {{ font-size: 1.8rem; font-weight: 700; margin-bottom: .5rem; color: #38bdf8; }}
    p  {{ color: #94a3b8; line-height: 1.6; }}
    button {{ margin-top: 1.5rem; padding: .6rem 1.4rem; border-radius: 8px;
              background: #0ea5e9; color: white; border: none; cursor: pointer;
              font-size: 1rem; transition: background .2s; }}
    button:hover {{ background: #0284c7; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>🚀 {name}</h1>
    <p>{description}</p>
    <button onclick="this.textContent='✅ Работи!'">Кликни ме</button>
  </div>
</body>
</html>
""",
    },
    "fastapi": {
        "main.py": """\
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title="{name}")

@app.get("/", response_class=HTMLResponse)
async def root():
    return \"\"\"<html><body style='font-family:sans-serif;padding:2rem'>
    <h1>🚀 {name}</h1><p>FastAPI работи!</p>
    <a href='/docs'>→ Swagger UI</a></body></html>\"\"\"

@app.get("/api/hello")
async def hello():
    return {{"message": "Hello from {name}!", "status": "ok"}}
""",
        "requirements.txt": "fastapi\nuvicorn[standard]\n",
    },
    "streamlit": {
        "app.py": """\
import streamlit as st

st.set_page_config(page_title="{name}", page_icon="🚀")
st.title("🚀 {name}")
st.write("{description}")

with st.sidebar:
    st.header("Настройки")
    name_input = st.text_input("Твоето име:", "World")

st.success(f"Hello, {{name_input}}! Streamlit работи!")
st.balloons()
""",
        "requirements.txt": "streamlit\n",
    },
    "vite": {
        "index.html": """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{name}</title>
</head>
<body>
  <div id="app"></div>
  <script type="module" src="/src/main.js"></script>
</body>
</html>
""",
        "src/main.js": """\
document.querySelector('#app').innerHTML = `
  <div style="font-family:sans-serif;padding:2rem;text-align:center">
    <h1>🚀 {name}</h1>
    <p style="color:#666">{description}</p>
    <p>Vite + Vanilla JS работи!</p>
  </div>
`
""",
        "package.json": '{{\n  "name": "{slug}",\n  "version": "0.0.1",\n  "scripts": {{\n    "dev": "vite",\n    "build": "vite build"\n  }},\n  "devDependencies": {{\n    "vite": "^5.0.0"\n  }}\n}}\n',
    },
    "vite-react": {
        "index.html": """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{name}</title>
</head>
<body>
  <div id="root"></div>
  <script type="module" src="/src/main.jsx"></script>
</body>
</html>
""",
        "src/main.jsx": """\
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
""",
        "src/App.jsx": """\
import React from 'react'
import './App.css'

export default function App() {{
  return (
    <div className="app">
      <h1>🚀 {name}</h1>
      <p className="desc">{description}</p>
      <p className="hint">React 18 готов! Редактирай <code>src/App.jsx</code></p>
    </div>
  )
}}
""",
        "src/App.css": """\
.app {{ font-family: system-ui; padding: 2rem; text-align: center; background: #0f172a; min-height: 100vh; color: #e2e8f0; }}
h1 {{ font-size: 1.8rem; font-weight: 700; color: #38bdf8; margin-bottom: .5rem; }}
.desc {{ color: #94a3b8; margin-bottom: 1rem; }}
.hint {{ font-size: .85rem; color: #64748b; }}
code {{ background: #1e293b; padding: 2px 6px; border-radius: 4px; font-size: .8rem; }}
""",
        "vite.config.js": """\
import {{ defineConfig }} from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({{
  plugins: [react()],
}})
""",
        "package.json": '{{\n  "name": "{slug}",\n  "version": "0.0.1",\n  "scripts": {{\n    "dev": "vite",\n    "build": "vite build"\n  }},\n  "dependencies": {{\n    "react": "^18.3.1",\n    "react-dom": "^18.3.1"\n  }},\n  "devDependencies": {{\n    "vite": "^5.0.0",\n    "@vitejs/plugin-react": "^4.3.4"\n  }}\n}}\n',
    },
    "node": {
        "index.js": """\
const http = require('http');
const PORT = process.env.PORT || 3000;

const server = http.createServer((req, res) => {{
  res.writeHead(200, {{ 'Content-Type': 'text/html; charset=utf-8' }});
  res.end(`<html><body style="font-family:sans-serif;padding:2rem">
    <h1>🚀 {name}</h1>
    <p>{description}</p>
    <p>Node.js работи на порт {{PORT}}!</p>
  </body></html>`);
}});

server.listen(PORT, () => console.log(`Server running on http://localhost:${{PORT}}`));
""",
        "package.json": '{{\n  "name": "{slug}",\n  "version": "0.0.1",\n  "main": "index.js",\n  "scripts": {{\n    "start": "node index.js"\n  }}\n}}\n',
    },
    "expo": {
        # Rich scaffold lives in playground_expo_scaffold.EXPO_SCAFFOLD (imported below).
    },
    "unity": {
        # Unity scaffold via playground_unity_scaffold (imported below).
    },
    "godot": {
        # Godot scaffold via playground_godot_scaffold (imported below).
    },
}

# Merge NativeBot-style Expo Router scaffold
from .playground_expo_scaffold import EXPO_SCAFFOLD as _EXPO_SCAFFOLD  # noqa: E402
_SCAFFOLD["expo"] = _EXPO_SCAFFOLD


def _write_scaffold(pdir: Path, kind: str, name: str, slug: str, description: str) -> None:
    """Write initial scaffold files if directory is empty (new project)."""
    if kind == "unity":
        from .playground_unity_scaffold import write_unity_scaffold
        write_unity_scaffold(pdir, name, slug, description)
        return
    if kind == "godot":
        from .playground_godot_scaffold import write_godot_scaffold
        write_godot_scaffold(pdir, name, slug, description)
        return
    if kind == "vite":
        from .playground_racer_scaffold import maybe_write_racer_scaffold
        if maybe_write_racer_scaffold(pdir, kind, name, slug, description):
            return
    templates = _SCAFFOLD.get(kind, _SCAFFOLD["static"])
    ctx = {"name": name, "slug": slug, "description": description or f"Проект: {name}"}
    html_ctx = {
        "name": html.escape(name or "", quote=True),
        "slug": html.escape(slug or "", quote=True),
        "description": html.escape(ctx["description"], quote=True),
    }
    from .playground_expo_scaffold import _apply_template_ctx as _expo_ctx
    for rel_path, content in templates.items():
        target = pdir / rel_path
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        fmt_ctx = html_ctx if rel_path.endswith((".html", ".htm")) else ctx
        try:
            if kind == "expo" and not rel_path.endswith(".json"):
                text = _expo_ctx(content, name, slug, ctx["description"])
            else:
                text = content.format(**fmt_ctx)
        except (KeyError, ValueError):
            if kind == "expo":
                text = _expo_ctx(content, name, slug, ctx["description"])
            else:
                text = content
        target.write_text(text, encoding="utf-8")


async def create_project(
    name: str,
    *,
    kind: str = "",
    source: str = "built",
    description: str = "",
    slug: str = "",
    github_repo: str = "",
    coding_mode: str = "lite",
    effort: str = "auto",
) -> dict[str, Any]:
    """Create a project row + workspace dir with scaffold files."""
    slug = slugify(slug or name)
    pid = uuid.uuid4().hex[:12]
    pdir = project_dir(slug)
    pdir.mkdir(parents=True, exist_ok=True)

    _requested_auto = (kind == "auto")
    if not kind:
        kind, run_cmd, entry = detect_kind(pdir)
    elif kind == "auto":
        run_cmd, entry = "", ""
    else:
        _, run_cmd, entry = detect_kind(pdir)
    if kind not in VALID_KINDS:
        kind = "static"
    if source not in VALID_SOURCES:
        source = "built"

    # Write scaffold if this is a freshly built project (no existing files)
    # ``auto`` — empty workspace; autopilot resolves platform before first build.
    if source == "built" and not _requested_auto and not any(pdir.iterdir()):
        _write_scaffold(pdir, kind, name, slug, description)
        # Re-detect after scaffold (picks up run_cmd / entry from real files)
        kind, run_cmd, entry = detect_kind(pdir)
    if source == "built":
        try:
            from .world_kit import needed as world_needed, write as write_world
            if world_needed(description, effort=effort):
                write_world(pdir, name=name, slug=slug, description=description, kind=kind)
        except Exception:
            log.debug("world kit skipped", exc_info=True)

    # Always ensure assets/ scaffold exists for built/cloned projects
    if source in ("built", "cloned", "imported"):
        from .playground_assets import ensure_assets_dir
        ensure_assets_dir(slug)

    if coding_mode not in ("lite", "pro", "premium"):
        coding_mode = "lite"

    try:
        root_rel = str(pdir.relative_to(repo_root()))
    except ValueError:
        root_rel = str(pdir)

    pool = await _pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO playground_projects
              (id, slug, name, description, kind, source, root_dir, run_cmd, entry, github_repo, status, coding_mode)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'idle',$11)
            ON CONFLICT (slug) DO UPDATE SET
              name=EXCLUDED.name, description=EXCLUDED.description,
              kind=EXCLUDED.kind, source=EXCLUDED.source,
              github_repo=EXCLUDED.github_repo, updated_at=NOW()
            """,
            pid, slug, name or slug, description, kind, source,
            root_rel, run_cmd, entry, github_repo, coding_mode,
        )
    log.info("Playground: created project %s (slug=%s kind=%s source=%s)", pid, slug, kind, source)
    return await get_project(slug)


async def list_projects() -> list[dict[str, Any]]:
    pool = await _pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM playground_projects ORDER BY created_at DESC"
        )
    return [dict(r) for r in rows]


async def get_project(id_or_slug: str) -> dict[str, Any] | None:
    pool = await _pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM playground_projects WHERE id=$1 OR slug=$1",
            id_or_slug,
        )
    return dict(row) if row else None


async def update_project(id_or_slug: str, **fields: Any) -> dict[str, Any] | None:
    allowed = {"name", "description", "kind", "run_cmd", "entry",
               "github_repo", "gitlab_repo", "status", "source", "coding_mode",
               "upstream_url", "cloned_branch", "cloned_ref", "upstream_sha",
               "compat_score", "compat_detail"}
    _jsonb_fields = {"compat_detail"}
    sets, vals = [], []
    for k, v in fields.items():
        if k in allowed:
            if k in _jsonb_fields and isinstance(v, (dict, list)):
                v = _json_pg.dumps(v, ensure_ascii=False)
            sets.append(f"{k}=${len(vals) + 1}")
            vals.append(v)
    if not sets:
        return await get_project(id_or_slug)
    vals.append(id_or_slug)
    pool = await _pool()
    async with pool.acquire() as conn:
        await conn.execute(
            f"UPDATE playground_projects SET {', '.join(sets)}, updated_at=NOW() "
            f"WHERE id=${len(vals)} OR slug=${len(vals)}",
            *vals,
        )
    return await get_project(id_or_slug)


async def delete_project(id_or_slug: str, *, wipe_files: bool = True) -> bool:
    proj = await get_project(id_or_slug)
    if not proj:
        return False
    slug = proj["slug"]
    pool = await _pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM playground_projects WHERE id=$1 OR slug=$1", id_or_slug
        )
        # Also clean up published app record if it exists
        await conn.execute(
            "DELETE FROM playground_published_apps WHERE slug=$1", slug
        )
    if wipe_files:
        pdir = project_dir(slug)
        if pdir.exists():
            shutil.rmtree(pdir, ignore_errors=True)
        # Remove published files too
        from . import playground_publish as _pub
        pub_dir = _pub.app_dir(slug)
        if pub_dir.exists():
            shutil.rmtree(pub_dir, ignore_errors=True)
    log.info("Playground: deleted project %s (incl. published record)", slug)
    return True


# ---------------------------------------------------------------------------
# Workspace file IO
# ---------------------------------------------------------------------------

def write_file(slug: str, path: str, content: str) -> None:
    """Write a single file (autopilot + admin routes)."""
    write_files(slug, {path: content})


def write_files(slug: str, files: dict[str, str]) -> int:
    """Write {relpath: content} into the project workspace. Returns count."""
    root = project_dir(slug)
    root.mkdir(parents=True, exist_ok=True)
    n = 0
    for rel, content in files.items():
        target = _safe_join(root, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        n += 1
    return n


def write_bytes(slug: str, rel: str, data: bytes) -> int:
    """Write binary content (image/audio asset) into the project workspace. Returns byte count."""
    root = project_dir(slug)
    root.mkdir(parents=True, exist_ok=True)
    target = _safe_join(root, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return len(data)


def edit_file(slug: str, rel: str, search: str, replace: str) -> dict[str, Any]:
    """Surgical edit: replace the first occurrence of ``search`` with ``replace``.

    Lets Дедал change one block of an existing file without re-emitting the whole
    thing (cheaper, safer — no risk of truncating a large file or dropping code it
    didn't mean to touch). Returns {ok, error?, occurrences, file}.
    The ``search`` text must match EXACTLY (whitespace included) and must be unique
    enough to identify a single location; if it appears more than once we refuse,
    to avoid editing the wrong spot.
    """
    target = _safe_join(project_dir(slug), rel)
    if not target.exists() or not target.is_file():
        return {"ok": False, "error": f"файлът не съществува: {rel}", "file": rel}
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return {"ok": False, "error": f"не мога да прочета файла: {exc}", "file": rel}
    if not search:
        return {"ok": False, "error": "празен search текст", "file": rel}
    count = content.count(search)
    if count == 0:
        return {"ok": False, "error": "search текстът не е намерен (трябва да съвпада точно, вкл. интервали)",
                "occurrences": 0, "file": rel}
    if count > 1:
        return {"ok": False, "error": f"search текстът се среща {count} пъти — добави повече контекст за уникалност",
                "occurrences": count, "file": rel}
    new_content = content.replace(search, replace, 1)
    target.write_text(new_content, encoding="utf-8")
    return {"ok": True, "occurrences": 1, "file": rel,
            "old_size": len(content), "new_size": len(new_content)}


def read_tree(slug: str, *, max_files: int = 500) -> list[dict[str, Any]]:
    """List files in a project workspace (relative paths + sizes)."""
    root = project_dir(slug)
    if not root.exists():
        return []
    out: list[dict[str, Any]] = []
    skip = {".git", "node_modules", "__pycache__", ".venv", "dist", ".cache"}
    for p in sorted(root.rglob("*")):
        if p.is_dir():
            continue
        if any(part in skip for part in p.relative_to(root).parts):
            continue
        try:
            out.append({
                "path": str(p.relative_to(root)),
                "size": p.stat().st_size,
            })
        except Exception:
            continue
        if len(out) >= max_files:
            break
    return out


def read_file(slug: str, rel: str, *, max_bytes: int = 200_000) -> str:
    target = _safe_join(project_dir(slug), rel)
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(rel)
    data = target.read_bytes()[:max_bytes]
    return data.decode("utf-8", errors="replace")


def project_size(slug: str) -> int:
    root = project_dir(slug)
    if not root.exists():
        return 0
    total = 0
    skip = {".git", "node_modules", "__pycache__", ".venv", ".cache"}
    for p in root.rglob("*"):
        if p.is_file() and not any(part in skip for part in p.parts):
            try:
                total += p.stat().st_size
            except Exception:
                pass
    return total


# ---------------------------------------------------------------------------
# Autopilot plan persistence (resume support)
# ---------------------------------------------------------------------------

async def save_autopilot_plan(slug: str, plan: dict) -> None:
    """Persist the build plan JSON to DB so autopilot can be resumed later."""
    pool = await _pool()
    import json as _j
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE playground_projects SET autopilot_plan=$1, updated_at=NOW() WHERE slug=$2",
            _j.dumps(plan, ensure_ascii=False), slug,
        )


async def load_autopilot_plan(slug: str) -> dict | None:
    """Load the saved autopilot plan for a project, or None if not set."""
    pool = await _pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT autopilot_plan FROM playground_projects WHERE slug=$1", slug
        )
    if not row or not row["autopilot_plan"]:
        return None
    raw = row["autopilot_plan"]
    if isinstance(raw, str):
        import json as _j
        return _j.loads(raw)
    if isinstance(raw, dict):
        return raw
    return None


async def mark_stage_done(slug: str, stage_id: int | str) -> None:
    """Mark a specific stage as done in the persisted plan (idempotent)."""
    plan = await load_autopilot_plan(slug)
    if not plan or not isinstance(plan.get("stages"), list):
        return
    sid = int(stage_id) if str(stage_id).isdigit() else stage_id
    changed = False
    for s in plan["stages"]:
        if s.get("id") == sid or s.get("id") == stage_id:
            s["status"] = "done"
            changed = True
    if changed:
        await save_autopilot_plan(slug, plan)


async def clear_autopilot_plan(slug: str) -> None:
    """Remove the saved plan so the next run starts fresh."""
    pool = await _pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE playground_projects SET autopilot_plan=NULL, updated_at=NOW() WHERE slug=$1",
            slug,
        )


async def get_resume_info(slug: str) -> dict | None:
    """Return resume info for display; also fetches project description.
    Returns None if no partial plan or plan is fully done."""
    plan = await load_autopilot_plan(slug)
    if not plan or not isinstance(plan.get("stages"), list):
        return None
    stages = plan["stages"]
    done = [s for s in stages if s.get("status") == "done"]
    pending = [s for s in stages if s.get("status") != "done"]
    if not pending:
        return None  # fully done — fresh build
    if not done:
        return None  # nothing done yet — no reason to resume
    next_s = pending[0]
    # Fetch project description for pre-filling the modal
    description = ""
    try:
        pool = await _pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT description FROM playground_projects WHERE slug=$1", slug
            )
            if row:
                description = row["description"] or ""
    except Exception:
        pass
    return {
        "plan_title": plan.get("title", ""),
        "done_stages": len(done),
        "total_stages": len(stages),
        "next_stage_id": next_s.get("id"),
        "next_stage": next_s.get("title", ""),
        "description": description,
    }
