FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY core core
COPY api api
COPY gsim/pyproject.toml gsim/pyproject.toml
COPY helper/pyproject.toml helper/pyproject.toml
RUN mkdir -p gsim/fleck_gsim helper/fleck_helper && touch gsim/fleck_gsim/__init__.py helper/fleck_helper/__init__.py \
 && uv sync --frozen --package fleck-api --no-dev
CMD ["sh", "-c", "uv run --no-sync --package fleck-api alembic -c api/alembic.ini upgrade head && exec uv run --no-sync --package fleck-api uvicorn fleck_api.main:app --host 0.0.0.0 --port 8000"]
