.PHONY: up down test gen-types ensure-ollama seed-library seed-demo helper-app fixtures
COMPOSE = docker compose -f deploy/docker-compose.yml $(if $(wildcard .env),--env-file .env)  # .env sets TZ for compose
up:
	$(COMPOSE) up -d --build --wait
down:
	$(COMPOSE) down
test:
	uv run --all-packages pytest core api gsim helper/tests/unit
ensure-ollama:
	scripts/ensure_ollama.sh

seed-library:
	uv run --package fleck-api python scripts/seed_library.py

seed-demo: seed-library  # the stack must be up (make up); ~8 min, most of it the replay
	uv run --all-packages python scripts/seed_demo.py

helper-app:
	scripts/make_cert.sh
	rm -rf build dist
	cd helper/packaging && uv run --package fleck-helper python setup.py py2app  # cwd without pyproject.toml: py2app rejects install_requires
	# mlx is a namespace package, which py2app cannot collect: copy it in (without its C headers and cmake files)
	rsync -a --exclude include --exclude cmake --exclude share --exclude __pycache__ \
	  "$$(uv run --package fleck-helper python -c 'import mlx; print(mlx.__path__[0])')" "dist/Fleck Helper.app/Contents/Resources/lib/python3.12/"
	helper/packaging/sign.sh "dist/Fleck Helper.app"

gen-types:
	uv run --package fleck-core python scripts/gen_event_types.py

fixtures:  # needs macOS `say`; output is deterministic
	uv run --script fixtures/build.py
