#!/usr/bin/env bash
# Start Ollama if needed, pull the pinned models, verify manifest digests (first 12 hex chars = `ollama list` ID).
set -euo pipefail
if ! curl -sf localhost:11434/api/tags >/dev/null; then
  nohup ollama serve >/tmp/ollama.log 2>&1 &
  for _ in $(seq 30); do curl -sf localhost:11434/api/tags >/dev/null && break; sleep 1; done
fi
rc=0
for pin in "qwen3:4b-q4_K_M 2bfd38a7daaf4b1037efe517ccb73d1a3bbd4822cf89f1a82be1569050a114e0" \
           "qwen3-embedding:0.6b ac6da0dfba84a81fdbfbaf330198c33cd77c4cdfc53e8bc50eb581914a15621d"; do
  set -- $pin
  ollama pull "$1"
  id=$(ollama list | awk -v m="$1" '$1==m {print $2}')
  if [ "$id" = "${2:0:12}" ]; then echo "ok $1 $id"; else echo "MISMATCH $1: got '$id', want ${2:0:12}" >&2; rc=1; fi
done
exit $rc
