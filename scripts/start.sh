#!/usr/bin/env bash
# Bring the whole Fleck stack up: Ollama + models, the containers, then Fleck Helper.
# Prints CPU/memory/GPU before and after, waits for things to settle, then checks every part.
# FLECK_SETTLE_S sets the wait (default 90 s).
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/sysstat.sh
snapshot "Before start"
make ensure-ollama
make up
if [ -d "dist/Fleck Helper.app" ]; then
  open "dist/Fleck Helper.app"
else
  echo "dist/Fleck Helper.app is missing: run 'make helper-app'." >&2
fi
settle=${FLECK_SETTLE_S:-90}
echo "Waiting ${settle} s for the api to warm the models and the helper to load Whisper..."
sleep "$settle"

fail=0
check() { if eval "$2" >/dev/null 2>&1; then echo "ok    $1"; else echo "FAIL  $1"; fail=1; fi; }
echo "=== Checks ==="
check "api health (postgres, minio, weaviate, ollama)" 'curl -sf localhost:8000/api/health | grep -q "\"status\":\"ok\""'
check "web app on :5173" 'curl -sf -o /dev/null localhost:5173/'
check "worker container running" 'docker ps --format "{{.Names}}" | grep -qx fleck-worker-1'
for m in qwen3:4b-q4_K_M qwen3-embedding:0.6b qwen3-embedding:4b; do
  check "ollama model $m loaded" "ollama ps | awk 'NR>1 {print \$1}' | grep -qx '$m'"
done
check "Fleck Helper running" 'pgrep -f "Fleck Helper.app/Contents/MacOS"'
echo
snapshot "After start (${settle} s settle)"
if [ "$fail" = 0 ]; then echo "Fleck is up."; else echo "Fleck is up, but some checks failed (see above)." >&2; exit 1; fi
