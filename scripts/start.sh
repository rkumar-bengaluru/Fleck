#!/usr/bin/env bash
# Bring the whole Fleck stack up: Ollama + models, the containers, then Fleck Helper.
set -euo pipefail
cd "$(dirname "$0")/.."
make ensure-ollama
make up
if [ -d "dist/Fleck Helper.app" ]; then
  open "dist/Fleck Helper.app"
  echo "Fleck is up. Whisper loads when the helper opens: wait ~30 s before recording."
else
  echo "Fleck is up, but dist/Fleck Helper.app is missing: run 'make helper-app'." >&2
fi
