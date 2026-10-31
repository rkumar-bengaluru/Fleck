"""Live pipeline (spec 7.2): source -> Segmenters -> gate + transcription -> classify -> Candidate, Trigger.

A Candidate comes at each client final whose turn-so-far classifies as a question, so the answer's search and
prefill can start before the turn closes (spec 7.1 item 2, decision 103); the Trigger still comes once per turn, when
it closes, and reuses the Candidate's verdict when the text is the same. Classification runs on this loop: at most
two looks of <= 1 s each per final (decision 128), which LiveSource's queue absorbs (decision 105). Events come out in audio
order; transcription runs off the loop thread because the transcriber holds You jobs while a Client segment is
open, and only this loop can close it. A turn the rules fallback said no to is classified again off the loop
(`_recheck`), and if the model then calls it a question a late Trigger joins the output (decision 128)."""
import logging
import queue
import threading
from collections import Counter, deque
from concurrent.futures import Future
from dataclasses import dataclass, field
from functools import partial
from typing import Iterator

import numpy as np

from fleck_core.events import SegmentEvent
from fleck_helper.audio import Frame, Gap, Unavailable
from fleck_helper import classify as _clf, question_rules
from fleck_helper.classify import Embed, classify, digests_ok, live_embed, rules_only, trigger_threshold
from fleck_helper.gate import drop_bleed_duplicates, gate, noise_floor
from fleck_helper.vad import MAX_MS, PREROLL_MS, SILENCE_MS, Discard, Final, Open, Segmenter

MERGE_MS = 600  # same-speaker finals this close are one turn: a client pausing mid-question triggers once
FLOOR_FRAMES = 500  # the mic noise floor is taken over the last 10 s
SECOND_LOOK_MIN = 0.1  # under this the whole turn is decisively not a question: no second look (ruled 0.2; the q_ip replay turn scores 0.14)
# Late recheck: a 5 s look (5x the live budget) on its own threads, given up 30 s after the turn closed
LATE_TIMEOUT_S = 5.0
LATE_GIVE_UP_S = 30.0
LATE_RETRY_S = 1.0
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Trigger:
    segment_id: str  # the turn's last segment
    question: str  # the turn's client-gated text
    end_ms: int
    # Embedder.embed_query(question) (0.6b), for search and brief matching (Tasks 13, 14); None if the rules decided
    embedding: list[float] | None = field(default=None, repr=False, compare=False)
    # Client turns with no You speech between them share a turn_id: a later Trigger with the same turn_id replaces
    # the earlier answer (one card per turn, decision 104)
    turn_id: int = field(default=0, compare=False)
    late: bool = field(default=False, compare=False)  # from the off-loop recheck, after the rules said no


@dataclass(frozen=True)
class Candidate:
    """The client's turn so far classifies as a question: start the answer's search and prefill (never shown)."""
    segment_id: str
    text: str
    embedding: list[float] | None = field(default=None, repr=False, compare=False)


class _Seg:
    def __init__(self, stream, final: Final, gate_, future, cut: bool):
        self.stream, self.final, self.gate, self.future, self.cut = stream, final, gate_, future, cut
        self.turn: _Turn | None = None  # the client turn it belongs to

    def event(self, meeting_id) -> SegmentEvent:
        f = self.final
        return SegmentEvent(type="segment", meeting_id=meeting_id, segment_id=f.segment_id, stream=self.stream,
                            start_ms=f.start_ms, end_ms=f.end_ms, text=self.text(), final=True, gate=self.gate)

    def text(self) -> str:
        try:
            return self.future.result()
        except Exception:
            log.exception("transcription failed for %s", self.final.segment_id)
            return ""


class _Turn(list):
    """Client segments classified together once the turn is over."""
    turn_id = 0


def _spawn(fn, *args) -> Future:
    # a thread per segment, not a pool: a full pool would queue a client final behind held You jobs
    f = Future()

    def run():
        try:
            f.set_result(fn(*args))
        except Exception as e:
            f.set_exception(e)

    threading.Thread(target=run, daemon=True).start()
    return f


class Pipeline:
    def __init__(self, meeting_id: str, transcriber=None, min_confidence: float | None = None, embed: Embed | None = None):
        """`embed` defaults to the live embedder with the 300 ms budget (over it, the rules decide)."""
        if transcriber is None:
            from fleck_helper.transcribe import StreamTranscriber
            transcriber = StreamTranscriber()
        self.meeting_id, self.tr, self.embed = meeting_id, transcriber, embed
        self.min_confidence = trigger_threshold() if min_confidence is None else min_confidence

    def run(self, source) -> Iterator[SegmentEvent | Candidate | Trigger | Gap | Unavailable]:
        self.degraded = False  # the rules decided the latest classification, or will (the menu bar shows it)
        if self.embed is None:
            try:  # load the embedding model before the first question, not during it
                if digests_ok():
                    live_embed("warm up", timeout=30, warm=True)
                else:
                    self.embed = rules_only
                    self.degraded = True
            except Exception as e:
                log.warning("embedder warm-up failed: %r", e)
                self.degraded = True
        self.fallbacks = 0  # classify calls the rules decided this run (embedder down, slow, or wrong digest)
        self.rechecks = Counter()  # late rechecks this run: "started", then one outcome each (logged)
        self._late, self._stop = queue.SimpleQueue(), threading.Event()
        self._closed, self._triggered = 0, {}  # turns closed so far; turn_id -> index of the turn it last triggered on
        segs = {"you": Segmenter("you"), "client": Segmenter("client")}
        # (t_ms, you, client) per frame: the longest segment plus the silence that ends it
        self._rms = deque(maxlen=(MAX_MS + SILENCE_MS + PREROLL_MS) // 20 + 10)
        self._out: deque = deque()  # _Seg | _Turn | Gap | Unavailable, in audio order
        self._turn, self._client_open, self._recent_client = _Turn(), False, deque(maxlen=8)
        self._turn_no, self._last_verdict = 0, ((), None, False)  # (texts, verdict, rules decided) of the latest classification
        for item in source.frames():
            if isinstance(item, Frame):
                self._rms.append((item.t_ms, _rms(item.you), _rms(item.client)))
                for stream in ("client", "you"):
                    for ev in segs[stream].push(item):
                        self._handle(stream, ev)
                if self._turn and not self._client_open and item.t_ms >= self._turn[-1].final.end_ms + MERGE_MS + PREROLL_MS:
                    # an Open reaches us up to PREROLL_MS after its start_ms
                    self._close_turn()
            else:
                if isinstance(item, Gap):  # audio cut off mid-turn is transcribed but never triggers, not even its first half
                    if item.stream in ("client", "both"):
                        self._turn = _Turn()
                    for stream in ("client", "you") if item.stream == "both" else (item.stream,):
                        for ev in segs[stream].flush():
                            self._handle(stream, ev, cut=True)
                self._out.append(item)
            yield from self._ready(block=False)
        for stream in ("client", "you"):
            for ev in segs[stream].flush():
                self._handle(stream, ev)
        self._close_turn()
        yield from self._ready(block=True)
        self._stop.set()  # the meeting is over: a late answer has nowhere to go
        if left := self.rechecks["started"] - sum(n for k, n in self.rechecks.items() if k != "started"):
            self.rechecks["stopped"] += left
            log.info("late recheck: %d still waiting when the run ended", left)
        log.info("question classifier: %d rules fallbacks, late rechecks %s this run", self.fallbacks, dict(self.rechecks))

    def _handle(self, stream, ev, cut=False):
        if isinstance(ev, Open) and stream == "client":
            self.tr.set_client_active(True)
            self._client_open = True
            if self._turn and ev.start_ms - self._turn[-1].final.end_ms > MERGE_MS:
                self._close_turn()
        elif isinstance(ev, Final):
            rows = [r for r in self._rms if ev.start_ms <= r[0] < ev.end_ms]
            floor = noise_floor(np.array([r[1] for r in list(self._rms)[-FLOOR_FRAMES:]]))
            g = gate(np.array([r[1] for r in rows]), np.array([r[2] for r in rows]), silence=floor)
            seg = _Seg(stream, ev, g, _spawn(self.tr.transcribe, ev.audio, True, stream, ev.segment_id), cut)
            self._out.append(seg)
            if stream == "you" and g == "you":
                self._turn_no += 1  # the vendor spoke: the client's next words are a new turn
            if stream == "client" and not cut:
                if not self._turn:
                    self._turn.turn_id = self._turn_no
                self._turn.append(seg)
                seg.turn = self._turn
        if stream == "client" and isinstance(ev, (Final, Discard)):
            self.tr.set_client_active(False)
            self._client_open = False

    def _close_turn(self):
        if self._turn:
            self._out.append(self._turn)
            self._turn = _Turn()

    def _ready(self, block: bool):
        yield from self._late_results()
        while self._out:
            head = self._out[0]
            pending = head if isinstance(head, _Turn) else [head] if isinstance(head, _Seg) else []
            if not block and not all(s.future.done() for s in pending):
                return
            self._out.popleft()
            if isinstance(head, _Seg):
                ev = head.event(self.meeting_id)
                if ev.stream == "client":
                    self._recent_client.append(ev)
                elif head.gate == "client" or drop_bleed_duplicates(ev, list(self._recent_client) + self._queued_client()):
                    # the client's voice leaking into the mic: the gate found only bleed under it, or Whisper heard it
                    # nearly word for word
                    continue
                if ev.text:
                    yield ev
                if head.turn is not None and head.gate == "client" and ev.text:
                    # the turn so far, up to this segment (its earlier segments came out before it)
                    v, _ = self._verdict(head.turn[: head.turn.index(head) + 1])
                    if v:
                        yield Candidate(ev.segment_id, v.question, v.embedding)
            elif isinstance(head, _Turn):
                self._closed += 1
                v, fell = self._verdict(head)
                if v:
                    self._triggered[head.turn_id] = self._closed
                    yield Trigger(head[-1].final.segment_id, v.question, head[-1].final.end_ms, v.embedding, head.turn_id)
                elif fell and self.embed is not rules_only:  # rules_only: the digest is wrong, the model can't judge
                    self._start_recheck(head)
            else:
                yield head

    def _verdict(self, segs):
        """(the turn's verdict if it triggers, else None; whether the rules fallback decided it). The same texts as
        last time reuse that verdict, so a turn whose Candidate came at its last final is not classified twice."""
        texts = self._texts(segs)
        if not texts:
            return None, False
        if texts != self._last_verdict[0]:
            self._last_verdict = (texts, *self._decide(texts, self._classify))
        return self._last_verdict[1:]

    @staticmethod
    def _texts(segs) -> tuple[str, ...]:
        # only client-gated segments supply text, so the question sentence is always clean client speech;
        # an overlapped segment elsewhere in the turn is left out, it doesn't veto the turn
        return tuple(t for s in segs if s.gate == "client" and (t := s.text().strip()))

    def _decide(self, texts, look):
        """(the verdict if `texts` trigger, else None; whether the rules fallback decided any look)."""
        # a full stop after a segment Whisper left unpunctuated, so "…just before that" doesn't glue onto the
        # next segment's "Who owns…?"; none after a cut-off one, so "How quickly could you" + "start?" stays whole
        text = " ".join(t if t[-1] in ".?!" or question_rules.is_fragment(question_rules._norm(t)) else t + "."
                        for t in texts)
        hit = lambda v: v.is_question and v.confidence >= self.min_confidence  # noqa: E731
        looks = [look(text)]
        if not hit(looks[0]) and len(texts) > 1 and looks[0].confidence >= SECOND_LOOK_MIN:
            # statements before a pause dilute the turn ("…just before that. Who owns the code?"), so the
            # last segment gets its own look; still one trigger per turn. Not when the whole turn is plainly
            # logistics ("I shared the deck in the chat. Can you open it on your side?" scores 0.00)
            looks.append(look(texts[-1]))
        return (looks[-1] if hit(looks[-1]) else None), any(v.reason.startswith("rules fallback") for v in looks)

    def _start_recheck(self, turn: _Turn):
        """The rules said no while the embedder was down or slow: ask the model again on a thread of its own, so
        the loop never waits; its outcome comes back through `_late`."""
        info = (turn.turn_id, self._closed, turn[-1].final.segment_id, turn[-1].final.end_ms)
        self.rechecks["started"] += 1
        log.info("late recheck: turn %d (segment %s) rules said no; re-classifying off the loop", info[0], info[2])
        threading.Thread(target=self._recheck, args=(self._texts(turn), info), daemon=True).start()

    def _recheck(self, texts, info):
        embed = self.embed or partial(live_embed, timeout=LATE_TIMEOUT_S, late=True)
        look = lambda t: classify(t, "client", embed)  # noqa: E731
        give_up, tries = _clf._clock() + LATE_GIVE_UP_S, 0
        while True:
            # an open breaker means a live look just timed out: wait it out rather than add to the load
            pause = max(_clf._open_until - _clf._clock(), LATE_RETRY_S if tries else 0.0)
            if _clf._clock() + pause >= give_up:
                return self._late.put((info, "gave up", None))
            if self._stop.wait(pause):
                return
            tries += 1
            v, fell = self._decide(texts, look)
            if not fell:
                return self._late.put((info, "late trigger" if v else "not a question", v))

    def _late_results(self):
        """Recheck outcomes, counted and logged on the loop thread; a late Trigger comes out here, between events."""
        while True:
            try:
                (turn_id, idx, segment_id, end_ms), outcome, v = self._late.get_nowait()
            except queue.Empty:
                return
            if v and self._triggered.get(turn_id, 0) > idx:
                outcome, v = "superseded", None  # its turn already has a newer answer
            self.rechecks[outcome] += 1
            log.info("late recheck: turn %d (segment %s): %s", turn_id, segment_id, outcome)
            if v:
                self._triggered[turn_id] = idx
                yield Trigger(segment_id, v.question, end_ms, v.embedding, turn_id, late=True)

    def _classify(self, text):
        v = classify(text, "client", self.embed)
        fell = v.reason.startswith("rules fallback")
        self.fallbacks += fell
        if v.reason != "no question-shaped sentence":  # that one never asked the embedder
            self.degraded = fell
        return v

    def _queued_client(self) -> list[SegmentEvent]:
        # a client final queued behind this You final is already transcribed when it overlaps it (it ran first)
        return [s.event(self.meeting_id) for s in self._out
                if isinstance(s, _Seg) and s.stream == "client" and s.future.done()]


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
