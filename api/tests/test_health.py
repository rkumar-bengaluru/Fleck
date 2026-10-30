import pytest
from fastapi.testclient import TestClient

from fleck_api.main import create_app


@pytest.mark.ollama  # needs the compose stack plus local Ollama
def test_health_reports_each_dependency(monkeypatch):
    r = TestClient(create_app()).get("/api/health")
    assert r.status_code == 200, r.text
    assert r.json() == {"status": "ok", "checks": {k: "ok" for k in ["postgres", "minio", "weaviate", "ollama"]}}

    monkeypatch.setenv("FLECK_OLLAMA_URL", "http://localhost:1")
    r = TestClient(create_app()).get("/api/health")
    assert r.status_code == 503
    assert r.json()["checks"]["ollama"].startswith("error")
