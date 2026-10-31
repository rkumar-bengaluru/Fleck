from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update

from fleck_api.models import Assist, AudioChunk, CalendarEvent, Meeting, PairingCode
from test_auth import _sign_in, env  # noqa: F401  (env is a fixture)

WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 24


def pair(env, name="Rupak's Mac"):
    """Sign in, pair a device; returns its Authorization header. The cookie jar is left signed in."""
    client, gs, _ = env
    _sign_in(client, gs, "rupak@northbeam.test")
    code = client.post("/api/devices/pairing-code").json()["code"]
    token = client.post("/api/devices/pair", json={"code": code, "name": name}).json()["device_token"]
    return {"Authorization": f"Bearer {token}"}


def test_pairing_code_single_use_and_expiry(env):
    client, gs, make = env
    assert client.post("/api/devices/pairing-code").status_code == 401
    _sign_in(client, gs, "rupak@northbeam.test")
    code = client.post("/api/devices/pairing-code").json()["code"]
    assert len(code) == 9 and code[4] == "-" and code[:4].isalpha() and code[5:].isdigit()
    client.cookies.clear()  # the helper has no session
    r = client.post("/api/devices/pair", json={"code": code, "name": "Mac"})
    assert r.status_code == 200 and r.json()["device_token"]
    assert client.post("/api/devices/pair", json={"code": code, "name": "Mac"}).status_code == 400  # single use
    _sign_in(client, gs, "rupak@northbeam.test")
    old = client.post("/api/devices/pairing-code").json()["code"]
    with make() as s:
        s.execute(update(PairingCode).where(PairingCode.code == old).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        s.commit()
    assert client.post("/api/devices/pair", json={"code": old, "name": "Mac"}).status_code == 400
    [d] = client.get("/api/devices").json()
    assert d["name"] == "Mac" and "token" not in str(d)


def test_device_token_creates_meeting_and_audio_put_is_idempotent(env):
    client, _, make = env
    auth = pair(env)
    client.cookies.clear()
    assert client.post("/api/meetings", json={"title": "Ad hoc"}, headers={"Authorization": "Bearer nope"}).status_code == 401
    m = client.post("/api/meetings", json={"title": "Ad hoc"}, headers=auth).json()
    assert m["status"] == "recording" and m["title"] == "Ad hoc"
    for _ in range(2):
        assert client.put(f"/api/meetings/{m['id']}/audio/3", content=WAV, headers=auth).status_code == 204
    with make() as s:
        assert s.scalar(select(func.count()).select_from(AudioChunk).where(AudioChunk.meeting_id == m["id"], AudioChunk.seq == 3)) == 1


def test_meeting_detail_notes_client_and_assist_used(env):
    client, _, make = env
    pair(env)
    ev = client.get("/api/meetings").json()
    assert ev == []
    with make() as s:
        e = s.scalars(select(CalendarEvent)).first()
        event_id, attendees = e.id, e.attendees
    m = client.post("/api/meetings", json={"event_id": event_id}).json()
    assert m["event_id"] == event_id and m["title"] and m["attendees"] == attendees
    mid = m["id"]
    assert client.put(f"/api/meetings/{mid}/notes", json={"text": "hello"}).status_code == 204
    assert client.put(f"/api/meetings/{mid}/client", json={"client_id": None}).status_code == 204
    with make() as s:
        s.add(Assist(meeting_id=mid, assist_id="a1", question="Q?", answer="A", kind="library"))
        s.commit()
    r = client.patch("/api/assists/a1", json={"used": True})
    assert r.status_code == 200 and r.json()["used"] is True and r.json()["used_by"] == "manual"
    d = client.get(f"/api/meetings/{mid}").json()
    assert set(d) == {"meeting", "segments", "assists", "notes", "summary", "brief"}
    assert d["notes"] == "hello" and d["assists"][0]["used_by"] == "manual" and d["segments"] == []
    assert [x["id"] for x in client.get("/api/meetings").json()] == [mid]
    assert client.get("/api/meetings/9999").status_code == 404
