#!/usr/bin/env bash
set -euo pipefail

DEMO_DIR="$(cd "$(dirname "$0")" && pwd)"
DEMO_PORT="${PORT:-8790}"
DEMO_URL="http://127.0.0.1:${DEMO_PORT}"

if [[ "${1:-}" == "test" ]]; then
  PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s "$DEMO_DIR/tests" -p 'test_*.py'
  if [[ -x "/usr/local/opt/node@22/bin/node" ]]; then
    NODE_EXE="/usr/local/opt/node@22/bin/node"
  else
    NODE_EXE="$(command -v node)"
  fi
  "$NODE_EXE" --check "$DEMO_DIR/guide-core.js"
  "$NODE_EXE" --check "$DEMO_DIR/site.js"
  "$NODE_EXE" --check "$DEMO_DIR/app.js"
  "$NODE_EXE" --check "$DEMO_DIR/replica-shell.js"
  "$NODE_EXE" --check "$DEMO_DIR/embed-frame.js"
  "$NODE_EXE" --check "$DEMO_DIR/evaluation.js"
  "$NODE_EXE" --check "$DEMO_DIR/wix-app/site/fortune-guide-element.js"
  "$NODE_EXE" --input-type=module --check < "$DEMO_DIR/wix-app/site/member-access.js"
  "$NODE_EXE" --check "$DEMO_DIR/wix-app/dashboard/provider-settings.js"
  "$NODE_EXE" --input-type=module --check < "$DEMO_DIR/wix-app/velo-backend/provider-config.web.js"
  "$NODE_EXE" --input-type=module --check < "$DEMO_DIR/wix-app/velo-backend/provider-secret.js"
  "$NODE_EXE" --test "$DEMO_DIR/tests/test_frontend.mjs"
  if [[ -f "$DEMO_DIR/tests/test_snapshot_generator.mjs" ]]; then
    "$NODE_EXE" --test "$DEMO_DIR/tests/test_snapshot_generator.mjs"
  fi
  exit 0
fi

if [[ "${1:-}" == "index" ]]; then
  # The Guide's factual corpus must come from the reviewed, rendered capture.
  # Raw Wix HTML can omit accordions, lazy collection items, and calendar rows.
  exec python3 "$DEMO_DIR/scripts/rebuild_site_index.py" --from-rendered-snapshots
fi

if [[ "${1:-}" == "crawl-index" ]]; then
  # Inventory-only operator path. Review its output against a rendered capture
  # before using it to replace the indexed source corpus.
  exec python3 "$DEMO_DIR/scripts/rebuild_site_index.py"
fi

if lsof -nP -iTCP:"$DEMO_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port $DEMO_PORT is already in use. The launcher left that process untouched."
  exit 1
fi

if [[ -z "${OLLAMA_API_KEY:-}" ]]; then
  read -r -s -p "Ollama Cloud API key: " OLLAMA_API_KEY
  echo
  export OLLAMA_API_KEY
fi

PORT="$DEMO_PORT" HOST="127.0.0.1" python3 "$DEMO_DIR/server.py" &
DEMO_PID=$!

cleanup() {
  if kill -0 "$DEMO_PID" >/dev/null 2>&1; then
    kill "$DEMO_PID"
    wait "$DEMO_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

for _ in {1..40}; do
  if curl -fsS "$DEMO_URL/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.15
done

if ! curl -fsS "$DEMO_URL/health" >/dev/null 2>&1; then
  echo "The local demo did not become ready."
  exit 1
fi

echo "Fortune Digital Equity meeting demo"
echo "  $DEMO_URL"
echo "  GLM-5.2 through Ollama Cloud; credential stays server-side"

if command -v open >/dev/null 2>&1; then
  open "$DEMO_URL"
fi

wait "$DEMO_PID"
