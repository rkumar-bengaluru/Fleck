"""Google simulator: just enough OAuth + Calendar for sign-in, tests and the demo. Not secure by design."""
import html
from collections.abc import Callable
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

ACCOUNTS = {
    "rupak@northbeam.test": "Rupak K",
    "priya@northbeam.test": "Priya S",
    "outsider@example.test": "Olivia Outsider",
}
# shortcut: codes and tokens are derived from the email (no expiry, no secrets), fine for a simulator only.
CODE, ACCESS, REFRESH = "gsim-code-", "gsim-at-", "gsim-rt-"


def _attendee(email: str, name: str, status: str = "accepted", **extra) -> dict:
    return {"email": email, "displayName": name, "responseStatus": status, **extra}


def fixture_events(now: datetime, lo: datetime, hi: datetime) -> list[dict]:
    """Calendar for the local day of `now`; recurring Standup is expanded per day inside [lo, hi)."""
    def dt(d: datetime) -> dict:
        return {"dateTime": d.isoformat()}

    def timed(id_, title, start, minutes, **kw):
        return {"id": id_, "summary": title, "status": "confirmed", "start": dt(start),
                "end": dt(start + timedelta(minutes=minutes)), **kw}

    today = now.replace(minute=0, second=0, microsecond=0)
    demo = today + timedelta(hours=1)  # next whole hour
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    me = _attendee("rupak@northbeam.test", "Rupak K", organizer=True, self=True)
    events = [
        timed("evt-demo", "Presales: demo prospect", demo, 45,
              description="Wants to automate first-pass redline review of NDAs and MSAs inside iManage.",
              hangoutLink="https://meet.google.com/abc-defg-hij",
              attendees=[me, _attendee("dana.whitfield@harbourline.test", "Dana Whitfield", "needsAction"),
                         _attendee("marcus.lee@harbourline.test", "Marcus Lee"),
                         _attendee("priya@northbeam.test", "Priya S")]),
        timed("evt-pricing", "Pricing review (internal)", demo + timedelta(hours=3), 30,
              attendees=[me, _attendee("priya@northbeam.test", "Priya S")]),
        {"id": "evt-offsite", "summary": "Offsite", "status": "confirmed",  # all-day: the app must skip it
         "start": {"date": midnight.date().isoformat()},
         "end": {"date": (midnight + timedelta(days=1)).date().isoformat()}},
        timed("evt-declined", "Vendor sync", demo + timedelta(hours=1), 30,
              attendees=[_attendee("rupak@northbeam.test", "Rupak K", "declined", self=True)]),
        timed("evt-orion", "Discovery: Orion Freight", midnight + timedelta(days=1, hours=11), 45,
              hangoutLink="https://meet.google.com/orn-freg-ht1",
              attendees=[me, _attendee("sam.ortiz@orionfreight.test", "Sam Ortiz", "needsAction")]),
    ]
    day = (lo.astimezone(now.tzinfo) - timedelta(days=1)).replace(hour=9, minute=30, second=0, microsecond=0)
    while day < hi:
        events.append(timed(f"evt-standup_{day:%Y%m%d}", "Standup", day, 15, recurringEventId="evt-standup",
                            location="https://zoom.us/j/5550001234", attendees=[me]))
        day += timedelta(days=1)
    return [e for e in events if _overlaps(e, lo, hi)]


def _overlaps(e: dict, lo: datetime, hi: datetime) -> bool:
    if "date" in e["start"]:
        s = datetime.fromisoformat(e["start"]["date"]).replace(tzinfo=lo.tzinfo)
        return s < hi and s + timedelta(days=1) > lo
    return datetime.fromisoformat(e["start"]["dateTime"]) < hi and datetime.fromisoformat(e["end"]["dateTime"]) > lo


def _page(body: str) -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><title>Sign in - Google (simulator)</title><body style='font-family:sans-serif;max-width:420px;margin:60px auto'>{body}</body>")


def create_app(clock: Callable[[], datetime] = lambda: datetime.now().astimezone()) -> FastAPI:
    app = FastAPI(title="gsim")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/o/oauth2/v2/auth")
    def consent(request: Request, email: str | None = None):
        q = dict(request.query_params)
        q.pop("email", None)
        if email is None:
            btns = "".join(f"<p><a href='?{html.escape(urlencode({**q, 'email': e}))}'><button type='button'>{e}</button></a></p>" for e in ACCOUNTS)
            return _page(f"<h2>Choose an account</h2>{btns}")
        if email not in ACCOUNTS:
            raise HTTPException(400, "unknown account")
        hidden = "".join(f"<input type='hidden' name='{html.escape(k)}' value='{html.escape(v)}'>" for k, v in q.items())
        return _page(f"<h2>{html.escape(ACCOUNTS[email])} ({email}) wants to use Fleck</h2>"
                     f"<p>See your name and email, and view your calendar events.</p>"
                     f"<form method='get' action='/o/oauth2/v2/allow'>{hidden}<input type='hidden' name='email' value='{email}'>"
                     f"<button type='submit'>Allow</button></form>")

    @app.get("/o/oauth2/v2/allow")
    def allow(request: Request, email: str, redirect_uri: str, state: str = ""):
        if email not in ACCOUNTS:
            raise HTTPException(400, "unknown account")
        return RedirectResponse(f"{redirect_uri}?{urlencode({'code': CODE + email, 'state': state})}", 302)

    @app.post("/token")
    async def token(request: Request):
        f = {k: v[0] for k, v in parse_qs((await request.body()).decode()).items()}
        grant, secret = f.get("grant_type"), f.get("code") or f.get("refresh_token") or ""
        prefix = {"authorization_code": CODE, "refresh_token": REFRESH}.get(grant)
        email = secret.removeprefix(prefix) if prefix and secret.startswith(prefix) else None
        if email not in ACCOUNTS:
            raise HTTPException(400, {"error": "invalid_grant"})
        out = {"access_token": ACCESS + email, "expires_in": 3600, "token_type": "Bearer"}
        if grant == "authorization_code":
            out["refresh_token"] = REFRESH + email
        return out

    def who(request: Request) -> str:
        t = request.headers.get("authorization", "").removeprefix("Bearer ")
        email = t.removeprefix(ACCESS)
        if not t.startswith(ACCESS) or email not in ACCOUNTS:
            raise HTTPException(401, "bad token")
        return email

    @app.get("/oauth2/v3/userinfo")
    def userinfo(request: Request):
        e = who(request)
        return {"sub": e, "email": e, "email_verified": True, "name": ACCOUNTS[e]}

    @app.get("/calendar/v3/calendars/primary/events")
    def events(request: Request, timeMin: str | None = None, timeMax: str | None = None, singleEvents: bool = False):
        who(request)
        now = clock()
        lo = datetime.fromisoformat(timeMin.replace("Z", "+00:00")) if timeMin else now.replace(hour=0, minute=0, second=0, microsecond=0)
        hi = datetime.fromisoformat(timeMax.replace("Z", "+00:00")) if timeMax else lo + timedelta(days=7)
        return {"kind": "calendar#events", "items": fixture_events(now, lo, hi)}

    return app


app = create_app()
