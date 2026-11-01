import json
import re
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

from fleck_core.numbers import normalize_numbers

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="mlx-whisper needs Apple silicon")
CONV = Path(__file__).parents[3] / "fixtures/conversation"


def _words(text: str) -> list[str]:
    return re.findall(r"\d+\.\d+|[a-z0-9%']+", normalize_numbers(text).lower())  # punctuation is not a word


def wer(ref: str, hyp: str) -> float:
    r, h = _words(ref), _words(hyp)
    d = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(h, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
    return d[-1] / len(r)


def test_wer_helper():
    assert wer("a b c d", "a b c d") == 0 and wer("a b c d", "a x c") == 0.5


def test_transcribe_question_clip():
    pytest.importorskip("mlx_whisper")
    from fleck_helper.transcribe import StreamTranscriber

    turn = next(t for t in json.loads((CONV / "reference.json").read_text())["turns"] if t["question_id"] == "q_rate")
    with wave.open(str(CONV / "conversation.wav")) as w:
        w.setpos(turn["start_ms"] * 16)
        raw = w.readframes((turn["end_ms"] - turn["start_ms"]) * 16)
    clip = np.frombuffer(raw, np.int16).reshape(-1, 2)[:, 1].astype(np.float32) / 32768
    text = StreamTranscriber().transcribe(clip, final=True)
    score = wer(turn["text"], text)
    print(f"q_rate WER {score:.3f}: {text!r}")
    assert score <= 0.10
