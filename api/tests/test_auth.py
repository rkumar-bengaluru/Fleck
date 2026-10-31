import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from fleck_api import auth
from fleck_api.db import get_session
from fleck_api.jobs import run_once
from fleck_api.main import create_app
from fleck_api.models import CalendarEvent, GoogleToken, Job, User, UserSession
from fleck_gsim.app import create_app as gsim_app

EXEMPT = {"/api/health", "/api/auth/login", "/api/auth/callback", "/api/devices/pair"}  # pair: the one-time code is the credential


@pytest.fixture
def env(make_session, monkeypatch):
    monkeypatch.setenv("FLECK_ALLOWED_DOMAINS", "northbeam.test")
    monkeypatch.setenv("FLECK_ALLOWED_EMAILS", "")
    monkeypatch.setenv("FLECK_SECRET_KEY", "k" * 40)
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
    u = urlparse(allow.headers["location"])
    return client.get(f"{u.path}?{u.query}")


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
        # first sign-in syncs inline (no job), so Today has the calendar at once
        assert s.scalar(select(Job)) is None and s.scalar(select(CalendarEvent.id)) is not None
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


def _walk(app):
    """Every route, however nested (FastAPI wraps included routers lazily), incl. WebSocket and hidden ones."""
    for r in app.routes:
        yield from (r.effective_route_contexts() if hasattr(r, "effective_route_contexts") else [r])


def test_every_api_route_is_guarded(env):
    client, _, _ = env
    checked = 0
    for r in _walk(client.app):
        path = getattr(r, "path", "")
        if not path.startswith("/api") or path in EXEMPT:
            continue
        url = re.sub(r"\{[^}]+\}", "1", path)
        if getattr(r, "methods", None):
            for m in r.methods:
                assert client.request(m, url).status_code == 401, f"{m} {path} is not behind current_user"
        else:  # WebSocket: must refuse the handshake without a session
            with pytest.raises(Exception):
                with client.websocket_connect(url):
                    pass
        checked += 1
    assert checked >= 8


def test_refuses_to_start_without_a_real_secret_key(monkeypatch):
    for bad in ("", "change-me", "short"):
        monkeypatch.setenv("FLECK_SECRET_KEY", bad)
        with pytest.raises(RuntimeError, match="FLECK_SECRET_KEY"):
            with TestClient(create_app()):
                pass


def test_refuses_to_start_in_google_mode_without_credentials(monkeypatch):
    monkeypatch.setenv("FLECK_SECRET_KEY", "k" * 40)
    monkeypatch.setenv("FLECK_GOOGLE_MODE", "google")
    for cid, secret in (("", ""), ("id", ""), ("", "secret")):
        monkeypatch.setenv("GOOGLE_CLIENT_ID", cid)
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", secret)
        with pytest.raises(RuntimeError, match="GOOGLE_CLIENT_ID"):
            with TestClient(create_app()):
                pass
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    auth.check_google_config()  # both set: starts
    monkeypatch.setenv("FLECK_GOOGLE_MODE", "gsim")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
    auth.check_google_config()  # the simulator needs neither


class _Unverified:  # stands in for Google answering with an unverified or missing email
    def __init__(self, info): self.info = info
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def post(self, *a, **k): return httpx.Response(200, json={"access_token": "t"}, request=httpx.Request("POST", "http://x"))
    def get(self, *a, **k): return httpx.Response(200, json=self.info, request=httpx.Request("GET", "http://x"))


@pytest.mark.parametrize("info", [{"email": "rupak@northbeam.test", "email_verified": False}, {"email": "rupak@northbeam.test"}, {"name": "x"}])
def test_unverified_or_missing_email_refused(env, monkeypatch, info):
    client, gs, make = env
    monkeypatch.setattr(auth, "http_client", lambda: _Unverified(info))
    client.cookies.set("fleck_oauth_state", "s")
    r = client.get("/api/auth/callback?code=c&state=s")
    assert r.headers["location"] == "http://localhost:5173/signin?error=not_allowed"
    with make() as s:
        assert s.scalar(select(User)) is None


def test_allowed_needs_an_at_sign(monkeypatch):
    monkeypatch.setenv("FLECK_ALLOWED_DOMAINS", "northbeam.test")
    assert auth._allowed("a@northbeam.test") and not auth._allowed("northbeam.test")


def test_cookies_secure_only_over_https(env, monkeypatch):
    client, gs, _ = env
    sc = lambda r: "; ".join(r.headers.get_list("set-cookie"))
    assert "Secure" not in sc(_sign_in(client, gs, "rupak@northbeam.test"))
    monkeypatch.setenv("FLECK_PUBLIC_API_URL", "https://fleck.example")
    assert "Secure" in sc(client.get("/api/auth/login"))
    client.cookies.clear()  # Secure cookies are not sent back over the test's http, so set the state by hand
    client.cookies.set("fleck_oauth_state", "s")
    r = client.get("/api/auth/callback?code=gsim-code-rupak@northbeam.test&state=s")
    assert "Secure" in next(c for c in r.headers.get_list("set-cookie") if c.startswith("fleck_session"))


def test_bad_state_clears_state_cookie(env):
    client, _, _ = env
    client.get("/api/auth/login")
    r = client.get("/api/auth/callback?code=x&state=forged")
    assert r.status_code == 400 and 'fleck_oauth_state=""' in r.headers["set-cookie"]


def test_unreadable_stored_tokens_are_overwritten(env):
    client, gs, make = env
    _sign_in(client, gs, "rupak@northbeam.test")
    with make() as s:
        s.execute(update(GoogleToken).values(token_enc="garbage"))
        s.commit()
    assert _sign_in(client, gs, "rupak@northbeam.test").status_code == 302
    with make() as s:
        assert s.scalar(select(GoogleToken.token_enc)) != "garbage"


def test_expired_session_is_401_and_logout_deletes_row(env):
    client, gs, make = env
    _sign_in(client, gs, "rupak@northbeam.test")
    with make() as s:
        s.execute(update(UserSession).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        s.commit()
    assert client.get("/api/me").status_code == 401
    with make() as s:
        s.execute(update(UserSession).values(expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
        s.commit()
    assert client.get("/api/me").status_code == 200
    assert client.post("/api/auth/logout").status_code == 204
    with make() as s:
        assert s.scalar(select(UserSession)) is None


def test_cross_origin_write_with_cookie_refused(env):
    client, gs, make = env
    _sign_in(client, gs, "rupak@northbeam.test")
    assert client.post("/api/auth/logout", headers={"Origin": "http://evil.test"}).status_code == 403
    assert client.post("/api/auth/logout", headers={"Referer": "http://evil.test/x"}).status_code == 403
    with make() as s:
        assert s.scalar(select(UserSession)) is not None
    assert client.post("/api/auth/logout", headers={"Origin": "http://localhost:5173"}).status_code == 204
