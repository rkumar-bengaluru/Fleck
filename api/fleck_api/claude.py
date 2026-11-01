"""Claude for the api and worker: one non-streaming call, always traced in llm_calls (the helper's live fallback is
traced from its own events, realtime.py). Raw HTTP over httpx, as core's fallback does: the anthropic SDK is not a dependency."""
import json
import time
from dataclasses import dataclass

import httpx
from sqlalchemy.orm import Session

from fleck_api.config import setting
from fleck_api.models import LlmCall

URL = "https://api.anthropic.com/v1/messages"
TIMEOUT_S = 600.0
# USD per million tokens, from the Claude API price list; FLECK_PRICES (JSON, same shape) overrides per model
PRICES = {"claude-sonnet-5-5": {"input": 2.0, "output": 10.0}, "claude-haiku-5-5": {"input": 0.1, "output": 0.5}}


@dataclass
class Message:
    text: str
    tool_input: dict | None  # the first tool_use block's input
    input_tokens: int | None
    output_tokens: int | None
    model: str
    stop_reason: str | None = None


def _send(body: dict) -> dict:
    r = httpx.post(URL, json=body, timeout=TIMEOUT_S, headers={"x-api-key": setting("ANTHROPIC_API_KEY", ""), "anthropic-version": "2023-06-01"})
    r.raise_for_status()
    return r.json()


def _api_error(e: httpx.HTTPStatusError) -> str:
    try:
        err = e.response.json()["error"]
        return f"HTTP {e.response.status_code} {err['type']}: {err['message']}"
    except Exception:
        return f"HTTP {e.response.status_code}"


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    p = (PRICES | json.loads((setting("FLECK_PRICES", "") or "{}"))).get(model)
    return None if p is None else (input_tokens * p["input"] + output_tokens * p["output"]) / 1e6


def claude_call(purpose: str, messages: list[dict], model: str, meeting_id: int | None = None, *, session: Session, event_id: int | None = None, **kw) -> Message:
    """kw (system, max_tokens, tools, tool_choice, thinking ...) goes into the request. The llm_calls row is written
    and committed whether the call succeeds or raises (the commit covers the caller's whole session). Never put the API key in anything that is stored."""
    body = {"max_tokens": 4096} | kw | {"model": model, "messages": messages}
    row = LlmCall(purpose=purpose, model=model, request={k: v for k, v in body.items() if k != "model"}, meeting_id=meeting_id, event_id=event_id)
    t = time.perf_counter()
    try:
        if not setting("ANTHROPIC_API_KEY", ""):
            raise RuntimeError("ANTHROPIC_API_KEY is empty")
        data = _send(body)
        blocks = data.get("content", [])
        text = "".join(b["text"] for b in blocks if b.get("type") == "text")
        use = data.get("usage", {})
        msg = Message(text, next((b["input"] for b in blocks if b.get("type") == "tool_use"), None), use.get("input_tokens"),
                      use.get("output_tokens"), data.get("model", model), data.get("stop_reason"))
        row.response = {"text": text, "tool_input": msg.tool_input, "stop_reason": msg.stop_reason}
        row.input_tokens, row.output_tokens = msg.input_tokens, msg.output_tokens
        row.cost_usd = cost_usd(model, msg.input_tokens or 0, msg.output_tokens or 0)
        return msg
    except Exception as e:  # the trace gets the type, the status and the API's own error message; the key is in no response body
        row.status = "error"
        row.error = f"{type(e).__name__}: {_api_error(e) if isinstance(e, httpx.HTTPStatusError) else e}"[:1000]
        raise
    finally:
        row.latency_ms = round((time.perf_counter() - t) * 1000)
        session.add(row)
        session.commit()
