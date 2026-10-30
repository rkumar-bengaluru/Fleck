import json
import os
import time
from pathlib import Path

import httpx
import pytest

from fleck_core import fallback
from fleck_core.fallback import FallbackResult, fallback_answer, stream_fallback


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


def test_sentences_stream_once_checked_and_a_later_block_ends_it():
    ok = list(stream_fallback("q?", [], client(reply("It depends on scope. It also depends on the data."))))
    assert ok[:2] == ["It depends on scope.", "It also depends on the data."] and ok[-1].ok
    bad = list(stream_fallback("q?", [], client(reply("It depends on scope. Most firms charge $120 per hour. Ok."))))
    assert bad[0] == "It depends on scope." and all(not isinstance(x, str) or "$" not in x for x in bad)
    assert isinstance(bad[-1], FallbackResult) and not bad[-1].ok and "currency" in bad[-1].reasons


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
