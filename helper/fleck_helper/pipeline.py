"""Live pipeline, first version (spec 7.2): source -> Segmenters -> gate + transcription -> classify -> Trigger.

Answers come later (Task 13). Events come out in audio order; transcription runs off the loop thread because
the transcriber holds You jobs while a Client segment is open, and only this loop can close it."""
import logging
import threading
from collections import deque
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Iterator

import numpy as np

from fleck_core.events import SegmentEvent
from fleck_helper.audio import Frame, Gap, Unavailable
from fleck_helper.classify import FLECK_TRIGGER_MIN_CONFIDENCE, triggers
from fleck_helper.gate import drop_bleed_duplicates, gate
from fleck_helper.vad import MAX_MS, PREROLL_MS, SILENCE_MS, Discard, Final, Open, Segmenter

MERGE_MS = 600  # same-speaker finals this close are one turn: a client pausing mid-question triggers once
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Trigger:
    segment_id: str  # the turn's last segment
    question: str  # the whole turn's text
    end_ms: int


class _Seg:
    def __init__(self, stream, final: Final, gate_, future, cut: bool):
        self.stream, self.final, self.gate, self.future, self.cut = stream, final, gate_, future, cut

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
    def __init__(self, meeting_id: str, transcriber=None, min_confidence: float = FLECK_TRIGGER_MIN_CONFIDENCE):
        if transcriber is None:
            from fleck_helper.transcribe import StreamTranscriber
            transcriber = StreamTranscriber()
        self.meeting_id, self.tr, self.min_confidence = meeting_id, transcriber, min_confidence

    def run(self, source) -> Iterator[SegmentEvent | Trigger | Gap | Unavailable]:
        segs = {"you": Segmenter("you"), "client": Segmenter("client")}
        # (t_ms, you, client) per frame: the longest segment plus the silence that ends it
        self._rms = deque(maxlen=(MAX_MS + SILENCE_MS + PREROLL_MS) // 20 + 10)
        self._out: deque = deque()  # _Seg | _Turn | Gap | Unavailable, in audio order
        self._turn, self._client_open, self._recent_client = _Turn(), False, deque(maxlen=8)
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
                if isinstance(item, Gap):  # audio cut off mid-segment is transcribed but never triggers
                    self._close_turn()
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

    def _handle(self, stream, ev, cut=False):
        if isinstance(ev, Open) and stream == "client":
            self.tr.set_client_active(True)
            self._client_open = True
            if self._turn and ev.start_ms - self._turn[-1].final.end_ms > MERGE_MS:
                self._close_turn()
        elif isinstance(ev, Final):
            rows = [r for r in self._rms if ev.start_ms <= r[0] < ev.end_ms]
            g = gate(np.array([r[1] for r in rows]), np.array([r[2] for r in rows]))
            seg = _Seg(stream, ev, g, _spawn(self.tr.transcribe, ev.audio, True, stream, ev.segment_id), cut)
            self._out.append(seg)
            if stream == "client" and not cut:
                self._turn.append(seg)
        if stream == "client" and isinstance(ev, (Final, Discard)):
            self.tr.set_client_active(False)
            self._client_open = False

    def _close_turn(self):
        if self._turn:
            self._out.append(self._turn)
            self._turn = _Turn()

    def _ready(self, block: bool):
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
                elif drop_bleed_duplicates(ev, list(self._recent_client) + self._queued_client()):
                    continue  # the client's voice leaking into the mic
                if ev.text:
                    yield ev
            elif isinstance(head, _Turn):
                texts = [t for s in head if (t := s.text().strip())]
                # a segment Whisper left unpunctuated must not glue onto the next one's question
                judged = " ".join(t if t[-1] in ".?!" else t + "." for t in texts)
                if triggers(judged, head[-1].gate, self.min_confidence):
                    text = " ".join(texts)
                    yield Trigger(head[-1].final.segment_id, text, head[-1].final.end_ms)
            else:
                yield head

    def _queued_client(self) -> list[SegmentEvent]:
        # a client final queued behind this You final is already transcribed when it overlaps it (it ran first)
        return [s.event(self.meeting_id) for s in self._out
                if isinstance(s, _Seg) and s.stream == "client" and s.future.done()]


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
