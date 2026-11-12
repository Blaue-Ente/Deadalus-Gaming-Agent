"""List OpenRouter model ids named in Daedalus's code that OpenRouter no longer lists.

Run by hand (it needs the network): python scripts/check_model_ids.py
Found 2026-11-12: catalogs and defaults had drifted two model generations behind. OpenRouter keeps some old ids as
hidden aliases (anthropic/claude-sonnet-4-5 still answers), so a hit here means "not listed", not always "broken":
check each one with a 5-token call before removing it. Ids for other providers (Novita, AllToken, NVIDIA) are only
checked when their line also says "openrouter".
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_ID = re.compile(r"""["'](?:openrouter:)?((?:anthropic|openai|google|deepseek|qwen|meta-llama|mistralai|x-ai|z-ai|"""
                 r"""moonshotai|nvidia|minimax|cohere)/[A-Za-z0-9._:\-]+)["']""")


def listed_ids() -> set[str]:
    with urllib.request.urlopen("https://openrouter.ai/api/v1/models", timeout=30) as r:
        ids = {m["id"] for m in json.load(r)["data"]}
    return ids | {i.split(":")[0] for i in ids}


def unlisted(ids: set[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for path in sorted((ROOT / "daedalus").rglob("*.py")):
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            other_provider = any(p in line for p in ('"novita"', '"alltoken"', '"nvidia"')) and "openrouter" not in line
            if other_provider:
                continue
            for model in _ID.findall(line):
                if model not in ids and model.split(":")[0] not in ids:
                    out.setdefault(model, []).append(f"{path.relative_to(ROOT)}:{n}")
    return out


def main() -> int:
    missing = unlisted(listed_ids())
    for model, where in sorted(missing.items(), key=lambda kv: -len(kv[1])):
        print(f"{model:55s} {len(where):3d}  {where[0]}")
    print(f"{len(missing)} model ids not listed by OpenRouter")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
