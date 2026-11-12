"""Дедал Playground — public serving router.

Публикуваните apps се зареждат като вътрешни ADN раздели на
``/apps/{slug}/``. Static файлове се serve-ват директно от materialize-натата
папка (работи в dev И prod). За non-static published apps с жив runner →
reverse-proxy към процеса (само dev).

DB routes (per-app scoped KV — Replit DB namespace):
  GET  /apps/{slug}/db/            — list keys
  GET  /apps/{slug}/db/{key}       — get value
  POST /apps/{slug}/db/{key}       — set value  (body: {"value": ...})
  DELETE /apps/{slug}/db/{key}     — delete key

Serving routes:
  GET /apps/                       — index (списък публикувани раздели)
  GET /apps/menu.json              — меню за навигация
  GET /apps/{slug}/                — entrypoint на app
  GET /apps/{slug}/{path:path}     — статичен файл / proxy
"""
from __future__ import annotations

import json as _json
import logging
import mimetypes
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response

from . import playground_publish as pub
from .playground_proxy import _proxy, rewrite_preview_paths as _rewrite_preview_paths
from . import playground_runner as runner

log = logging.getLogger(__name__)

router = APIRouter(prefix="/apps", tags=["playground-apps"])

# ---------------------------------------------------------------------------
# Playground SDK injection (error catcher + PlaygroundDB client)
# ---------------------------------------------------------------------------

_SDK_TEMPLATE = """\
<script>
/* ── ADN Playground SDK ─────────────────────────────────── */
(function(){{
  var _slug = {slug_json};
  var _base = '/apps/' + _slug + '/db/';
  window.PlaygroundDB = {{
    get:   function(k)   {{ return fetch(_base+encodeURIComponent(k)).then(function(r){{return r.json()}}).then(function(d){{return d.ok?d.value:undefined}}); }},
    set:   function(k,v) {{ return fetch(_base+encodeURIComponent(k),{{method:'POST',body:JSON.stringify({{value:v}}),headers:{{'Content-Type':'application/json'}}}}).then(function(r){{return r.json()}}); }},
    del:   function(k)   {{ return fetch(_base+encodeURIComponent(k),{{method:'DELETE'}}).then(function(r){{return r.json()}}); }},
    list:  function()    {{ return fetch(_base).then(function(r){{return r.json()}}).then(function(d){{return d.keys||[]}}); }},
    clear: function()    {{ return window.PlaygroundDB.list().then(function(keys){{return Promise.all(keys.map(function(k){{return window.PlaygroundDB.del(k);}}));}}); }},
  }};
  window.onerror = function(msg,src,line,col,err){{
    window.parent.postMessage({{type:'pg-error',slug:_slug,msg:String(msg)+(src?' @ '+src+':'+line:''),stack:(err&&err.stack)||''}}, '*');
    return false;
  }};
  window.addEventListener('unhandledrejection',function(e){{
    var r=e.reason;
    window.parent.postMessage({{type:'pg-error',slug:_slug,msg:'Unhandled: '+(r&&r.message||String(r)),stack:(r&&r.stack)||''}}, '*');
  }});
}})();
</script>"""


def _inject_playground_scripts(html: str, slug: str) -> str:
    """Inject PlaygroundDB client + error reporter into an HTML string."""
    script = _SDK_TEMPLATE.format(slug_json=_json.dumps(slug))
    for marker in ("<head>", "<HEAD>", "<html>", "<HTML>", "<body>", "<BODY>"):
        idx = html.find(marker)
        if idx != -1:
            pos = idx + len(marker)
            return html[:pos] + "\n" + script + html[pos:]
    return script + "\n" + html


# ---------------------------------------------------------------------------
# Per-app KV database (Replit DB when present, else DAEDALUS_DATA/kv)
# ---------------------------------------------------------------------------

_DB_MAX_KEYS = 200
_DB_MAX_VAL_BYTES = 65_536


def _db_ns(slug: str) -> str:
    from . import playground as _pg
    return f"playground:db:{_pg.slugify(slug)}:"


def _db_key(slug: str, key: str) -> str:
    return _db_ns(slug) + key


def _store():
    from .kv import db as _rdb
    return _rdb


@router.get("/{slug}/db/")
async def app_db_list(slug: str):
    """List all stored keys for this app."""
    try:
        _rdb = _store()
        prefix = _db_ns(slug)
        keys = [k[len(prefix):] for k in _rdb.prefix(prefix)]
        return JSONResponse({"ok": True, "keys": keys[:_DB_MAX_KEYS]})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@router.get("/{slug}/db/{key:path}")
async def app_db_get(slug: str, key: str):
    """Get a value by key."""
    try:
        _rdb = _store()
        full = _db_key(slug, key)
        val = _rdb.get(full)
        if val is None:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        return JSONResponse({"ok": True, "value": val})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


async def _published_app(slug: str) -> bool:
    """True when this slug is a live published app (registry or materialized files)."""
    try:
        row = await pub.get_published(slug)
        if row:
            return True
    except Exception:
        log.debug("published lookup failed for %s", slug, exc_info=True)
    dest = pub.app_dir(slug)
    try:
        return dest.is_dir() and any(dest.iterdir())
    except OSError:
        return False


@router.post("/{slug}/db/{key:path}")
async def app_db_set(slug: str, key: str, request: Request):
    """Set a value (body: {"value": ...}). Max 64KB per value, 200 keys per app."""
    if not await _published_app(slug):
        return JSONResponse({"ok": False, "error": "not a published app"}, status_code=404)
    try:
        _rdb = _store()
        body = await request.json()
        value = body.get("value")
        raw = _json.dumps(value).encode()
        if len(raw) > _DB_MAX_VAL_BYTES:
            return JSONResponse({"ok": False, "error": f"value too large (max {_DB_MAX_VAL_BYTES // 1024}KB)"}, status_code=413)
        full = _db_key(slug, key)
        prefix = _db_ns(slug)
        existing_keys = list(_rdb.prefix(prefix))
        if full not in existing_keys and len(existing_keys) >= _DB_MAX_KEYS:
            return JSONResponse({"ok": False, "error": f"key limit ({_DB_MAX_KEYS}) reached for this app"}, status_code=429)
        _rdb[full] = value
        return JSONResponse({"ok": True})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@router.delete("/{slug}/db/")
async def app_db_clear_all(slug: str):
    """Delete ALL keys for this app (clear). Owner token required by middleware."""
    try:
        _rdb = _store()
        prefix = _db_ns(slug)
        keys = list(_rdb.prefix(prefix))
        for k in keys:
            del _rdb[k]
        return JSONResponse({"ok": True, "deleted": len(keys)})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@router.delete("/{slug}/db/{key:path}")
async def app_db_delete(slug: str, key: str):
    """Delete a key."""
    if not await _published_app(slug):
        return JSONResponse({"ok": False, "error": "not a published app"}, status_code=404)
    try:
        _rdb = _store()
        full = _db_key(slug, key)
        existing = list(_rdb.prefix(_db_ns(slug)))
        if full not in existing:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        del _rdb[full]
        return JSONResponse({"ok": True})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


# ---------------------------------------------------------------------------
# App menu & index
# ---------------------------------------------------------------------------

@router.get("/menu.json")
async def apps_menu():
    apps = await pub.list_published(enabled_only=True)
    return JSONResponse([
        {"slug": a["slug"], "name": a["name"], "icon": a.get("icon", "🎮"),
         "description": a.get("description", ""), "url": f"/apps/{a['slug']}/"}
        for a in apps
    ])


@router.post("/api/{slug}/delete")
async def apps_delete_published(slug: str, request: Request):
    """Admin-only: fully remove a published app from /apps/ (works without playground project)."""
    from .host import current_user
    if current_user(request) is None:
        return JSONResponse({"ok": False, "error": "Sign in to /admin to delete apps."}, status_code=401)
    from . import playground as pg_mod
    try:
        ok = await pub.delete_published(slug)
        proj = await pg_mod.get_project(slug)
        if proj and proj.get("status") == "published":
            await pg_mod.update_project(slug, status="idle")
        return JSONResponse({"ok": ok, "slug": slug})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@router.get("/", response_class=HTMLResponse)
async def apps_index(request: Request):
    import json as _json
    from .host import current_user
    apps = await pub.list_published(enabled_only=True)
    apps_json = _json.dumps(apps, ensure_ascii=False, default=str).replace("<", "\\u003c")
    is_admin = current_user(request) is not None
    is_admin_js = "true" if is_admin else "false"
    html = f"""<!doctype html>
<html lang="bg">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Дедал — публикувани приложения</title>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{--bg:#090d13;--surface:#0f1520;--border:#1a2332;--border-hover:#2dd4bf;
  --text:#e2e8f0;--muted:#64748b;--accent:#38bdf8;--accent2:#2dd4bf}}
body{{background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,sans-serif;min-height:100vh}}

/* ── Header ── */
.header{{padding:24px 32px 0;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px}}
.logo{{display:flex;align-items:center;gap:10px}}
.logo-icon{{font-size:26px}}
.logo-text{{font-size:18px;font-weight:700;letter-spacing:.02em}}
.logo-sub{{font-size:11px;color:var(--muted);margin-top:1px}}
.header-right{{display:flex;align-items:center;gap:8px}}
.count-badge{{font-size:11px;font-family:monospace;padding:3px 9px;border-radius:20px;
  background:#1e293b;color:var(--accent);border:1px solid #334155}}
.admin-link{{font-size:12px;color:var(--muted);text-decoration:none;padding:5px 12px;
  border:1px solid var(--border);border-radius:8px;transition:.15s}}
.admin-link:hover{{color:var(--text);border-color:#334155}}

/* ── Search ── */
.search-wrap{{padding:20px 32px 0}}
.search-box{{display:flex;align-items:center;gap:0;background:var(--surface);
  border:1px solid var(--border);border-radius:12px;overflow:hidden;max-width:460px}}
.search-icon{{padding:0 12px;color:var(--muted);font-size:15px;pointer-events:none}}
.search-input{{flex:1;background:transparent;border:none;outline:none;color:var(--text);
  font-size:14px;padding:10px 12px 10px 0}}
.search-input::placeholder{{color:var(--muted)}}

/* ── Empty / no-results ── */
.empty{{padding:64px 32px;text-align:center;color:var(--muted)}}
.empty-ico{{font-size:48px;margin-bottom:16px}}
.empty p{{font-size:14px;line-height:1.6}}

/* ── Grid ── */
.grid-wrap{{padding:20px 32px 48px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:16px}}

/* ── Card ── */
.card{{background:var(--surface);border:1px solid var(--border);border-radius:16px;
  padding:22px 22px 18px;text-decoration:none;color:inherit;
  display:flex;flex-direction:column;gap:0;
  transition:border-color .2s,transform .2s,box-shadow .2s;position:relative;overflow:hidden}}
.card::before{{content:'';position:absolute;inset:0;background:radial-gradient(circle at 80% 0%,#2dd4bf0a,transparent 60%);
  opacity:0;transition:.2s}}
.card:hover{{border-color:var(--border-hover);transform:translateY(-3px);
  box-shadow:0 8px 32px #2dd4bf18}}
.card:hover::before{{opacity:1}}
.card-icon{{font-size:40px;line-height:1;margin-bottom:12px}}
.card-name{{font-size:15px;font-weight:600;margin-bottom:4px;letter-spacing:.01em}}
.card-desc{{font-size:12px;color:var(--muted);line-height:1.5;flex:1;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}}
.card-foot{{margin-top:14px;display:flex;align-items:center;justify-content:space-between}}
.card-slug{{font-family:monospace;font-size:10px;color:#334155;background:#0f1f30;
  padding:2px 8px;border-radius:20px;border:1px solid #1e3a50}}
.card-open{{font-size:11px;color:var(--accent);opacity:0;transition:.2s;white-space:nowrap}}
.card:hover .card-open{{opacity:1}}
.card-link{{text-decoration:none;color:inherit;display:flex;flex-direction:column;flex:1}}
.card-actions{{display:flex;align-items:center;gap:6px;margin-top:14px}}
.card-del{{font-size:11px;padding:5px 10px;border-radius:8px;border:1px solid #7f1d1d;
  background:#450a0a;color:#fca5a5;cursor:pointer;transition:.15s;white-space:nowrap}}
.card-del:hover{{background:#7f1d1d;color:#fff}}
.admin-hint{{font-size:11px;color:var(--muted);padding:0 32px 8px;display:none}}
.admin-hint.show{{display:block}}

/* ── No-results state ── */
.no-results{{display:none;padding:48px 32px;text-align:center;color:var(--muted);font-size:13px}}

@media(max-width:600px){{
  .header,.search-wrap,.grid-wrap{{padding-left:16px;padding-right:16px}}
  .grid{{grid-template-columns:1fr}}
}}
</style>
</head>
<body>

<div class="header">
  <div class="logo">
    <span class="logo-icon">🗂</span>
    <div>
      <div class="logo-text">Дедал /apps</div>
      <div class="logo-sub">Публикувани приложения от Дедал Playground</div>
    </div>
  </div>
  <div class="header-right">
    <span class="count-badge" id="count-badge">зарежда…</span>
    <a href="/admin/daedalus/playground" class="admin-link">⚙ Playground</a>
  </div>
</div>

<div class="search-wrap">
  <div class="search-box">
    <span class="search-icon">🔍</span>
    <input class="search-input" id="search" placeholder="Търси по название…" oninput="filterApps()" autocomplete="off">
  </div>
</div>
<p id="admin-hint" class="admin-hint">🛡 Админ режим — натисни <b>Изтрий</b> на картата, за да махнеш app от каталога.</p>

<div class="grid-wrap">
  <div id="empty-state" class="empty" style="display:none">
    <div class="empty-ico">🎮</div>
    <p>Все още няма публикувани приложения.<br>
    Отиди в <a href="/admin/daedalus/playground" style="color:var(--accent)">Playground</a>,
    създай проект и натисни <b>Публикувай</b>.</p>
  </div>
  <p class="no-results" id="no-results">Нито едно приложение не съвпада с търсенето.</p>
  <div class="grid" id="grid"></div>
</div>

<script>
const _apps = {apps_json};
const _isAdmin = {is_admin_js};

function render(apps) {{
  const grid = document.getElementById('grid');
  const empty = document.getElementById('empty-state');
  const noRes = document.getElementById('no-results');
  const badge = document.getElementById('count-badge');

  badge.textContent = _apps.length + ' app' + (_apps.length !== 1 ? 's' : '');

  if (!_apps.length) {{
    empty.style.display = 'block';
    grid.style.display = 'none';
    return;
  }}
  if (!apps.length) {{
    noRes.style.display = 'block';
    grid.style.display = 'none';
    return;
  }}
  noRes.style.display = 'none';
  grid.style.display = 'grid';
  grid.innerHTML = apps.map(a => `
    <div class="card" data-slug="${{esc(a.slug)}}">
      <a class="card-link" href="/apps/${{esc(a.slug)}}/">
        <div class="card-icon">${{esc(a.icon || '🎮')}}</div>
        <div class="card-name">${{esc(a.name)}}</div>
        <div class="card-desc">${{esc(a.description || '')}}</div>
        <div class="card-foot">
          <span class="card-slug">${{esc(a.slug)}}</span>
          <span class="card-open">Отвори →</span>
        </div>
      </a>
      ${{_isAdmin ? `<div class="card-actions"><button type="button" class="card-del" data-slug="${{esc(a.slug)}}" data-name="${{esc(a.name || a.slug)}}" onclick="deleteAppFromBtn(this, event)">🗑 Изтрий</button></div>` : ''}}
    </div>`).join('');
}}

function filterApps() {{
  const q = document.getElementById('search').value.trim().toLowerCase();
  if (!q) {{ render(_apps); return; }}
  render(_apps.filter(a =>
    (a.name || '').toLowerCase().includes(q) ||
    (a.description || '').toLowerCase().includes(q) ||
    (a.slug || '').toLowerCase().includes(q)
  ));
}}

function esc(s) {{
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}}

function deleteAppFromBtn(btn, ev) {{
  deleteApp(btn.getAttribute('data-slug'), btn.getAttribute('data-name'), ev);
}}

async function deleteApp(slug, name, ev) {{
  if (ev) {{ ev.preventDefault(); ev.stopPropagation(); }}
  if (!_isAdmin) {{
    alert('Влез в /admin (същия браузър), после опитай отново.');
    return;
  }}
  if (!confirm(`Изтрий „${{name}}” от /apps/?\\n\\nSlug: ${{slug}}\\n\\nПроектът в Playground (ако съществува) остава — само публикуването се маха.`)) return;
  try {{
    const r = await fetch('/apps/api/' + encodeURIComponent(slug) + '/delete', {{method:'POST', credentials:'same-origin'}});
    const d = await r.json();
    if (!r.ok || !d.ok) throw new Error(d.error || ('HTTP ' + r.status));
    const i = _apps.findIndex(x => x.slug === slug);
    if (i >= 0) _apps.splice(i, 1);
    filterApps();
  }} catch (e) {{
    alert('Грешка при изтриване: ' + e.message);
  }}
}}

render(_apps);
if (_isAdmin && _apps.length) {{
  const hint = document.getElementById('admin-hint');
  if (hint) hint.classList.add('show');
}}

// Focus search on Ctrl+K / /
document.addEventListener('keydown', e => {{
  if ((e.ctrlKey && e.key === 'k') || (e.key === '/' && document.activeElement.tagName !== 'INPUT')) {{
    e.preventDefault();
    document.getElementById('search').focus();
  }}
}});
</script>
</body>
</html>"""
    return HTMLResponse(html)


# ---------------------------------------------------------------------------
# App serving (static files + dev-runner proxy)
# ---------------------------------------------------------------------------

@router.get("/{slug}")
async def app_root_noslash(slug: str):
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=f"/apps/{slug}/")


@router.get("/{slug}/")
async def app_entry(slug: str, request: Request):
    return await _serve(slug, "", request)


@router.get("/{slug}/{path:path}")
async def app_path(slug: str, path: str, request: Request):
    return await _serve(slug, path, request)


async def _serve(slug: str, path: str, request: Request) -> Response:
    meta = await pub.get_published(slug)
    if not meta:
        return HTMLResponse(f"<h1>404</h1><p>App '{slug}' not found or disabled.</p>",
                            status_code=404)

    if meta.get("kind") not in ("static",):
        port = runner.get_port(slug)
        if port:
            return await _proxy(
                port, path, request, slug=slug, kind=meta.get("kind", ""),
                proxy_base=f"/apps/{slug}",
            )
        return HTMLResponse(
            f"<h1>{meta['name']}</h1><p>Това е <b>{meta['kind']}</b> app — "
            f"изисква активен runner (само в dev средата). Стартирай го от "
            f"Playground панела.</p>", status_code=503,
        )

    base = pub.app_dir(slug)
    rel = path or meta.get("entry") or "index.html"
    try:
        target = (base / rel).resolve()
        if base.resolve() not in target.parents and target != base.resolve():
            return Response("forbidden", status_code=403)
    except Exception:
        return Response("bad path", status_code=400)

    if target.is_dir():
        target = target / "index.html"
    if not target.exists():
        entry = base / (meta.get("entry") or "index.html")
        if entry.exists():
            target = entry
        else:
            return Response("not found", status_code=404)

    ctype, _ = mimetypes.guess_type(str(target))
    if (ctype or "").startswith("text/html") or target.suffix.lower() in (".html", ".htm"):
        try:
            html = target.read_text(encoding="utf-8", errors="replace")
            return Response(_inject_playground_scripts(html, slug), media_type="text/html")
        except Exception:
            pass
    return FileResponse(str(target), media_type=ctype or "application/octet-stream")

