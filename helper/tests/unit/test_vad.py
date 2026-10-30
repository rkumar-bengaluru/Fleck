import json
import wave
from pathlib import Path

import numpy as np

from fleck_helper.audio import FRAME, Frame
from fleck_helper.vad import Final, Open, Partial, Segmenter

CONV = Path(__file__).parents[3] / "fixtures/conversation"


def _client_turn(i: int) -> np.ndarray:
    t = json.loads((CONV / "reference.json").read_text())["turns"][i]
    assert t["speaker"] == "client"
    with wave.open(str(CONV / "conversation.wav")) as w:
        w.setpos(t["start_ms"] * 16)
        raw = w.readframes((t["end_ms"] - t["start_ms"]) * 16)
    return np.frombuffer(raw, np.int16).reshape(-1, 2)[:, 1].astype(np.float32) / 32768


def _run(seg: Segmenter, client: np.ndarray) -> list:
    out = []
    for i in range(len(client) // FRAME):
        out += seg.push(Frame(i * 20, np.zeros(FRAME, np.float32), client[i * FRAME:(i + 1) * FRAME]))
    return out + seg.flush()


def test_segmenter_splits_on_silence():
    a, b = _client_turn(1), _client_turn(3)
    gap = np.zeros(16000, np.float32)
    ev = _run(Segmenter("client"), np.concatenate([gap, a, gap, b, gap]))
    finals = [e for e in ev if isinstance(e, Final)]
    assert len(finals) == 2
    assert [e.segment_id for e in ev if isinstance(e, Open)] == [f.segment_id for f in finals]
    assert all(f.segment_id == f"client-{f.start_ms}" for f in finals)
    assert 800 <= finals[0].start_ms <= 1100 and finals[1].start_ms > finals[0].end_ms + 500  # 1 s gap less the 200 ms pre-roll
    assert all(len(f.audio) == (f.end_ms - f.start_ms) * 16 for f in finals)
    assert any(isinstance(e, Partial) for e in ev)


def test_segmenter_caps_at_20s():
    noise = np.random.default_rng(0).normal(0, 0.1, 16000 * 25).astype(np.float32)  # webrtcvad calls this speech
    ev = _run(Segmenter("client"), noise)
    finals = [e for e in ev if isinstance(e, Final)]
    assert finals[0].end_ms - finals[0].start_ms == 20_000 and len(finals[0].audio) == 20 * 16000
    assert len(finals) == 2 and finals[1].start_ms == 20_000
    partials = [e for e in ev if isinstance(e, Partial) and e.segment_id == finals[0].segment_id]
    assert len(partials) == 19  # one per second of speech, the 20th second is the Final


def test_segmenter_reads_only_its_stream():
    noise = np.random.default_rng(0).normal(0, 0.1, FRAME * 50).astype(np.float32)
    seg = Segmenter("you")
    assert sum((seg.push(Frame(i * 20, np.zeros(FRAME, np.float32), noise[i * FRAME:(i + 1) * FRAME])) for i in range(50)), []) == []
