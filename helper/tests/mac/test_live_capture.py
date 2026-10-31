"""Live capture through the speakers (spec 7.0, 7.2): the client's voice comes in on system audio, its bleed into the
mic never becomes a You line. Plays about 10 s out loud: run it rarely, with FLECK_SPEAKERS=1."""
import json
import os
import re
import shutil
import subprocess
import wave
from difflib import SequenceMatcher

import numpy as np
import pytest

from bundle import ROOT, needs_bundle, run_in_bundle

# it plays audio out loud, so it never runs in a plain `pytest` run
pytestmark = needs_bundle + [pytest.mark.skipif(not os.environ.get("FLECK_SPEAKERS"), reason="plays audio: set FLECK_SPEAKERS=1")]
CONV = ROOT / "fixtures/conversation"
QUESTIONS = ("q_rate", "q_start", "q_sla")  # 8.7 s of the client's voice, 0.8 s apart: separate turns
GAP_MS = 800


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower().replace(",", ""))


def _client_clip(path):
    ref = json.loads((CONV / "reference.json").read_text())
    turns = [t for t in ref["turns"] if t["question_id"] in QUESTIONS]
    with wave.open(str(CONV / "conversation.wav")) as w:
        rate = w.getframerate()
        parts = []
        for t in turns:
            w.setpos(t["start_ms"] * rate // 1000)
            parts += [np.frombuffer(w.readframes((t["end_ms"] - t["start_ms"]) * rate // 1000), np.int16).reshape(-1, 2)[:, 1],
                      np.zeros(GAP_MS * rate // 1000, np.int16)]
    with wave.open(str(path), "wb") as o:
        o.setnchannels(1), o.setsampwidth(2), o.setframerate(rate)
        o.writeframes(np.concatenate(parts).tobytes())
    return " ".join(t["text"] for t in turns)


def _rms(x) -> float:
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64)))) if len(x) else 0.0


def test_live_capture_speakers(tmp_path):
    pytest.importorskip("mlx_whisper")
    from fleck_core.events import SegmentEvent
    from fleck_helper.audio import ReplaySource
    from fleck_helper.classify import rules_only
    from fleck_helper.pipeline import Pipeline
    from fleck_helper.transcribe import StreamTranscriber

    reference = _client_clip(tmp_path / "client.wav")
    run_in_bundle("--capture-test", "--play", str(tmp_path / "client.wav"), "--out", str(tmp_path), timeout=120)
    keep = ROOT / ".superpowers/live-capture"  # kept for offline checks (pytest's tmp dirs rotate away)
    keep.mkdir(parents=True, exist_ok=True)
    for f in ("capture.wav", "capture.json", "client.wav"):
        shutil.copy(tmp_path / f, keep / f)
    info = json.loads((tmp_path / "capture.json").read_text())
    assert "error" not in info and not info["unavailable"], info
    with wave.open(str(tmp_path / "capture.wav")) as w:
        x = np.frombuffer(w.readframes(w.getnframes()), np.int16).reshape(-1, 2).astype(np.float32) / 32768
    a, b = info["play_start_ms"] * 16, info["play_end_ms"] * 16
    loud = np.abs(x[a:b, 1]) > 0.02  # samples where the client is speaking
    client_rms, you_rms = _rms(x[a:b, 1][loud]), _rms(x[a:b, 0][loud])
    volume = subprocess.run(["osascript", "-e", "output volume of (get volume settings)"], capture_output=True, text=True).stdout.strip()
    print(f"live_capture: client RMS {client_rms:.4f}, mic RMS over the same samples {you_rms:.4f}, "
          f"echo ratio {you_rms / max(client_rms, 1e-9):.3f} (speaker gate cut-off 0.3), output volume {volume}, gaps {info['gaps']}")
    assert client_rms > 0.01

    events = list(Pipeline("live-test", StreamTranscriber(), embed=rules_only).run(ReplaySource(tmp_path / "capture.wav", realtime=False)))
    segs = [e for e in events if isinstance(e, SegmentEvent)]
    client = " ".join(e.text for e in segs if e.stream == "client")
    ref_w, got_w = _words(reference), _words(client)
    matched = sum(m.size for m in SequenceMatcher(None, ref_w, got_w).get_matching_blocks())
    you = [e.text for e in segs if e.stream == "you"]
    dups = [t for t in you if SequenceMatcher(None, _words(t), got_w).find_longest_match().size >= max(3, len(_words(t)) // 2)]
    print(f"live_capture: client words {matched}/{len(ref_w)}; client {client!r}; you lines {you!r}")
    assert matched >= 0.8 * len(ref_w)
    assert not dups, dups
