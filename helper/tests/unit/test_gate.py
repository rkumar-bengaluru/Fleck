from types import SimpleNamespace as S

import numpy as np

from fleck_helper.gate import drop_bleed_duplicates, frame_rms, gate

LOUD, BLEED, QUIET = 0.2, 0.005, 0.0005


def _rms(level, n=100, seed=0):
    return frame_rms(np.random.default_rng(seed).normal(0, level, 320 * n).astype(np.float32))


def test_gate_client_only():
    assert gate(_rms(BLEED), _rms(LOUD)) == "client"
    assert gate(np.full(50, QUIET), np.full(50, LOUD)) == "client"


def test_gate_overlap_when_both_loud():
    assert gate(_rms(LOUD), _rms(LOUD, seed=1)) == "overlap"
    assert gate(_rms(LOUD * 0.5), _rms(LOUD)) == "overlap"  # client louder, but the mic isn't quiet


def test_gate_you_only():
    assert gate(_rms(LOUD), _rms(QUIET)) == "you"


def test_gate_smooths_a_click():
    you = np.full(100, QUIET)
    you[50] = 0.1  # one 20 ms click on the mic is averaged over 200 ms
    assert gate(you, np.full(100, LOUD)) == "client"


def test_bleed_you_duplicates_dropped():
    clients = [S(start_ms=10_000, text="What does a senior engineer cost per month, offshore?"),
               S(start_ms=30_000, text="And who owns the code?")]
    assert drop_bleed_duplicates(S(start_ms=10_400, text="what does a senior engineer cost per month offshore"), clients)
    assert drop_bleed_duplicates(S(start_ms=11_900, text="What does the senior engineer cost per month, offshore?"), clients)
    assert not drop_bleed_duplicates(S(start_ms=13_000, text="What does a senior engineer cost per month, offshore?"), clients)
    assert not drop_bleed_duplicates(S(start_ms=10_400, text="Around six thousand nine hundred a month."), clients)
    assert not drop_bleed_duplicates(S(start_ms=10_400, text=""), clients)
