"""Unity local batch build — shell script + Editor C# + docs (runs on developer Mac/PC only)."""
from __future__ import annotations

import json
from typing import Any

from . import playground as pg

BUILD_SCRIPT_PATH = "studio/unity/build-local.sh"
BUILD_DOC_PATH = "studio/unity/BUILD_LOCAL.md"
EDITOR_BUILD_CS = "Assets/_Project/Editor/DaedalusBuild.cs"
BUILD_CONFIG_PATH = "studio/unity/build.config.json"


def _build_config(slug: str, name: str, bundle_id: str) -> dict[str, Any]:
    return {
        "project_name": name,
        "slug": slug,
        "unity_version": "2022.3.48f1",
        "bundle_id_ios": bundle_id,
        "bundle_id_android": bundle_id,
        "scenes": ["Assets/_Project/Scenes/Boot.unity"],
        "targets": {
            "StandaloneOSX": {"enabled": True, "output": "Builds/Mac"},
            "StandaloneWindows64": {"enabled": True, "output": "Builds/Win"},
            "Android": {"enabled": True, "output": "Builds/Android"},
            "iOS": {"enabled": True, "output": "Builds/iOS"},
        },
        "notes": "Run build-local.sh on machine with Unity Hub + Build Support modules installed.",
    }


def _build_local_md(name: str, slug: str, bundle_id: str) -> str:
    return f"""# Unity Local Batch Build

**{name}** (`{slug}`) — Daedalus-generated build scripts. Unity Editor does **not** run in ADN cloud sandbox.

## Prerequisites
1. [Unity Hub](https://unity.com/download) — **2022.3 LTS** (see `ProjectSettings/ProjectVersion.txt`)
2. Modules: **iOS Build Support**, **Android Build Support** (as needed)
3. macOS for iOS Archive / Xcode

## Quick build (CLI)
```bash
# From project root (where Assets/ lives)
chmod +x studio/unity/build-local.sh
./studio/unity/build-local.sh StandaloneOSX
./studio/unity/build-local.sh Android
./studio/unity/build-local.sh iOS        # macOS only → Xcode project
```

## Editor menu
Unity → **Daedalus** → **Build** → pick target (uses `DaedalusBuild.cs`)

## iOS pipeline
1. `./studio/unity/build-local.sh iOS`
2. Open `Builds/iOS/Unity-iPhone.xcodeproj` in Xcode
3. Signing & Capabilities → Team, Bundle ID `{bundle_id}`
4. Product → Archive → TestFlight or export IPA
5. Optional: **ios-app-signer** / **LiveContainer** for dev sideload

## Android
1. `./studio/unity/build-local.sh Android`
2. Output: `Builds/Android/*.apk` or `.aab` per script args

## Config
Edit `{BUILD_CONFIG_PATH}` for scenes, bundle IDs, output paths.

## Firebase
After iOS build, ensure `GoogleService-Info.plist` is in Xcode project (see `studio/integrations/firebase.md`).

## Troubleshooting
- `Unity not found` → set `UNITY_PATH` env var in `build-local.sh`
- Batchmode logs: `Logs/daedalus-build.log`
"""


def _build_shell_script() -> str:
    return r"""#!/usr/bin/env bash
# Daedalus Unity batch build — LOCAL ONLY (developer machine)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

UNITY="${UNITY_PATH:-}"
if [[ -z "$UNITY" ]]; then
  for cand in \
    "/Applications/Unity/Hub/Editor/"*"/Unity.app/Contents/MacOS/Unity" \
    "$HOME/Unity/Hub/Editor/"*"/Editor/Unity" \
    "/opt/unity/Editor/Unity"; do
  if [[ -x "$cand" ]]; then UNITY="$cand"; break; fi
  done
fi
if [[ -z "$UNITY" || ! -x "$UNITY" ]]; then
  echo "Set UNITY_PATH to your Unity executable"
  exit 1
fi

TARGET="${1:-StandaloneOSX}"
LOG="$ROOT/Logs/daedalus-build.log"
mkdir -p "$ROOT/Logs" "$ROOT/Builds"

echo "Unity: $UNITY"
echo "Target: $TARGET"
echo "Project: $ROOT"

"$UNITY" -batchmode -nographics -quit \
  -projectPath "$ROOT" \
  -executeMethod Daedalus.Editor.DaedalusBuild.BuildFromCLI \
  -buildTarget "$TARGET" \
  -logFile "$LOG" \
  "$@"

echo "Done. Log: $LOG"
"""


def _editor_build_cs(name: str) -> str:
    safe_name = name.replace(" ", "_")
    return f"""#if UNITY_EDITOR
using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;

namespace Daedalus.Editor
{{
    public static class DaedalusBuild
    {{
        private const string ConfigPath = "studio/unity/build.config.json";

        [MenuItem("Daedalus/Build/Standalone macOS")]
        public static void BuildMac() => Build(BuildTarget.StandaloneOSX);

        [MenuItem("Daedalus/Build/Standalone Windows")]
        public static void BuildWin() => Build(BuildTarget.StandaloneWindows64);

        [MenuItem("Daedalus/Build/Android")]
        public static void BuildAndroid() => Build(BuildTarget.Android);

        [MenuItem("Daedalus/Build/iOS")]
        public static void BuildIOS() => Build(BuildTarget.iOS);

        public static void BuildFromCLI()
        {{
            var args = Environment.GetCommandLineArgs();
            var idx = Array.IndexOf(args, "-buildTarget");
            var target = idx >= 0 && idx + 1 < args.Length
                ? args[idx + 1]
                : "StandaloneOSX";
            if (!Enum.TryParse<BuildTarget>(target, out var bt))
                bt = BuildTarget.StandaloneOSX;
            Build(bt);
        }}

        private static void Build(BuildTarget target)
        {{
            var scenes = EditorBuildSettings.scenes
                .Where(s => s.enabled)
                .Select(s => s.path)
                .ToArray();
            if (scenes.Length == 0)
                scenes = new[] {{ "Assets/_Project/Scenes/Boot.unity" }};

            var outDir = Path.Combine("Builds", target.ToString());
            Directory.CreateDirectory(outDir);
            var ext = target == BuildTarget.Android ? ".apk"
                : target == BuildTarget.iOS ? "" : "";
            var loc = target == BuildTarget.iOS
                ? outDir
                : Path.Combine(outDir, "{safe_name}" + ext);

            var opts = new BuildPlayerOptions
            {{
                scenes = scenes,
                locationPathName = loc,
                target = target,
                options = BuildOptions.None,
            }};

            Debug.Log($"Daedalus build → {{target}} @ {{loc}}");
            var report = BuildPipeline.BuildPlayer(opts);
            if (report.summary.result != BuildResult.Succeeded)
                throw new Exception("Build failed: " + report.summary.result);
        }}
    }}
}}
#endif
"""


def unity_build_files(slug: str, name: str, *, bundle_id: str = "") -> dict[str, str]:
    bundle = bundle_id or f"com.daedalus.{slug}"
    cfg = _build_config(slug, name, bundle)
    return {
        BUILD_CONFIG_PATH: json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
        BUILD_DOC_PATH: _build_local_md(name, slug, bundle),
        BUILD_SCRIPT_PATH: _build_shell_script(),
        EDITOR_BUILD_CS: _editor_build_cs(name),
    }


async def generate_unity_build_scripts(
    slug: str,
    *,
    bundle_id: str = "",
    name: str = "",
) -> dict[str, Any]:
    proj = await pg.get_project(slug)
    if not proj:
        raise ValueError("project not found")
    pname = name or proj.get("name", slug)
    bundle = bundle_id or f"com.daedalus.{slug}"
    files = unity_build_files(slug, pname, bundle_id=bundle)
    written: list[str] = []
    for path, content in files.items():
        pg.write_file(slug, path, content)
        written.append(path)
    script = pg.project_dir(slug) / BUILD_SCRIPT_PATH
    if script.exists():
        script.chmod(0o755)
    return {
        "ok": True,
        "written": written,
        "bundle_id": bundle,
        "local_only": True,
        "usage": f"./{BUILD_SCRIPT_PATH} StandaloneOSX",
    }
