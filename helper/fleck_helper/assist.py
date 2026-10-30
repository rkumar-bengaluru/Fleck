"""Live answer orchestration (spec 7.1, 7.4): a brief answer, else the library, else the labelled Claude fallback.

The pipeline's consumer calls `prepare` on each Candidate and `handle` on each Trigger, and must iterate `handle`'s
events on its own thread: they block on search and generation. Never raises; every failure ends in a `done`."""
import logging
import re
import threading
import time
import uuid
from concurrent.futures import Future
from dataclasses import dataclass, field
from functools import partial
from typing import Iterator, Literal

import numpy as np

from fleck_core.answer import Delta, Done, Sentence, Superseded, prefill, stream_library_answer
from fleck_core.events import AssistEvent, LlmCall
from fleck_core.fallback import stream_fallback
from fleck_core.retrieval import RetrievalTimeout
from fleck_core.types import BriefItem
from fleck_helper.pipeline import Trigger

BRIEF_MIN_COSINE = 0.85  # a live question this close to a brief question replays the brief's answer
SEARCH_WAIT_S = 2.5  # a prepared search still running is waited for up to the live search budget (retrieval.Retriever)
PREFILL_WAIT_S = 1.5  # ...and its prefill up to this, so the answer reads the warm cache instead of a cold one beside it
# A question asking for a price or an amount: the fallback may never state one (spec 7.4 rule 5), so its honest answer is
# "Not in our material" (spec 7.4 Tests) and Claude is not called. "fees" alone is not here: "cap liability at twelve
# months of fees?" asks about terms, not an amount (decision 107).
_PRICE_ASK = re.compile(r"\b(how much|ballpark|cost(s|ing)?|pric(e|es|ed|ing)|rates?|quot(e|es|ed|ation)|budget)\b", re.I)
log = logging.getLogger(__name__)


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", text.lower()))


def _cosine(a, b) -> float:
    a, b = np.asarray(a, np.float64), np.asarray(b, np.float64)
    return float(a @ b / ((np.linalg.norm(a) * np.linalg.norm(b)) or 1.0))


@dataclass
class _Prepared:
    key: str  # the normalised candidate text
    passages: Future = field(default_factory=Future)
    prefilled: threading.Event = field(default_factory=threading.Event)
    dropped: bool = False  # a newer candidate replaced it, or the Trigger's text differs: no prefill


@dataclass
class _Live:
    turn_id: int
    assist_id: str
    cancel: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)


class Assistant:
    def __init__(self, retriever, ollama, fallback_mode: Literal["claude", "off"], client_names: list[str],
                 brief_items: list[BriefItem], claude=None):
        """`claude` is an httpx client for the Messages API (tests pass a MockTransport one); None uses the default."""
        self.retriever, self.ollama, self.fallback_mode = retriever, ollama, fallback_mode
        self.client_names, self.brief_items, self.claude = client_names, brief_items, claude
        self._lock = threading.Lock()
        self._prepared: _Prepared | None = None
        self._live: _Live | None = None

    def prepare(self, text: str, embedding: list[float] | None = None) -> None:
        """Start search and prefill for a Candidate on a thread; returns at once. A newer call replaces this one."""
        prep = _Prepared(_norm(text))
        with self._lock:
            if self._prepared:
                self._prepared.dropped = True
            self._prepared = prep
        threading.Thread(target=self._prepare, args=(prep, text, embedding), daemon=True).start()

    def _prepare(self, prep: _Prepared, text: str, embedding) -> None:
        try:
            try:
                passages = self.retriever.search(text, vector=embedding)
            except Exception as e:
                prep.passages.set_exception(e)
                return
            prep.passages.set_result(passages)
            if passages and not prep.dropped:
                prefill(passages, text, self.ollama)
        except Exception as e:
            log.warning("prefill failed: %r", e)
        finally:
            prep.prefilled.set()

    def handle(self, trigger: Trigger, meeting_id: str, stream_start_wall: float | None = None
               ) -> Iterator[AssistEvent | Superseded]:
        """The assist events for one Trigger: `thinking` at once, then the answer, ending in `done`.

        A Trigger with the same turn_id as a previous one still running supersedes it (decision 104): the older
        stream yields Superseded() and ends, and the new one keeps its assist_id, so the card is replaced.
        first_words_ms and full_ms are measured from the question's end, `stream_start_wall + trigger.end_ms`, or
        from this call when stream_start_wall is None."""
        with self._lock:
            prev = self._live
            same = prev is not None and prev.turn_id == trigger.turn_id and not prev.finished.is_set()
            if same:
                prev.cancel.set()
            live = self._live = _Live(trigger.turn_id, prev.assist_id if same else uuid.uuid4().hex)
            prep, self._prepared = self._prepared, None
            if prep and prep.key != _norm(trigger.question):
                prep.dropped, prep = True, None  # the final text differs: search again, never prefill this one
        return self._run(trigger, meeting_id, live, prep, stream_start_wall)

    def _run(self, trigger, meeting_id, live: _Live, prep, start_wall):
        events = self._answer(trigger, meeting_id, live.assist_id, prep, start_wall)
        try:
            for e in events:
                if live.cancel.is_set():
                    yield Superseded()
                    return
                yield e
        finally:
            events.close()  # closes the Ollama stream too
            live.finished.set()

    def _answer(self, trig: Trigger, meeting_id, assist_id, prep: _Prepared | None, start_wall):
        started = time.time()
        q_end = started if start_wall is None else start_wall + trig.end_ms / 1000
        ms = lambda: round((time.time() - q_end) * 1000)  # noqa: E731
        ev = partial(AssistEvent, type="assist", meeting_id=meeting_id, assist_id=assist_id,
                     segment_id=trig.segment_id, question=trig.question)
        yield ev(phase="thinking")
        vector = trig.embedding
        if self.brief_items:
            item, vector = self._brief_match(trig.question, vector)
            if item:
                t = ms()
                yield ev(phase="sentence", sentence_index=0, text=item.answer, citations=item.citations, kind=item.kind)
                yield ev(phase="done", kind=item.kind, from_brief=True, citations=item.citations, first_words_ms=t, full_ms=t)
                return
        try:
            if prep:
                passages = prep.passages.result(timeout=SEARCH_WAIT_S)
                prep.prefilled.wait(PREFILL_WAIT_S)
            else:  # no candidate, or the final text differs from it: the preparation is dropped
                passages = self.retriever.search(trig.question, vector=vector)
        except Exception as e:
            timed_out = isinstance(e, (RetrievalTimeout, TimeoutError))
            log.warning("live search failed: %r", e)
            yield ev(phase="done", kind="not_in_material", rejected=["retrieval_timeout" if timed_out else "retrieval_error"],
                     full_ms=ms())
            return
        why = ["no_passages"]
        if passages:
            for u in stream_library_answer(trig.question, passages, self.client_names, self.ollama, started, q_end):
                if isinstance(u, Delta):
                    yield ev(phase="delta", sentence_index=u.index, text=u.text)
                elif isinstance(u, Sentence):
                    yield ev(phase="sentence", sentence_index=u.index, text=u.text, citations=u.citations)
                elif isinstance(u, Done):
                    if u.check.reasons != ["none"]:  # NONE hands over to the fallback (spec 7.4 rule 1)
                        yield ev(phase="done", kind=u.check.kind, citations=u.check.citations,
                                 first_words_ms=u.first_words_ms, full_ms=u.full_ms, rejected=u.check.reasons)
                        return
            why = ["none"]
        yield from self._fallback(ev, trig.question, why, ms)

    def _brief_match(self, question: str, vector):
        """(the best brief item at or over BRIEF_MIN_COSINE or None, the question's search vector or None)."""
        if vector is None:
            try:
                vector = self.retriever.embedder.embed_query(question)
            except Exception as e:
                log.warning("brief match skipped, embed failed: %r", e)
                return None, None
        score, item = max(((_cosine(vector, b.question_vector), b) for b in self.brief_items), key=lambda x: x[0])
        return (item if score >= BRIEF_MIN_COSINE else None), vector

    def _fallback(self, ev, question: str, why: list[str], ms):
        """Claude Haiku from the question text only, labelled kind "general" (General knowledge, not from our
        material) on each sentence and on done (decision 102). Sentences stream once checked (decision 108); a block
        ends in a not_in_material done that replaces them. A blocked answer is never retried."""
        if self.fallback_mode != "claude" or _PRICE_ASK.search(question):
            extra = ["price_question"] if self.fallback_mode == "claude" else []
            yield ev(phase="done", kind="not_in_material", rejected=why + extra, full_ms=ms())
            return
        first, i = None, 0
        for r in stream_fallback(question, self.client_names, self.claude):
            if isinstance(r, str):  # a sentence that passed check_fallback with everything before it
                first = first if first is not None else ms()
                yield ev(phase="sentence", sentence_index=i, text=r, kind="general")
                i += 1
        llm = None if r.reasons == ["no_api_key"] else LlmCall(
            purpose="live_fallback", model=r.model, request=question, response=r.text or None, input_tokens=r.input_tokens,
            output_tokens=r.output_tokens, latency_ms=r.latency_ms, status=r.status, error=r.error)
        if not r.ok:  # replaces any sentence already shown
            yield ev(phase="done", kind="not_in_material", rejected=why + r.reasons, first_words_ms=first, full_ms=ms(), llm=llm)
            return
        yield ev(phase="done", kind="general", first_words_ms=first, full_ms=ms(), rejected=why, llm=llm)
