"""Daedalus coding tools — four specialised capabilities.

1. pypi_search        — search PyPI for packages
2. npm_search         — search npm for packages
3. docs_fetch         — fetch and strip a documentation page to clean text
4. dependency_analyze — check latest version, changelog, CVEs for a package
5. git_log            — recent commits in the ADN repo
6. git_diff           — diff of a file or the working tree
7. code_sandbox       — run a Python snippet safely and return stdout/stderr

All tools are synchronous (run in executor when called from async agent).
No shell injection: all subprocess calls use list-form args, no shell=True.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import textwrap
import traceback
from typing import Any

import httpx

log = logging.getLogger(__name__)

_PYPI_API = "https://pypi.org/pypi"
_NPM_API = "https://registry.npmjs.org"
_OSSVULNDB = "https://api.osv.dev/v1/query"

_HTTP_TIMEOUT = 12


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _get(url: str, params: dict | None = None, timeout: int = _HTTP_TIMEOUT) -> dict | None:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as c:
            r = c.get(url, params=params or {})
            r.raise_for_status()
            ct = r.headers.get("content-type", "")
            if "json" in ct:
                return r.json()
            return {"_text": r.text}
    except Exception as exc:
        log.debug("_get %s failed: %s", url, exc)
        return None


def _strip_html(html: str, max_chars: int = 6000) -> str:
    """Very light HTML → plain-text (no BeautifulSoup dep required here)."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator="\n")
    except Exception:
        text = re.sub(r"<[^>]+>", " ", html)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines)[:max_chars]


# ---------------------------------------------------------------------------
# 1. PyPI search
# ---------------------------------------------------------------------------

def pypi_search(query: str, limit: int = 6) -> dict[str, Any]:
    """Search PyPI for packages matching *query*.

    Strategy:
    1. Search PyPI Simple index (full package list, JSON) for name matches
    2. For each match fetch individual /pypi/<pkg>/json for version+summary
    3. Fallback: try direct exact-name lookup if no matches
    """
    if not query or not query.strip():
        return {"ok": False, "error": "empty query"}

    query_lower = query.lower()
    results: list[dict] = []

    # Step 1: get candidate names from the Simple index (cached ~1MB JSON)
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as c:
            r = c.get(
                "https://pypi.org/simple/",
                headers={"Accept": "application/vnd.pypi.simple.v1+json"},
            )
            r.raise_for_status()
            all_projects = r.json().get("projects", [])

        # Score: exact prefix > contains > word boundary
        query_words = query_lower.split()
        candidates: list[tuple[int, str]] = []
        for p in all_projects:
            name = p.get("name", "")
            nl = name.lower()
            if nl == query_lower:
                candidates.append((0, name))
            elif nl.startswith(query_lower):
                candidates.append((1, name))
            elif query_lower in nl:
                candidates.append((2, name))
            elif all(w in nl for w in query_words):
                candidates.append((3, name))

        candidates.sort(key=lambda x: x[0])
        top_names = [n for _, n in candidates[:limit * 3]]  # fetch more, filter after

        # Step 2: fetch individual package JSON for version + summary
        with httpx.Client(timeout=10, follow_redirects=True) as c:
            for name in top_names:
                if len(results) >= limit:
                    break
                try:
                    pdata = c.get(f"{_PYPI_API}/{name}/json")
                    if pdata.status_code == 200:
                        info = pdata.json().get("info", {})
                        results.append({
                            "name": info.get("name", name),
                            "version": info.get("version", ""),
                            "summary": (info.get("summary") or "")[:200],
                            "url": info.get("project_url") or f"https://pypi.org/project/{name}/",
                            "install": f"pip install {info.get('name', name)}",
                        })
                except Exception:
                    pass

    except Exception as exc:
        log.debug("pypi_search simple index failed: %s", exc)

    # Fallback: try query as exact package name
    if not results:
        # Try treating the first word as an exact package name
        candidate = query.split()[0]
        data = _get(f"{_PYPI_API}/{candidate}/json")
        if data and "info" in data:
            info = data["info"]
            results.append({
                "name": info["name"],
                "version": info["version"],
                "summary": (info.get("summary") or "")[:200],
                "url": info.get("project_url") or f"https://pypi.org/project/{info['name']}/",
                "install": f"pip install {info['name']}",
            })

    return {"ok": True, "query": query, "count": len(results), "results": results}


# ---------------------------------------------------------------------------
# 2. npm search
# ---------------------------------------------------------------------------

def npm_search(query: str, limit: int = 6) -> dict[str, Any]:
    """Search npm registry for JavaScript / TypeScript packages."""
    if not query or not query.strip():
        return {"ok": False, "error": "empty query"}

    data = _get(
        "https://registry.npmjs.org/-/v1/search",
        params={"text": query, "size": str(limit)},
    )
    if not data or "objects" not in data:
        return {"ok": False, "error": "npm search unavailable", "results": []}

    results = []
    for obj in data["objects"]:
        pkg = obj.get("package", {})
        results.append({
            "name": pkg.get("name", ""),
            "version": pkg.get("version", ""),
            "description": pkg.get("description", ""),
            "url": pkg.get("links", {}).get("npm", f"https://www.npmjs.com/package/{pkg.get('name','')}"),
            "install": f"npm install {pkg.get('name','')}",
        })

    return {"ok": True, "query": query, "count": len(results), "results": results}


# ---------------------------------------------------------------------------
# 3. Docs fetch
# ---------------------------------------------------------------------------

def docs_fetch(url: str, max_chars: int = 5000) -> dict[str, Any]:
    """Fetch a documentation URL and return clean plain text (no HTML).

    Useful for: ReadTheDocs pages, GitHub READMEs (raw), FastAPI docs, etc.
    SSRF guard: only http/https, no internal IPs.
    """
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "Only http/https URLs are allowed"}

    # SSRF: block private IP ranges
    import ipaddress, socket
    try:
        host = url.split("/")[2].split(":")[0]
        ip = socket.gethostbyname(host)
        addr = ipaddress.ip_address(ip)
        if addr.is_private or addr.is_loopback or addr.is_reserved:
            return {"ok": False, "error": "SSRF: private/loopback address blocked"}
    except Exception:
        pass

    try:
        with httpx.Client(timeout=15, follow_redirects=True) as c:
            r = c.get(url, headers={"User-Agent": "ArgosDataNexus/1.0 docs-fetch"})
            r.raise_for_status()
            content_type = r.headers.get("content-type", "")
            if "html" in content_type:
                text = _strip_html(r.text, max_chars=max_chars)
            else:
                text = r.text[:max_chars]
        return {"ok": True, "url": url, "chars": len(text), "content": text}
    except Exception as exc:
        return {"ok": False, "url": url, "error": str(exc)}


# ---------------------------------------------------------------------------
# 4. Dependency analyzer
# ---------------------------------------------------------------------------

def dependency_analyze(package: str, ecosystem: str = "auto") -> dict[str, Any]:
    """Check latest version, release date, changelog snippet, and CVEs.

    ecosystem: "pypi" | "npm" | "auto" (auto-detects from package name style)
    Checks: OSV vulnerability database for CVEs.
    """
    package = package.strip()
    if not package:
        return {"ok": False, "error": "empty package name"}

    # Auto-detect ecosystem
    if ecosystem == "auto":
        if "/" in package or package.startswith("@"):
            ecosystem = "npm"
        else:
            ecosystem = "pypi"

    result: dict[str, Any] = {
        "ok": True,
        "package": package,
        "ecosystem": ecosystem,
        "latest_version": None,
        "released": None,
        "summary": "",
        "home_url": "",
        "changelog_url": "",
        "vulnerabilities": [],
        "vuln_count": 0,
    }

    # --- Version + metadata ---
    if ecosystem == "pypi":
        data = _get(f"{_PYPI_API}/{package}/json")
        if data and "info" in data:
            info = data["info"]
            result["latest_version"] = info.get("version")
            result["summary"] = (info.get("summary") or "")[:300]
            result["home_url"] = info.get("project_url") or f"https://pypi.org/project/{package}/"
            result["changelog_url"] = info.get("project_urls", {}).get("Changelog", "")
            # Release date of latest version
            releases = data.get("releases", {})
            latest = info.get("version")
            if latest and latest in releases and releases[latest]:
                result["released"] = releases[latest][-1].get("upload_time", "")[:10]
        else:
            result["ok"] = False
            result["error"] = f"Package '{package}' not found on PyPI"
            return result

    elif ecosystem == "npm":
        data = _get(f"{_NPM_API}/{package}/latest")
        if data and "name" in data:
            result["latest_version"] = data.get("version")
            result["summary"] = (data.get("description") or "")[:300]
            result["home_url"] = (data.get("homepage") or f"https://www.npmjs.com/package/{package}")
            result["changelog_url"] = ""
        else:
            result["ok"] = False
            result["error"] = f"Package '{package}' not found on npm"
            return result

    # --- CVE / vulnerability check via OSV.dev ---
    ecosystem_osv = "PyPI" if ecosystem == "pypi" else "npm"
    try:
        with httpx.Client(timeout=10) as c:
            resp = c.post(
                _OSSVULNDB,
                json={"package": {"name": package, "ecosystem": ecosystem_osv}},
            )
            if resp.status_code == 200:
                vulns = resp.json().get("vulns", [])
                result["vuln_count"] = len(vulns)
                result["vulnerabilities"] = [
                    {
                        "id": v.get("id"),
                        "summary": (v.get("summary") or "")[:200],
                        "severity": _osv_severity(v),
                        "fixed_in": _osv_fixed(v),
                    }
                    for v in vulns[:5]
                ]
    except Exception as exc:
        log.debug("OSV query failed for %s: %s", package, exc)

    return result


def _osv_severity(vuln: dict) -> str:
    for sev in vuln.get("severity", []):
        if sev.get("type") == "CVSS_V3":
            score = sev.get("score", "")
            if score:
                try:
                    s = float(score.split("/")[0]) if "/" in str(score) else float(score)
                    if s >= 9.0: return f"CRITICAL ({score})"
                    if s >= 7.0: return f"HIGH ({score})"
                    if s >= 4.0: return f"MEDIUM ({score})"
                    return f"LOW ({score})"
                except Exception:
                    pass
    return "UNKNOWN"


def _osv_fixed(vuln: dict) -> str:
    for affected in vuln.get("affected", []):
        for r in affected.get("ranges", []):
            for event in r.get("events", []):
                if "fixed" in event:
                    return str(event["fixed"])
    return ""


# ---------------------------------------------------------------------------
# 5. Git log
# ---------------------------------------------------------------------------

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)
))))


def git_log(n: int = 15, path: str | None = None, author: str | None = None) -> dict[str, Any]:
    """Return the last N git commits in the ADN repo.

    Optional: filter by file path or author.
    """
    cmd = [
        "git", "--no-optional-locks", "log",
        f"--max-count={min(n, 50)}",
        "--pretty=format:%H|%an|%ae|%ai|%s",
    ]
    if author:
        cmd += [f"--author={author}"]
    if path:
        cmd += ["--", path]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=_REPO_ROOT, timeout=10,
        )
        lines = [l for l in proc.stdout.strip().splitlines() if l]
        commits = []
        for line in lines:
            parts = line.split("|", 4)
            if len(parts) == 5:
                commits.append({
                    "hash": parts[0][:8],
                    "author": parts[1],
                    "email": parts[2],
                    "date": parts[3][:19],
                    "subject": parts[4][:120],
                })
        return {"ok": True, "count": len(commits), "commits": commits}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "commits": []}


# ---------------------------------------------------------------------------
# 6. Git diff
# ---------------------------------------------------------------------------

def git_diff(path: str | None = None, ref: str = "HEAD", staged: bool = False) -> dict[str, Any]:
    """Return git diff output.

    path   — specific file (optional, default = all tracked files)
    ref    — diff against this ref (default HEAD)
    staged — diff staged/index changes (--cached)
    """
    cmd = ["git", "--no-optional-locks", "diff"]
    if staged:
        cmd.append("--cached")
    else:
        cmd.append(ref)
    if path:
        cmd += ["--", path]

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=_REPO_ROOT, timeout=10,
        )
        diff_text = proc.stdout or "(no diff)"
        # Trim very long diffs
        if len(diff_text) > 8000:
            diff_text = diff_text[:8000] + "\n... [truncated]"
        return {"ok": True, "path": path or "all", "ref": ref, "diff": diff_text}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "diff": ""}


# ---------------------------------------------------------------------------
# 7. Code sandbox
# ---------------------------------------------------------------------------

_SANDBOX_TIMEOUT_S = 10
_SANDBOX_MAX_OUTPUT = 3000

_SANDBOX_BLOCKED = [
    "import os", "import sys", "import subprocess", "import socket",
    "import importlib", "__import__", "open(", "exec(", "eval(",
    "compile(", "os.system", "os.popen", "shutil", "pathlib",
    "requests", "httpx", "urllib", "ftplib", "smtplib",
]


def code_sandbox(code: str, timeout: int = _SANDBOX_TIMEOUT_S) -> dict[str, Any]:
    """Execute a Python snippet in a restricted subprocess and return stdout/stderr.

    The sandbox:
    - Runs in a fresh subprocess (no shared state)
    - Blocks dangerous imports statically before execution
    - Hard timeout (SIGKILL after N seconds)
    - Output capped at 3000 chars
    - No network, no file I/O, no shell

    Use for: verifying algorithms, testing logic, confirming library APIs,
    checking type behaviour, validating regex patterns, quick maths.
    Do NOT use for: fetching URLs, reading files, calling external APIs.
    """
    code = code.strip()
    if not code:
        return {"ok": False, "error": "empty code"}

    # Static safety check
    blocked_found = [b for b in _SANDBOX_BLOCKED if b in code]
    if blocked_found:
        return {
            "ok": False,
            "error": f"Sandbox blocks these patterns: {blocked_found}",
            "stdout": "",
            "stderr": "",
        }

    # Wrap in a script that captures stdout/stderr and prints them to real stdout/stderr
    # Key: restore sys.stdout BEFORE printing captured output, otherwise the print
    # itself goes into the buffer and capture_output never sees it.
    escaped_code = code.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    script = textwrap.dedent(f"""
import sys, io, traceback, resource

try:
    resource.setrlimit(resource.RLIMIT_AS, (128 * 1024 * 1024, 128 * 1024 * 1024))
except Exception:
    pass

_real_stdout = sys.__stdout__
_real_stderr = sys.__stderr__
_buf_out = io.StringIO()
_buf_err = io.StringIO()
sys.stdout = _buf_out
sys.stderr = _buf_err

try:
    exec(compile({repr(code)}, '<sandbox>', 'exec'), {{}})
except SystemExit:
    pass
except Exception:
    traceback.print_exc(file=_buf_err)

sys.stdout = _real_stdout
sys.stderr = _real_stderr
_real_stdout.write(_buf_out.getvalue())
_real_stdout.flush()
_real_stderr.write(_buf_err.getvalue())
_real_stderr.flush()
""")

    try:
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True,
            timeout=timeout,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        )
        stdout = (proc.stdout or "")[:_SANDBOX_MAX_OUTPUT]
        stderr = (proc.stderr or "")[:_SANDBOX_MAX_OUTPUT]

        return {
            "ok": True,
            "returncode": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": False,
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": f"Sandbox timed out after {timeout}s",
            "stdout": "",
            "stderr": "",
            "timed_out": True,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "stdout": "", "stderr": ""}
