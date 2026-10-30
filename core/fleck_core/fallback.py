"""Live fallback (spec 7.4 rules 5-6): Claude Haiku answers from general knowledge when the library can't.

Only the question text is sent. The reply streams; each sentence is shown only once the text up to it passes
`check_fallback`, and a block anywhere ends the answer in "Not in our material" (decision 108). A blocked answer is
never retried. Raw HTTP over httpx, because the anthropic SDK is not a dependency of the live path."""
import json
import os
import queue
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass

import httpx

from .validate import check_fallback, split_sentences

TIMEOUT_S = 5.0  # wall clock for the whole call; past it the answer is "Not in our material"
URL = "https://api.anthropic.com/v1/messages"
SYSTEM = ("You answer a client's question during a sales call, from general industry knowledge. Reply in at most two "
          "short, plain sentences; the first one gives the gist in under 12 words. Never state prices, rates, percentages, SLA or response-time figures, team sizes, "
          "or any number of money or people. Never name any company or client, and never speak for the vendor or "
          "claim work it has done: no 'we', 'our' or 'us'. If the question can't be answered without such facts, "
          "say what it usually depends on.")
_http = httpx.Client(timeout=TIMEOUT_S)
_END = object()


def model() -> str:
    return os.environ.get("FLECK_CLAUDE_FAST_MODEL") or "claude-haiku-5-5"


@dataclass
class FallbackResult:
    text: str
    ok: bool  # False: show "Not in our material"
    reasons: list[str]  # check_fallback's block reasons, or "timeout" / "claude_error" / "no_api_key" / "refusal"
    latency_ms: int
    model: str = ""
    status: str = "ok"  # ok | blocked | error | timeout (the llm_calls trace)
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None


def _pump(http: httpx.Client, question: str, key: str, q: queue.Queue, stop: threading.Event) -> None:
    """Server-sent events, as dicts, onto q; then _END, or the exception."""
    body = {"model": model(), "max_tokens": 200, "system": SYSTEM, "stream": True,
            # Haiku 5.5 thinks by default; off (allowed at effort high or below) for the live latency budget
            "thinking": {"type": "disabled"}, "output_config": {"effort": "low"},
            "messages": [{"role": "user", "content": question}]}
    try:
        with http.stream("POST", URL, json=body, headers={"x-api-key": key, "anthropic-version": "2023-06-01"}) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if stop.is_set():
                    return
                if line.startswith("data:"):
                    q.put(json.loads(line[5:]))
        q.put(_END)
    except Exception as e:
        q.put(e)


def stream_fallback(question: str, client_names: list[str], http: httpx.Client | None = None
                    ) -> Iterator[str | FallbackResult]:
    """Each sentence as soon as the answer up to it passes `check_fallback`, then the FallbackResult (always last).
    A result that is not ok means: replace anything shown with "Not in our material". Never raises."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        yield FallbackResult("", False, ["no_api_key"], 0, model(), "error", error="ANTHROPIC_API_KEY is empty")
        return
    t = time.perf_counter()
    ms = lambda: round((time.perf_counter() - t) * 1000)  # noqa: E731
    q, stop = queue.Queue(), threading.Event()
    threading.Thread(target=_pump, args=(http or _http, question, key, q, stop), daemon=True).start()
    text, shown, used, stop_reason, name = "", 0, {}, None, model()
    try:
        while True:
            try:
                ev = q.get(timeout=max(0.0, TIMEOUT_S - (time.perf_counter() - t)))
            except queue.Empty:
                yield FallbackResult(text, False, ["timeout"], ms(), name, "timeout", error=f"over {TIMEOUT_S} s")
                return
            if isinstance(ev, Exception):  # never let the key reach a log: only the type and status go in the trace
                err = f"HTTP {ev.response.status_code}" if isinstance(ev, httpx.HTTPStatusError) else type(ev).__name__
                yield FallbackResult(text, False, ["claude_error"], ms(), name, "error", error=err)
                return
            if ev is _END:
                break
            kind = ev.get("type")
            if kind == "message_start":
                name = ev["message"].get("model", name)
                used.update(ev["message"].get("usage", {}))
            elif kind == "message_delta":
                stop_reason = ev.get("delta", {}).get("stop_reason") or stop_reason
                used.update(ev.get("usage", {}))
            elif kind == "error":
                yield FallbackResult(text, False, ["claude_error"], ms(), name, "error", error=ev.get("error", {}).get("type"))
                return
            elif kind == "content_block_delta" and ev["delta"].get("type") == "text_delta":
                text += ev["delta"]["text"]
                done = split_sentences(text)[:-1]  # the last piece may still grow
                while shown < len(done) and check_fallback(" ".join(done[: shown + 1]), client_names).ok:
                    yield done[shown]
                    shown += 1
        text = text.strip()
        reasons = check_fallback(text, client_names).reasons + ([] if text else ["empty"])
        reasons += ["refusal"] if stop_reason == "refusal" else []
        if not reasons:
            yield from split_sentences(text)[shown:]
        yield FallbackResult(text, not reasons, reasons, ms(), name, "blocked" if reasons else "ok",
                             used.get("input_tokens"), used.get("output_tokens"))
    finally:
        stop.set()


def fallback_answer(question: str, client_names: list[str], http: httpx.Client | None = None) -> FallbackResult:
    """The whole checked answer at once. `http` is for tests (an httpx.MockTransport client)."""
    *_, result = stream_fallback(question, client_names, http)
    return result
