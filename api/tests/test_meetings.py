from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update

from fleck_api.models import Assist, AudioChunk, CalendarEvent, Job, LlmCall, Meeting, PairingCode, User
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
    assert set(d) == {"meeting", "segments", "assists", "notes", "summary", "brief", "cost_usd"}
    assert d["notes"] == "hello" and d["assists"][0]["used_by"] == "manual" and d["segments"] == []
    assert [x["id"] for x in client.get("/api/meetings").json()] == [mid]
    assert client.get("/api/meetings/9999").status_code == 404


def test_audio_put_is_for_the_recording_device_only(env):
    client, gs, _ = env
    mine = pair(env)
    mid = client.post("/api/meetings", json={"title": "Mine"}, headers=mine).json()["id"]
    assert client.put(f"/api/meetings/{mid}/audio/0", content=WAV).status_code == 401  # a cookie is not enough
    client.cookies.clear()
    _sign_in(client, gs, "priya@northbeam.test")
    code = client.post("/api/devices/pairing-code").json()["code"]
    token = client.post("/api/devices/pair", json={"code": code, "name": "Priya's Mac"}).json()["device_token"]
    r = client.put(f"/api/meetings/{mid}/audio/0", content=WAV, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_the_helper_reads_me_events_meetings_and_stops_with_its_device_token(env):
    """The menu bar's Today list, "Connected as", resume and Stop recording (Task 18)."""
    client, _, make = env
    auth = pair(env)
    client.cookies.clear()
    assert client.get("/api/me", headers=auth).json()["email"] == "rupak@northbeam.test"
    assert client.get("/api/events?range=today", headers=auth).status_code == 200
    m = client.post("/api/meetings", json={"title": "Ad hoc"}, headers=auth).json()
    assert client.get("/api/meetings", headers=auth).status_code == 401  # the meeting list stays a signed-in view
    with make() as s:  # another user's recording
        other = s.scalar(select(User).where(User.email != "rupak@northbeam.test")) or User(email="dana@northbeam.test", name="Dana")
        s.add(other)
        s.flush()
        s.add(theirs := Meeting(title="Theirs", recorded_by=other.id))
        s.commit()
        theirs_id = theirs.id
    assert client.post(f"/api/meetings/{theirs_id}/stop", headers=auth).status_code == 403
    assert client.post(f"/api/meetings/{m['id']}/stop", headers=auth).status_code == 204
    assert client.get(f"/api/meetings/{m['id']}", headers=auth).json()["meeting"]["status"] == "processing"
    for path in ("/api/me", "/api/events"):
        assert client.get(path, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_detail_has_claude_cost_total_and_recorder(env):
    client, _, make = env
    pair(env)
    mid = client.post("/api/meetings", json={"title": "Costly"}).json()["id"]
    with make() as s:
        s.add_all([LlmCall(purpose="summary", model="m", meeting_id=mid, cost_usd=0.05), LlmCall(purpose="chat", model="m", meeting_id=mid, cost_usd=0.012),
                   LlmCall(purpose="other", model="m", meeting_id=mid), LlmCall(purpose="elsewhere", model="m", cost_usd=9)])
        s.commit()
    d = client.get(f"/api/meetings/{mid}").json()
    assert d["cost_usd"] == 0.062 and d["meeting"]["recorded_by_name"]
    empty = client.post("/api/meetings", json={"title": "Free"}).json()["id"]
    assert client.get(f"/api/meetings/{empty}").json()["cost_usd"] == 0


def test_meetings_list_newest_first(env):
    client, _, make = env
    pair(env)
    a = client.post("/api/meetings", json={"title": "Older"}).json()["id"]
    b = client.post("/api/meetings", json={"title": "Newer"}).json()["id"]
    with make() as s:
        s.get(Meeting, a).started_at = datetime.now(timezone.utc) - timedelta(days=2)
        s.commit()
    assert [m["id"] for m in client.get("/api/meetings").json()] == [b, a]


def test_retry_requeues_only_a_failed_meeting_once(env):
    client, _, make = env
    pair(env)
    mid = client.post("/api/meetings", json={"title": "Broke"}).json()["id"]
    assert client.post(f"/api/meetings/{mid}/retry").status_code == 204  # still recording: nothing to retry
    with make() as s:
        assert s.get(Meeting, mid).status == "recording" and s.scalar(select(func.count()).select_from(Job).where(Job.kind == "process_meeting")) == 0
        s.get(Meeting, mid).status, s.get(Meeting, mid).failure_reason = "failed", "boom"
        s.commit()
    for _ in range(2):
        assert client.post(f"/api/meetings/{mid}/retry").status_code == 204
    with make() as s:
        m = s.get(Meeting, mid)
        assert m.status == "processing" and m.failure_reason is None
        assert s.scalar(select(func.count()).select_from(Job).where(Job.kind == "process_meeting")) == 1
    assert client.post("/api/meetings/9999/retry").status_code == 404


def test_retry_needs_a_signed_in_user(env):
    client, _, _ = env
    mid = client.post("/api/meetings", json={"title": "x"}, headers=pair(env)).json()["id"]
    client.cookies.clear()
    assert client.post(f"/api/meetings/{mid}/retry").status_code == 401
