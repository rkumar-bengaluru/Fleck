"""Audio sources (replay WAV, live mic + system audio) and the on-disk chunk writer (spec 7.0, 7.7).

Every source yields 20 ms `Frame`s of float32 16 kHz mono per stream; live capture may also yield `Gap`s.
macOS-only imports are lazy so Linux CI can import this module.
"""
import queue
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np

SR = 16000
FRAME = SR // 50  # 20 ms
CHUNK_FRAMES = 500  # 10 s


@dataclass(frozen=True)
class Frame:
    t_ms: int
    you: np.ndarray
    client: np.ndarray


@dataclass(frozen=True)
class Gap:
    """Client capture was down from start_ms to end_ms; becomes a transcript marker."""
    start_ms: int
    end_ms: int


def to_16k(x: np.ndarray, rate: int) -> np.ndarray:
    if rate == SR:
        return x
    if rate % SR == 0:  # 48 kHz and 32 kHz: box-filter decimation doubles as a crude anti-alias filter
        k = rate // SR
        return x[: len(x) // k * k].reshape(-1, k).mean(axis=1)
    # shortcut: linear interpolation without anti-aliasing; upgrade to a polyphase filter if odd rates show up live
    n = round(len(x) * SR / rate)
    return np.interp(np.arange(n) * rate / SR, np.arange(len(x)), x).astype(np.float32)


class ReplaySource:
    """Stereo 16-bit WAV, left = You, right = Client, streamed from disk."""

    def __init__(self, path: Path, realtime: bool = True):
        self.path, self.realtime = Path(path), realtime

    def frames(self) -> Iterator[Frame]:
        with wave.open(str(self.path)) as w:
            if w.getnchannels() != 2 or w.getsampwidth() != 2:
                raise ValueError(f"{self.path}: need stereo 16-bit PCM, got {w.getnchannels()} ch x {w.getsampwidth() * 8} bit")
            rate = w.getframerate()
            block = rate // 50  # source samples per 20 ms
            t0, i = time.monotonic(), 0
            while len(raw := w.readframes(block)) == block * 4:  # a trailing partial frame is dropped
                x = np.frombuffer(raw, np.int16).reshape(-1, 2).astype(np.float32) / 32768
                you, client = to_16k(x[:, 0], rate)[:FRAME], to_16k(x[:, 1], rate)[:FRAME]
                if self.realtime and (wait := t0 + i * 0.02 - time.monotonic()) > 0:
                    time.sleep(wait)
                yield Frame(i * 20, you, client)
                i += 1


class LiveSource:
    """Mic (You, Apple voice processing) + system audio (Client, ScreenCaptureKit), paced by the mic clock.

    `capture_factory(push)` returns an object with start_mic(), start_system(), stop(); it calls
    push("you"|"client", float32 16 kHz samples) and push("stopped") when the system stream ends.
    The source restarts the system stream and yields a Gap once client audio flows again."""

    MAX_LAG = SR // 2  # mic runs 0.5 s ahead of client audio before client is padded with silence
    RETRY_S = 1.0

    def __init__(self, capture_factory=None):
        self._factory = capture_factory or _MacCapture

    def frames(self) -> Iterator[Frame | Gap]:
        q: queue.Queue = queue.Queue()
        cap = self._factory(lambda kind, x=None: q.put((kind, x)))
        you, cli = np.zeros(0, np.float32), np.zeros(0, np.float32)
        n, gap_start = 0, None
        try:
            cap.start_mic()
            cap.start_system()
            while True:
                kind, x = q.get()
                if kind == "stopped":
                    if gap_start is None:
                        gap_start = n * 20
                    try:
                        cap.start_system()
                    except Exception:  # retry until it comes back; the gap stays open meanwhile
                        threading.Timer(self.RETRY_S, q.put, args=(("stopped", None),)).start()
                    continue
                if kind == "you":
                    you = np.concatenate([you, x])
                else:
                    if gap_start is not None:
                        yield Gap(gap_start, n * 20)
                        gap_start = None
                    cli = np.concatenate([cli, x])
                while len(you) >= FRAME and (len(cli) >= FRAME or len(you) > self.MAX_LAG):
                    if len(cli) >= FRAME:
                        c, cli = cli[:FRAME], cli[FRAME:]
                    else:  # client stalled or down: silence keeps both streams on the mic clock
                        # shortcut: alignment is by sample count, so after a stall Client can sit up to MAX_LAG
                        # behind You; upgrade to the capture timestamps if the live check shows skew
                        c = np.zeros(FRAME, np.float32)
                    yield Frame(n * 20, you[:FRAME], c)
                    you, n = you[FRAME:], n + 1
                cli = cli[-self.MAX_LAG:]  # bounded if the system clock runs ahead of the mic
        finally:
            cap.stop()


class AudioChunkWriter:
    """Streams frames to 10 s stereo int16 WAVs `chunk-{seq:05d}.wav` (left You, right Client) in `dir`."""

    def __init__(self, dir: Path):
        self.dir, self.seq, self._w, self._n = Path(dir), 0, None, 0
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, seq: int) -> Path:
        return self.dir / f"chunk-{seq:05d}.wav"

    def push(self, frame: Frame) -> tuple[int, Path] | None:
        """Returns (seq, path) when this frame completes a chunk."""
        if self._w is None:
            self._w = wave.open(str(self.path(self.seq)), "wb")
            self._w.setnchannels(2)
            self._w.setsampwidth(2)
            self._w.setframerate(SR)
        pcm = np.clip(np.column_stack([frame.you, frame.client]) * 32768, -32768, 32767).astype("<i2")
        self._w.writeframes(pcm.tobytes())
        self._n += 1
        return self.close() if self._n == CHUNK_FRAMES else None

    def close(self) -> tuple[int, Path] | None:
        """Finishes the open chunk, if any (call at the end of the recording)."""
        if self._w is None:
            return None
        self._w.close()
        done = (self.seq, self.path(self.seq))
        self._w, self._n, self.seq = None, 0, self.seq + 1
        return done

    def write(self, frames) -> Iterator[tuple[int, Path]]:
        """Writes Frames (Gaps are skipped) and yields each finished chunk."""
        for f in frames:
            if isinstance(f, Frame) and (done := self.push(f)):
                yield done
        if done := self.close():
            yield done


# --- macOS capture (patterns proven by probe.py under the signed bundle) ---

_objc_classes: dict = {}


def _sc_classes():
    """NSObject subclasses must be defined once per process."""
    if not _objc_classes:
        import ScreenCaptureKit as SC
        from Foundation import NSObject

        class FleckAudioOut(NSObject):
            def stream_didOutputSampleBuffer_ofType_(self, stream, sbuf, typ):
                if typ == SC.SCStreamOutputTypeAudio:
                    self.cb(sbuf)

        class FleckStreamDelegate(NSObject):
            def stream_didStopWithError_(self, stream, err):
                self.cb()

        _objc_classes.update(out=FleckAudioOut, delegate=FleckStreamDelegate)
    return _objc_classes


class _MacCapture:
    def __init__(self, push):
        self.push, self.engine, self.stream = push, None, None

    def start_mic(self):
        import AVFoundation as AV

        self.engine = AV.AVAudioEngine.alloc().init()
        inp = self.engine.inputNode()
        ok, err = inp.setVoiceProcessingEnabled_error_(True, None)
        if not ok:
            raise RuntimeError(f"voice processing: {err}")
        rate = int(inp.outputFormatForBus_(0).sampleRate())

        def tap(buf, when):
            n = buf.frameLength()
            ch = buf.floatChannelData()
            if ch and n:
                x = np.frombuffer(ch[0].as_buffer(n), np.float32).copy()
                self.push("you", to_16k(x, rate).astype(np.float32))

        inp.installTapOnBus_bufferSize_format_block_(0, 1024, None, tap)
        ok, err = self.engine.startAndReturnError_(None)
        if not ok:
            raise RuntimeError(f"mic: {err}")

    def start_system(self):
        import CoreMedia as CM
        import ScreenCaptureKit as SC

        got: list = []
        ev = threading.Event()
        SC.SCShareableContent.getShareableContentWithCompletionHandler_(lambda c, e: (got.append((c, e)), ev.set()))
        if not ev.wait(10) or got[0][0] is None:
            raise RuntimeError(f"no shareable content (screen recording not granted?): {got and got[0][1]}")
        flt = SC.SCContentFilter.alloc().initWithDisplay_excludingWindows_(got[0][0].displays()[0], [])
        cfg = SC.SCStreamConfiguration.alloc().init()
        cfg.setCapturesAudio_(True)
        cfg.setExcludesCurrentProcessAudio_(True)  # never transcribe our own panel or notifications
        cfg.setSampleRate_(SR)
        cfg.setChannelCount_(1)
        cls = _sc_classes()
        self._delegate = cls["delegate"].alloc().init()
        self._delegate.cb = lambda: self.push("stopped")

        def on_audio(sbuf):
            blk = CM.CMSampleBufferGetDataBuffer(sbuf)
            n = CM.CMBlockBufferGetDataLength(blk)
            st, data = CM.CMBlockBufferCopyDataBytes(blk, 0, n, None)
            if st == 0 and data:
                self.push("client", np.frombuffer(bytes(data)[: n // 4 * 4], np.float32).copy())

        self._out = cls["out"].alloc().init()
        self._out.cb = on_audio
        stream = SC.SCStream.alloc().initWithFilter_configuration_delegate_(flt, cfg, self._delegate)
        ok, err = stream.addStreamOutput_type_sampleHandlerQueue_error_(self._out, SC.SCStreamOutputTypeAudio, None, None)
        if not ok:
            raise RuntimeError(f"system audio: {err}")
        started: list = []
        ev = threading.Event()
        stream.startCaptureWithCompletionHandler_(lambda e: (started.append(e), ev.set()))
        if not ev.wait(10) or started[0] is not None:
            raise RuntimeError(f"system audio start failed: {started and started[0]}")
        self.stream = stream

    def stop(self):
        if self.stream is not None:
            self.stream.stopCaptureWithCompletionHandler_(lambda e: None)
            self.stream = None
        if self.engine is not None:
            self.engine.stop()
            self.engine.inputNode().removeTapOnBus_(0)
            self.engine = None
