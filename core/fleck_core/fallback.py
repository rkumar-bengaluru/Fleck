"""Live fallback (spec 7.4 rules 5-6): Claude Haiku answers from general knowledge when the library can't.

Only the client's question text is sent (spec 6): `question_only` keeps the turn's question sentences, without a
leading vocative and with library client names redacted. The reply streams token by token until the sentence so far
could hold a figure, money, a rate or a client name; the rest of that sentence waits for `check_fallback` on the text
up to it, and a block anywhere ends the answer in "Not in our material" (decision 108). A blocked answer is
never retried. Raw HTTP over httpx, because the anthropic SDK is not a dependency of the live path."""
import json
import os
import re
import queue
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass

import httpx

from .answer import _NUMBER_WORD_TAIL, Delta, Sentence
from .numbers import CODES, extract_figures
from .validate import check_fallback, name_words, redact_clients, redact_people, split_sentences

TIMEOUT_S = 5.0  # wall clock for the whole call; past it the answer is "Not in our material"
URL = "https://api.anthropic.com/v1/messages"
SYSTEM = ("You answer a client's question during a sales call, from general industry knowledge. Reply in at most two "
          "short, plain sentences; the first one gives the gist in under 12 words. Never state prices, rates, percentages, SLA or response-time figures, team sizes, "
          "or any number of money or people. Never name any company or client, and never speak for the vendor or "
          "claim work it has done: no 'we', 'our' or 'us'. If the question can't be answered without such facts, "
          "say what it usually depends on.")
# a connection kept for 5 min, so a fallback mid-meeting skips the TCP + TLS handshake (`warm`)
_http = httpx.Client(timeout=TIMEOUT_S, limits=httpx.Limits(keepalive_expiry=300))
_END = object()
_VOCATIVE = re.compile(r"^(\w+ )?[A-Z][a-z]+, ")  # "Marcus, how do you…" (as classify._VOCATIVE)
SLICE_S = 0.05  # how often a waiting stream checks its cancel event
_RISKY = re.compile(rf"\d|%|[$€£¥₹]|\bper\b|/|\b(?:{CODES})\b", re.I)  # a figure, money or a rate could follow: hold the sentence


def question_only(turn: str, client_names: list[str], people: list[str] = ()) -> str:
    """What may go to Claude: the turn's sentences ending in "?" (else its last sentence), a leading vocative
    dropped from each, client names (the library's and the meeting's) replaced by "a client" and the meeting's
    attendees by "someone". Statements around the question stay home."""
    sents = split_sentences(turn) or [turn.strip()]
    asks = [s for s in sents if s.endswith("?")] or sents[-1:]
    return redact_clients(redact_people(" ".join(_VOCATIVE.sub("", s) for s in asks), people), client_names)


def warm(http: httpx.Client | None = None) -> None:
    """Open the API connection ahead of a likely fallback: a HEAD to the host, with no key and no meeting content.
    Never raises; call it on a thread."""
    try:
        (http or _http).head("https://api.anthropic.com/")
    except Exception:
        pass


def model() -> str:
    return os.environ.get("FLECK_CLAUDE_FAST_MODEL") or "claude-haiku-5-5"


@dataclass
class FallbackResult:
    text: str
    ok: bool  # False: show "Not in our material"
    reasons: list[str]  # check_fallback's block reasons, or "timeout" / "claude_error" / "no_api_key" / "refusal"
    latency_ms: int
    model: str = ""
    status: str = "ok"  # ok | blocked | error | timeout | discarded (the llm_calls trace)
    input_tokens: int | None = None
    output_tokens: int | None = None
    error: str | None = None
    started: float = 0.0  # wall clock (time.time()) when the request was sent
    first_text: float | None = None  # wall clock of Claude's first text token


def _hold(partial: str, client_names: list[str]) -> bool:
    """True when the sentence so far must not stream further: it has a figure, money, a rate or anything
    check_fallback blocks, or its last words could still become a client name ("Callo" of "Calloway & Marsh")."""
    if _RISKY.search(partial) or extract_figures(partial) or not check_fallback(partial, client_names).ok:
        return True
    low = partial.lower()
    for name in (w.lower() for c in client_names for w in name_words(c)):
        starts = [m.start() for m in re.finditer(r"\b\w", low)][-len(name.split()):]
        if any(name.startswith(low[i:]) for i in starts):
            return True
    return False


def _pump(http: httpx.Client, question: str, key: str, q: queue.Queue, stop: threading.Event, box: dict) -> None:
    """Server-sent events, as dicts, onto q; then _END, or the exception. box["r"] is the open response."""
    body = {"model": model(), "max_tokens": 200, "system": SYSTEM, "stream": True,
            # Haiku 5.5 thinks by default; off (allowed at effort high or below) for the live latency budget
            "thinking": {"type": "disabled"}, "output_config": {"effort": "low"},
            "messages": [{"role": "user", "content": question}]}
    try:
        with http.stream("POST", URL, json=body, headers={"x-api-key": key, "anthropic-version": "2023-06-01"}) as r:
            box["r"] = r
            if stop.is_set():
                return
            r.raise_for_status()
            for line in r.iter_lines():
                if stop.is_set():
                    return
                if line.startswith("data:"):
                    q.put(json.loads(line[5:]))
        q.put(_END)
    except Exception as e:
        q.put(e)


def stream_fallback(question: str, client_names: list[str], http: httpx.Client | None = None,
                    cancel: threading.Event | None = None) -> Iterator[Delta | Sentence | FallbackResult]:
    """The library contract (answer.stream_library_answer): Deltas append to sentence `index` until it could hold a
    figure, money, a rate or a client name (`_hold`); Sentence(index, text) replaces them once the answer up to it
    passes `check_fallback`. A block yields Sentence(index, "", False, reason) and ends at once with a blocked
    FallbackResult (always last). A result that is not ok means: replace anything shown with "Not in our material". `question` is sent as given:
    pass it through `question_only` first. Setting `cancel` closes the HTTP stream within SLICE_S and ends with a
    "discarded" result. Never raises."""
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        yield FallbackResult("", False, ["no_api_key"], 0, model(), "error", error="ANTHROPIC_API_KEY is empty")
        return
    t, started = time.perf_counter(), time.time()
    ms = lambda: round((time.perf_counter() - t) * 1000)  # noqa: E731
    q, stop, box = queue.Queue(), threading.Event(), {}
    threading.Thread(target=_pump, args=(http or _http, question, key, q, stop, box), daemon=True).start()
    text, used, stop_reason, name, first = "", {}, None, model(), None
    pos, index, shown, held, kept = 0, 0, "", False, []

    def result(ok, reasons, status, **kw) -> FallbackResult:
        return FallbackResult(text.strip(), ok, reasons, ms(), name, status, used.get("input_tokens"),
                              used.get("output_tokens"), started=started, first_text=first, **kw)

    def emit(final: bool) -> Iterator[Delta | Sentence]:
        """Sentences that ended get their verdict; a block is a Sentence with "" (the caller then stops)."""
        nonlocal pos, index, shown, held
        pieces = split_sentences(text[pos:])
        for piece in pieces if final else pieces[:-1]:  # the last piece may still grow
            pos = text.index(piece, pos) + len(piece)
            chk = check_fallback(" ".join(kept + [piece]), client_names)
            if not chk.ok:
                yield Sentence(index, "", False, chk.reasons[0])
                return
            kept.append(piece)
            yield Sentence(index, piece, True, None)
            index, shown, held = index + 1, "", False
        partial = text[pos:].lstrip()
        if final or held or not partial:
            return
        if _hold(partial, client_names):
            held = True
            return
        disp = partial[: len(partial) - len(re.search(r"\S*$", partial)[0])]  # never half a word: "costs fi", "W"
        disp = _NUMBER_WORD_TAIL.sub("", disp)  # "costs one" could become a figure with the next word
        if len(disp) > len(shown) and disp.startswith(shown):
            yield Delta(index, disp[len(shown):])
            shown = disp
    try:
        while True:
            if cancel is not None and cancel.is_set():
                yield result(False, ["cancelled"], "discarded")
                return
            left = TIMEOUT_S - (time.perf_counter() - t)
            try:
                ev = q.get(timeout=max(0.0, min(left, SLICE_S)))
            except queue.Empty:
                if left > SLICE_S:
                    continue
                yield result(False, ["timeout"], "timeout", error=f"over {TIMEOUT_S} s")
                return
            if isinstance(ev, Exception):  # never let the key reach a log: only the type and status go in the trace
                err = f"HTTP {ev.response.status_code}" if isinstance(ev, httpx.HTTPStatusError) else type(ev).__name__
                yield result(False, ["claude_error"], "error", error=err)
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
                yield result(False, ["claude_error"], "error", error=ev.get("error", {}).get("type"))
                return
            elif kind == "content_block_delta" and ev["delta"].get("type") == "text_delta":
                first = first or time.time()
                text += ev["delta"]["text"]
                for x in emit(final=False):
                    yield x
                    if isinstance(x, Sentence) and not x.ok:
                        yield result(False, check_fallback(text, client_names).reasons, "blocked")
                        return
        reasons = (["refusal"] if stop_reason == "refusal" else []) + ([] if text.strip() else ["empty"])
        if reasons:  # nothing more is shown; the done replaces what was
            yield result(False, check_fallback(text, client_names).reasons + reasons, "blocked")
            return
        for x in emit(final=True):
            yield x
            if isinstance(x, Sentence) and not x.ok:
                yield result(False, check_fallback(text, client_names).reasons, "blocked")
                return
        yield result(True, [], "ok")
    finally:
        stop.set()
        if r := box.get("r"):
            r.close()  # an unfinished stream is cut here, not at its next chunk


def fallback_answer(question: str, client_names: list[str], http: httpx.Client | None = None) -> FallbackResult:
    """The whole checked answer at once. `http` is for tests (an httpx.MockTransport client)."""
    *_, result = stream_fallback(question, client_names, http)
    return result
