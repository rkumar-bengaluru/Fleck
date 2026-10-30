"""Assist orchestration with fakes: retriever, Ollama and Claude (httpx.MockTransport). No services needed."""
import json
import threading
import time

import httpx
import pytest

from fleck_core.answer import Superseded
from fleck_core.retrieval import RetrievalError, RetrievalTimeout
from fleck_core.types import BriefItem, Citation, Passage
from fleck_helper.assist import Assistant
from fleck_helper.pipeline import Trigger

FAQ = Passage(n=1, doc_id="d2", chunk_id="d2-0", title="Delivery FAQ", client=None, kind="firm",
              text="Delivery FAQ · Can you work with our in-house team?\nYes. Mixed teams are common.")
CIT = Citation(doc_id="d2", chunk_id="d2-0", title="Delivery FAQ", client=None, kind="firm")
Q = "Can you work with our in-house team?"


class FakeRetriever:
    def __init__(self, passages=(FAQ,), error=None, delay=0.0):
        self.passages, self.error, self.delay, self.calls = list(passages), error, delay, []
        self.embedder = self

    def embed_query(self, text):
        return [1.0, 0.0]

    def search(self, question, limit=3, client_slug=None, vector=None):
        self.calls.append(question)
        time.sleep(self.delay)
        if self.error:
            raise self.error
        return self.passages


class FakeOllama:
    def __init__(self, tokens=("Mixed teams", " are common [1]."), delay=0.0):
        self.tokens, self.delay, self.calls = tokens, delay, []

    def stream(self, messages, model, options=None, **kw):
        self.calls.append((messages[-1]["content"], options))
        for t in self.tokens:
            time.sleep(self.delay)
            yield t


class FakeClaude:
    """An httpx client whose transport answers like the Messages API and counts calls."""
    def __init__(self, text="It usually depends on the documents involved."):
        self.text, self.requests = text, []
        self.client = httpx.Client(transport=httpx.MockTransport(self._reply))

    def _reply(self, req):
        self.requests.append(json.loads(req.content))
        evs = [{"type": "message_start", "message": {"model": "claude-haiku-5-5", "usage": {"input_tokens": 80}}},
               {"type": "content_block_delta", "delta": {"type": "text_delta", "text": self.text}},
               {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 12}}]
        return httpx.Response(200, text="".join(f"data: {json.dumps(e)}\n\n" for e in evs))


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


def trig(question=Q, turn_id=1, embedding=None, seg="c-1"):
    return Trigger(seg, question, 10_000, embedding, turn_id)


def make(retriever=None, ollama=None, mode="claude", brief=(), claude=None):
    return Assistant(retriever or FakeRetriever(), ollama or FakeOllama(), mode, ["Larkspur Retail Group"], list(brief),
                     claude=(claude or FakeClaude()).client)


def test_brief_match_short_circuits():
    item = BriefItem(question=Q, answer="Yes, mixed teams are common.", kind="library", citations=[CIT],
                     question_vector=[1.0, 0.0])
    far = BriefItem(question="Who owns the IP?", answer="You do.", kind="library", citations=[CIT], question_vector=[0.0, 1.0])
    r, ol = FakeRetriever(), FakeOllama()
    ev = list(make(r, ol, brief=[far, item]).handle(trig(embedding=[0.9, 0.1]), "m1"))
    assert [e.phase for e in ev] == ["thinking", "sentence", "done"]
    assert ev[1].text == item.answer and ev[2].from_brief and ev[2].kind == "library" and ev[2].citations == [CIT]
    assert r.calls == [] and ol.calls == []  # no search, no generation
    # under 0.85 (cosine 0.6 against both) goes on to the library
    ev = list(make(r, ol, brief=[item, far]).handle(trig(embedding=[0.6, 0.8]), "m1"))
    assert not ev[-1].from_brief and r.calls == [Q]


def test_empty_library_falls_back():
    claude = FakeClaude()
    ev = list(make(FakeRetriever(passages=()), claude=claude).handle(trig(), "m1"))
    assert [e.phase for e in ev] == ["thinking", "sentence", "done"]
    assert ev[1].kind == "general" and ev[1].text == claude.text and ev[1].citations == []  # labelled as it streams
    done = ev[-1]
    assert done.kind == "general" and done.first_words_ms is not None and done.rejected == ["no_passages"]
    assert done.llm.status == "ok" and done.llm.purpose == "live_fallback" and done.llm.request == Q
    assert claude.requests[0]["messages"] == [{"role": "user", "content": Q}]  # the question text only


@pytest.mark.parametrize("error, reason", [(RetrievalError("weaviate down"), "retrieval_error"),
                                           (RetrievalTimeout("slow"), "retrieval_timeout"),
                                           (RuntimeError("bug"), "retrieval_error")])
def test_retrieval_error_degrades(error, reason):
    claude = FakeClaude()
    ev = list(make(FakeRetriever(error=error), claude=claude).handle(trig(), "m1"))
    assert [e.phase for e in ev] == ["thinking", "done"]
    assert ev[-1].kind == "not_in_material" and ev[-1].rejected == [reason] and claude.requests == []


def test_fallback_off_gives_not_in_material():
    claude = FakeClaude()
    ev = list(make(ollama=FakeOllama(("NONE",)), mode="off", claude=claude).handle(trig(), "m1"))
    assert [e.phase for e in ev] == ["thinking", "done"]
    assert ev[-1].kind == "not_in_material" and ev[-1].rejected == ["none"] and ev[-1].llm is None
    assert claude.requests == []


def test_blocked_fallback_not_retried():
    claude = FakeClaude("Typically about $150 per hour for this kind of work.")
    ev = list(make(ollama=FakeOllama(("NONE",)), claude=claude).handle(trig(), "m1"))
    assert len(claude.requests) == 1
    assert [e.phase for e in ev] == ["thinking", "done"]  # the blocked text is never shown
    done = ev[-1]
    assert done.kind == "not_in_material" and done.rejected[0] == "none" and "currency" in done.rejected
    assert done.llm.status == "blocked" and done.llm.response.startswith("Typically")


def test_price_question_never_reaches_claude():
    claude = FakeClaude()
    ev = list(make(ollama=FakeOllama(("NONE",)), claude=claude).handle(trig("Ballpark, what would the whole project cost us?"), "m1"))
    assert ev[-1].kind == "not_in_material" and ev[-1].rejected == ["none", "price_question"] and claude.requests == []
    ev = list(make(ollama=FakeOllama(("NONE",)), claude=claude).handle(trig("Would you cap liability at twelve months of fees?"), "m1"))
    assert ev[-1].kind == "general" and len(claude.requests) == 1  # terms, not an amount (decision 101)


def test_no_api_key_is_not_in_material_without_a_call(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    claude = FakeClaude()
    ev = list(make(FakeRetriever(passages=()), claude=claude).handle(trig(), "m1"))
    assert ev[-1].kind == "not_in_material" and ev[-1].rejected == ["no_passages", "no_api_key"]
    assert ev[-1].llm is None and claude.requests == []


def test_event_order_thinking_delta_done():
    ev = list(make().handle(trig(), "m1", stream_start_wall=time.time() - 10.5))
    phases = [e.phase for e in ev]
    assert phases[0] == "thinking" and phases[-1] == "done" and "delta" in phases
    assert phases.index("delta") < phases.index("sentence")
    assert ev[0].question == Q and len({e.assist_id for e in ev}) == 1
    done = ev[-1]
    assert done.kind == "library" and done.citations == [CIT] and not done.from_brief
    assert 500 <= done.first_words_ms < 2000  # measured from the question's end, 0.5 s before handle()


def test_speculation_discarded_when_final_differs():
    r, ol = FakeRetriever(), FakeOllama()
    a = make(r, ol)
    a.prepare("Can you work with our team")
    time.sleep(0.1)
    ev = list(a.handle(trig(), "m1"))
    assert r.calls == ["Can you work with our team", Q]  # searched again for the final text
    answers = [c for c, opts in ol.calls if opts["num_predict"] != 1]
    assert len(answers) == 1 and answers[0].endswith(f"Question: {Q}")  # only the final question is answered
    assert ev[-1].kind == "library"


def test_speculation_reused_when_final_matches():
    r, ol = FakeRetriever(delay=0.2), FakeOllama()  # the Trigger arrives while the prepared search still runs
    a = make(r, ol)
    a.prepare("can you work with our in-house team")  # case and punctuation don't matter
    ev = list(a.handle(trig(), "m1"))
    assert r.calls == ["can you work with our in-house team"]  # one search, from prepare
    assert [o["num_predict"] for _, o in ol.calls][0] == 1  # the prefill ran before the answer
    assert ev[-1].kind == "library"


def test_same_turn_trigger_supersedes_and_keeps_the_card():
    a = make(ollama=FakeOllama(("Mixed", " teams", " are", " common [1]."), delay=0.2))
    first = a.handle(trig("Can you work with", seg="c-1"), "m1")
    t0 = next(first)
    second = a.handle(trig(seg="c-2"), "m1")  # same turn_id: the client went on after a pause
    assert next(first) == Superseded() and next(first, None) is None
    ev = list(second)
    assert ev[0].assist_id == t0.assist_id and ev[-1].phase == "done"  # one card per turn
    third = a.handle(trig(turn_id=2, seg="c-3"), "m1")  # a new turn after the first finished: a new card
    assert next(third).assist_id != t0.assist_id


def test_new_turn_does_not_supersede():
    a = make(ollama=FakeOllama(delay=0.05))
    first = a.handle(trig(turn_id=1), "m1")
    next(first)
    second = a.handle(trig(turn_id=2, seg="c-2"), "m1")
    out = {}
    th = threading.Thread(target=lambda: out.setdefault("b", list(second)))
    th.start()
    rest = list(first)
    th.join()
    assert rest[-1].phase == "done" and out["b"][-1].phase == "done"
    assert rest[-1].assist_id != out["b"][-1].assist_id
