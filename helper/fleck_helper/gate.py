"""Speaker gate (spec 7.2) and bleed de-duplication.

client = sys_energy > mic_energy x ratio AND mic_energy < silence; "you" mirrors it; anything else is overlap.
Thresholds are starting values; Task 12 tunes them on the bleed recording (decision 25).
"""
import os
import re
from typing import Literal

import numpy as np

FLECK_GATE_SILENCE = float(os.environ.get("FLECK_GATE_SILENCE", "0.02"))
SMOOTH_FRAMES = 10  # 200 ms of 20 ms frames


def frame_rms(audio: np.ndarray, frame: int = 320) -> np.ndarray:
    """RMS per 20 ms frame, the gate's input."""
    x = audio[: len(audio) // frame * frame].reshape(-1, frame)
    return np.sqrt((x.astype(np.float64) ** 2).mean(axis=1))


def _level(rms: np.ndarray) -> float:
    if len(rms) == 0:
        return 0.0
    k = min(SMOOTH_FRAMES, len(rms))
    return float(np.convolve(rms, np.ones(k) / k, mode="valid").max())  # loudest 200 ms of the window


def gate(you_rms: np.ndarray, client_rms: np.ndarray, ratio: float = 1.5,
         silence: float = FLECK_GATE_SILENCE) -> Literal["client", "you", "overlap"]:
    you, client = _level(you_rms), _level(client_rms)
    if client > you * ratio and you < silence:
        return "client"
    if you > client * ratio and client < silence:
        return "you"
    return "overlap"


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def drop_bleed_duplicates(you_final, recent_client_finals, window_ms: int = 2000, min_overlap: float = 0.8) -> bool:
    """True if a You final is the client's speech leaking into the mic: a Client final starting within
    window_ms holds at least min_overlap of its words. Finals are anything with start_ms and text."""
    words = _words(you_final.text)
    if not words:
        return False
    for c in recent_client_finals:
        if abs(you_final.start_ms - c.start_ms) <= window_ms:
            theirs = set(_words(c.text))
            if sum(w in theirs for w in words) / len(words) >= min_overlap:
                return True
    return False
