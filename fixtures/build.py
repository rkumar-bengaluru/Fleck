# /// script
# requires-python = ">=3.12"
# dependencies = ["numpy>=2", "pyyaml>=6"]
# ///
"""Build the scripted test audio with macOS `say`. Same scripts -> same bytes. Run: make fixtures"""
import hashlib
import json
import random
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).parent
SR = 16000
OVERLAP_S, QUESTION_GAP_S, DEFAULT_GAP_S = 0.4, 2.5, 0.6
_cache: dict[tuple[str, str], np.ndarray] = {}
_tmp = Path(tempfile.mkdtemp(prefix="fleck-say-"))


def speak(voice: str, text: str) -> np.ndarray:
    """One utterance as int16 mono 16 kHz, leading/trailing silence trimmed (keeps 30 ms tail)."""
    key = (voice, text)
    if key not in _cache:
        out = _tmp / (hashlib.sha1(f"{voice}|{text}".encode()).hexdigest() + ".wav")
        subprocess.run(["say", "-v", voice, "-o", str(out), "--file-format=WAVE", "--data-format=LEI16@16000", text], check=True)
        with wave.open(str(out)) as w:
            assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (SR, 1, 2)
            x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        loud = np.flatnonzero(np.abs(x.astype(np.int32)) > 150)
        assert loud.size, f"silent audio for {text!r}"
        _cache[key] = x[loud[0]:min(loud[-1] + 1 + SR * 30 // 1000, len(x))]
    return _cache[key]


def write_wav(path: Path, left: np.ndarray, right: np.ndarray) -> None:
    n = max(len(left), len(right))
    st = np.zeros((n, 2), dtype="<i2")
    st[:len(left), 0], st[:len(right), 1] = left, right
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2), w.setsampwidth(2), w.setframerate(SR)
        w.writeframes(st.tobytes())


class Timeline:
    def __init__(self):
        self.chan = {"you": [], "client": []}  # (start_sample, audio)
        self.turns = []
        self.end = 0  # samples

    def add(self, who, text, voice, start, **extra):
        a = speak(voice, text)
        self.chan[who].append((start, a))
        self.end = max(self.end, start + len(a))
        self.turns.append({"speaker": who, "start_ms": round(start * 1000 / SR), "end_ms": round((start + len(a)) * 1000 / SR),
                           "text": text, "question_id": extra.pop("question_id", None), **extra})
        return start + len(a)

    def render(self):
        out = {}
        for who, parts in self.chan.items():
            buf = np.zeros(self.end, dtype=np.int32)
            for s, a in parts:  # additive mix
                buf[s:s + len(a)] += a
            out[who] = np.clip(buf, -32768, 32767).astype(np.int16)
        return out["you"], out["client"]


def bleed(left: np.ndarray, right: np.ndarray) -> np.ndarray:
    """Call audio leaking into the mic: right channel at -14 dB, 30 ms delay, 250 ms decaying reverb tail."""
    rng = np.random.default_rng(1234)
    t = np.arange(SR // 4) / SR
    ir = rng.standard_normal(len(t)) * np.exp(-t / 0.06) * 0.35
    ir[0] = 1.0
    n = len(right) + len(ir)
    wet = np.fft.irfft(np.fft.rfft(right.astype(np.float64), n) * np.fft.rfft(ir, n), n)[:len(right)]
    wet = np.concatenate([np.zeros(SR * 30 // 1000), wet])[:len(right)] * 10 ** (-14 / 20)
    return np.clip(left + np.rint(wet), -32768, 32767).astype(np.int16)


def build_conversation():
    sc = yaml.safe_load((ROOT / "conversation/script.yaml").read_text())
    v, tl, cursor, prev_q = sc["voices"], Timeline(), 0, False
    questions = []
    for i, t in enumerate(sc["turns"]):
        gap = t.get("gap", QUESTION_GAP_S if prev_q else DEFAULT_GAP_S)
        start = round(cursor + gap * SR) if i else 0
        if t.get("overlap"):
            start = cursor - round(OVERLAP_S * SR)
        cursor = tl.add(t["who"], t["text"], v[t["who"]], start, question_id=t.get("q"))
        prev_q = "q" in t
        if prev_q:
            questions.append({"id": t["q"], "text": t["text"], "end_ms": tl.turns[-1]["end_ms"],
                              "expect": t["expect"], "doc": t["doc"], "used": t["used"]})
    left, right = tl.render()
    d = ROOT / "conversation"
    write_wav(d / "conversation.wav", left, right)
    write_wav(d / "conversation-bleed.wav", bleed(left, right), right)
    ref = {"duration_ms": round(tl.end * 1000 / SR), "turns": tl.turns, "questions": questions}
    (d / "reference.json").write_text(json.dumps(ref, indent=1, ensure_ascii=False) + "\n")
    return d, tl


def build_negative():
    sc = yaml.safe_load((ROOT / "negative/script.yaml").read_text())
    v = sc["voices"]
    voice_of = lambda i: ("you", "client")[i % 2]  # noqa: E731
    items = [("short", sc["short"][i % len(sc["short"])], ("you", "client")[(i // len(sc["short"])) % 2]) for i in range(120)]
    items += [("medium", s, w) for s in sc["medium"] for w in ("you", "client")]
    items += [("logistics", s, voice_of(i)) for i, s in enumerate(sc["logistics"])]
    random.Random(sc["seed"]).shuffle(items)
    tl, cursor = Timeline(), 0
    for i, (cat, text, who) in enumerate(items):
        start = cursor + round(sc["gaps"][i % len(sc["gaps"])] * SR)
        cursor = tl.add(who, text, v[who], start, category=cat)
    for t in tl.turns:  # medium is nominally 2-3 s; voices differ, so allow 1.5-3.5 s
        dur = t["end_ms"] - t["start_ms"]
        assert dur < 1000 if t["category"] == "short" else 1500 <= dur <= 3500 if t["category"] == "medium" else True, (dur, t["text"])
    left, right = tl.render()
    write_wav(ROOT / "negative/negative.wav", left, right)
    ref = {"duration_ms": round(tl.end * 1000 / SR), "turns": tl.turns, "questions": []}
    (ROOT / "negative/reference.json").write_text(json.dumps(ref, indent=1, ensure_ascii=False) + "\n")
    return tl


if __name__ == "__main__":
    d, tl = build_conversation()
    print(f"conversation: {tl.end / SR:.1f} s ({tl.end / SR / 60:.2f} min), {len(tl.turns)} turns")
    for t in json.loads((d / "reference.json").read_text())["questions"]:
        print(f"  {t['id']}: question ends {t['end_ms'] / 1000:.2f} s")
    nl = build_negative()
    print(f"negative: {nl.end / SR:.1f} s ({nl.end / SR / 60:.2f} min), {len(nl.turns)} segments")
