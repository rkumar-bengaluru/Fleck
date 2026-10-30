import json
import logging
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from fleck_api import auth, calendar_sync
from fleck_api.calendar_sync import meeting_link, schedule_syncs, sync_user
from fleck_api.jobs import enqueue, run_once
from fleck_api.models import Brief, CalendarEvent, Client, Device, GoogleToken, Job, Meeting, User
from fleck_gsim.app import create_app as gsim_app
from test_auth import _sign_in, env  # noqa: F401  (env is a fixture)


def _tokens(make, **changes):
    with make() as s:
        row = s.scalar(select(GoogleToken))
        blob = json.loads(auth._fernet().decrypt(row.token_enc.encode())) | changes
        row.token_enc = auth._fernet().encrypt(json.dumps(blob).encode()).decode()
        s.commit()
        return blob


def _signed_in(env):
    client, gs, make = env
    _sign_in(client, gs, "rupak@northbeam.test")
    with make() as s:
        s.execute(update(Job).values(status="done"))  # the sign-in sync: tests run sync_user themselves
        s.commit()
        return s.scalar(select(User.id))


def _events(make):
    with make() as s:
        return {e.google_event_id: e for e in s.scalars(select(CalendarEvent))}


def test_sync_skips_all_day_and_declined(env):
    uid = _signed_in(env)
    with env[2]() as s:
        sync_user(s, uid)
    titles = {e.title for e in _events(env[2]).values()}
    assert "Presales: demo prospect" in titles and "Standup" in titles and "Discovery: Orion Freight" in titles
    assert "Offsite" not in titles and "Vendor sync" not in titles


def test_sync_extracts_meet_and_zoom_links(env):
    uid = _signed_in(env)
    with env[2]() as s:
        sync_user(s, uid)
    evs = _events(env[2]).values()
    assert next(e for e in evs if e.title == "Presales: demo prospect").meeting_link == "https://meet.google.com/abc-defg-hij"
    assert {e.meeting_link for e in evs if e.title == "Standup"} == {"https://zoom.us/j/5550001234"}
    # conferenceData and links in the description, as real Google sends them
    assert meeting_link({"conferenceData": {"entryPoints": [{"entryPointType": "phone", "uri": "tel:+1"},
                                                            {"entryPointType": "video", "uri": "https://acme.zoom.us/j/9"}]}}) == "https://acme.zoom.us/j/9"
    assert meeting_link({"description": "Join: <a href=\"https://teams.microsoft.com/l/meetup-join/19%3a\">here</a>"}) \
        == "https://teams.microsoft.com/l/meetup-join/19%3a"
    assert meeting_link({"location": "Room 4", "description": "See https://example.com then https://acme.webex.com/meet/x."}) \
        == "https://acme.webex.com/meet/x"
    assert meeting_link({"location": "Room 4", "description": "no link"}) is None


def test_sync_is_idempotent(env):
    uid = _signed_in(env)
    make = env[2]
    with make() as s:
        sync_user(s, uid)
        c = Client(name="Harbourline Legal", slug="harbourline-legal")
        s.add(c)
        s.flush()
        s.execute(update(CalendarEvent).where(CalendarEvent.google_event_id == "evt-demo").values(client_id=c.id))
        s.commit()
    first = {k: (e.title, e.start_at) for k, e in _events(make).items()}
    with make() as s:
        sync_user(s, uid)
    after = _events(make)
    assert {k: (e.title, e.start_at) for k, e in after.items()} == first
    assert after["evt-demo"].client_id is not None  # a sync never drops the client link
    assert not any(e.cancelled for e in after.values())


def test_sync_keeps_used_events_gone_from_google_as_cancelled(env):
    uid = _signed_in(env)
    make = env[2]
    now = datetime.now(timezone.utc)
    with make() as s:
        c = Client(name="Harbourline Legal", slug="harbourline-legal")
        s.add(c)
        s.flush()
        ev = {g: CalendarEvent(user_id=uid, google_event_id=g, title=g, start_at=now + timedelta(hours=2),
                               end_at=now + timedelta(hours=3), attendees=[], client_id=c.id if g == "linked" else None)
              for g in ("bare", "linked", "briefed", "recorded")}
        s.add_all(ev.values())
        s.flush()
        s.add_all([Brief(event_id=ev["briefed"].id), Meeting(event_id=ev["recorded"].id, title="recorded")])
        s.commit()
        sync_user(s, uid)
    after = _events(make)
    assert "bare" not in after
    assert all(after[g].cancelled for g in ("linked", "briefed", "recorded"))
    assert not after["evt-demo"].cancelled
    with make() as s:  # it comes back in Google: no longer cancelled
        s.execute(update(CalendarEvent).where(CalendarEvent.google_event_id == "evt-demo").values(cancelled=True))
        s.commit()
        sync_user(s, uid)
    assert not _events(make)["evt-demo"].cancelled


def _client_api(env):
    client, gs, make = env
    uid = _signed_in(env)
    with make() as s:
        sync_user(s, uid)
    return client, make


def test_set_event_client(env):
    client, make = _client_api(env)
    ev = client.get("/api/events", params={"range": "today"}).json()
    demo = next(e for e in ev if e["title"] == "Presales: demo prospect")
    cid = client.post("/api/clients", json={"name": "Harbourline Legal"}).json()["id"]
    r = client.put(f"/api/events/{demo['id']}/client", json={"client_id": cid})
    assert r.status_code == 200 and r.json()["client"] == {"id": cid, "name": "Harbourline Legal"}
    assert client.get(f"/api/events/{demo['id']}").json()["client"]["name"] == "Harbourline Legal"
    assert client.put(f"/api/events/{demo['id']}/client", json={"client_id": 99999}).status_code == 404
    assert client.put(f"/api/events/{demo['id']}/client", json={"client_id": None}).json()["client"] is None
    with make() as s:  # someone else's event is invisible
        other = User(email="priya@northbeam.test")
        s.add(other)
        s.flush()
        now = datetime.now(timezone.utc)
        e = CalendarEvent(user_id=other.id, google_event_id="x", title="Theirs", start_at=now, end_at=now, attendees=[])
        s.add(e)
        s.commit()
        theirs = e.id
    assert client.get(f"/api/events/{theirs}").status_code == 404
    assert client.put(f"/api/events/{theirs}/client", json={"client_id": cid}).status_code == 404


def test_events_today_is_the_users_local_day(env, monkeypatch):
    client, make = _client_api(env)
    upcoming = client.get("/api/events", params={"range": "upcoming"}).json()
    assert any(e["title"] == "Discovery: Orion Freight" and e["repeats"] is None for e in upcoming)
    assert all(e["title"] != "Presales: demo prospect" for e in upcoming)
    [standup] = [e for e in upcoming if e["title"] == "Standup"]  # the series once, at its next occurrence
    with make() as s:
        first = s.scalar(select(func.min(CalendarEvent.start_at)).where(
            CalendarEvent.title == "Standup", CalendarEvent.start_at >= datetime.fromisoformat(upcoming[0]["start_at"])))
    assert standup["repeats"] == "daily" and datetime.fromisoformat(standup["start_at"]) == first
    with make() as s:
        uid = s.scalar(select(User.id))
        s.add_all([CalendarEvent(user_id=uid, google_event_id=g, title=g, start_at=at, end_at=at + timedelta(minutes=30), attendees=[])
                   for g, at in [("ist-only", datetime(2030, 1, 1, 22, 0, tzinfo=timezone.utc)),  # 03:30 on 2 Jan in Kolkata
                                 ("utc-only", datetime(2030, 1, 2, 20, 0, tzinfo=timezone.utc))]])  # 01:30 on 3 Jan in Kolkata
        s.commit()
    clock = datetime(2030, 1, 2, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(calendar_sync, "_now", lambda: clock)  # 10:00 UTC = 15:30 in Kolkata, both on 2 January
    ist = {e["title"] for e in client.get("/api/events", params={"range": "today", "tz": "Asia/Kolkata"}).json()}
    utc = {e["title"] for e in client.get("/api/events", params={"range": "today", "tz": "UTC"}).json()}
    assert ist == {"ist-only"} and utc == {"utc-only"}
    assert client.get("/api/events", params={"range": "today", "tz": "Mars/Base"}).status_code == 422


def test_expired_token_is_refreshed_and_stored_encrypted(env):
    uid = _signed_in(env)
    make = env[2]
    _tokens(make, access_token="stale", expires_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())
    with make() as s:
        sync_user(s, uid)
        enc = s.scalar(select(GoogleToken.token_enc))
    assert "gsim-at" not in enc
    blob = _tokens(make)
    assert blob["access_token"] == "gsim-at-rupak@northbeam.test" and blob["refresh_token"] == "gsim-rt-rupak@northbeam.test"
    assert datetime.fromisoformat(blob["expires_at"]) > datetime.now(timezone.utc)
    assert "Presales: demo prospect" in {e.title for e in _events(make).values()}


def test_failed_refresh_is_logged_and_skipped(env, caplog):
    uid = _signed_in(env)
    make = env[2]
    _tokens(make, refresh_token="revoked", expires_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat())
    with make() as s:
        enqueue(s, "sync_calendar", {"user_id": uid})
        with caplog.at_level(logging.WARNING, "fleck.calendar"):
            assert run_once(s)
        assert s.scalar(select(Job.status).order_by(Job.id.desc())) == "done"
    assert "refresh failed" in caplog.text and "revoked" not in caplog.text
    assert _events(make) == {}


def test_scheduler_does_not_double_enqueue(env):
    uid = _signed_in(env)
    make = env[2]
    with make() as s:
        assert schedule_syncs(s) == 1
        assert schedule_syncs(s) == 0  # one already queued
        s.execute(update(Job).values(status="running"))
        s.commit()
        assert schedule_syncs(s) == 0  # and one running
        s.execute(update(Job).values(status="done"))
        s.commit()
        assert schedule_syncs(s) == 1
        assert s.scalar(select(Job.payload).where(Job.status == "queued")) == {"user_id": uid}


def test_devices_connected(env):
    client, gs, make = env
    uid = _signed_in(env)
    assert client.get("/api/devices").json() == []
    with make() as s:
        s.add(Device(user_id=uid, name="MacBook", token_hash="h", last_seen=datetime.now(timezone.utc)))
        s.commit()
    [d] = client.get("/api/devices").json()
    assert d["name"] == "MacBook" and d["last_seen"]
