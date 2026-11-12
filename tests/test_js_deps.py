"""Imports that package.json does not declare are reported to the writer right after its write.

Found live (2026-11-11, Expo Habit Sprout): package.json lost @tanstack/react-query while _layout.tsx still imported
it; also the Expo scaffold's babel config used expo-router/babel, which SDK 50+ refuses, so no Expo project bundled."""
from __future__ import annotations

import json

from a0_support import a0_tmp  # noqa: F401

from daedalus.js_deps import undeclared, undeclared_note


def _proj(tmp_path, deps, files):
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": deps, "devDependencies": {"vite": "^5"}}))
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return tmp_path


def test_missing_packages_are_named_and_local_imports_are_not(tmp_path):
    root = _proj(tmp_path, {"react": "18", "expo": "~51"}, {
        "src/app/_layout.tsx": "import { QueryClient } from '@tanstack/react-query';\nimport { create } from 'zustand/react';\n"
                               "import x from './local';\nimport y from '@/lib/store';\nimport fs from 'fs';\n"
                               "import React from 'react';\nconst z = require('lodash');\nimport('three');\n",
        "node_modules/pkg/index.js": "import nope from 'not-scanned';",
    })
    assert undeclared(root) == ["@tanstack/react-query", "lodash", "three", "zustand"]
    note = undeclared_note(root)
    assert "MISSING DEPENDENCIES" in note and "@tanstack/react-query" in note


def test_a_complete_project_has_no_note(tmp_path):
    root = _proj(tmp_path, {"three": "^0.160"}, {"src/main.js": "import * as THREE from 'three';\n"})
    assert undeclared(root) == [] and undeclared_note(root) == ""
    assert undeclared(tmp_path / "nowhere") == []


def test_the_expo_scaffold_bundles_with_sdk_50_plus(a0_tmp):  # noqa: F811
    import asyncio

    from daedalus import playground as pg

    slug = asyncio.run(pg.create_project("Habit", kind="expo"))["slug"]
    babel = (pg.project_dir(slug) / "babel.config.js").read_text()
    assert "expo-router/babel'" not in babel.replace("// No 'expo-router/babel'", "")
    assert undeclared(pg.project_dir(slug)) == []           # the scaffold declares what it imports



def test_builtins_and_path_aliases_are_not_missing_packages(tmp_path):
    """Review 2026-11-11: perf_hooks / vm and tsconfig aliases were reported as npm packages to add."""
    root = _proj(tmp_path, {"react": "18"}, {
        "tsconfig.json": '{ // comment\n "compilerOptions": {"baseUrl": ".", "paths": {"@components/*": ["src/c/*"]}}}',
        "src/c/Button.tsx": "export const B = 1;",
        "src/app.tsx": "import { performance } from 'perf_hooks';\nconst vm = require('vm');\n"
                       "import B from '@components/Button';\nimport u from 'src/utils';\nimport left from 'left-pad';\n",
    })
    assert undeclared(root) == ["left-pad"]
