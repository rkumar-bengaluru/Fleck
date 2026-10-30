.PHONY: up down test ensure-ollama helper-app
up:
	docker compose -f deploy/docker-compose.yml up -d --build --wait
down:
	docker compose -f deploy/docker-compose.yml down
test:
	uv run --all-packages pytest core api gsim helper/tests/unit
ensure-ollama:
	scripts/ensure_ollama.sh

helper-app:
	scripts/make_cert.sh
	rm -rf build dist
	cd helper/packaging && uv run --package fleck-helper python setup.py py2app  # cwd without pyproject.toml: py2app rejects install_requires
	helper/packaging/sign.sh "dist/Fleck Helper.app"
