"""Live answer orchestration (spec 7.1, 7.4): a brief answer, else the library, else the labelled Claude fallback.

The pattern for the helper (and scripts/measure_e2e.py): on each Candidate call `prepare` on the pipeline's loop
thread; on each Trigger call `start` (or `handle`, then iterate it on a worker), also on the loop thread, so a later
Candidate can never replace this Trigger's preparation first. Never raises; every failure ends in a `done`."""
import logging
import queue
import re
import threading
import time
import uuid
from concurrent.futures import Future
from dataclasses import dataclass, field
from functools import partial
from typing import Callable, Iterator, Literal

import numpy as np

from fleck_core import fallback
from fleck_core.answer import Delta, Done, Sentence, Superseded, prefill, stream_library_answer
from fleck_core.events import AssistEvent, LlmCall
from fleck_core.fallback import FallbackResult, question_only, stream_fallback
from fleck_core.retrieval import RetrievalTimeout
from fleck_core.types import BriefItem
from fleck_core.validate import PRICE_ASK
from fleck_helper.pipeline import Trigger

BRIEF_MIN_COSINE = 0.85  # a live question this close to a brief question replays the brief's answer
SEARCH_WAIT_S = 2.5  # a prepared search still running is waited for up to the live search budget (retrieval.Retriever)
PREFILL_WAIT_S = 1.5  # ...and its prefill up to this, so the answer reads the warm cache instead of a cold one beside it
SLICE_S = 0.05  # a stream checks for supersede this often, even while blocked on Ollama or Claude
SUPERSEDE_WAIT_S = 0.2  # a newer stream holds its `thinking` until the older one stopped, at most this long
log = logging.getLogger(__name__)
_END = object()


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", text.lower()))


def _cosine(a, b) -> float:
    a, b = np.asarray(a, np.float64), np.asarray(b, np.float64)
    return float(a @ b / ((np.linalg.norm(a) * np.linalg.norm(b)) or 1.0))


def _llm(r: FallbackResult, ask: str, q_end: float) -> LlmCall:
    rel = lambda w: None if w is None else round((w - q_end) * 1000)  # noqa: E731
    return LlmCall(purpose="live_fallback", model=r.model, request=ask, response=r.text or None,
                   input_tokens=r.input_tokens, output_tokens=r.output_tokens, latency_ms=r.latency_ms,
                   status=r.status, error=r.error, started_at=rel(r.started or None), first_text_at=rel(r.first_text))


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
    prev: "_Live | None" = None  # the stream this one supersedes
    cancel: threading.Event = field(default_factory=threading.Event)
    finished: threading.Event = field(default_factory=threading.Event)  # its done is out: never superseded
    stopped: threading.Event = field(default_factory=threading.Event)  # it yields nothing more


class Assistant:
    def __init__(self, retriever, ollama, fallback_mode: Literal["claude", "off"], client_names: list[str],
                 brief_items: list[BriefItem], claude=None, people: list[str] = ()):
        """`claude` is an httpx client for the Messages API (tests pass a MockTransport one); None uses the default.
        `people` are the meeting's attendees (either side); the meeting's client name goes in `client_names`. Neither
        reaches Claude (decision 109a)."""
        self.retriever, self.ollama, self.fallback_mode = retriever, ollama, fallback_mode
        self.client_names, self.brief_items, self.claude = client_names, brief_items, claude
        self.people = list(people)
        self._lock = threading.Lock()
        self._prepared: _Prepared | None = None
        self._live: dict[int, _Live] = {}  # by turn_id: a late Trigger for an old turn leaves the current one alone

    def prepare(self, text: str, embedding: list[float] | None = None) -> None:
        """Start search and prefill for a Candidate on a thread; returns at once. A newer call replaces this one."""
        prep = _Prepared(_norm(text))
        with self._lock:
            if self._prepared:
                self._drop(self._prepared)
            self._prepared = prep
        if self.fallback_mode == "claude":  # no content: opens the TLS connection a fallback would use
            threading.Thread(target=fallback.warm, args=(self.claude,), daemon=True).start()
        threading.Thread(target=self._prepare, args=(prep, text, embedding), daemon=True).start()

    def _drop(self, prep: _Prepared) -> None:
        """Under the lock."""
        prep.dropped = True

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

        A Trigger with the same turn_id as a previous one whose `done` is not out yet supersedes it (decision 104):
        within SLICE_S the older stream yields Superseded() and ends, and its Ollama or Claude request is closed; the
        new one keeps its assist_id, so the card is replaced. Post each event before asking for the next (see
        AssistEvent). first_words_ms and full_ms are measured from the question's end,
        `stream_start_wall + trigger.end_ms`, or from this call when stream_start_wall is None."""
        with self._lock:
            prev = self._live.get(trigger.turn_id)
            same = prev is not None and not prev.finished.is_set()
            if same:
                prev.cancel.set()
            self._live = {t: lv for t, lv in self._live.items() if not lv.finished.is_set()}
            live = self._live[trigger.turn_id] = _Live(trigger.turn_id, prev.assist_id if same else uuid.uuid4().hex,
                                                       prev if same else None)
            prep = self._prepared
            if prep and prep.key == _norm(trigger.question):
                self._prepared = None
            elif trigger.late:
                prep = None  # the preparation is for the question being asked now, not this old one
            else:
                self._prepared = None
                if prep:
                    self._drop(prep)  # the final text differs: search again, never prefill or show this one
                prep = None
        return self._run(trigger, meeting_id, live, prep, stream_start_wall)

    def start(self, trigger: Trigger, meeting_id: str, post: Callable[[AssistEvent], None],
              stream_start_wall: float | None = None) -> threading.Thread:
        """`handle` now, on the caller's (loop) thread; its events go to `post` on a worker thread, in order.
        A superseded stream just stops. Returns the started worker."""
        events = self.handle(trigger, meeting_id, stream_start_wall)

        def work():
            for e in events:
                if isinstance(e, Superseded):
                    return
                post(e)
        th = threading.Thread(target=work, daemon=True)
        th.start()
        return th

    def _run(self, trigger, meeting_id, live: _Live, prep, start_wall):
        """The answer runs on its own thread, so a supersede cuts this stream within SLICE_S even mid-call."""
        q: queue.Queue = queue.Queue()

        def work():
            events = self._answer(trigger, meeting_id, live.assist_id, prep, start_wall, live.cancel)
            shown: dict[int, str] = {}  # what the card shows, by sentence index; the api stores it from `done`
            try:
                for e in events:
                    if e.phase == "sentence" and e.sentence_index is not None:
                        shown[e.sentence_index] = e.text
                    elif e.phase == "done":
                        text = "" if e.kind == "not_in_material" else " ".join(t for _, t in sorted(shown.items()) if t)
                        e = e.model_copy(update={"text": text})
                    q.put(e)
                    if live.cancel.is_set():
                        return
            finally:
                events.close()  # closes the Ollama or Claude stream too
                q.put(_END)
        try:
            if live.prev:
                live.prev.stopped.wait(SUPERSEDE_WAIT_S)
            threading.Thread(target=work, daemon=True).start()
            while True:
                try:
                    e = q.get(timeout=SLICE_S)
                except queue.Empty:
                    e = None
                if live.cancel.is_set() and not live.finished.is_set():
                    live.stopped.set()
                    yield Superseded()
                    return
                if e is _END:
                    return
                if e is not None:
                    if e.phase == "done":
                        live.finished.set()  # from here on a same-turn Trigger is a new card
                    yield e
        finally:
            live.finished.set()
            live.cancel.set()  # the consumer stopped early: the worker stops too
            live.stopped.set()

    def _answer(self, trig: Trigger, meeting_id, assist_id, prep: _Prepared | None, start_wall, cancel):
        started = time.time()
        q_end = started if start_wall is None else start_wall + trig.end_ms / 1000
        ms = lambda: round((time.time() - q_end) * 1000)  # noqa: E731
        ev = partial(AssistEvent, type="assist", meeting_id=meeting_id, assist_id=assist_id,
                     segment_id=trig.segment_id, question=trig.question)
        yield ev(phase="thinking", late=trig.late)
        vector = trig.embedding
        if self.brief_items:
            item, vector = self._brief_match(trig.question, vector)
            if item:
                if prep:
                    with self._lock:
                        self._drop(prep)  # no prefill for it now
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
            yield ev(phase="done", kind="not_in_material",
                     rejected=["retrieval_timeout" if timed_out else "retrieval_error"], full_ms=ms())
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
        yield from self._fallback(ev, question_only(trig.question, self.client_names, self.people), why, ms, q_end, cancel)

    def _brief_match(self, question: str, vector):
        """(the best brief item at or over BRIEF_MIN_COSINE or None, the question's search vector or None)."""
        if vector is None:
            try:
                vector = self.retriever.embedder.embed_query(question)
            except Exception as e:
                log.warning("brief match skipped, embed failed: %r", e)
                return None, None
        scored = [(_cosine(vector, b.question_vector), b) for b in self.brief_items
                  if len(b.question_vector) == len(vector)]
        if len(scored) < len(self.brief_items):
            log.warning("%d brief items skipped: question_vector is not %d long", len(self.brief_items) - len(scored), len(vector))
        score, item = max(scored, key=lambda x: x[0], default=(0.0, None))
        return (item if score >= BRIEF_MIN_COSINE else None), vector

    def _fallback(self, ev, ask: str, why: list[str], ms, q_end: float, cancel):
        """Claude Haiku from `ask` (question_only: the question sentences, no client names), labelled kind "general"
        (General knowledge, not from our material) on each delta, sentence and done (decision 102). It streams under the
        library's hold rules, each sentence replaced once checked (decision 108); a block ends in a not_in_material done
        that replaces them. Called only once the library came up empty; never retried. A price ask never reaches Claude
        (decision 107)."""
        if self.fallback_mode != "claude" or PRICE_ASK.search(ask):
            extra = ["price_question"] if self.fallback_mode == "claude" else []
            yield ev(phase="done", kind="not_in_material", rejected=why + extra, full_ms=ms())
            return
        first = None
        for r in stream_fallback(ask, self.client_names, self.claude, cancel):
            if isinstance(r, FallbackResult):
                break
            first = first if first is not None or not r.text else ms()
            yield ev(phase="delta" if isinstance(r, Delta) else "sentence", sentence_index=r.index, text=r.text,
                     kind="general")
        used = [] if r.reasons == ["no_api_key"] else [_llm(r, ask, q_end)]
        if not r.ok:  # replaces any sentence already shown
            yield ev(phase="done", kind="not_in_material", rejected=why + r.reasons, first_words_ms=first, full_ms=ms(), llm=used)
            return
        yield ev(phase="done", kind="general", first_words_ms=first, full_ms=ms(), rejected=why, llm=used)
