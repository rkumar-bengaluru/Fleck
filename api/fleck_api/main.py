import httpx
import psycopg
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from fleck_api.config import setting


def _check(fn) -> str:
    try:
        fn()
        return "ok"
    except Exception as e:  # health must never raise
        return f"error: {type(e).__name__}: {e}"[:200]


def _get(url: str):
    httpx.get(url, timeout=3).raise_for_status()


def create_app() -> FastAPI:
    app = FastAPI(title="fleck-api")

    @app.get("/api/health")
    def health():
        # read env per request so tests can repoint a dependency
        db = setting("FLECK_DATABASE_URL", "postgresql://fleck:fleck@localhost:5432/fleck")
        minio = setting("FLECK_MINIO_URL", "http://localhost:9000")
        weaviate = setting("FLECK_WEAVIATE_URL", "http://localhost:8080")
        ollama = setting("FLECK_OLLAMA_URL", "http://localhost:11434")
        checks = {
            "postgres": _check(lambda: psycopg.connect(db, connect_timeout=3).close()),
            "minio": _check(lambda: _get(f"{minio}/minio/health/live")),
            "weaviate": _check(lambda: _get(f"{weaviate}/v1/.well-known/ready")),
            "ollama": _check(lambda: _get(f"{ollama}/api/tags")),
        }
        ok = all(v == "ok" for v in checks.values())
        return JSONResponse({"status": "ok" if ok else "error", "checks": checks}, status_code=200 if ok else 503)

    return app


app = create_app()
