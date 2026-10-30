"""Sign-in with Google (or gsim), the allow-list, sessions and token storage."""
import base64
import hashlib
import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode, urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from fleck_api.config import setting
from fleck_api.db import get_session
from fleck_api.jobs import enqueue_once
from fleck_api.models import GoogleToken, User, UserSession

log = logging.getLogger("fleck.auth")
COOKIE, STATE_COOKIE = "fleck_session", "fleck_oauth_state"
SESSION_DAYS = 30
SCOPE = "openid email profile https://www.googleapis.com/auth/calendar.readonly"


def _urls() -> dict[str, str]:
    """Browser-facing URLs (consent page) and server-side URLs (api -> gsim by service name) are separate."""
    if setting("FLECK_GOOGLE_MODE", "gsim") == "google":
        return {"auth": "https://accounts.google.com/o/oauth2/v2/auth", "token": "https://oauth2.googleapis.com/token",
                "userinfo": "https://openidconnect.googleapis.com/v1/userinfo",
                "calendar": "https://www.googleapis.com/calendar/v3/calendars/primary/events"}
    server = setting("FLECK_GSIM_URL", "http://localhost:8090")
    return {"auth": setting("FLECK_GSIM_PUBLIC_URL", "http://localhost:8090") + "/o/oauth2/v2/auth",
            "token": server + "/token", "userinfo": server + "/oauth2/v3/userinfo",
            "calendar": server + "/calendar/v3/calendars/primary/events"}


def http_client() -> httpx.Client:  # tests swap this for an in-process gsim
    return httpx.Client(timeout=10)


def _web() -> str:
    return setting("FLECK_WEB_URL", "http://localhost:5173")


def _redirect_uri() -> str:
    return setting("FLECK_PUBLIC_API_URL", "http://localhost:8000") + "/api/auth/callback"


def check_secret_key() -> None:
    key = setting("FLECK_SECRET_KEY", "")
    if key in ("", "change-me") or len(key) < 32:
        raise RuntimeError("FLECK_SECRET_KEY must be set to a random value of at least 32 characters; generate one with: "
                           'python -c "import secrets;print(secrets.token_urlsafe(32))"')


def _secure() -> bool:
    return setting("FLECK_PUBLIC_API_URL", "http://localhost:8000").startswith("https://")


def _fernet() -> Fernet:
    key = setting("FLECK_SECRET_KEY", "")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _allowed(email: str) -> bool:
    csv = lambda name: {x.strip().lower() for x in setting(name, "").split(",") if x.strip()}
    email = email.lower()
    if "@" not in email:
        return False
    return email in csv("FLECK_ALLOWED_EMAILS") or email.rsplit("@", 1)[-1] in csv("FLECK_ALLOWED_DOMAINS")


def current_user(fleck_session: str | None = Cookie(None), s: Session = Depends(get_session)) -> User:
    if fleck_session:
        row = s.execute(select(User, UserSession.expires_at).join(UserSession, UserSession.user_id == User.id)
                        .where(UserSession.token_hash == _hash(fleck_session))).first()
        if row and row[1] > datetime.now(timezone.utc):
            return row[0]
    raise HTTPException(401, "not signed in")


router = APIRouter(prefix="/api")


@router.get("/auth/login")
def login():
    state = secrets.token_urlsafe(24)
    q = urlencode({"client_id": setting("GOOGLE_CLIENT_ID", "gsim-client"), "redirect_uri": _redirect_uri(),
                   "response_type": "code", "scope": SCOPE, "state": state, "access_type": "offline", "prompt": "consent"})
    r = RedirectResponse(f"{_urls()['auth']}?{q}", 302)
    r.set_cookie(STATE_COOKIE, state, max_age=600, httponly=True, samesite="lax", secure=_secure())
    return r


def _fail(error: str) -> RedirectResponse:
    r = RedirectResponse(f"{_web()}/signin?error={error}", 302)
    r.delete_cookie(STATE_COOKIE)
    return r


def origin_guard_allowed(request: Request) -> bool:
    """CSRF defence for cookie-authenticated writes: a browser always sends Origin (or Referer) cross-origin."""
    if request.method in ("GET", "HEAD", "OPTIONS") or not request.url.path.startswith("/api") or COOKIE not in request.cookies:
        return True
    ok = {setting("FLECK_WEB_URL", "http://localhost:5173").rstrip("/"), setting("FLECK_PUBLIC_API_URL", "http://localhost:8000").rstrip("/")}
    origin = request.headers.get("origin")
    if origin is None and (ref := request.headers.get("referer")):
        u = urlparse(ref)
        origin = f"{u.scheme}://{u.netloc}"
    return origin is None or origin.rstrip("/") in ok  # no Origin = non-browser client; none authenticates by cookie yet


@router.get("/auth/callback")
def callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None,
             s: Session = Depends(get_session)):
    expected = request.cookies.get(STATE_COOKIE)
    if not state or not expected or not secrets.compare_digest(state, expected):
        r = Response("bad state", 400)
        r.delete_cookie(STATE_COOKIE)
        return r
    if error or not code:
        return _fail("denied")
    urls = _urls()
    try:
        with http_client() as c:
            tok = c.post(urls["token"], data={
                "grant_type": "authorization_code", "code": code, "redirect_uri": _redirect_uri(),
                "client_id": setting("GOOGLE_CLIENT_ID", "gsim-client"),
                "client_secret": setting("GOOGLE_CLIENT_SECRET", "gsim-secret")}).raise_for_status().json()
            info = c.get(urls["userinfo"], headers={"Authorization": f"Bearer {tok['access_token']}"}).raise_for_status().json()
    except (httpx.HTTPError, KeyError, ValueError) as e:  # never log the response: it may carry tokens
        log.warning("google sign-in failed: %s", type(e).__name__)
        return _fail("failed")
    email = (info.get("email") or "").lower()
    if info.get("email_verified") is not True or not _allowed(email):
        return _fail("not_allowed")
    user = s.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email)
        s.add(user)
    user.name = info.get("name") or user.name
    s.flush()
    blob = {"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"),
            "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=tok.get("expires_in", 3600))).isoformat()}
    row = s.scalar(select(GoogleToken).where(GoogleToken.user_id == user.id))
    if row is None:
        row = GoogleToken(user_id=user.id, token_enc="")
        s.add(row)
    elif not blob["refresh_token"]:  # Google sends the refresh token only on first consent; keep the old one
        try:
            blob["refresh_token"] = json.loads(_fernet().decrypt(row.token_enc.encode())).get("refresh_token")
        except InvalidToken:  # key rotated or row corrupt: treat as no tokens and overwrite
            log.warning("stored tokens for user %s are unreadable; overwriting", user.id)
    row.token_enc = _fernet().encrypt(json.dumps(blob).encode()).decode()
    raw = secrets.token_urlsafe(32)
    s.add(UserSession(user_id=user.id, token_hash=_hash(raw),
                      expires_at=datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)))
    s.commit()
    enqueue_once(s, "sync_calendar", {"user_id": user.id})
    r = RedirectResponse(f"{_web()}/", 302)
    r.delete_cookie(STATE_COOKIE)
    r.set_cookie(COOKIE, raw, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax", secure=_secure())
    return r


@router.post("/auth/logout", status_code=204)
def logout(response: Response, fleck_session: str | None = Cookie(None), _u: User = Depends(current_user),
           s: Session = Depends(get_session)):
    s.execute(delete(UserSession).where(UserSession.token_hash == _hash(fleck_session or "")))
    s.commit()
    response.delete_cookie(COOKIE)


@router.get("/me")
def me(u: User = Depends(current_user)):
    return {"id": u.id, "email": u.email, "name": u.name}

