.PHONY: up down test ensure-ollama
up:
	docker compose -f deploy/docker-compose.yml up -d --build --wait
down:
	docker compose -f deploy/docker-compose.yml down
test:
	uv run --all-packages pytest core api gsim helper/tests/unit
ensure-ollama:
	scripts/ensure_ollama.sh
