"""A project description with code characters never breaks the scaffold.

Found live (2026-11-11): Game Studio passes its whole GDD (Markdown with backticks) as the description; pasted into
the vite src/main.js template literal it was a syntax error, every Guided stage failed the build check and all its
files were rejected, while the milestone still read "complete"."""
from __future__ import annotations

import asyncio
import shutil
import subprocess

import pytest
from a0_support import a0_tmp  # noqa: F401

NASTY = ("Comet Dodge. # GDD Lite\\n- **Preset:** `custom` ``vite``\\n${alert(1)} \\\\ \"quotes\" {braces} <b>tag</b>\n"
         + "very long line " * 60)


@pytest.mark.parametrize("kind, rel", [("vite", "src/main.js"), ("streamlit", "app.py")])
def test_scaffold_code_stays_valid(a0_tmp, kind, rel):  # noqa: F811
    from daedalus import playground as pg

    slug = asyncio.run(pg.create_project("Comet `Dodge`", kind=kind, description=NASTY))["slug"]
    path = pg.project_dir(slug) / rel
    if not path.is_file():
        pytest.skip(f"{kind} scaffold has no {rel}")
    text = path.read_text(encoding="utf-8")
    assert len(text) < 2000                                   # a short placeholder line, not the whole GDD
    if rel.endswith(".py"):
        compile(text, rel, "exec")
    else:
        node = shutil.which("node")
        if not node:
            pytest.skip("node not installed")
        mjs = path.with_suffix(".mjs")
        mjs.write_text(text.replace("document.querySelector('#app').innerHTML =", "const x ="), encoding="utf-8")
        res = subprocess.run([node, "--check", str(mjs)], capture_output=True, text=True)
        assert res.returncode == 0, res.stderr
