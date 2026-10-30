from fastapi.testclient import TestClient

from fleck_gsim.app import app


def test_health():
    assert TestClient(app).get("/health").json() == {"status": "ok"}
