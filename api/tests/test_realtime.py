import time
from contextlib import contextmanager

import pytest
from sqlalchemy import func, select
from starlette.websockets import WebSocketDisconnect

from fleck_api.models import Assist, CalendarEvent, LlmCall, Segment
from test_auth import env  # noqa: F401  (env is a fixture)
from test_meetings import pair


@pytest.fixture(autouse=True)
def one_loop(env):
    """`with client` shares one event loop across websockets, as under uvicorn; the hub sends from one to another."""
    with env[0]:
        yield


@contextmanager
def browser_of(client, mid):
    """A browser socket, past its meeting_state greeting (so it is subscribed)."""
    with client.websocket_connect(f"/api/ws/meetings/{mid}") as ws:
        assert ws.receive_json()["type"] == "meeting_state"
        yield ws


@contextmanager
def helper_of(client, auth):
    """The helper socket, once the api lists the device as connected."""
    with client.websocket_connect("/api/ws/helper", headers=dict(auth)) as ws:
        while not (r := client.get("/api/devices")).json()[0].get("connected"):
            assert r.status_code == 200, r.text
            time.sleep(0.02)
        yield ws


def seg(mid, n, text="hello", final=True):
    return {"type": "segment", "meeting_id": str(mid), "segment_id": f"c-{n}", "stream": "client", "start_ms": n * 1000,
            "end_ms": n * 1000 + 900, "text": text, "final": final, "gate": "client"}


def assist(mid, phase, **kw):
    return {"type": "assist", "meeting_id": str(mid), "assist_id": "a1", "segment_id": "c-1", "phase": phase,
            "question": "Can you?", **kw}


def new_meeting(client, auth):
    return client.post("/api/meetings", json={"title": "Call"}, headers=auth).json()["id"]


def test_segment_dedup_by_id(env):
    client, _, make = env
    auth = pair(env)
    mid = new_meeting(client, auth)
    with browser_of(client, mid) as browser, helper_of(client, auth) as helper:
        helper.send_json(seg(mid, 1))
        helper.send_json(seg(mid, 1))  # resent after a reconnect
        helper.send_json(seg(mid, 2, final=False))  # a partial: relayed, never stored
        helper.send_json(seg(mid, 3))
        got = [browser.receive_json()["segment_id"] for _ in range(3)]
    assert got == ["c-1", "c-2", "c-3"]  # the duplicate was not relayed
    with make() as s:
        assert s.scalar(select(func.count()).select_from(Segment)) == 2


def test_assist_stored_once_on_done(env):
    client, _, make = env
    auth = pair(env)
    mid = new_meeting(client, auth)
    llm = {"purpose": "live_fallback", "model": "m", "request": "Can you?", "response": "Yes.", "input_tokens": 1,
           "output_tokens": 2, "latency_ms": 5, "status": "ok"}
    with browser_of(client, mid) as browser, helper_of(client, auth) as helper:
        for e in (assist(mid, "thinking"), assist(mid, "sentence", sentence_index=0, text="Yes.", kind="general"),
                  assist(mid, "done", kind="general", text="Yes.", llm=[llm], full_ms=40),
                  assist(mid, "done", kind="general", llm=[llm], full_ms=40)):
            helper.send_json(e)
        helper.send_json(seg(mid, 9))
        assert [browser.receive_json()["phase"] for _ in range(3)] == ["thinking", "sentence", "done"]
        assert browser.receive_json()["segment_id"] == "c-9"  # the second done was ignored
    with make() as s:
        a = s.scalar(select(Assist))
        assert (a.answer, a.kind, a.full_ms) == ("Yes.", "general", 40)
        assert s.scalar(select(func.count()).select_from(LlmCall)) == 1


def test_resent_answer_does_not_reopen_a_finished_card(env):
    client, _, make = env
    auth = pair(env)
    mid = new_meeting(client, auth)
    llm = {"purpose": "live_fallback", "model": "m", "request": "Can you?", "response": "Yes.", "input_tokens": 1,
           "output_tokens": 2, "latency_ms": 5, "status": "ok"}
    answer = [assist(mid, "thinking"), assist(mid, "sentence", sentence_index=0, text="Yes.", kind="general"),
              assist(mid, "done", kind="general", text="Yes.", llm=[llm])]
    with browser_of(client, mid) as browser, helper_of(client, auth) as helper:
        for e in answer + answer:  # the acks were lost: everything comes again
            helper.send_json(e)
        helper.send_json(seg(mid, 9))
        assert [browser.receive_json()["phase"] for _ in range(3)] == ["thinking", "sentence", "done"]
        assert browser.receive_json()["segment_id"] == "c-9"  # nothing of the resend was relayed
    with make() as s:
        assert s.scalar(select(func.count()).select_from(LlmCall)) == 1
        assert s.scalar(select(Assist.answer)) == "Yes."


def test_event_that_fails_to_store_is_acked_and_the_socket_stays(env):
    client, _, make = env
    auth = pair(env)
    mid = new_meeting(client, auth)
    with helper_of(client, auth) as helper:
        helper.send_json(seg(mid, 1, text="a\x00b"))  # NUL: postgres refuses it
        assert helper.receive_json() == {"type": "ack"}
        helper.send_json(seg(mid, 2))
        assert helper.receive_json() == {"type": "ack"}
    with make() as s:
        assert s.scalar(select(Segment.segment_id)) == "c-2"


def test_browser_ws_receives_helper_events(env):
    client, _, _ = env
    auth = pair(env)
    mid, other = new_meeting(client, auth), new_meeting(client, auth)
    with browser_of(client, mid) as browser, helper_of(client, auth) as helper:
        helper.send_json(seg(other, 1))  # another meeting: not for this browser
        helper.send_json(assist(mid, "delta", sentence_index=0, text="Ye"))
        assert browser.receive_json()["text"] == "Ye"


def test_record_from_web_sends_start_to_helper(env):
    client, _, make = env
    auth = pair(env)
    with make() as s:
        event_id = s.scalars(select(CalendarEvent)).first().id
    with helper_of(client, auth) as helper:
        r = client.post("/api/meetings", json={"event_id": event_id, "start_helper": True})
        assert r.status_code == 200
        mid = r.json()["id"]
        msg = helper.receive_json()
        assert msg["type"] == "start" and msg["meeting_id"] == str(mid) and msg["event_id"] == event_id
        assert msg["client_name"] is None and isinstance(msg["people"], list)
        assert client.post(f"/api/meetings/{mid}/stop").status_code == 204
        assert helper.receive_json() == {"type": "stop", "meeting_id": str(mid)}


def test_record_without_helper_returns_409(env):
    client, _, make = env
    pair(env)
    with make() as s:
        event_id = s.scalars(select(CalendarEvent)).first().id
    r = client.post("/api/meetings", json={"event_id": event_id, "start_helper": True})
    assert r.status_code == 409 and "Desktop app not connected" in r.json()["detail"]
    assert client.get("/api/meetings").json() == []  # no half-created meeting


def test_other_session_without_cookie_rejected(env):
    client, _, _ = env
    auth = pair(env)
    mid = new_meeting(client, auth)
    sign_in_cookie = dict(client.cookies)
    client.cookies.clear()
    assert client.get(f"/api/meetings/{mid}").status_code == 401
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/api/ws/meetings/{mid}"):
            pass
    with pytest.raises(WebSocketDisconnect):  # signed in, but from another site
        client.cookies.update(sign_in_cookie)
        with client.websocket_connect(f"/api/ws/meetings/{mid}", headers={"origin": "https://evil.test"}):
            pass
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/ws/helper", headers={"Authorization": "Bearer nope"}):
            pass


def test_email_only_attendee_is_titlecased_for_redaction(env):
    from fleck_core.fallback import question_only
    client, _, make = env
    pair(env)
    with make() as s:
        e = s.scalars(select(CalendarEvent)).first()
        e.attendees = [{"email": "anna.lee@harbourline.test", "name": None}]
        s.commit()
        event_id = e.id
    people = client.post("/api/meetings", json={"event_id": event_id}).json()["people"]
    assert people == ["Anna Lee"]
    assert "Anna" not in question_only("Can Anna Lee join the next call?", [], people)
