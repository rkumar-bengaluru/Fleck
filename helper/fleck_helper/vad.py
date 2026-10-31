"""VAD segmenter: one per stream, turns 20 ms frames into Open / Partial / Final | Discard events (spec 7.2).

Every Open is closed by exactly one Final or Discard with the same segment_id."""
from collections import deque
from dataclasses import dataclass
from typing import Literal

import numpy as np
import webrtcvad

from fleck_helper.audio import SR, Frame

PARTIAL_MS = 1000  # a Partial per this much speech
SILENCE_MS = 500  # silence that ends a segment
MAX_MS = 20_000  # a segment is cut here (also bounds its buffer, spec 7.7)
PREROLL_MS = 200  # audio kept before the first speech frame, so onsets aren't clipped
MIN_SPEECH_MS = 300  # less voiced audio than this (a click, a chime) is discarded; counts webrtcvad's ~100 ms hangover


@dataclass(frozen=True)
class Open:
    """A segment started; the pipeline uses this to tell the transcriber a Client segment is active."""
    segment_id: str
    start_ms: int


@dataclass(frozen=True)
class Partial:
    segment_id: str
    start_ms: int
    audio: np.ndarray


@dataclass(frozen=True)
class Final:
    segment_id: str
    start_ms: int
    end_ms: int
    audio: np.ndarray


@dataclass(frozen=True)
class Discard:
    """An opened segment had too little speech; closes the Open without a Final."""
    segment_id: str


SegEvent = Open | Partial | Final | Discard


class Segmenter:
    def __init__(self, stream: Literal["you", "client"]):
        self.stream = stream
        self._vad = webrtcvad.Vad(2)
        self._pre: deque[Frame] = deque(maxlen=PREROLL_MS // 20)
        self._seg: list[np.ndarray] | None = None

    def push(self, frame: Frame) -> list[SegEvent]:
        x = frame.you if self.stream == "you" else frame.client
        speech = self._vad.is_speech(np.clip(x * 32768, -32768, 32767).astype("<i2").tobytes(), SR)
        if self._seg is None:
            self._pre.append(frame)
            if not speech:
                return []
            pre = [getattr(f, self.stream) for f in self._pre]
            self._pre.clear()
            self._start = frame.t_ms - (len(pre) - 1) * 20
            self._id = f"{self.stream}-{self._start}"
            self._seg, self._speech_ms, self._end, self._silence = pre, 20, frame.t_ms + 20, 0
            return [Open(self._id, self._start)]
        self._seg.append(x)
        if speech:
            self._speech_ms += 20
            self._end, self._silence = frame.t_ms + 20, 0
        else:
            self._silence += 20
        if self._silence >= SILENCE_MS or frame.t_ms + 20 - self._start >= MAX_MS:
            return self.flush()
        if speech and self._speech_ms % PARTIAL_MS == 0:
            return [Partial(self._id, self._start, np.concatenate(self._seg))]
        return []

    def flush(self) -> list[SegEvent]:
        """Closes the open segment (also at the end of the recording): a Final, or a Discard if it was too short."""
        if self._seg is None:
            return []
        if self._speech_ms < MIN_SPEECH_MS:
            self._seg = None
            return [Discard(self._id)]
        audio = np.concatenate(self._seg)[: (self._end - self._start) * SR // 1000]
        self._seg = None
        return [Final(self._id, self._start, self._end, audio)]
