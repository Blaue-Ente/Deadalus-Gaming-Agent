"""Unity project scaffold — C# URP structure for Daedalus codegen (no Unity Editor on host)."""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

MANIFEST_JSON = """{
  "dependencies": {
    "com.unity.collab-proxy": "2.4.4",
    "com.unity.feature.mobile": "1.0.0",
    "com.unity.ide.rider": "3.0.31",
    "com.unity.ide.visualstudio": "2.0.22",
    "com.unity.render-pipelines.universal": "14.0.11",
    "com.unity.textmeshpro": "3.0.6",
    "com.unity.timeline": "1.7.6",
    "com.unity.ugui": "1.0.0",
    "com.unity.modules.ai": "1.0.0",
    "com.unity.modules.androidjni": "1.0.0",
    "com.unity.modules.animation": "1.0.0",
    "com.unity.modules.assetbundle": "1.0.0",
    "com.unity.modules.audio": "1.0.0",
    "com.unity.modules.jsonserialize": "1.0.0",
    "com.unity.modules.physics": "1.0.0",
    "com.unity.modules.ui": "1.0.0",
    "com.unity.modules.uielements": "1.0.0",
    "com.unity.modules.unitywebrequest": "1.0.0"
  }
}
"""

PROJECT_VERSION = """m_EditorVersion: 2022.3.48f1
"""

GAME_ENTRY_CS = """using UnityEngine;

namespace Daedalus.Game.Core
{{
    /// <summary>Boot entry — attach to a GameObject in Boot scene.</summary>
    public class GameEntry : MonoBehaviour
    {{
        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.BeforeSceneLoad)]
        private static void Bootstrap()
        {{
            DontDestroyOnLoad(new GameObject("DaedalusGameRoot").AddComponent<GameEntry>().gameObject);
        }}

        private void Awake()
        {{
            Debug.Log("Daedalus Unity — {name}");
            // TODO: wire ServiceRegistry, SceneLoader, SaveService
        }}
    }}
}}
"""

SAVE_SERVICE_CS = """using System;
using UnityEngine;

namespace Daedalus.Game.Core.Save
{
    [Serializable]
    public class SaveData
    {
        public int Version = 1;
        public string PlayerId = "local";
    }

    public static class SaveService
    {
        private const string Key = "daedalus_save_v1";

        public static SaveData Load()
        {
            var json = PlayerPrefs.GetString(Key, "{}");
            try { return JsonUtility.FromJson<SaveData>(json) ?? new SaveData(); }
            catch { return new SaveData(); }
        }

        public static void Save(SaveData data)
        {
            PlayerPrefs.SetString(Key, JsonUtility.ToJson(data));
            PlayerPrefs.Save();
        }
    }
}
"""

ENTITY_DEFINITION_CS = """using UnityEngine;

namespace Daedalus.Game.Data
{
    /// <summary>Static definition — any game entity (unit, item, card, vehicle, etc.)</summary>
    [CreateAssetMenu(menuName = "Daedalus/Entity Definition")]
    public class EntityDefinition : ScriptableObject
    {
        public string Id;
        public string DisplayName;
        public string Category;
        public string Rarity;
    }
}
"""

REGISTRY_ENTITIES = """{
  "version": 1,
  "entities": {}
}
"""

REGISTRY_LEVELS = """{
  "version": 1,
  "levels": {}
}
"""

README_UNITY = """# {name} — Unity (Daedalus)

## Open in Unity Hub
1. Install **Unity 2022.3 LTS** + Android/iOS modules
2. Unity Hub → Open → select this folder
3. First open: let URP packages resolve

## Structure
- `Assets/_Project/Code/` — gameplay C# (Daedalus generates here)
- `Assets/_Project/Data/Registry/` — JSON data layer
- `studio/` — PROJECT_RULES, ARCHITECTURE, integrations

## External templates
Use Playground → **Unity → Attach template** for Unity Game Framework.

## Build
- Android: File → Build Settings → Android
- iOS: requires macOS + Xcode

## Preview in Daedalus
Unity does not run in browser sandbox — use **studio/preview/index.html** status page.
"""

PREVIEW_HTML = """<!DOCTYPE html>
<html lang="bg">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{name} — Unity Project</title>
  <style>
    body {{ font-family: system-ui; background: #1a1a2e; color: #e2e8f0; padding: 2rem; max-width: 720px; margin: 0 auto; }}
    h1 {{ color: #fbbf24; }}
    code {{ background: #0f172a; padding: 2px 8px; border-radius: 4px; }}
    .card {{ background: #16213e; border: 1px solid #334155; border-radius: 8px; padding: 1rem; margin: 1rem 0; }}
  </style>
</head>
<body>
  <h1>🎮 {name}</h1>
  <p>Unity C# проект — отвори в <strong>Unity Hub</strong> на локална машина.</p>
  <div class="card">
    <p><strong>Стъпки:</strong></p>
    <ol>
      <li>Clone/download project folder</li>
      <li>Unity Hub → Open → тази папка</li>
      <li>Scenes: <code>Assets/_Project/Scenes/</code></li>
    </ol>
  </div>
  <p class="card">Daedalus генерира C# + JSON. Build-ът е локален (Android/iOS/PC).</p>
</body>
</html>
"""


def unity_scaffold_files(name: str, slug: str, description: str) -> dict[str, str]:
    ctx = {"name": name, "slug": slug, "description": description or name}
    return {
        "Packages/manifest.json": MANIFEST_JSON,
        "ProjectSettings/ProjectVersion.txt": PROJECT_VERSION,
        "Assets/_Project/Code/Core/Bootstrap/GameEntry.cs": GAME_ENTRY_CS.format(**ctx),
        "Assets/_Project/Code/Core/Save/SaveService.cs": SAVE_SERVICE_CS,
        "Assets/_Project/Code/Data/EntityDefinition.cs": ENTITY_DEFINITION_CS,
        "Assets/_Project/Data/Registry/entities.json": REGISTRY_ENTITIES,
        "Assets/_Project/Data/Registry/levels.json": REGISTRY_LEVELS,
        "Assets/_Project/Data/Registry/README.md": (
            "# Registry\n\nGeneric entities/levels for any game genre.\n"
        ),
        "README.md": README_UNITY.format(**ctx),
        "studio/preview/index.html": PREVIEW_HTML.format(**ctx),
        ".gitignore": (
            "[Ll]ibrary/\n[Tt]emp/\n[Oo]bj/\n[Bb]uild/\n[Bb]uilds/\n"
            "[Ll]ogs/\n[Uu]ser[Ss]ettings/\n*.csproj\n*.sln\n.vs/\n"
        ),
    }


def write_unity_scaffold(pdir: Path, name: str, slug: str, description: str) -> list[str]:
    written: list[str] = []
    for rel, content in unity_scaffold_files(name, slug, description).items():
        target = pdir / rel
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(rel)
    return written
