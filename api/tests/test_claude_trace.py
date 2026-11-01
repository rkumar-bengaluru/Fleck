import os
from pathlib import Path

import pytest
from sqlalchemy import select

from fleck_api import claude
from fleck_api.models import LlmCall, Meeting


def _real_key() -> str:
    if k := os.environ.get("ANTHROPIC_API_KEY"):
        return k
    env = Path(__file__).parents[2] / ".env"
    lines = env.read_text().splitlines() if env.exists() else []
    return next((ln.split("=", 1)[1].strip() for ln in lines if ln.startswith("ANTHROPIC_API_KEY=")), "")


def test_llm_call_traced_with_cost(make_session, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setenv("FLECK_PRICES", '{"claude-sonnet-5-5": {"input": 2, "output": 10}}')
    seen = {}
    monkeypatch.setattr(claude, "_send", lambda body: seen.update(body) or {
        "model": "claude-sonnet-5-5", "stop_reason": "end_turn", "usage": {"input_tokens": 1_000_000, "output_tokens": 500_000},
        "content": [{"type": "text", "text": "Hello"}]})
    with make_session() as s:
        m = Meeting(title="t")
        s.add(m)
        s.commit()
        msg = claude.claude_call("summary", [{"role": "user", "content": "Hi"}], "claude-sonnet-5-5", m.id, session=s, max_tokens=50)
        assert msg.text == "Hello" and seen["max_tokens"] == 50
        row = s.scalar(select(LlmCall))
    assert (row.purpose, row.model, row.status, row.meeting_id) == ("summary", "claude-sonnet-5-5", "ok", m.id)
    assert (row.input_tokens, row.output_tokens) == (1_000_000, 500_000) and abs(row.cost_usd - 7.0) < 1e-9
    assert row.latency_ms is not None and row.request["messages"][0]["content"] == "Hi" and row.response["text"] == "Hello"


def test_failed_call_is_traced_and_raised(make_session, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    def boom(body):
        raise RuntimeError("HTTP 529")
    monkeypatch.setattr(claude, "_send", boom)
    with make_session() as s:
        try:
            claude.claude_call("summary", [], "claude-sonnet-5-5", session=s)
        except RuntimeError:
            pass
        else:
            raise AssertionError("expected a raise")
        row = s.scalar(select(LlmCall))
    assert row.status == "error" and row.error == "RuntimeError: HTTP 529" and row.cost_usd is None


@pytest.mark.claude
@pytest.mark.skipif(not (_KEY := _real_key()), reason="ANTHROPIC_API_KEY is empty")
def test_real_haiku_call_is_traced(make_session, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", _KEY)
    with make_session() as s:
        msg = claude.claude_call("ping", [{"role": "user", "content": "Reply with the single word: ok"}], "claude-haiku-5-5",
                                 session=s, max_tokens=20, thinking={"type": "disabled"})
        row = s.scalar(select(LlmCall))
    assert msg.text.strip() and row.status == "ok" and row.cost_usd > 0 and row.input_tokens > 0


def test_failed_call_keeps_the_api_error_message(make_session, monkeypatch):
    import httpx
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    resp = httpx.Response(400, json={"error": {"type": "invalid_request_error", "message": "tool_choice: not supported"}},
                          request=httpx.Request("POST", claude.URL))
    monkeypatch.setattr(claude, "_send", lambda body: resp.raise_for_status())
    with make_session() as s:
        with pytest.raises(httpx.HTTPStatusError):
            claude.claude_call("summary", [], "claude-sonnet-5-5", session=s)
        assert s.scalar(select(LlmCall.error)) == "HTTPStatusError: HTTP 400 invalid_request_error: tool_choice: not supported"
