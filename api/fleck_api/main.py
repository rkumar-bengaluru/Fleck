import httpx
import psycopg
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from fleck_api import auth, calendar_sync, devices, library, meetings, realtime
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
    @asynccontextmanager
    async def lifespan(_app):
        auth.check_secret_key()
        auth.check_google_config()
        try:
            library.ensure_index()
        except Exception:  # weaviate may still be starting; /api/health reports it and the indexer retries
            logging.getLogger("fleck.api").exception("could not create the Weaviate schema at startup")
        yield

    app = FastAPI(title="fleck-api", lifespan=lifespan)
    app.middleware("http")(library.size_guard)

    @app.middleware("http")
    async def origin_guard(request, call_next):
        if not auth.origin_guard_allowed(request):
            return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
        return await call_next(request)
    app.include_router(auth.router)
    app.include_router(library.router)
    app.include_router(calendar_sync.router)
    app.include_router(devices.router)
    app.include_router(meetings.router)
    app.include_router(realtime.router)

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
