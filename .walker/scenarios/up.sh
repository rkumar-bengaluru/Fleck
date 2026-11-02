#!/usr/bin/env bash
# Off camera: the set. Writes .env from .env.example (gsim sign-in, a generated secret key, the Anthropic key from
# the environment that `walker with-key` gives, never printed), starts Ollama with the pinned models, brings the
# stack up as its own Compose project, and indexes the library (clients/, all fictional or public).
set -euo pipefail
project=$1
[ -n "${ANTHROPIC_API_KEY:-}" ] || { echo "ANTHROPIC_API_KEY missing: run under walker with-key" >&2; exit 1; }
tz=$(readlink /etc/localtime | sed 's|.*zoneinfo/||')
python3 - "$tz" <<'PY'
import os, secrets, sys
out = []
for line in open(".env.example"):
    k = line.split("=", 1)[0].strip()
    if k == "ANTHROPIC_API_KEY": line = f"ANTHROPIC_API_KEY={os.environ['ANTHROPIC_API_KEY']}\n"
    elif k == "FLECK_SECRET_KEY": line = f"FLECK_SECRET_KEY={secrets.token_urlsafe(32)}\n"
    elif k == "TZ": line = f"TZ={sys.argv[1]}\n"
    elif k == "FLECK_ALLOWED_DOMAINS": line = "FLECK_ALLOWED_DOMAINS=northbeam.test\n"  # gsim's demo team
    out.append(line)
fd = os.open(".env", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
os.write(fd, "".join(out).encode()); os.close(fd)
PY
scripts/ensure_ollama.sh
uv sync --all-packages -q
docker compose -p "$project" -f deploy/docker-compose.yml --env-file .env up -d --build --wait
uv run --package fleck-api python scripts/seed_library.py
echo "set up: $project"
