import threading
import time
import wave

import numpy as np

import logging

import pytest

from fleck_helper.audio import FRAME, SR, AudioChunkWriter, Frame, Gap, LiveSource, ReplaySource, Resampler, Unavailable


def _wav(path, secs, rate=16000):
    n = int(secs * rate)
    left = (np.arange(n) % 100).astype(np.int16)  # distinct channels to check the mapping
    right = -left
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(np.column_stack([left, right]).astype("<i2").tobytes())
    return path


def test_replay_source_realtime_pacing(tmp_path):
    src = ReplaySource(_wav(tmp_path / "a.wav", 2.0), realtime=True)
    t0 = time.monotonic()
    frames = list(src.frames())
    took = time.monotonic() - t0
    assert len(frames) == 100 and 1.9 <= took <= 2.2
    assert [f.t_ms for f in frames[:3]] == [0, 20, 40]
    f = frames[1]
    assert f.you.dtype == np.float32 and len(f.you) == len(f.client) == FRAME
    assert f.you[1] > 0 and np.allclose(f.client, -f.you)  # left = You, right = Client


def test_replay_source_resamples(tmp_path):
    frames = list(ReplaySource(_wav(tmp_path / "b.wav", 1.0, rate=48000), realtime=False).frames())
    assert len(frames) == 50 and all(len(f.you) == FRAME for f in frames)


def test_replay_source_loops_keep_time_increasing(tmp_path):
    frames = list(ReplaySource(_wav(tmp_path / "c.wav", 0.5), realtime=False, loops=2).frames())
    assert [f.t_ms for f in frames] == [i * 20 for i in range(50)]


@pytest.mark.parametrize("rate", [44100, 48000, 22050, 24000])
def test_resampler_no_drift_over_60s(rate):
    rs, out, n_in = Resampler(rate), 0, 0
    for _ in range(60 * rate // 1024):
        y, lead = rs(np.zeros(1024, np.float32))
        assert abs((n_in / rate + lead) * SR - out) < 1  # each buffer's timestamp matches the samples so far
        out, n_in = out + len(y), n_in + 1024
    assert abs(out - n_in * SR / rate) < 0.02 * SR


def test_chunk_writer_10s_chunks(tmp_path):
    w = AudioChunkWriter(tmp_path / "chunks")
    frames = [Frame(i * 20, np.full(FRAME, 0.25, np.float32), np.full(FRAME, -0.5, np.float32)) for i in range(25 * 50)]
    out = list(w.write(frames + [Gap(0, 1)]))
    assert [(s, p.name) for s, p in out] == [(0, "chunk-00000.wav"), (1, "chunk-00001.wav"), (2, "chunk-00002.wav")]
    for (_, p), secs in zip(out, (10, 10, 5)):
        with wave.open(str(p)) as r:
            assert (r.getnchannels(), r.getframerate(), r.getnframes()) == (2, 16000, secs * 16000)
            x = np.frombuffer(r.readframes(2), np.int16).reshape(-1, 2)
            assert x[0].tolist() == [8192, -16384]




def test_chunk_writer_closes_open_chunk_on_error(tmp_path):
    def frames():
        for i in range(12 * 50):
            yield Frame(i * 20, np.zeros(FRAME, np.float32), np.zeros(FRAME, np.float32))
        raise RuntimeError("source died")

    w = AudioChunkWriter(tmp_path)
    with pytest.raises(RuntimeError):
        list(w.write(frames()))
    with wave.open(str(tmp_path / "chunk-00001.wav")) as r:
        assert r.getnframes() == 2 * SR


class _FakeCapture:
    """20 ms buffers stamped on a virtual host clock running SPEED x real time; sample values encode
    1 + their own timestamp, so the test can measure skew and loss."""
    SPEED = 4

    def __init__(self, push, stall=None, stop_at=None, fail_first=False):
        self.push, self.stall, self.stop_at, self.fail_first = push, stall or {}, stop_at, fail_first
        self._r0, self.system_starts = time.monotonic(), 0
        self.stopped, self.client_on = threading.Event(), threading.Event()

    def now(self):
        return (time.monotonic() - self._r0) * self.SPEED

    def _run(self, stream):
        t = self.now()
        while not self.stopped.is_set():
            while self.now() < t + 0.02:
                time.sleep(0.001)
            if stream == "client" and self.stop_at is not None and t >= self.stop_at:
                self.stop_at = None
                self.client_on.clear()
                self.push("stopped")
            a, b = self.stall.get(stream, (-1, -1))
            if not a <= t < b and (stream == "you" or self.client_on.is_set()):
                self.push(stream, (1 + t + np.arange(FRAME) / SR).astype(np.float32), t)
            t += 0.02

    def start_mic(self):
        threading.Thread(target=self._run, args=("you",), daemon=True).start()

    def start_system(self):
        self.system_starts += 1
        if self.fail_first and self.system_starts == 1:
            raise RuntimeError("screen recording not granted")
        if not self.client_on.is_set() and self.system_starts == 1 + self.fail_first:
            threading.Thread(target=self._run, args=("client",), daemon=True).start()
        self.client_on.set()

    def stop(self):
        self.stopped.set()


def _live(until_ms, pause_after=None, **kw):
    caps = []
    src = LiveSource(capture_factory=lambda push: caps.append(_FakeCapture(push, **kw)) or caps[-1])
    items = []
    for item in src.frames():
        items.append(item)
        if isinstance(item, Frame):
            if item.t_ms == pause_after:
                time.sleep(1.0)  # consumer stalls 4 s of virtual time
            if item.t_ms >= until_ms:
                break
    frames = [i for i in items if isinstance(i, Frame)]
    assert [f.t_ms for f in frames] == [i * 20 for i in range(len(frames))]
    return caps[0], frames, [i for i in items if isinstance(i, Gap)], [i for i in items if isinstance(i, Unavailable)]


def _skew_ok(frames):
    both = [f for f in frames if f.you[0] and f.client[0]]
    assert both and all(abs(f.you[0] - f.client[0]) < 0.04 for f in both)
    for s in ("you", "client"):  # every stream stays where its timestamps put it: t0 is the same for all frames
        t0s = [getattr(f, s)[0] - 1 - f.t_ms / 1000 for f in frames if getattr(f, s)[0]]
        assert max(t0s) - min(t0s) < 0.04


def _continuous(frames, stream, from_ms, to_ms):
    v = [getattr(f, stream)[0] for f in frames if from_ms <= f.t_ms < to_ms]
    assert all(v) and np.allclose(np.diff(v), 0.02, atol=1e-4)


def test_capture_restart_marks_gap():
    cap, frames, gaps, _ = _live(4000, stop_at=2.0)
    assert len(gaps) == 1 and gaps[0].stream == "client" and cap.system_starts == 2 and cap.stopped.is_set()
    assert 1900 <= gaps[0].start_ms <= gaps[0].end_ms <= 2300
    assert frames[-1].client[0] != 0  # client audio flows again after the restart
    _skew_ok(frames)


@pytest.mark.parametrize("stalled, other", [("you", "client"), ("client", "you")])
def test_stall_of_either_stream_keeps_the_other_and_alignment(stalled, other):
    _, frames, gaps, _ = _live(6000, stall={stalled: (1.0, 4.0)})
    _continuous(frames, other, 500, 6000)  # nothing of the other stream is lost
    assert gaps == []  # no buffers is silence, not an outage
    assert all(getattr(f, stalled)[0] == 0 for f in frames if 1100 <= f.t_ms < 3900)
    _continuous(frames, stalled, 4100, 6000)
    _skew_ok(frames)


def test_quiet_call_without_client_buffers_makes_no_gap():
    _, frames, gaps, _ = _live(12000, stall={"client": (0.5, 10.5)})  # ScreenCaptureKit silent for 10 s
    assert gaps == []
    _continuous(frames, "you", 500, 12000)
    _continuous(frames, "client", 10600, 12000)
    _skew_ok(frames)


def test_system_audio_unavailable_at_start_keeps_mic(monkeypatch):
    monkeypatch.setattr(LiveSource, "BACKOFF_S", (0.3,))  # 1.2 s of virtual time
    cap, frames, gaps, unavailable = _live(3000, fail_first=True)
    assert len(unavailable) == 1 and "not granted" in unavailable[0].reason and cap.system_starts == 2
    _continuous(frames, "you", 500, 3000)
    assert [g.stream for g in gaps] == ["client"] and gaps[0].start_ms == 0 and gaps[0].end_ms > 1000
    _skew_ok(frames)


def test_queue_overflow_drops_oldest_and_marks_gap(monkeypatch, caplog):
    monkeypatch.setattr(LiveSource, "QUEUE_MAX", 20)
    with caplog.at_level(logging.WARNING, logger="fleck_helper.audio"):
        _, frames, gaps, _ = _live(7000, pause_after=1000)
    assert len(gaps) == 1 and gaps[0].stream == "both" and gaps[0].start_ms < gaps[0].end_ms
    assert len([r for r in caplog.records if "queue full" in r.message]) == 1
    _continuous(frames, "client", 6000, 7000)
    _skew_ok(frames)
