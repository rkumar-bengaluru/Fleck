"""Speaker gate (spec 7.2) and bleed de-duplication.

client = sys_energy > mic_energy x ratio AND mic_energy < silence over most of the segment; "you" mirrors it;
anything else is overlap.
Thresholds are starting values; Task 12 tunes them on the bleed recording (decision 25).
"""
import os
import re
from typing import Literal

import numpy as np

FLECK_GATE_SILENCE = float(os.environ.get("FLECK_GATE_SILENCE", "0.02"))
SMOOTH_FRAMES = 10  # 200 ms of 20 ms frames
MIN_WORDS, MIN_LENGTH_RATIO = 3, 0.6  # a bleed duplicate repeats most of the client's sentence


def frame_rms(audio: np.ndarray, frame: int = 320) -> np.ndarray:
    """RMS per 20 ms frame, the gate's input."""
    x = audio[: len(audio) // frame * frame].reshape(-1, frame)
    return np.sqrt((x.astype(np.float64) ** 2).mean(axis=1))


def _smooth(rms: np.ndarray) -> np.ndarray:
    k = min(SMOOTH_FRAMES, len(rms))
    return np.convolve(rms, np.ones(k) / k, mode="valid")


def gate(you_rms: np.ndarray, client_rms: np.ndarray, ratio: float = 1.5,
         silence: float = FLECK_GATE_SILENCE, share: float = 0.75) -> Literal["client", "you", "overlap"]:
    """Per 200 ms window, a stream dominates when it is ratio x louder and the other is under `silence`.
    A stream wins the segment when it dominates `share` of the windows where anything is audible,
    so one "mm-hm" or cough on the mic doesn't turn a client question into overlap."""
    n = min(len(you_rms), len(client_rms))
    if n == 0:
        return "overlap"
    you, client = _smooth(np.asarray(you_rms[:n])), _smooth(np.asarray(client_rms[:n]))
    audible = (you >= silence) | (client >= silence)
    if not audible.any():
        audible[:] = True
    if ((client > you * ratio) & (you < silence))[audible].mean() >= share:
        return "client"
    if ((you > client * ratio) & (client < silence))[audible].mean() >= share:
        return "you"
    return "overlap"


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def drop_bleed_duplicates(you_final, recent_client_finals, window_ms: int = 2000, min_overlap: float = 0.8) -> bool:
    """True if a You final is the client's speech leaking into the mic: it has >= 3 words, is >= 0.6 of the
    length of a Client final starting within window_ms, and that final holds at least min_overlap of its words.
    Finals are anything with start_ms and text."""
    words = _words(you_final.text)
    if len(words) < MIN_WORDS:  # "Okay." / "Yes, sure." are real replies, never bleed
        return False
    for c in recent_client_finals:
        theirs = _words(c.text)
        if abs(you_final.start_ms - c.start_ms) <= window_ms and len(words) >= MIN_LENGTH_RATIO * len(theirs):
            if sum(w in set(theirs) for w in words) / len(words) >= min_overlap:
                return True
    return False
