#!/usr/bin/env bash
# Boot standalone Daedalus the way a laptop/VPS would, and probe product URLs.
# Usage: from Daedalus/  ./scripts/smoke-local.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export DAEDALUS_STANDALONE=1
export HOST=127.0.0.1
export PORT="${PORT:-8787}"
export DAEDALUS_DATA="${DAEDALUS_DATA:-$ROOT/data/daedalus}"
export DAEDALUS_PLAYGROUND="${DAEDALUS_PLAYGROUND:-$ROOT/playground/projects}"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

python3 -m app.cli serve --host "$HOST" --port "$PORT" &
PID=$!
cleanup() { kill "$PID" 2>/dev/null || true; wait "$PID" 2>/dev/null || true; }
trap cleanup EXIT

ok=0
for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  if curl -fsS "http://127.0.0.1:${PORT}/health" >/tmp/daedalus-health.json; then
    ok=1
    break
  fi
  sleep 1
done
if [[ "$ok" != 1 ]]; then
  echo "health did not come up" >&2
  exit 1
fi
python3 - << PY
import json
h=json.load(open("/tmp/daedalus-health.json"))
assert h.get("ok") is True
assert h.get("standalone") is True
assert h.get("sqlite")
assert h.get("chroma")
print("health", h.get("service"), "sqlite", h.get("sqlite"), "chroma", h.get("chroma"), "llm", h.get("llm"))
PY
curl -fsS -o /tmp/daedalus-studio.html "http://127.0.0.1:${PORT}/"
curl -fsS -o /tmp/daedalus-cc.html "http://127.0.0.1:${PORT}/admin/daedalus"
curl -fsS -o /tmp/daedalus-plans.json "http://127.0.0.1:${PORT}/admin/daedalus/planner/plans"
curl -fsS -o /tmp/daedalus-radar.json "http://127.0.0.1:${PORT}/admin/daedalus/radar/sources"
grep -q "Дедал" /tmp/daedalus-studio.html
grep -q "Дедал" /tmp/daedalus-cc.html
grep -q '"plans"' /tmp/daedalus-plans.json
grep -q '"sources"' /tmp/daedalus-radar.json
curl -fsS -o /tmp/daedalus-apps.json "http://127.0.0.1:${PORT}/apps/menu.json"
curl -fsS -o /tmp/daedalus-mode.json "http://127.0.0.1:${PORT}/admin/daedalus/mode"
python3 - << PY
import json
apps=json.load(open("/tmp/daedalus-apps.json"))
assert isinstance(apps, list)
mode=json.load(open("/tmp/daedalus-mode.json"))
assert mode.get("mode") in ("auto", "build", "chat", "plan") or mode.get("ok") is not False
print("apps", len(apps), "mode", mode)
PY
echo "smoke-local ok"
