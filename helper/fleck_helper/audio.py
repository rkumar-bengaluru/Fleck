"""Audio sources (replay WAV, live mic + system audio) and the on-disk chunk writer (spec 7.0, 7.7).

Every source yields 20 ms `Frame`s of float32 16 kHz mono per stream; live capture may also yield
`Gap`s and `Unavailable`. macOS-only imports are lazy so Linux CI can import this module.
"""
import itertools
import logging
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
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Frame:
    t_ms: int
    you: np.ndarray
    client: np.ndarray


@dataclass(frozen=True)
class Gap:
    """A stream ("you", "client") had no audio from start_ms to end_ms; becomes a transcript marker."""
    start_ms: int
    end_ms: int
    stream: str = "client"


@dataclass(frozen=True)
class Unavailable:
    """A stream could not be started at t_ms (the pipeline shows "System audio unavailable"); a Gap follows if it returns."""
    stream: str
    t_ms: int
    reason: str


class Resampler:
    """Streaming conversion to 16 kHz that carries leftover input between buffers, so buffer sizes never drift."""

    def __init__(self, rate: int):
        self.rate, self._buf, self._pos = rate, np.zeros(0, np.float32), 0.0

    def __call__(self, x: np.ndarray) -> tuple[np.ndarray, float]:
        """Returns the 16 kHz samples and the time of the first one relative to the start of x, in seconds."""
        back = len(self._buf)
        buf = np.concatenate([self._buf, np.asarray(x, np.float32)])
        if self.rate % SR == 0:  # 16/32/48 kHz: box-filter decimation doubles as a crude anti-alias filter
            k = self.rate // SR
            n = len(buf) // k
            self._buf = buf[n * k:]
            return buf[: n * k].reshape(-1, k).mean(axis=1), -back / self.rate
        # shortcut: linear interpolation without anti-aliasing; upgrade to a polyphase filter if odd rates show up live
        step = self.rate / SR
        n = int((len(buf) - 1 - self._pos) // step) + 1 if len(buf) - 1 >= self._pos else 0
        y = np.interp(self._pos + np.arange(n) * step, np.arange(len(buf)), buf).astype(np.float32)
        start = (self._pos - back) / self.rate
        nxt = self._pos + n * step  # input index of the next output sample; may lie past this buffer
        keep = min(int(nxt), len(buf))
        self._buf, self._pos = buf[keep:], nxt - keep
        return y, start


class ReplaySource:
    """Stereo 16-bit WAV, left = You, right = Client, streamed from disk. `loops` replays it with t_ms
    still increasing (the soak run), so segment ids never repeat."""

    def __init__(self, path: Path, realtime: bool = True, loops: int = 1):
        self.path, self.realtime, self.loops = Path(path), realtime, loops

    def frames(self) -> Iterator[Frame]:
        t0, i = time.monotonic(), 0
        for _ in range(self.loops):
            with wave.open(str(self.path)) as w:
                if w.getnchannels() != 2 or w.getsampwidth() != 2:
                    raise ValueError(f"{self.path}: need stereo 16-bit PCM, got {w.getnchannels()} ch x {w.getsampwidth() * 8} bit")
                rs_you, rs_client = Resampler(w.getframerate()), Resampler(w.getframerate())
                you = client = np.zeros(0, np.float32)
                while raw := w.readframes(w.getframerate() // 50):
                    x = np.frombuffer(raw, np.int16).reshape(-1, 2).astype(np.float32) / 32768
                    you, client = np.concatenate([you, rs_you(x[:, 0])[0]]), np.concatenate([client, rs_client(x[:, 1])[0]])
                    while len(you) >= FRAME and len(client) >= FRAME:  # a trailing partial frame is dropped
                        if self.realtime and (wait := t0 + i * 0.02 - time.monotonic()) > 0:
                            time.sleep(wait)
                        yield Frame(i * 20, you[:FRAME], client[:FRAME])
                        you, client, i = you[FRAME:], client[FRAME:], i + 1


class _Stream:
    """One stream's audio laid on the shared timeline (sample index from the source's t0)."""

    def __init__(self):
        self.buf, self.start = np.zeros(0, np.float32), 0  # buf[0] is sample `start` = the next slot to emit
        self.last_end = 0  # end of the last real audio; a later start means a hole
        self.force_gap = False  # report the next hole whatever its size (stream stopped, buffers dropped)

    def place(self, x: np.ndarray, idx: int) -> tuple[int, int] | None:
        """Adds audio starting at sample idx; returns the hole (start, end) it closes, if any."""
        hole = None
        if idx - self.last_end > LiveSource.GAP_S * SR or (self.force_gap and idx - self.last_end > 2):  # 2: timestamp rounding
            hole = (self.last_end, idx)
            self.force_gap = False
        self.last_end = max(self.last_end, idx + len(x))
        end = self.start + len(self.buf)
        if idx > end:  # missing audio becomes silence
            self.buf = np.concatenate([self.buf, np.zeros(idx - end, np.float32)])
        self.buf = np.concatenate([self.buf, x[max(0, end - idx):]])  # audio already emitted or present is dropped
        return hole

    def take(self) -> np.ndarray:
        out = self.buf[:FRAME] if len(self.buf) >= FRAME else np.pad(self.buf, (0, FRAME - len(self.buf)))
        self.buf, self.start = self.buf[FRAME:], self.start + FRAME
        return out


class LiveSource:
    """Mic (You, Apple voice processing) + system audio (Client, ScreenCaptureKit) on one host clock.

    `capture_factory(push)` returns an object with now() (host-clock seconds), start_mic(), start_system(),
    stop(). It calls push(stream, samples, host_s) with float32 16 kHz audio stamped with the host time of
    its first sample, push("stopped") when the system stream ends, and push("interrupted", stream) after
    it restarted a stream itself (mic route change). Frames are 20 ms wall-clock slots: a slot is emitted
    once both streams cover it, or STALL_S after its end with silence for a stream that has nothing.
    A hole over GAP_S, a stop, an interruption or dropped buffers yields a Gap when audio returns."""

    STALL_S = 0.3
    GAP_S = 1.0
    QUEUE_MAX = 250  # capture buffers: >= 5 s at the smallest (20 ms) buffers
    BACKOFF_S = (1, 2, 4, 8, 15, 30)

    def __init__(self, capture_factory=None):
        self._factory = capture_factory or _MacCapture

    def frames(self) -> Iterator[Frame | Gap | Unavailable]:
        q: queue.Queue = queue.Queue(maxsize=self.QUEUE_MAX)
        warned, closed, restarting = [], threading.Event(), threading.Event()
        dropped: set = set()  # streams that lost buffers to a full queue

        def push(kind, x=None, host_s=None):
            while True:
                try:
                    return q.put_nowait((kind, x, host_s))
                except queue.Full:  # consumer is behind: drop the oldest audio, keep the newest
                    try:
                        old = q.get_nowait()
                    except queue.Empty:
                        continue
                    if old[0] in ("you", "client"):
                        dropped.add(old[0])
                        if not warned:
                            warned.append(1)
                            log.warning("live audio queue full: dropping the oldest buffers")

        cap = self._factory(push)

        lock, asked, served = threading.Lock(), [0], [0]

        def bring_up_system():  # own thread: a slow or failing start never blocks frames
            while True:
                with lock:
                    if served[0] == asked[0]:  # a stop that arrived during a start is served by another round
                        restarting.clear()
                        return
                    served[0] = asked[0]
                for i in itertools.count():
                    try:
                        cap.start_system()
                        break
                    except Exception as e:
                        if i == 0:
                            push("unavailable", str(e))
                        if closed.wait(self.BACKOFF_S[min(i, len(self.BACKOFF_S) - 1)]):
                            return

        def restart_system():
            with lock:
                asked[0] += 1
                if restarting.is_set():
                    return
                restarting.set()
            threading.Thread(target=bring_up_system, daemon=True).start()

        streams = {"you": _Stream(), "client": _Stream()}
        try:
            t0 = cap.now()
            cap.start_mic()
            restart_system()
            while True:
                items = []
                try:
                    items.append(q.get(timeout=0.02))
                    while True:
                        items.append(q.get_nowait())
                except queue.Empty:
                    pass
                for s in list(dropped):
                    dropped.discard(s)
                    streams[s].force_gap = True
                for kind, x, host_s in items:
                    if kind in streams:
                        if hole := streams[kind].place(x, round((host_s - t0) * SR)):
                            yield Gap(hole[0] // 16, hole[1] // 16, kind)
                    elif kind == "stopped":
                        streams["client"].force_gap = True
                        restart_system()
                    elif kind == "interrupted":
                        streams[x].force_gap = True
                    elif kind == "unavailable":
                        yield Unavailable("client", streams["client"].start // 16, x)
                deadline = cap.now() - t0 - self.STALL_S  # slots ending before this are emitted regardless
                you, client = streams["you"], streams["client"]
                while (len(you.buf) >= FRAME and len(client.buf) >= FRAME) or (you.start + FRAME) / SR <= deadline:
                    t_ms = you.start // 16
                    yield Frame(t_ms, you.take(), client.take())
        finally:
            closed.set()
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
        """Writes Frames (other items are skipped) and yields each finished chunk. The open chunk is closed
        even if the frames raise or the caller stops iterating."""
        try:
            for f in frames:
                if isinstance(f, Frame) and (done := self.push(f)):
                    yield done
        finally:
            last = self.close()
        if last:
            yield last


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
    MIC_RETRY_S = (0.2, 0.5, 1, 2, 5)

    def __init__(self, push):
        self.push, self.engine, self.stream, self._observer = push, None, None, None
        self._closed = False

    def now(self) -> float:
        import CoreMedia as CM

        return CM.CMTimeGetSeconds(CM.CMClockGetTime(CM.CMClockGetHostTimeClock()))

    def start_mic(self):
        import AVFoundation as AV
        from Foundation import NSNotificationCenter

        self._stop_mic()
        engine = AV.AVAudioEngine.alloc().init()
        inp = engine.inputNode()
        ok, err = inp.setVoiceProcessingEnabled_error_(True, None)
        if not ok:
            raise RuntimeError(f"voice processing: {err}")
        rs = Resampler(int(inp.outputFormatForBus_(0).sampleRate()))  # re-read on every (re)start: routes change it

        def tap(buf, when):
            n = buf.frameLength()
            ch = buf.floatChannelData()
            if ch and n:
                y, lead = rs(np.frombuffer(ch[0].as_buffer(n), np.float32))
                self.push("you", y, AV.AVAudioTime.secondsForHostTime_(when.hostTime()) + lead)

        inp.installTapOnBus_bufferSize_format_block_(0, 1024, None, tap)
        # AirPods connecting etc. stop the engine; rebuild it off the notification thread
        self._observer = NSNotificationCenter.defaultCenter().addObserverForName_object_queue_usingBlock_(
            AV.AVAudioEngineConfigurationChangeNotification, engine, None,
            lambda note: threading.Thread(target=self._restart_mic, daemon=True).start())
        self.engine = engine
        ok, err = engine.startAndReturnError_(None)
        if not ok:
            raise RuntimeError(f"mic: {err}")

    def _restart_mic(self):
        for i in itertools.count():
            if self._closed:
                return
            try:
                self.start_mic()
                self.push("interrupted", "you")
                return
            except Exception as e:
                log.warning("mic restart failed: %s", e)
                time.sleep(self.MIC_RETRY_S[min(i, len(self.MIC_RETRY_S) - 1)])

    def _stop_mic(self):
        if self._observer is not None:
            from Foundation import NSNotificationCenter

            NSNotificationCenter.defaultCenter().removeObserver_(self._observer)
            self._observer = None
        if self.engine is not None:
            self.engine.stop()
            self.engine.inputNode().removeTapOnBus_(0)
            self.engine = None

    def start_system(self):
        import CoreMedia as CM
        import ScreenCaptureKit as SC

        self._stop_system()
        got: list = []
        ev = threading.Event()

        def on_content(c, e):  # ObjC completion blocks must return None
            got.append((c, e))
            ev.set()

        SC.SCShareableContent.getShareableContentWithCompletionHandler_(on_content)
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
                pts = CM.CMTimeGetSeconds(CM.CMSampleBufferGetPresentationTimeStamp(sbuf))  # host clock
                self.push("client", np.frombuffer(bytes(data)[: n // 4 * 4], np.float32).copy(), pts)

        self._out = cls["out"].alloc().init()
        self._out.cb = on_audio
        stream = SC.SCStream.alloc().initWithFilter_configuration_delegate_(flt, cfg, self._delegate)
        ok, err = stream.addStreamOutput_type_sampleHandlerQueue_error_(self._out, SC.SCStreamOutputTypeAudio, None, None)
        if not ok:
            raise RuntimeError(f"system audio: {err}")
        started: list = []
        ev = threading.Event()

        def on_start(e):
            started.append(e)
            ev.set()

        stream.startCaptureWithCompletionHandler_(on_start)
        if not ev.wait(10) or started[0] is not None:
            raise RuntimeError(f"system audio start failed: {started and started[0]}")
        self.stream = stream

    def _stop_system(self):
        if self.stream is not None:
            self.stream.stopCaptureWithCompletionHandler_(lambda e: None)
            self.stream = None

    def stop(self):
        self._closed = True
        self._stop_system()
        self._stop_mic()
