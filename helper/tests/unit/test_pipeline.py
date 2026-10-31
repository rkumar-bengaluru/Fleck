"""Pipeline wiring on real speech clips with a fake transcriber (texts are scripted, audio drives the VAD)."""
import json
import wave
from pathlib import Path

import numpy as np
import pytest

from fleck_core.events import SegmentEvent
from fleck_helper.audio import FRAME, Frame, Gap, Unavailable
from fleck_helper.pipeline import Candidate, Pipeline, Trigger

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


EMBED = None


@pytest.fixture(autouse=True)
def _cached(cached_embed):
    global EMBED
    EMBED = cached_embed  # the committed CI embedding cache, so no Ollama


def _run(items, texts):
    tr = FakeTranscriber(texts)
    return list(Pipeline("m1", tr, embed=EMBED).run(Src(items))), tr


def test_paused_question_triggers_once_on_the_whole_turn():
    a = _clip(1)
    audio = np.concatenate([_silence(500), a, _silence(700), a, _silence(1500)])
    out, tr = _run(_frames(audio), {"client": ["How quickly could you", "start a team on this?"]})
    segs = [e for e in out if isinstance(e, SegmentEvent)]
    trig = [e for e in out if isinstance(e, Trigger)]
    assert len(segs) == 2 and all(s.gate == "client" for s in segs)
    assert trig == [Trigger(segs[1].segment_id, "How quickly could you start a team on this?", segs[1].end_ms)]
    assert tr.active == [True, False, True, False]


def test_separate_turns_classified_apart():
    a = _clip(1)
    audio = np.concatenate([_silence(500), a, _silence(2000), a, _silence(1500)])
    out, _ = _run(_frames(audio), {"client": ["Who owns the code you write for us?", "Okay, that works for us."]})
    trig = [e for e in out if isinstance(e, Trigger)]
    assert len(trig) == 1 and trig[0].question == "Who owns the code you write for us?"


def test_unpunctuated_segment_does_not_hide_the_question(embedding_cache):
    a = _clip(1)
    audio = np.concatenate([_silence(500), a, _silence(700), a, _silence(1500)])
    search = [0.25] * 1024  # stands in for the embed_query vector the live path fetches with the classify vector

    def embed(q):  # the first segment's own Candidate look isn't in the CI cache: the rules decide it
        if q not in embedding_cache:
            raise RuntimeError("not cached")
        return EMBED(q)[0], search
    tr = FakeTranscriber({"client": ["we close our budget cycle just before that", "Who owns the code you write for us?"]})
    out = list(Pipeline("m1", tr, embed=embed).run(Src(_frames(audio))))
    trig = [e for e in out if isinstance(e, Trigger)]
    # the whole turn, with a full stop where Whisper left the first segment unpunctuated
    assert [e.question for e in trig] == ["we close our budget cycle just before that. Who owns the code you write for us?"]
    assert trig[0].embedding == search  # the search vector travels on to Tasks 13 and 14 (decision 94)


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


def _qclip(qid):
    return _clip(next(i for i, t in enumerate(REF["turns"]) if t["question_id"] == qid))


def _two_segments(first_mic=0.0, second_mic=0.0, a=None):
    """A first segment (default: two questions, 200 ms apart), a 700 ms pause, a short second one; mic noise at the given level."""
    a = np.concatenate([_qclip("q_rate"), _silence(200), _qclip("q_sla")]) if a is None else a
    b = _clip(1)[: 16 * 900]
    rng = np.random.default_rng(0)
    client = np.concatenate([_silence(500), a, _silence(700), b, _silence(1500)])
    you = np.zeros_like(client)
    a0, b0 = 500 * 16, (500 + 900) * 16 + len(a)
    you[a0:a0 + len(a)] = rng.normal(0, first_mic, len(a)) if first_mic else 0
    you[b0:b0 + len(b)] = rng.normal(0, second_mic, len(b)) if second_mic else 0
    return _frames(client.astype(np.float32), you=you.astype(np.float32))


def test_turn_speaker_is_its_client_share_not_its_last_segment():
    q = "What does a senior engineer cost per month?"
    out, _ = _run(_two_segments(second_mic=0.2), {"client": [q, "offshore"]})  # a talk-over on the short tail
    gates = [e.gate for e in out if isinstance(e, SegmentEvent)]
    assert gates == ["client", "overlap"]
    assert [e.question for e in out if isinstance(e, Trigger)] == [q]  # the overlap tail adds no text


def test_long_overlapped_tail_does_not_veto_a_clean_question():
    q = "Who owns the code you write for us?"  # ~1.8 s question, ~0.9 s overlapped tail: under 75% client time
    out, _ = _run(_two_segments(second_mic=0.2, a=_qclip("q_ip")), {"client": [q, "mm-hm, and the IP"]})
    assert [e.gate for e in out if isinstance(e, SegmentEvent)] == ["client", "overlap"]
    assert [e.question for e in out if isinstance(e, Trigger)] == [q]


def test_overlapped_question_with_a_clean_tail_does_not_trigger():
    out, _ = _run(_two_segments(first_mic=0.2), {"client": ["What does a senior engineer cost per month?", "Is that right?"]})
    assert [e.gate for e in out if isinstance(e, SegmentEvent)] == ["overlap", "client"]
    assert not any(isinstance(e, Trigger) for e in out)


def _with_gap_in_pause(stream):
    a = _clip(1)
    frames = _frames(np.concatenate([_silence(500), a, _silence(700), a, _silence(1500)]))
    i = (500 + len(a) // 16 + 300) // 20  # 300 ms into the pause
    return frames[:i] + [Gap(frames[i].t_ms, frames[i].t_ms, stream)] + frames[i:]


def test_you_gap_does_not_split_a_client_turn():
    out, _ = _run(_with_gap_in_pause("you"), {"client": ["How quickly could you", "have a team working on this?"]})
    assert [e.question for e in out if isinstance(e, Trigger)] == ["How quickly could you have a team working on this?"]


def test_client_gap_drops_the_whole_turn():
    out, _ = _run(_with_gap_in_pause("client"), {"client": ["Who owns the code you write for us?", "And the IP?"]})
    assert not any(isinstance(e, Trigger) for e in out)


def test_last_segment_gets_its_own_look_when_a_preamble_dilutes_the_turn(monkeypatch):
    """Replay showed q_ip lost behind "The 15th of November would suit us. We close our budget cycle…" (decision 95).
    The whole turn scores between the floor and the threshold; the last segment alone scores as the question it is."""
    from fleck_helper import pipeline
    from fleck_helper.classify import QuestionVerdict
    q = "Who owns the code you write for us?"
    preamble = "The 15th of November would suit us. We close our budget cycle just before that."
    calls = []

    def fake(text, speaker, embed):
        calls.append(text)
        return QuestionVerdict(True, 0.9 if text == q else 0.3, "model", text)
    monkeypatch.setattr(pipeline, "classify", fake)
    a = _clip(1)
    audio = np.concatenate([_silence(500), a, _silence(700), a, _silence(1500)])
    out = list(Pipeline("m1", FakeTranscriber({"client": [preamble, q]}), 0.6, embed=EMBED).run(Src(_frames(audio))))
    assert [e.question for e in out if isinstance(e, Trigger)] == [q]
    # the first final's own look, then the whole turn and its last segment at the second final; the turn's close
    # reuses that verdict. One trigger, at most two looks per classification
    assert calls == [preamble, f"{preamble} {q}", q]
    assert [e.text for e in out if isinstance(e, Candidate)] == [q]


def test_no_second_look_when_the_whole_turn_is_plainly_logistics():
    """Re-review 2: the last segment alone scores as a question; the whole turn shows it is call logistics."""
    calls = []

    def embed(text):
        calls.append(text)
        return EMBED(text)
    a = _clip(1)
    audio = np.concatenate([_silence(500), a, _silence(700), a, _silence(1500)])
    texts = ["I just shared the deck in the chat.", "Are you able to open it on your side?"]
    out = list(Pipeline("m1", FakeTranscriber({"client": list(texts)}), embed=embed).run(Src(_frames(audio))))
    assert not any(isinstance(e, Trigger) for e in out)
    assert calls == [" ".join(texts)]


def test_candidate_at_the_final_and_the_trigger_reuses_its_verdict():
    a = _clip(1)
    audio = np.concatenate([_silence(500), a, _silence(1500)])
    calls = []

    def embed(text):
        calls.append(text)
        return EMBED(text)
    q = "How quickly could you start a team on this?"
    out = list(Pipeline("m1", FakeTranscriber({"client": [q]}), embed=embed).run(Src(_frames(audio))))
    kinds = [type(e).__name__ for e in out]
    assert kinds == ["SegmentEvent", "Candidate", "Trigger"]
    assert out[1] == Candidate(out[0].segment_id, q) and out[2].question == q
    assert calls == [q]  # classified once, at the final


def test_turn_id_changes_only_when_the_vendor_speaks():
    a = _clip(1)
    q = "How quickly could you start a team on this?"
    gap = _silence(1500)  # longer than MERGE_MS + PREROLL_MS: three separate pipeline turns
    client = np.concatenate([_silence(500), a, gap, a, gap, np.zeros_like(a), gap, a, gap])
    you = np.concatenate([_silence(500), np.zeros_like(a), gap, np.zeros_like(a), gap, a, gap, np.zeros_like(a), gap])
    tr = FakeTranscriber({"client": [q, q, q], "you": ["We can start in two weeks."]})
    out = list(Pipeline("m1", tr, embed=EMBED).run(Src(_frames(client, you))))
    ids = [e.turn_id for e in out if isinstance(e, Trigger)]
    assert len(ids) == 3 and ids[0] == ids[1] != ids[2]  # no vendor speech between the first two


def test_degraded_while_the_rules_decide():
    def down(text):
        raise ConnectionError("ollama down")
    items = _frames(np.concatenate([_silence(500), _clip(1), _silence(1500)]))
    q = "Who owns the code you write for us?"
    p = Pipeline("m1", FakeTranscriber({"client": [q]}), embed=down)
    list(p.run(Src(items)))
    assert p.degraded and p.fallbacks >= 1
    p = Pipeline("m1", FakeTranscriber({"client": [q]}), embed=EMBED)
    list(p.run(Src(items)))
    assert not p.degraded and p.fallbacks == 0


def test_garbled_bleed_on_the_mic_is_not_a_you_line():
    """Whisper on echo-cancelled bleed hears other words ("Something great to describe. How can we?" for "If
    something breaks at night…"): no duplicate to match, but the gate says the mic held only bleed, so it is dropped."""
    c = np.concatenate([_silence(500), _clip(1), _silence(1500)])
    out, _ = _run(_frames(c, you=c * 0.14), {"client": ["Who owns the code you write for us?"],
                                             "you": ["Something great to describe. How can we?"]})
    assert [e.stream for e in out if isinstance(e, SegmentEvent)] == ["client"]


def test_you_speaking_over_quiet_client_audio_is_kept():
    c = np.concatenate([_silence(500), _clip(1), _silence(1500)])
    out, _ = _run(_frames(np.zeros_like(c), you=c), {"you": ["Who owns the code you write for us?"]})
    assert [(e.stream, e.gate) for e in out if isinstance(e, SegmentEvent)] == [("you", "you")]


# Late recheck (Task 19b, decision 128): a turn the rules said no to while the embedder was down or slow is
# classified again off the loop, and a late Trigger comes if the model then says it is a question.
import logging  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

from fleck_helper import classify as clf  # noqa: E402
from fleck_helper import pipeline as pl  # noqa: E402

RULES_NO = "Who owns the code you write for us?"  # the strict rules veto "us"; the model scores 0.97
RULES_YES = "What does a senior engineer cost per month?"


@pytest.fixture(autouse=True)
def _quick_retry(monkeypatch):
    monkeypatch.setattr(pl, "LATE_RETRY_S", 0.2)
    monkeypatch.setattr(clf, "_open_until", 0.0)


def _outcome(p):
    return any(k != "started" for k in getattr(p, "rechecks", {}))


class TailSrc:
    """`items`, then silence frames until `until()` holds (10 s cap), so off-loop work finishes mid-run without a
    fixed wall-clock budget. `pace` > 0 hands frames over in real time; `times` holds when each tail frame went."""
    def __init__(self, items, until, pace=0.0):
        self.items, self.until, self.pace, self.times = items, until, pace, []

    def frames(self):
        yield from self.items
        t, cap = self.items[-1].t_ms + 20, time.monotonic() + 10
        while not self.until() and time.monotonic() < cap:
            time.sleep(self.pace or 0.002)  # 2 ms: lets the recheck thread run
            self.times.append(time.monotonic())
            yield Frame(t, np.zeros(FRAME, np.float32), np.zeros(FRAME, np.float32))
            t += 20


def _down_for(seconds, calls=None):
    """An embed that times out until `seconds` after its first call, then answers from the CI cache."""
    first = []

    def embed(text):
        first.append(first[0] if first else time.monotonic())
        if calls is not None:
            calls.append((time.monotonic(), text))
        if time.monotonic() - first[0] < seconds:
            raise TimeoutError("embeds over 1000 ms")
        return EMBED(text)
    return embed


def _items():
    return _frames(np.concatenate([_silence(500), _clip(1), _silence(1500)]))


def _one_turn(text, embed, until=_outcome, pace=0.0):
    p = Pipeline("m1", FakeTranscriber({"client": [text]}), embed=embed)
    src = TailSrc(_items(), lambda: until(p), pace)
    return list(p.run(src)), p, src


def test_rules_no_turn_gets_a_late_trigger_once_the_embedder_recovers(caplog):
    with caplog.at_level(logging.INFO):
        out, p, _ = _one_turn(RULES_NO, _down_for(1.0))
    seg = next(e for e in out if isinstance(e, SegmentEvent))
    trig = [e for e in out if isinstance(e, Trigger)]
    assert trig == [Trigger(seg.segment_id, RULES_NO, seg.end_ms)]
    assert trig[0].late and trig[0].turn_id == 0
    assert p.rechecks["started"] == 1 and p.rechecks["late trigger"] == 1
    assert any("late recheck" in r.message for r in caplog.records)


def test_no_recheck_when_the_rules_triggered():
    out, p, _ = _one_turn(RULES_YES, _down_for(60), until=lambda p: True)
    trig = [e for e in out if isinstance(e, Trigger)]
    assert len(trig) == 1 and not trig[0].late and not p.rechecks


def test_non_question_rechecks_and_emits_nothing():
    out, p, _ = _one_turn("Okay, that works for us.", _down_for(0.3))
    assert not any(isinstance(e, Trigger) for e in out)
    assert p.rechecks["started"] == 1 and p.rechecks["not a question"] == 1


def test_hard_vetoed_turn_is_never_rechecked():
    out, p, _ = _one_turn("Can you hear me okay?", _down_for(60), until=lambda p: True)
    assert not any(isinstance(e, Trigger) for e in out) and not p.rechecks


def test_no_recheck_when_the_session_is_rules_only():
    out, p, _ = _one_turn(RULES_NO, clf.rules_only, until=lambda p: True)
    assert not any(isinstance(e, Trigger) for e in out) and not p.rechecks


def test_recheck_gives_up_when_the_embedder_stays_down(monkeypatch, caplog):
    monkeypatch.setattr(pl, "LATE_GIVE_UP_S", 1.0)
    with caplog.at_level(logging.INFO):
        out, p, _ = _one_turn(RULES_NO, _down_for(60))
    assert not any(isinstance(e, Trigger) for e in out)
    assert p.rechecks["gave up"] == 1
    assert any("gave up" in r.message for r in caplog.records)


def test_an_answer_arriving_after_the_give_up_limit_is_dropped(monkeypatch):
    monkeypatch.setattr(pl, "LATE_GIVE_UP_S", 0.5)
    calls = []

    def embed(text):  # the live look fails; the late look answers yes, but only after the limit
        calls.append(text)
        if len(calls) == 1:
            raise TimeoutError("embeds over 1000 ms")
        time.sleep(0.8)
        return EMBED(text)
    out, p, _ = _one_turn(RULES_NO, embed)
    assert not any(isinstance(e, Trigger) for e in out) and p.rechecks["gave up"] == 1


def test_frames_keep_flowing_while_a_recheck_waits():
    calls = []

    def embed(text):  # the live look fails at once; the recheck's call hangs 1.5 s, then answers
        calls.append(text)
        if len(calls) == 1:
            raise TimeoutError("embeds over 1000 ms")
        time.sleep(1.5)
        return EMBED(text)
    out, p, src = _one_turn(RULES_NO, embed, pace=0.02)  # real time here: the gaps are what is measured
    assert len(src.times) > 50 and max(b - a for a, b in zip(src.times, src.times[1:])) < 0.2
    assert [e.late for e in out if isinstance(e, Trigger)] == [True]


def test_first_late_try_ignores_the_breaker_and_later_ones_wait_it_out():
    calls = []

    def embed(text):
        calls.append(time.monotonic())
        if len(calls) == 2:  # the first late try: at once, though the breaker is open; it fails and the
            clf._open_until = clf._clock() + 0.4  # breaker is opened again by a live look meanwhile...
            threading.Timer(0.2, lambda: setattr(clf, "_open_until", clf._clock() + 0.6)).start()  # ...and again
        if len(calls) <= 2:
            raise TimeoutError("embeds over 1000 ms")
        return EMBED(text)
    clf._open_until = clf._clock() + 10  # a live look just timed out
    out, p, _ = _one_turn(RULES_NO, embed)
    assert calls[1] - calls[0] < 0.5  # no 10 s wait before the first late try
    assert calls[2] - calls[1] >= 0.75  # waited out the re-read breaker (0.2 + 0.6 s), not just 0.4 s
    assert [e.late for e in out if isinstance(e, Trigger)] == [True]


def test_one_late_embed_in_flight():
    a, gap = _clip(1), _silence(1500)
    client = np.concatenate([_silence(500), a, gap, np.zeros_like(a), gap, a, gap])
    you = np.concatenate([_silence(500), np.zeros_like(a), gap, a, gap, np.zeros_like(a), gap])
    live, peak, n, lock = [0], [0], [0], threading.Lock()

    def embed(text):
        with lock:
            n[0] += 1
            if n[0] <= 2:  # each turn's live look
                raise TimeoutError("embeds over 1000 ms")
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.3)
        with lock:
            live[0] -= 1
        return EMBED(text)
    tr = FakeTranscriber({"client": [RULES_NO, RULES_NO], "you": ["We can start in two weeks."]})
    p = Pipeline("m1", tr, embed=embed)
    out = list(p.run(TailSrc(_frames(client, you), lambda: p.rechecks["late trigger"] == 2)))
    assert sorted(t.turn_id for t in out if isinstance(t, Trigger)) == [0, 1] and peak[0] == 1


def test_newer_trigger_for_the_same_turn_supersedes_the_late_one():
    a, gap = _clip(1), _silence(1500)
    items = _frames(np.concatenate([_silence(500), a, gap, a, gap]))  # two client turns, no vendor speech between
    p = Pipeline("m1", FakeTranscriber({"client": [RULES_NO, RULES_YES]}), embed=_down_for(1.0))
    out = list(p.run(TailSrc(items, lambda: _outcome(p))))
    trig = [e for e in out if isinstance(e, Trigger)]
    assert [(t.question, t.late) for t in trig] == [(RULES_YES, False)]  # the rules caught the second turn first
    assert p.rechecks["superseded"] == 1


def test_a_new_client_turn_does_not_cancel_the_late_recheck():
    a, gap = _clip(1), _silence(1500)
    client = np.concatenate([_silence(500), a, gap, np.zeros_like(a), gap, a, gap])
    you = np.concatenate([_silence(500), np.zeros_like(a), gap, a, gap, np.zeros_like(a), gap])
    tr = FakeTranscriber({"client": [RULES_NO, "Okay, that works for us."], "you": ["We can start in two weeks."]})
    p = Pipeline("m1", tr, embed=_down_for(1.0))
    out = list(p.run(TailSrc(_frames(client, you), lambda: p.rechecks["late trigger"] > 0)))
    trig = [e for e in out if isinstance(e, Trigger)]
    assert [(t.question, t.late, t.turn_id) for t in trig] == [(RULES_NO, True, 0)]  # the vendor spoke since: still shown


def test_a_recheck_still_waiting_at_the_end_is_counted_stopped():
    out, p, _ = _one_turn(RULES_NO, _down_for(60), until=lambda p: p.rechecks["started"] > 0)
    assert p.rechecks["stopped"] == 1 and p._stop.is_set()


def test_a_run_that_dies_still_stops_its_rechecks():
    p = Pipeline("m1", FakeTranscriber({"client": [RULES_NO]}), embed=_down_for(60))

    def frames():
        yield from TailSrc(_items(), lambda: p.rechecks["started"] > 0).frames()
        raise OSError("capture died")
    with pytest.raises(OSError):
        list(p.run(type("S", (), {"frames": staticmethod(frames)})()))
    assert p._stop.is_set() and p.rechecks["stopped"] == 1


def test_production_recheck_uses_live_embed_late_with_the_long_timeout(monkeypatch):
    """No injected embed: warm-up, the 1 s live look and the 5 s late look all go through live_embed, with the two
    Ollama calls stubbed (embed_classify's httpx post and Embedder.embed_query)."""
    from fleck_core import retrieval
    timeouts = []

    def classify_embed(texts, timeout=clf.EMBED_TIMEOUT_S):
        timeouts.append(timeout)
        if timeout == clf.EMBED_TIMEOUT_S:
            raise clf.httpx.ConnectError("refused")  # the live look: opens the breaker for 10 s
        return [EMBED(texts[0])[0] if texts[0] != "warm up" else [0.0]]
    monkeypatch.setattr(clf, "embed_classify", classify_embed)
    monkeypatch.setattr(retrieval.Embedder, "embed_query", lambda self, t: [0.2])
    monkeypatch.setattr(pl, "digests_ok", lambda: True)
    out, p, _ = _one_turn(RULES_NO, None)
    assert timeouts[0] == 30 and clf.EMBED_TIMEOUT_S in timeouts and timeouts[-1] == pl.LATE_TIMEOUT_S
    trig = [e for e in out if isinstance(e, Trigger)]
    assert [(t.question, t.late, t.embedding) for t in trig] == [(RULES_NO, True, [0.2])]
