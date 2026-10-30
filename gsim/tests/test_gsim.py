from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from fleck_gsim.app import create_app

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 10, 30, 10, 20, tzinfo=IST)


def _client(now=NOW):
    return TestClient(create_app(lambda: now), follow_redirects=False)


def test_gsim_full_oauth_flow():
    c = _client()
    page = c.get("/o/oauth2/v2/auth", params={"redirect_uri": "http://app/cb", "state": "s1"}).text
    assert all(f"<button type='button'>{e}</button>" in page for e in
               ("rupak@northbeam.test", "priya@northbeam.test", "outsider@example.test"))
    consent = c.get("/o/oauth2/v2/auth", params={"redirect_uri": "http://app/cb", "state": "s1", "email": "priya@northbeam.test"}).text
    assert "<button type='submit'>Allow</button>" in consent
    r = c.get("/o/oauth2/v2/allow", params={"redirect_uri": "http://app/cb", "state": "s1", "email": "priya@northbeam.test"})
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert r.status_code == 302 and q["state"] == ["s1"]
    tok = c.post("/token", data={"grant_type": "authorization_code", "code": q["code"][0]}).json()
    info = c.get("/oauth2/v3/userinfo", headers={"Authorization": f"Bearer {tok['access_token']}"}).json()
    assert info["email"] == "priya@northbeam.test" and info["name"] == "Priya S"
    again = c.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]}).json()
    assert again["access_token"] == tok["access_token"] and "refresh_token" not in again
    assert c.post("/token", data={"grant_type": "authorization_code", "code": "nope"}).status_code == 400
    assert c.get("/oauth2/v3/userinfo").status_code == 401


def _events(c, lo, hi):
    h = {"Authorization": "Bearer gsim-at-rupak@northbeam.test"}
    r = c.get("/calendar/v3/calendars/primary/events", headers=h,
              params={"timeMin": lo.isoformat(), "timeMax": hi.isoformat(), "singleEvents": "true"})
    return {e["summary"] + ("" if "Standup" != e["summary"] else e["id"][-9:]): e for e in r.json()["items"]}


def test_gsim_events_relative_to_now():
    c = _client()
    midnight = NOW.replace(hour=0, minute=0)
    ev = _events(c, midnight, midnight + timedelta(days=2))
    demo = ev["Presales: demo prospect"]
    assert demo["start"]["dateTime"] == "2026-10-30T11:00:00+05:30" and demo["end"]["dateTime"] == "2026-10-30T11:45:00+05:30"
    assert {a["email"] for a in demo["attendees"]} >= {"dana.whitfield@harbourline.test", "marcus.lee@harbourline.test", "priya@northbeam.test"}
    assert demo["hangoutLink"].startswith("https://meet.google.com/") and "iManage" in demo["description"]
    assert ev["Pricing review (internal)"]["start"]["dateTime"] == "2026-10-30T14:00:00+05:30"  # demo + 3 h
    assert "hangoutLink" not in ev["Pricing review (internal)"]
    assert ev["Discovery: Orion Freight"]["start"]["dateTime"].startswith("2026-10-31")
    assert "date" in ev["Offsite"]["start"]  # all-day, for the app to skip
    assert ev["Vendor sync"]["attendees"][0]["responseStatus"] == "declined"
    standups = [e for e in ev.values() if e["summary"] == "Standup"]
    assert len(standups) == 2 and "zoom.us" in standups[0]["location"]
    assert {e["start"]["dateTime"][11:] for e in standups} == {"09:30:00+05:30"}  # 09:30 local, clear of the demo
    # the clock moves the fixtures
    later = _events(_client(NOW + timedelta(days=1, hours=2)), midnight + timedelta(days=1), midnight + timedelta(days=2))
    assert later["Presales: demo prospect"]["start"]["dateTime"] == "2026-10-31T13:00:00+05:30"


def test_gsim_late_evening_stays_today():
    midnight = NOW.replace(hour=0, minute=0)
    for hh, mm in ((22, 40), (23, 30)):
        now = NOW.replace(hour=hh, minute=mm)
        ev = _events(_client(now), midnight, midnight + timedelta(days=1))
        assert ev["Presales: demo prospect"]["start"]["dateTime"] == "2026-10-30T23:00:00+05:30"
        assert "Pricing review (internal)" not in ev  # would cross midnight, so it is dropped


def test_gsim_allow_appends_to_existing_query():
    r = _client().get("/o/oauth2/v2/allow", params={"redirect_uri": "http://app/cb?x=1", "state": "s", "email": "rupak@northbeam.test"})
    assert r.headers["location"].startswith("http://app/cb?x=1&code=")


def test_gsim_demo_time_fixed_at_start():
    t = [NOW]
    c = TestClient(create_app(lambda: t[0]))
    midnight = NOW.replace(hour=0, minute=0)
    first = _events(c, midnight, midnight + timedelta(days=1))["Presales: demo prospect"]["start"]["dateTime"]
    t[0] = NOW + timedelta(hours=1)  # an hour later, same process
    assert _events(c, midnight, midnight + timedelta(days=1))["Presales: demo prospect"]["start"]["dateTime"] == first \
        == "2026-10-30T11:00:00+05:30"


def test_gsim_demo_start_env(monkeypatch):
    monkeypatch.setenv("GSIM_DEMO_START", "16:30")
    midnight = NOW.replace(hour=0, minute=0)
    ev = _events(_client(), midnight, midnight + timedelta(days=1))
    assert ev["Presales: demo prospect"]["start"]["dateTime"] == "2026-10-30T16:30:00+05:30"
    assert ev["Pricing review (internal)"]["start"]["dateTime"] == "2026-10-30T19:30:00+05:30"  # still demo + 3 h
