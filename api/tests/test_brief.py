import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from fleck_api import brief, claude
from fleck_api.jobs import run_once
from fleck_api.models import Brief, BriefItem, CalendarEvent, Client, Document, Job, LlmCall, Meeting, User
from fleck_core.answer import OllamaClient
from fleck_core.types import Passage
from test_auth import env  # noqa: F401  (fixture)
from test_calendar import _signed_in
from test_claude_trace import _real_key
from test_meetings import pair

RATE = Passage(n=1, doc_id="d1", chunk_id="d1-0", title="Rate card FY2026", client=None, kind="firm",
               text="Rate card FY2026 · Senior engineer\nSenior engineer: $5,200 a month offshore, $13,500 a month onsite.")
RATE_Q = "What do your people cost?"
SECOND_Q = "Can you sign an exclusivity clause?"


class FakeRetriever:
    def __init__(self):
        self.calls = []
        self.embedder = self

    def embed_query(self, text):
        return [1.0, 0.0] if "cost" in text else [0.0, 1.0]

    def search(self, question, limit=3, client_slug=None, vector=None):
        self.calls.append((question, client_slug))
        return [RATE] if "cost" in question or client_slug else []


class FakeOllama:
    def warm(self, model):
        pass

    def stream(self, messages, model, **kw):
        yield "Senior engineers cost $5,200 a month offshore [1]." if RATE_Q in messages[-1]["content"] else "NONE"


class Sent(list):
    """The bodies api.claude sent, with the API's reply."""

    def __init__(self, monkeypatch, questions):
        super().__init__()
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
        monkeypatch.setattr(claude, "_send", lambda body: self.append(body) or {
            "model": "claude-sonnet-5-5", "stop_reason": "tool_use", "usage": {"input_tokens": 1000, "output_tokens": 200},
            "content": [{"type": "tool_use", "id": "t", "name": "propose_questions", "input": {"questions": questions}}]})


@pytest.fixture
def stubbed(monkeypatch):
    r = FakeRetriever()
    monkeypatch.setattr(brief, "_deps", lambda: (r, FakeOllama()))
    return r


def make_event(env, title="Presales: Harbourline Legal", description=None, client=True, attendees=(), hours=2.0):
    uid = _signed_in(env)
    with env[2]() as s:
        c = Client(name="Harbourline Legal", slug="harbourline-legal") if client else None
        s.add_all([c] if c else [])
        s.flush()
        start = datetime.now(timezone.utc) + timedelta(hours=hours)
        e = CalendarEvent(user_id=uid, google_event_id="g1", title=title, description=description, start_at=start,
                          end_at=start + timedelta(minutes=45), attendees=list(attendees), client_id=c.id if c else None)
        s.add(e)
        if c:
            s.add(Document(client_id=c.id, kind="client", title="Harbourline NDA playbook", filename="p.pdf", blob_key="k", status="indexed"))
        s.commit()
        return e.id


def build(env, event_id):
    with env[2]() as s:
        brief.build_brief(s, {"event_id": event_id})


def items(env, event_id):
    with env[2]() as s:
        return list(s.scalars(select(BriefItem).join(Brief).where(Brief.event_id == event_id).order_by(BriefItem.id)))


QS = [{"topic": "commercial", "question": RATE_Q}, {"topic": "risk", "question": SECOND_Q}]


def test_brief_items_validated_and_cited(env, stubbed, monkeypatch):
    Sent(monkeypatch, QS)
    eid = make_event(env)
    build(env, eid)
    rate, thin = items(env, eid)
    assert rate.topic == "commercial" and rate.kind == "library" and "$5,200" in rate.answer
    assert [c["title"] for c in rate.citations] == ["Rate card FY2026"] and rate.question_vector == [1.0, 0.0]
    assert thin.kind == "not_in_material" and thin.answer == ""
    got = env[0].get(f"/api/events/{eid}/brief").json()
    assert got["status"] == "ready" and "1 library document" in got["context"]


def test_brief_figure_not_in_passage_is_rejected(env, stubbed, monkeypatch):
    Sent(monkeypatch, QS[:1])
    monkeypatch.setattr(FakeOllama, "stream", lambda self, m, model, **kw: (t for t in ["Senior engineers cost $9,999 a month [1]."]))
    eid = make_event(env)
    build(env, eid)
    [only] = items(env, eid)
    assert only.kind == "not_in_material" and "9,999" not in only.answer


def test_brief_without_client(env, stubbed, monkeypatch):
    sent = Sent(monkeypatch, QS)
    eid = make_event(env, title="Intro call", description="Bring the Orion deck", client=False)
    build(env, eid)
    assert len(items(env, eid)) == 2
    prompt = sent[0]["messages"][0]["content"]
    assert "Intro call" in prompt and "Orion" not in prompt  # the title only
    assert all(slug is None for _, slug in stubbed.calls)
    assert "No client" in env[0].get(f"/api/events/{eid}/brief").json()["context"]


def test_thin_items_listed_separately(env, stubbed, monkeypatch):
    Sent(monkeypatch, QS)
    eid = make_event(env)
    build(env, eid)
    got = env[0].get(f"/api/events/{eid}/brief").json()
    assert [(i["question"], i["kind"]) for i in got["items"]] == [(RATE_Q, "library"), (SECOND_Q, "not_in_material")]


def test_brief_names_never_reach_claude(env, stubbed, monkeypatch):
    sent = Sent(monkeypatch, QS)
    people = [{"email": "priya@x.test", "name": "Priya Shah"}, {"email": "marcus.webb@x.test", "name": None}]
    eid = make_event(env, title="Harbourline Legal x Northbeam — Priya Shah",
                     description="Marcus Webb leads the NDA review for Harbourline. Priya's team joins.", attendees=people)
    build(env, eid)
    with env[2]() as s:
        row = s.scalar(select(LlmCall))
    for text in (json.dumps(sent[0]), json.dumps(row.request)):  # what went out, and what the trace kept
        assert not any(n in text for n in ("Priya", "Shah", "Marcus", "Webb", "Harbourline Legal", "sk-test-key"))
    assert "someone" in sent[0]["messages"][0]["content"] and "a client" in sent[0]["messages"][0]["content"]


def test_brief_cost_lands_on_its_event_and_meeting(env, stubbed, monkeypatch):
    Sent(monkeypatch, QS)
    monkeypatch.setenv("FLECK_PRICES", '{"claude-sonnet-5-5": {"input": 2, "output": 10}}')
    eid = make_event(env)
    build(env, eid)
    with env[2]() as s:
        row = s.scalar(select(LlmCall))
        assert row.purpose == "brief_questions" and row.event_id == eid and row.cost_usd == pytest.approx(0.004)
        m = Meeting(title="x", event_id=eid)
        s.add(m)
        s.commit()
        mid = m.id
    pair(env)
    assert env[0].get(f"/api/meetings/{mid}").json()["cost_usd"] == pytest.approx(0.004)


def test_meeting_feed_has_vectors_and_skips_thin_items(env, stubbed, monkeypatch):
    Sent(monkeypatch, QS)
    eid = make_event(env)
    build(env, eid)
    with env[2]() as s:
        m = Meeting(title="x", event_id=eid)
        s.add(m)
        s.commit()
        mid = m.id
    auth = pair(env)
    [it] = env[0].get(f"/api/meetings/{mid}/brief", headers=auth).json()["items"]
    assert it["question"] == RATE_Q and it["question_vector"] == [1.0, 0.0] and it["kind"] == "library" and it["citations"]
    with env[2]() as s:
        m2 = Meeting(title="y")
        s.add(m2)
        s.commit()
        assert env[0].get(f"/api/meetings/{m2.id}/brief", headers=auth).json() == {"items": []}


def test_post_refreshes_and_queues_once(env):
    eid = make_event(env)
    client = env[0]
    assert client.get(f"/api/events/{eid}/brief").json()["status"] == "none"
    for _ in range(2):
        assert client.post(f"/api/events/{eid}/brief").json()["status"] == "queued"
    with env[2]() as s:
        assert len(list(s.scalars(select(Job).where(Job.kind == "build_brief")))) == 1


def test_client_link_queues_a_build(env):
    eid = make_event(env, client=False)
    with env[2]() as s:
        cid = Client(name="Orion", slug="orion")
        s.add(cid)
        s.commit()
        cid = cid.id
    env[0].put(f"/api/events/{eid}/client", json={"client_id": cid})
    env[0].put(f"/api/events/{eid}/client", json={"client_id": cid})
    with env[2]() as s:
        [j] = s.scalars(select(Job).where(Job.kind == "build_brief"))
        assert j.payload == {"event_id": eid}


def test_brief_scheduled_30_min_before_start_once(env):
    eid = make_event(env, hours=3)
    with env[2]() as s:
        uid = s.scalar(select(User.id))
        for _ in range(3):  # re-syncs
            brief.schedule_briefs(s, uid, datetime.now(timezone.utc))
        [j] = s.scalars(select(Job).where(Job.kind == "build_brief"))
        e = s.get(CalendarEvent, eid)
        assert j.run_after == e.start_at - timedelta(minutes=30) and j.payload["event_id"] == eid
        e.start_at += timedelta(hours=1)  # moved: the old job goes, a new one is queued
        s.commit()
        brief.schedule_briefs(s, uid, datetime.now(timezone.utc))
        [j2] = s.scalars(select(Job).where(Job.kind == "build_brief"))
        assert j2.id != j.id and j2.run_after == e.start_at - timedelta(minutes=30)
        assert run_once(s) is False  # not due yet
        e.start_at -= timedelta(hours=3)  # starts within 30 min: nothing new is scheduled
        s.commit()
        brief.schedule_briefs(s, uid, datetime.now(timezone.utc))
        assert len(list(s.scalars(select(Job).where(Job.kind == "build_brief", Job.status == "queued")))) == 1


def test_failed_build_marks_the_brief(env, monkeypatch):
    eid = make_event(env)
    with env[2]() as s:
        brief.request_build(s, eid)
        brief.mark_failed(s, {"event_id": eid}, "RuntimeError: ANTHROPIC_API_KEY is empty")
        b = s.scalar(select(Brief))
        assert b.status == "failed" and "ANTHROPIC" in b.error


@pytest.mark.claude
@pytest.mark.skipif(not (_KEY := _real_key()), reason="ANTHROPIC_API_KEY is empty")
def test_real_sonnet_proposes_questions(make_session, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", _KEY)
    with make_session() as s:
        got = brief.propose_questions(s, "Presales: a client’s NDA review", "someone wants to automate first-pass review", ["Rate card FY2026"], [], None)
    assert 1 <= len(got) <= 8 and all(q["topic"] in brief.TOPICS and q["question"].endswith("?") for q in got)


@pytest.mark.ollama
def test_real_qwen_answers_brief_questions():
    r = FakeRetriever()
    out = brief.answer_question(RATE_Q, r, OllamaClient(), ["Harbourline Legal"])
    assert out["kind"] == "library" and "5,200" in out["answer"]
    assert brief.answer_question(SECOND_Q, r, OllamaClient(), [])["kind"] == "not_in_material"
