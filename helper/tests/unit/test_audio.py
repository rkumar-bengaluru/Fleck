import threading
import time
import wave

import numpy as np

from fleck_helper.audio import FRAME, AudioChunkWriter, Frame, Gap, LiveSource, ReplaySource


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


class _FakeCapture:
    """Mic and system audio in 20 ms pieces; the system stream ends after 2 s, once."""

    def __init__(self, push):
        self.push, self.system_starts, self.stopped = push, 0, False
        self._client_on = threading.Event()

    def start_mic(self):
        def run():
            for i in range(300):
                if self.stopped:
                    return
                self.push("you", np.full(FRAME, 0.1, np.float32))
                if self._client_on.is_set():
                    self.push("client", np.full(FRAME, 0.2, np.float32))
                if i == 100 and self.system_starts == 1:
                    self._client_on.clear()
                    self.push("stopped")
                time.sleep(0.001)
        threading.Thread(target=run, daemon=True).start()

    def start_system(self):
        self.system_starts += 1
        self._client_on.set()

    def stop(self):
        self.stopped = True


def test_capture_restart_marks_gap():
    caps = []
    src = LiveSource(capture_factory=lambda push: caps.append(_FakeCapture(push)) or caps[-1])
    items = []
    for item in src.frames():
        items.append(item)
        if sum(isinstance(i, Frame) for i in items) == 250:
            break
    gaps = [i for i in items if isinstance(i, Gap)]
    assert len(gaps) == 1 and caps[0].system_starts == 2 and caps[0].stopped
    assert 1500 <= gaps[0].start_ms <= gaps[0].end_ms
    frames = [i for i in items if isinstance(i, Frame)]
    assert [f.t_ms for f in frames] == [i * 20 for i in range(250)]
    assert frames[-1].client[0] == np.float32(0.2)  # client audio flows again after the restart
