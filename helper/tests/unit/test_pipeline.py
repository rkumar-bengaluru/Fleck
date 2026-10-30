"""Pipeline wiring on real speech clips with a fake transcriber (texts are scripted, audio drives the VAD)."""
import json
import wave
from pathlib import Path

import numpy as np

from fleck_core.events import SegmentEvent
from fleck_helper.audio import FRAME, Frame, Gap, Unavailable
from fleck_helper.pipeline import Pipeline, Trigger

CONV = Path(__file__).parents[3] / "fixtures/conversation"
REF = json.loads((CONV / "reference.json").read_text())


def _clip(i: int) -> np.ndarray:
    t = REF["turns"][i]
    with wave.open(str(CONV / "conversation.wav")) as w:
        w.setpos(t["start_ms"] * 16)
        raw = w.readframes((t["end_ms"] - t["start_ms"]) * 16)
    return np.frombuffer(raw, np.int16).reshape(-1, 2)[:, 1].astype(np.float32) / 32768


def _silence(ms):
    return np.zeros(ms * 16, np.float32)


class FakeTranscriber:
    def __init__(self, texts):
        self.texts, self.calls, self.active = dict(texts), [], []

    def set_client_active(self, a):
        self.active.append(a)

    def transcribe(self, audio, final, speaker="client", segment=None):
        self.calls.append(speaker)
        return self.texts[speaker].pop(0)


class Src:
    def __init__(self, items):
        self.items = items

    def frames(self):
        yield from self.items


def _frames(client, you=None, t0=0):
    you = _silence(len(client) // 16) if you is None else you
    return [Frame(t0 + i * 20, you[i * FRAME:(i + 1) * FRAME], client[i * FRAME:(i + 1) * FRAME])
            for i in range(len(client) // FRAME)]


def _run(items, texts):
    tr = FakeTranscriber(texts)
    return list(Pipeline("m1", tr).run(Src(items))), tr


def test_paused_question_triggers_once_on_the_whole_turn():
    a = _clip(1)
    audio = np.concatenate([_silence(500), a, _silence(700), a, _silence(1500)])
    out, tr = _run(_frames(audio), {"client": ["How quickly could you", "have a team working on this?"]})
    segs = [e for e in out if isinstance(e, SegmentEvent)]
    trig = [e for e in out if isinstance(e, Trigger)]
    assert len(segs) == 2 and all(s.gate == "client" for s in segs)
    assert trig == [Trigger(segs[1].segment_id, "How quickly could you have a team working on this?", segs[1].end_ms)]
    assert tr.active == [True, False, True, False]


def test_separate_turns_classified_apart():
    a = _clip(1)
    audio = np.concatenate([_silence(500), a, _silence(2000), a, _silence(1500)])
    out, _ = _run(_frames(audio), {"client": ["Who owns the code you write for us?", "Okay, that works for us."]})
    trig = [e for e in out if isinstance(e, Trigger)]
    assert len(trig) == 1 and trig[0].question == "Who owns the code you write for us?"


def test_unpunctuated_segment_does_not_hide_the_question():
    a = _clip(1)
    audio = np.concatenate([_silence(500), a, _silence(700), a, _silence(1500)])
    out, _ = _run(_frames(audio), {"client": ["we close our budget cycle just before that", "Who owns the code you write for us?"]})
    assert [e.question for e in out if isinstance(e, Trigger)] == [
        "we close our budget cycle just before that Who owns the code you write for us?"]


def test_gap_mid_question_never_triggers():
    a = _clip(next(i for i, t in enumerate(REF["turns"]) if t["question_id"] == "q_rate"))
    frames = _frames(np.concatenate([_silence(500), a]))
    cut = frames[: len(frames) * 2 // 3]
    gap = Gap(cut[-1].t_ms + 20, cut[-1].t_ms + 3000, "client")
    after = _frames(_silence(2000), t0=gap.end_ms)
    out, tr = _run(cut + [gap] + after, {"client": ["What does a senior engineer cost per month?"]})
    assert gap in out and not any(isinstance(e, Trigger) for e in out)
    assert [e.text for e in out if isinstance(e, SegmentEvent)] == ["What does a senior engineer cost per month?"]  # would trigger if whole
    assert tr.active[:2] == [True, False] and tr.active[-1] is False  # webrtcvad's hangover may open (and discard) one more


def test_unavailable_passes_through_and_mic_goes_on():
    un = Unavailable("client", 0, "TCC")
    you = np.concatenate([_silence(500), _clip(1), _silence(1500)])
    out, tr = _run([un] + _frames(_silence(len(you) // 16), you=you), {"you": ["Can you hear me okay?"]})
    assert out[0] == un and tr.calls == ["you"] and tr.active == []
    seg = [e for e in out if isinstance(e, SegmentEvent)]
    assert len(seg) == 1 and seg[0].stream == "you" and seg[0].gate == "you"
    assert not any(isinstance(e, Trigger) for e in out)


def test_bleed_duplicate_dropped():
    c = np.concatenate([_silence(500), _clip(1), _silence(1500)])
    q = "What does a senior engineer cost per month, offshore?"
    out, _ = _run(_frames(c, you=c * 0.2), {"client": [q], "you": [q]})
    assert [e.stream for e in out if isinstance(e, SegmentEvent)] == ["client"]
