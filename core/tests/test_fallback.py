import json
import os
import re
import time
from pathlib import Path

import httpx
import pytest

from fleck_core import fallback
import threading

from fleck_core.answer import Delta, Sentence
from fleck_core.fallback import FallbackResult, fallback_answer, question_only, stream_fallback


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def sse(text, stop="end_turn", chunk=8):
    """A Messages API event stream for `text`, in `chunk`-character deltas."""
    evs = [{"type": "message_start", "message": {"model": "claude-haiku-5-5", "usage": {"input_tokens": 9}}}]
    evs += [{"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text[i:i + chunk]}}
            for i in range(0, len(text), chunk)]
    evs += [{"type": "message_delta", "delta": {"stop_reason": stop}, "usage": {"output_tokens": 5}},
            {"type": "message_stop"}]
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in evs)


def reply(text, stop="end_turn"):
    return lambda req: httpx.Response(200, text=sse(text, stop), headers={"content-type": "text/event-stream"})


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")


def test_sends_only_the_question_and_checks_the_answer():
    sent = []

    def handler(req):
        sent.append((req.headers["x-api-key"], json.loads(req.content)))
        return reply("It usually depends on how the documents are structured.")(req)
    r = fallback_answer("Can AI read scanned contracts?", ["Larkspur Retail Group"], client(handler))
    assert r.ok and r.status == "ok" and r.reasons == [] and (r.input_tokens, r.output_tokens) == (9, 5)
    key, body = sent[0]
    assert key == "test-key" and body["model"] == "claude-haiku-5-5" and body["stream"]
    assert body["messages"] == [{"role": "user", "content": "Can AI read scanned contracts?"}]


@pytest.mark.parametrize("text, reason", [("Most firms charge $120 per hour.", "currency"),
                                          ("Larkspur Retail Group did this well.", "client_name:Larkspur Retail Group"),
                                          ("We have done this many times.", "firm_claim")])
def test_blocked_answer(text, reason):
    r = fallback_answer("q?", ["Larkspur Retail Group"], client(reply(text)))
    assert not r.ok and r.status == "blocked" and reason in r.reasons and r.text == text  # kept for the trace


def stream(text, names=(), chunk=8):
    return list(stream_fallback("q?", list(names), client(lambda req: httpx.Response(
        200, text=sse(text, chunk=chunk), headers={"content-type": "text/event-stream"}))))


def shown(items, i):
    """What a viewer saw at any moment for sentence i: every Delta's text."""
    return "".join(x.text for x in items if isinstance(x, Delta) and x.index == i)


def test_clean_sentences_stream_token_by_token():
    ev = stream("It depends on scope. It also depends on the data.", chunk=4)
    assert sum(isinstance(x, Delta) and x.index == 0 for x in ev) > 2  # word by word, not at the full stop
    assert "It depends on scope.".startswith(shown(ev, 0)) and len(shown(ev, 0)) > 10
    sents = [(x.index, x.text) for x in ev if isinstance(x, Sentence)]
    assert sents == [(0, "It depends on scope."), (1, "It also depends on the data.")] and ev[-1].ok
    assert ev.index(next(x for x in ev if isinstance(x, Delta))) < ev.index(next(x for x in ev if isinstance(x, Sentence)))


@pytest.mark.parametrize("chunk", [2, 3, 5])
def test_client_name_split_across_tokens_is_never_shown(chunk):
    ev = stream("It depends on scope. Teams like Calloway & Marsh did it.", ["Calloway & Marsh"], chunk)
    assert all("Ca" not in x.text for x in ev if isinstance(x, (Delta, Sentence)))
    assert Sentence(1, "", False, "client_name:Calloway & Marsh") in ev
    assert isinstance(ev[-1], FallbackResult) and not ev[-1].ok and ev[-1].status == "blocked"


@pytest.mark.parametrize("chunk", [2, 3, 5])
def test_price_split_across_tokens_is_never_shown(chunk):
    ev = stream("It depends on scope. Most firms charge $120 per hour. Ok.", chunk=chunk)
    assert all(not re.search(r"\d|\$", x.text) for x in ev if isinstance(x, (Delta, Sentence)))
    assert Sentence(0, "It depends on scope.", True, None) in ev and Sentence(1, "", False, "currency") in ev
    assert not ev[-1].ok and "currency" in ev[-1].reasons and "Most firms charge $120" in ev[-1].text


@pytest.mark.parametrize("text, never", [("Typically it costs five thousand dollars.", "fi"),
                                         ("It takes about two lakh rupees.", "two"),
                                         ("We usually see this.", "W")])
@pytest.mark.parametrize("chunk", [1, 2, 3])
def test_half_words_never_flash(text, never, chunk):
    ev = stream(text, chunk=chunk)
    seen = [shown(ev[: k + 1], 0) for k in range(len(ev))]  # the panel after each event
    assert all(never not in t for t in seen) and not ev[-1].ok


def test_result_carries_wall_times():
    *_, r = stream("It depends.")
    assert r.started and r.first_text and r.started <= r.first_text <= time.time()


def test_timeout(monkeypatch):
    monkeypatch.setattr(fallback, "TIMEOUT_S", 0.1)
    r = fallback_answer("q?", [], client(lambda req: (time.sleep(0.5), reply("ok")(req))[1]))
    assert not r.ok and r.reasons == ["timeout"] and r.status == "timeout" and r.latency_ms < 400


def test_http_error_never_raises_or_leaks_the_key():
    r = fallback_answer("q?", [], client(lambda req: httpx.Response(401, json={"error": "bad key test-key"})))
    assert not r.ok and r.reasons == ["claude_error"] and r.error == "HTTP 401"


def test_refusal_is_blocked():
    r = fallback_answer("q?", [], client(reply("", stop="refusal")))
    assert not r.ok and "refusal" in r.reasons


def test_no_key_makes_no_call(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    r = fallback_answer("q?", [], client(lambda req: pytest.fail("called")))
    assert not r.ok and r.reasons == ["no_api_key"]


def _real_key() -> str:
    """From the environment or .env (read at collection, before the fixture swaps in a fake key); never printed."""
    if k := os.environ.get("ANTHROPIC_API_KEY"):
        return k
    env = Path(__file__).parents[2] / ".env"
    lines = env.read_text().splitlines() if env.exists() else []
    return next((ln.split("=", 1)[1].strip() for ln in lines if ln.startswith("ANTHROPIC_API_KEY=")), "")


@pytest.mark.claude
@pytest.mark.skipif(not (_KEY := _real_key()), reason="ANTHROPIC_API_KEY is empty")
def test_real_haiku_general_answer(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", _KEY)
    r = fallback_answer("How do AI systems usually handle scanned PDFs with handwriting?", ["Larkspur Retail Group"])
    assert r.status in ("ok", "blocked") and r.latency_ms < 5000 and r.text, r.error


@pytest.mark.parametrize("turn, ask", [
    ("Our playbook is long. Marcus, how do you stop a model inventing clauses?", "how do you stop a model inventing clauses?"),
    ("We met Larkspur last week. Did it work for Larkspur Retail Group?", "Did it work for a client?"),
    ("Thanks. How do you handle OCR.", "How do you handle OCR."),  # no "?": the last sentence
    ("Is it safe? And is it fast?", "Is it safe? And is it fast?"),
])
def test_question_only(turn, ask):
    assert question_only(turn, ["Larkspur Retail Group"]) == ask


@pytest.mark.parametrize("turn, gone", [
    ("Marcus, how would you handle this for Harbourline Legal?", ["Harbourline", "Marcus"]),
    ("Could Priya on our side own the migration?", ["Priya"]),
    ("Does Shah's team on your side agree, Marcus Webb?", ["Shah", "Marcus", "Webb"]),
])
def test_question_only_redacts_attendees_and_meeting_client(turn, gone):
    ask = question_only(turn, ["Larkspur Retail Group", "Harbourline Legal"], ["Marcus Webb", "Priya Shah"])
    assert not any(g in ask for g in gone), ask


@pytest.mark.parametrize("turn, ask", [
    ("Did Li's group test it?", "Did someone's group test it?"),
    ("Priya's team on our side, will they own it?", "someone's team on our side, will they own it?"),
    ("Will you support SSO?", "someone you support SSO?"),  # opens a sentence: redacted, privacy over quality
    ("What is the max file size?", "What is the max file size?"),
])
def test_question_only_name_words_are_case_sensitive_and_short_names_count(turn, ask):
    assert question_only(turn, [], ["Wei Li", "Priya Shah", "Will Turner", "Max Ng"]) == ask


@pytest.mark.parametrize("turn, people, ask", [
    ("Does O'Brien agree?", ["Seán O'Brien"], "Does someone agree?"),
    ("A client asked about SSO?", ["Rupak A"], "A client asked about SSO?"),
    ("Can Anna Lee join the next call?", ["Anna Lee"], "Can someone join the next call?"),
])
def test_question_only_name_parts(turn, people, ask):
    assert question_only(turn, [], people) == ask


def test_cancel_ends_with_a_discarded_result():
    cancel = threading.Event()

    def slow(req):
        time.sleep(1.0)
        return reply("ok")(req)
    t = time.perf_counter()
    threading.Timer(0.1, cancel.set).start()
    *_, r = stream_fallback("q?", [], client(slow), cancel)
    assert r.status == "discarded" and r.reasons == ["cancelled"] and time.perf_counter() - t < 0.3
