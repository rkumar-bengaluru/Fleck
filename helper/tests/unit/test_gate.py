from types import SimpleNamespace as S

import numpy as np

from fleck_helper.gate import drop_bleed_duplicates, frame_rms, gate, noise_floor

LOUD, BLEED, QUIET = 0.2, 0.005, 0.0005


def _rms(level, n=100, seed=0):
    return frame_rms(np.random.default_rng(seed).normal(0, level, 320 * n).astype(np.float32))


def test_gate_client_only():
    assert gate(_rms(BLEED), _rms(LOUD)) == "client"
    assert gate(np.full(50, QUIET), np.full(50, LOUD)) == "client"


def test_gate_overlap_when_both_loud():
    assert gate(_rms(LOUD), _rms(LOUD, seed=1)) == "overlap"
    assert gate(_rms(LOUD * 0.7), _rms(LOUD)) == "overlap"  # client louder, but not ratio x louder


def test_gate_bleed_cap_and_adaptive_floor():
    """Decision 93: the mic may hold the client only as bleed (<= 0.3x), over a floor that adapts to mic noise."""
    flat = lambda level: np.full(50, level)
    assert gate(flat(0.45 * 0.2), flat(0.45)) == "client"  # loud client, bleed at 0.2x
    assert gate(flat(0.137), flat(0.3)) == "overlap"  # your voice under a louder client: double-talk, never a trigger
    assert gate(flat(0.15), flat(0.3)) == "overlap"  # weak echo cancellation (0.5x): known limit, precision first
    assert gate(flat(0.15), flat(0.15)) == "overlap"  # equal-energy talk-over
    for client, mic in [(0.03, 0.01), (0.02, 0.012), (0.03, 0.02)]:  # quiet client, slightly noisy mic
        assert gate(flat(mic), flat(client), silence=noise_floor(np.full(500, mic))) == "client"
    assert noise_floor(np.zeros(500)) == 0.005 and noise_floor(np.array([])) == 0.005


def test_gate_you_only():
    assert gate(_rms(LOUD), _rms(QUIET)) == "you"


def test_gate_smooths_a_click():
    you = np.full(100, QUIET)
    you[50] = 0.1  # one 20 ms click on the mic is averaged over 200 ms
    assert gate(you, np.full(100, LOUD)) == "client"


def test_gate_client_question_survives_a_mic_blip():
    import json, wave
    from pathlib import Path
    conv = Path(__file__).parents[3] / "fixtures/conversation"
    turn = next(t for t in json.loads((conv / "reference.json").read_text())["turns"] if t["question_id"] == "q_rate")
    with wave.open(str(conv / "conversation.wav")) as w:
        w.setpos(turn["start_ms"] * 16)
        client = np.frombuffer(w.readframes((turn["end_ms"] - turn["start_ms"]) * 16), np.int16)[1::2] / 32768
    you = np.random.default_rng(0).normal(0, QUIET, len(client))
    you[16000:16000 + 4800] = np.random.default_rng(1).normal(0, LOUD, 4800)  # a 300 ms "mm-hm" on the mic
    assert gate(frame_rms(you), frame_rms(client)) == "client"
    you[:] = np.random.default_rng(1).normal(0, LOUD, len(you))  # talking over the whole question
    assert gate(frame_rms(you), frame_rms(client)) == "overlap"


def test_bleed_you_duplicates_dropped():
    clients = [S(start_ms=10_000, text="What does a senior engineer cost per month, offshore?"),
               S(start_ms=30_000, text="And who owns the code?")]
    assert drop_bleed_duplicates(S(start_ms=10_400, text="what does a senior engineer cost per month offshore"), clients)
    assert drop_bleed_duplicates(S(start_ms=11_900, text="What does the senior engineer cost per month, offshore?"), clients)
    assert not drop_bleed_duplicates(S(start_ms=13_000, text="What does a senior engineer cost per month, offshore?"), clients)
    assert not drop_bleed_duplicates(S(start_ms=10_400, text="Around six thousand nine hundred a month."), clients)
    assert not drop_bleed_duplicates(S(start_ms=10_400, text=""), clients)
    okay = [S(start_ms=10_000, text="Okay, so what does a senior engineer cost?")]
    assert not drop_bleed_duplicates(S(start_ms=10_500, text="Okay."), okay)  # under 3 words
    assert not drop_bleed_duplicates(S(start_ms=10_500, text="So what does"), okay)  # under 0.6 of its length
