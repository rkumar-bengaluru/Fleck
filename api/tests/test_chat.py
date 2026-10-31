import os
import re
import uuid

import httpx
import pytest
from fleck_core.retrieval import Embedder, ensure_schema, put_passages, weaviate_client
from sqlalchemy import select

from fleck_api import chat, claude, jobs
from fleck_api.models import Assist, Client, Document, Job, LlmCall, Meeting, Note, Segment, Summary
from test_auth import env  # noqa: F401  (fixture)
from test_claude_trace import _real_key
from test_meetings import pair

WEAVIATE = "http://localhost:8080"
VEC = [0.1, 0.2, 0.3, 0.4]


@pytest.fixture
def chatenv(env, monkeypatch):
    suffix = uuid.uuid4().hex[:8]
    monkeypatch.setenv("FLECK_WEAVIATE_MEETING_COLLECTION", f"MeetingTextT{suffix}")
    monkeypatch.setenv("FLECK_WEAVIATE_COLLECTION", f"PassageT{suffix}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    monkeypatch.setattr(Embedder, "embed", lambda self, texts: [VEC] * len(texts))
    pair(env)
    yield env
    for c in (f"MeetingTextT{suffix}", f"PassageT{suffix}"):
        httpx.delete(f"{WEAVIATE}/v1/schema/{c}")


class Sent(list):
    """Stub Claude: cites the first source whose text mentions `cites`, like a model reading the sources."""

    def __init__(self, monkeypatch, cites="iManage"):
        super().__init__()

        def send(body):
            self.append(body)
            prompt = body["messages"][-1]["content"]
            n = re.findall(rf"\[(\d+)\] [^\n]*\n[^\n]*(?:{cites})", prompt)
            return {"model": "claude-sonnet-5-5", "stop_reason": "end_turn", "usage": {"input_tokens": 1000, "output_tokens": 100},
                    "content": [{"type": "text", "text": f"Everything stays inside iManage [{n[0]}]." if n else "I could not find that."}]}
        monkeypatch.setattr(claude, "_send", send)


def ready_meeting(env, title, lines, notes="", summary=None):
    with env[2]() as s:
        m = Meeting(title=title, status="ready")
        s.add(m)
        s.flush()
        s.add_all(Segment(meeting_id=m.id, segment_id=f"s{i}", stream="client" if i % 2 else "you", start_ms=ms, end_ms=ms + 3000, text=t)
                  for i, (ms, t) in enumerate(lines))
        if notes:
            s.add(Note(meeting_id=m.id, text=notes))
        if summary:
            s.add(Summary(meeting_id=m.id, items=summary))
        s.commit()
        mid = m.id
    with env[2]() as s:
        chat.index_meeting(s, {"meeting_id": mid})
    return mid


def ask(env, text, meeting_id=None):
    r = env[0].post("/api/chat", json={"meeting_id": meeting_id, "text": text})
    assert r.status_code == 200, r.text
    return r.json()


HARBOUR = [(10_000, "Thanks for joining"), (751_000, "Everything must stay inside iManage, no copies in outside storage."), (900_000, "Pricing next week")]


def test_meeting_chat_cites_transcript_timestamp(chatenv, monkeypatch):
    sent = Sent(monkeypatch)
    mid = ready_meeting(chatenv, "Harbourline call", HARBOUR)
    ready_meeting(chatenv, "Other call", [(5_000, "iManage is not mentioned in this one, only storage")])
    got = ask(chatenv, "Where can documents live?", mid)
    n = int(re.search(r"\[(\d+)\]", got["text"])[1])  # whichever number the stub saw the iManage line under
    [c] = got["citations"]
    assert c == {"type": "meeting", "n": n, "meeting_id": mid, "start_ms": 751_000, "label": "Harbourline call 12:31", "kind": "transcript"}
    assert "Other call" not in sent[0]["messages"][-1]["content"]  # scoped to this meeting


def test_document_citation_carries_title_and_id(chatenv, monkeypatch):
    Sent(monkeypatch, cites="Rate card")
    mid = ready_meeting(chatenv, "Harbourline call", HARBOUR)
    coll = os.environ["FLECK_WEAVIATE_COLLECTION"]
    with weaviate_client(WEAVIATE) as w:
        ensure_schema(w, coll)
        put_passages(w, [{"text": "Rate card FY2026\nSenior engineer: $5,200 a month", "doc_id": "42", "chunk_id": "42-0", "title": "Rate card FY2026",
                          "client": None, "client_slug": None, "kind": "firm"}], [VEC], coll)
    got = ask(chatenv, "What do senior engineers cost?", mid)
    doc = next(c for c in got["citations"] if c["type"] == "document")
    assert doc["doc_id"] == "42" and doc["label"] == "Rate card FY2026"


def test_cross_meeting_chat_spans_meetings(chatenv, monkeypatch):
    sent = Sent(monkeypatch, cites="documents")
    ready_meeting(chatenv, "Call A", [(60_000, "Our documents must stay in the client's tenant")])
    ready_meeting(chatenv, "Call B", [(120_000, "Their documents live in SharePoint")], notes="SharePoint for documents")
    ask(chatenv, "Where do documents live?", None)
    prompt = sent[0]["messages"][-1]["content"]
    assert "Call A" in prompt and "Call B" in prompt


def test_chat_history_persisted(chatenv, monkeypatch):
    sent = Sent(monkeypatch)
    mid = ready_meeting(chatenv, "Harbourline call", HARBOUR)
    ask(chatenv, "Where can documents live?", mid)
    ask(chatenv, "And copies?", mid)
    ask(chatenv, "Across meetings?", None)
    msgs = chatenv[0].get(f"/api/chat?meeting_id={mid}").json()
    assert [(m["role"], m["text"][:12]) for m in msgs] == [("user", "Where can do"), ("assistant", "Everything s"), ("user", "And copies?"), ("assistant", "Everything s")]
    assert msgs[1]["citations"][0]["start_ms"] == 751_000
    assert [m["text"] for m in chatenv[0].get("/api/chat").json() if m["role"] == "user"] == ["Across meetings?"]  # its own thread
    assert sent[1]["messages"][0]["content"] == "Where can documents live?" and len(sent[1]["messages"]) == 3  # earlier turn is sent along
    # the page's Claude cost includes chat
    with chatenv[2]() as s:
        assert {r.meeting_id for r in s.scalars(select(LlmCall).where(LlmCall.purpose == "chat"))} == {mid, None}
    assert chatenv[0].get(f"/api/meetings/{mid}").json()["cost_usd"] > 0


def test_index_job_enqueued_once_per_ready_meeting(chatenv):
    with chatenv[2]() as s:
        s.add_all([Meeting(title="Done", status="ready"), Meeting(title="Live", status="recording")])
        s.commit()
        chat.backfill(s)
        chat.backfill(s)
        assert len(list(s.scalars(select(Job).where(Job.kind == "index_meeting")))) == 1


def test_library_lists_assists_citing_each_document(chatenv):
    client = chatenv[0]
    with chatenv[2]() as s:
        d = Document(kind="firm", title="Rate card", filename="r.md", blob_key="k", status="indexing", progress_done=32, progress_total=120)
        s.add(d)
        s.flush()
        m = Meeting(title="M", status="ready")
        s.add(m)
        s.flush()
        cite = [{"doc_id": str(d.id), "chunk_id": "x", "title": "Rate card", "client": None, "kind": "firm"}]
        s.add_all([Assist(meeting_id=m.id, assist_id=f"a{i}", question="q", citations=cite if i < 2 else []) for i in range(3)])
        s.commit()
    [row] = client.get("/api/library/documents").json()
    assert row["used_count"] == 2 and (row["progress_done"], row["progress_total"]) == (32, 120)


@pytest.mark.ollama
@pytest.mark.claude
@pytest.mark.skipif(not (_KEY := _real_key()), reason="ANTHROPIC_API_KEY is empty")
def test_real_embedding_and_sonnet_chat(env, monkeypatch):
    monkeypatch.setenv("FLECK_WEAVIATE_MEETING_COLLECTION", f"MeetingTextT{uuid.uuid4().hex[:8]}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", _KEY)
    pair(env)
    try:
        mid = ready_meeting(env, "Harbourline call", HARBOUR + [(1_000_000, "We have 40 lawyers in the Leeds office")])
        got = ask(env, "Where must the documents stay?", mid)
        assert "iManage" in got["text"] and got["citations"][0]["start_ms"] == 751_000
    finally:
        httpx.delete(f"{WEAVIATE}/v1/schema/{os.environ['FLECK_WEAVIATE_MEETING_COLLECTION']}")
