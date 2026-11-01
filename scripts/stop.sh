#!/usr/bin/env bash
# Bring the whole Fleck stack down and free the GPU. Meetings, documents and accounts are kept
# (this never runs `make reset`). Prints CPU/memory/GPU before and after. Ollama's server stays running but holds no models.
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/sysstat.sh
snapshot "Before stop"
if docker ps --format '{{.Names}}' | grep -qx fleck-postgres-1; then
  rec=$(docker exec fleck-postgres-1 psql -U fleck -d fleck -Atc "select id from meetings where status='recording'") \
    || rec="unknown (could not ask the database)"
  if [ -n "$rec" ] && [ "${1:-}" != "--force" ]; then
    echo "Meeting(s) still recording: $rec. Stop the recording first, or run with --force." >&2
    exit 1
  fi
fi
pkill -TERM -f "Fleck Helper.app/Contents/MacOS" && echo "Fleck Helper quit." || true
if curl -sf localhost:11434/api/tags >/dev/null; then
  ollama ps | awk 'NR>1 {print $1}' | while read -r m; do ollama stop "$m" && echo "Unloaded $m." || echo "Could not unload $m." >&2; done
fi
make down
sleep 5  # let the GPU and memory figures catch up
echo
snapshot "After stop"
echo "Fleck is down. Bring it back with scripts/start.sh."
