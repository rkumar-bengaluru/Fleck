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
        self.last_end = 0  # end of the last real audio
        self.down = False  # the stream stopped, failed or restarted: the next audio closes a Gap
        self.lag = 0.0  # recent delivery delay (s): slots wait this long for the stream
        self.late = 0  # samples that arrived after their slot went out, not yet logged

    def place(self, x: np.ndarray, idx: int) -> tuple[int, int] | None:
        """Adds audio starting at sample idx; returns the outage (start, end) it ends, if the stream was down.
        Missing buffers alone are silence, not a Gap: ScreenCaptureKit may send nothing while the call is quiet."""
        hole = None
        if self.down:
            hole, self.down = (self.last_end, max(idx, self.last_end)), False
        self.last_end = max(self.last_end, idx + len(x))
        idx = min(idx, self.start + LiveSource.MAX_AHEAD_S * SR)  # bounds the zero-fill below
        end = self.start + len(self.buf)
        if idx > end:  # missing audio becomes silence
            self.buf = np.concatenate([self.buf, np.zeros(idx - end, np.float32)])
        self.late += min(len(x), max(0, self.start - idx))  # its slots already went out
        self.buf = np.concatenate([self.buf, x[max(0, end - idx):]])  # audio already emitted or present is dropped
        return hole

    def take(self) -> np.ndarray:
        out = self.buf[:FRAME] if len(self.buf) >= FRAME else np.pad(self.buf, (0, FRAME - len(self.buf)))
        self.buf, self.start = self.buf[FRAME:], self.start + FRAME
        return out


_AUDIO = ("you", "client")


class LiveSource:
    """Mic (You, Apple voice processing) + system audio (Client, ScreenCaptureKit) on one host clock.

    `capture_factory(push)` returns an object with now() (host-clock seconds), start_mic(), start_system(),
    stop(). It calls push(stream, samples, host_s) with float32 16 kHz audio stamped with the host time of
    its first sample, push("stopped") when the system stream ends, and push("interrupted", stream) when it
    restarts a stream itself (mic route change). Frames are 20 ms wall-clock slots: a slot is emitted once
    both streams cover it, or a stall time after its end (STALL_S, longer while a stream delivers late),
    with silence for a stream that has nothing. Only a real outage yields a Gap: a stopped or failed stream
    or a restart (when its audio returns), or audio dropped from a full queue (one Gap, "both" if both lost)."""

    STALL_S = 0.3
    MAX_STALL_S = 1.0  # a stream delivering later than this loses audio (logged) rather than delay everything
    BAD_STAMP_S = 2.0  # a timestamp this far from arrival is not on the host clock: use arrival time
    MAX_AHEAD_S = 5  # no stream buffers audio further than this past the next slot
    LOG_EVERY_S = 10
    QUEUE_MAX = 250  # audio buffers: >= 5 s at the smallest (20 ms) buffers; control messages are never dropped
    BACKOFF_S = (1, 2, 4, 8, 15, 30)

    def __init__(self, capture_factory=None):
        self._factory = capture_factory or _MacCapture

    def frames(self) -> Iterator[Frame | Gap | Unavailable]:
        cv, items, n_audio = threading.Condition(), [], [0]
        warned, closed, restarting = set(), threading.Event(), threading.Event()
        dropped: dict = {}  # stream -> [first, last] host seconds of audio lost to a full queue

        def push(kind, x=None, host_s=None):
            arrived = cap.now() if kind in _AUDIO else None
            with cv:
                if kind in _AUDIO:
                    if n_audio[0] >= self.QUEUE_MAX:  # consumer is behind: drop the oldest audio, keep the newest
                        old = items.pop(next(i for i, it in enumerate(items) if it[0] in _AUDIO))
                        n_audio[0] -= 1
                        span = dropped.setdefault(old[0], [old[2], old[2]])
                        span[1] = old[2] + len(old[1]) / SR
                        _once(warned, "overflow", "live audio queue full: dropping the oldest buffers")
                    n_audio[0] += 1
                items.append((kind, x, host_s, arrived))
                cv.notify()

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
        next_log = 0.0
        try:
            t0 = cap.now()
            cap.start_mic()
            restart_system()
            while True:
                with cv:
                    cv.wait_for(lambda: items, timeout=0.02)
                    batch = items[:]
                    items.clear()
                    n_audio[0] = 0
                    lost = {k: dropped.pop(k) for k in list(dropped)}
                if lost:  # one Gap per overflow episode, whichever streams lost audio
                    yield Gap(round((min(a for a, _ in lost.values()) - t0) * 1000),
                              round((max(b for _, b in lost.values()) - t0) * 1000),
                              "both" if len(lost) == 2 else next(iter(lost)))
                for kind, x, host_s, arrived in batch:
                    if kind in streams:
                        st, dur = streams[kind], len(x) / SR
                        if abs(arrived - dur - host_s) > self.BAD_STAMP_S:
                            _once(warned, kind, f"{kind} timestamps are {host_s - arrived + dur:+.1f} s off the host clock; using arrival time")
                            host_s = arrived - dur
                        st.lag = max(arrived - host_s, st.lag * 0.99)  # from buffer start: a long buffer covers slots that already went out
                        if hole := st.place(x, round((host_s - t0) * SR)):
                            yield Gap(hole[0] // 16, hole[1] // 16, kind)
                    elif kind == "stopped":
                        streams["client"].down = True
                        restart_system()
                    elif kind == "interrupted":
                        streams[x].down = True
                    elif kind == "unavailable":
                        streams["client"].down = True
                        yield Unavailable("client", streams["client"].start // 16, x)
                now = cap.now() - t0
                if now >= next_log and any(st.late for st in streams.values()):  # rate-limited
                    log.warning("late audio dropped: %s", ", ".join(f"{k} {st.late * 1000 // SR} ms" for k, st in streams.items() if st.late))
                    for st in streams.values():
                        st.late = 0
                    next_log = now + self.LOG_EVERY_S
                # slots ending before this are emitted regardless; a late stream widens the wait, up to MAX_STALL_S
                deadline = now - min(self.MAX_STALL_S, max(self.STALL_S, max(st.lag for st in streams.values()) + 0.1))
                you, client = streams["you"], streams["client"]
                while (len(you.buf) >= FRAME and len(client.buf) >= FRAME) or (you.start + FRAME) / SR <= deadline:
                    t_ms = you.start // 16
                    yield Frame(t_ms, you.take(), client.take())
        finally:
            closed.set()
            cap.stop()


def _once(seen: set, key, msg: str) -> None:
    if key not in seen:
        seen.add(key)
        log.warning(msg)


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
        self._lock = threading.RLock()  # start/stop never interleave; stop() wins over an in-flight start
        self._mic_restarting = self._mic_again = False

    def now(self) -> float:
        import CoreMedia as CM

        return CM.CMTimeGetSeconds(CM.CMClockGetTime(CM.CMClockGetHostTimeClock()))

    def start_mic(self):
        import AVFoundation as AV
        from Foundation import NSNotificationCenter

        with self._lock:
            if self._closed:
                raise RuntimeError("capture closed")
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
            # AirPods connecting etc. stop the engine; it is rebuilt off the notification thread
            self._observer = NSNotificationCenter.defaultCenter().addObserverForName_object_queue_usingBlock_(
                AV.AVAudioEngineConfigurationChangeNotification, engine, None, self._on_config_change)
            self.engine = engine
            ok, err = engine.startAndReturnError_(None)
            if not ok:
                self._stop_mic()
                raise RuntimeError(f"mic: {err}")

    def _on_config_change(self, note) -> None:
        self.push("interrupted", "you")  # the outage starts now, not when the new engine runs
        with self._lock:
            self._mic_again = True
            if self._mic_restarting:  # a burst of changes is served by the one restart in flight
                return
            self._mic_restarting = True
        threading.Thread(target=self._restart_mic, daemon=True).start()

    def _restart_mic(self):
        for i in itertools.count():
            with self._lock:
                if self._closed or not self._mic_again:
                    self._mic_restarting = False
                    return
                self._mic_again = False
            try:
                self.start_mic()
            except Exception as e:
                log.warning("mic restart failed: %s", e)
                with self._lock:
                    self._mic_again = True
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

        with self._lock:
            if self._closed:
                raise RuntimeError("capture closed")
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
        with self._lock:
            if self._closed:  # stop() ran while this start was in flight: don't leave it capturing
                stream.stopCaptureWithCompletionHandler_(lambda e: None)
                raise RuntimeError("capture closed")
            self._stop_system()
            self.stream = stream

    def _stop_system(self):
        if self.stream is not None:
            self.stream.stopCaptureWithCompletionHandler_(lambda e: None)
            self.stream = None

    def stop(self):
        with self._lock:
            self._closed = True
            self._stop_system()
            self._stop_mic()
