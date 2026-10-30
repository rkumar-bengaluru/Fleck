import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from fleck_api import auth
from fleck_api.db import get_session
from fleck_api.jobs import run_once
from fleck_api.main import create_app
from fleck_api.models import GoogleToken, Job, User, UserSession
from fleck_gsim.app import create_app as gsim_app

EXEMPT = {"/api/health", "/api/auth/login", "/api/auth/callback"}


@pytest.fixture
def env(make_session, monkeypatch):
    monkeypatch.setenv("FLECK_ALLOWED_DOMAINS", "northbeam.test")
    monkeypatch.setenv("FLECK_ALLOWED_EMAILS", "")
    monkeypatch.setenv("FLECK_SECRET_KEY", "test-secret")
    gs = TestClient(gsim_app(), follow_redirects=False)
    monkeypatch.setattr(auth, "http_client", lambda: gs)  # api -> in-process gsim (absolute URLs ignored)
    app = create_app()

    def sess():
        with make_session() as s:
            yield s
    app.dependency_overrides[get_session] = sess
    return TestClient(app, follow_redirects=False), gs, make_session


def _sign_in(client, gs, email):
    r = client.get("/api/auth/login")
    assert r.status_code == 302 and "calendar.readonly" in r.headers["location"]
    q = r.headers["location"].split("?", 1)[1]
    allow = gs.get(f"/o/oauth2/v2/allow?{q}&email={email}")  # what clicking the account and Allow does
    return client.get(allow.headers["location"].replace("http://localhost:8000", ""))


def test_sign_in_allowed_domain(env):
    client, gs, make = env
    r = _sign_in(client, gs, "rupak@northbeam.test")
    assert r.status_code == 302 and r.headers["location"] == "http://localhost:5173/"
    cookie = r.headers["set-cookie"]
    assert "fleck_session=" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie and "Max-Age=2592000" in cookie
    assert client.get("/api/me").json()["email"] == "rupak@northbeam.test"
    raw = client.cookies["fleck_session"]
    with make() as s:
        assert s.scalar(select(UserSession.token_hash)) != raw  # only the sha256 is stored
        enc = s.scalar(select(GoogleToken.token_enc))
        assert "gsim-at" not in enc and "gsim-rt" not in enc
        assert s.scalar(select(Job.kind)) == "sync_calendar"
        assert run_once(s) and s.scalar(select(Job.status)) == "done"  # placeholder keeps the worker green
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/me").status_code == 401


def test_sign_in_refused_outside_allow_list(env):
    client, gs, make = env
    r = _sign_in(client, gs, "outsider@example.test")
    assert r.headers["location"] == "http://localhost:5173/signin?error=not_allowed"
    assert "fleck_session" not in client.cookies
    with make() as s:
        assert s.scalar(select(User)) is None and s.scalar(select(Job)) is None


def test_allowed_email_outside_domain(env, monkeypatch):
    client, gs, _ = env
    monkeypatch.setenv("FLECK_ALLOWED_EMAILS", "Outsider@example.test")
    assert _sign_in(client, gs, "outsider@example.test").headers["location"] == "http://localhost:5173/"


def test_bad_state_rejected(env):
    client, _, _ = env
    client.get("/api/auth/login")
    assert client.get("/api/auth/callback?code=gsim-code-rupak@northbeam.test&state=forged").status_code == 400
    client.cookies.clear()
    assert client.get("/api/auth/callback?code=x&state=x").status_code == 400


def test_api_requires_session(env):
    assert env[0].get("/api/clients").status_code == 401


def test_every_api_route_is_guarded(env):
    # OpenAPI lists every HTTP route however it was included (app.routes nests routers lazily).
    # shortcut: WebSocket routes are not in OpenAPI; the realtime task must test its own 401/close.
    client, _, _ = env
    checked = 0
    for path, ops in client.app.openapi()["paths"].items():
        if path.startswith("/api") and path not in EXEMPT:
            for method in ops:
                assert client.request(method, re.sub(r"\{[^}]+\}", "1", path)).status_code == 401, f"{method} {path} is not behind current_user"
                checked += 1
    assert checked >= 8
