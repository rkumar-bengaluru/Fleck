FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
WORKDIR /app
COPY pyproject.toml .python-version ./
COPY gsim gsim
COPY core/pyproject.toml core/pyproject.toml
COPY api/pyproject.toml api/pyproject.toml
COPY helper/pyproject.toml helper/pyproject.toml
RUN mkdir -p core/fleck_core api/fleck_api helper/fleck_helper && touch core/fleck_core/__init__.py api/fleck_api/__init__.py helper/fleck_helper/__init__.py \
 && uv sync --package fleck-gsim --no-dev
CMD ["uv", "run", "--package", "fleck-gsim", "uvicorn", "fleck_gsim.app:app", "--host", "0.0.0.0", "--port", "8090"]
