"""Record the call window (Zoom or Meet) as 60 s H.264 pieces (decisions 165, 170).

Its own SCStream on its own threads: it never touches the system-audio stream, and any failure
ends video only. macOS-only imports are lazy so Linux CI can import this module.
"""
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

FPS = 10
MAX_W, MAX_H = 1280, 720
BITRATE = 1_500_000
NO_VIDEO = "Video: none (audio only)"


@dataclass(frozen=True)
class Win:
    window_id: int
    owner: str
    title: str
    on_screen: bool


def pick_window(wins: list[Win]) -> Win | None:
    ok = [w for w in wins if w.on_screen and w.title and not w.title.startswith("Fleck")]
    zoom = [w for w in ok if w.owner == "us.zoom.xos" and "Meeting" in w.title]
    meet = [w for w in ok if w.owner == "com.google.Chrome" and w.title.startswith("Meet")]
    return (zoom + meet + [None])[0]


# --- macOS capture ---

_objc_classes: dict = {}


def _sc_classes():
    """NSObject subclasses must be defined once per process."""
    if not _objc_classes:
        import ScreenCaptureKit as SC
        from Foundation import NSObject

        class FleckVideoOut(NSObject):
            def stream_didOutputSampleBuffer_ofType_(self, stream, sbuf, typ):
                if typ == SC.SCStreamOutputTypeScreen:
                    self.cb(sbuf)

        class FleckVideoDelegate(NSObject):
            def stream_didStopWithError_(self, stream, err):
                self.cb()

        _objc_classes.update(out=FleckVideoOut, delegate=FleckVideoDelegate)
    return _objc_classes


def _shareable():
    import ScreenCaptureKit as SC

    got: list = []
    ev = threading.Event()

    def on_content(c, e):  # ObjC completion blocks must return None
        got.append((c, e))
        ev.set()

    SC.SCShareableContent.getShareableContentWithCompletionHandler_(on_content)
    if not ev.wait(10) or got[0][0] is None:
        raise RuntimeError(f"no shareable content (screen recording not granted?): {got and got[0][1]}")
    return got[0][0]


def sck_windows() -> list[Win]:
    out = []
    for w in _shareable().windows():
        app = w.owningApplication()
        out.append(Win(int(w.windowID()), str(app.bundleIdentifier() or "") if app else "",
                       str(w.title() or ""), bool(w.isOnScreen())))
    return out


def _fit(w: float, h: float) -> tuple[int, int]:
    s = min(1.0, MAX_W / w, MAX_H / h)
    return max(2, int(w * s) // 2 * 2), max(2, int(h * s) // 2 * 2)  # H.264 wants even sides


def _frame_handler(on_frame, on_stopped, status_of, wall_of):
    """SCK calls this on its GCD queue: nothing may raise out of it (an uncaught error aborts the process, audio too)."""
    dead = []

    def cb(sbuf):
        if dead:
            return
        try:
            st = status_of(sbuf)
            if st != "stopped":
                if st == "complete":  # idle/blank frames carry no image; skip them
                    on_frame(sbuf, wall_of(sbuf))
                return
        except Exception:
            log.exception("video frame callback failed")
        dead.append(1)
        on_stopped()

    return cb


class _Stream:
    def __init__(self, stream, keep):
        self._stream, self._keep = stream, keep  # keep: the ObjC output/delegate must outlive the stream

    def stop(self):
        self._stream.stopCaptureWithCompletionHandler_(lambda e: None)


def sck_window_stream(window_id: int, on_frame: Callable[[object, float], None], on_stopped: Callable[[], None]):
    import CoreMedia as CM
    import ScreenCaptureKit as SC

    win = next((w for w in _shareable().windows() if int(w.windowID()) == window_id), None)
    if win is None:
        raise RuntimeError(f"window {window_id} is gone")
    flt = SC.SCContentFilter.alloc().initWithDesktopIndependentWindow_(win)  # window only, never the display
    cfg = SC.SCStreamConfiguration.alloc().init()
    size = win.frame().size
    w, h = _fit(size.width, size.height)
    cfg.setWidth_(w)
    cfg.setHeight_(h)
    cfg.setMinimumFrameInterval_(CM.CMTimeMake(1, FPS))
    cfg.setCapturesAudio_(False)
    host = CM.CMClockGetHostTimeClock()
    # a closed window may only show up as a Stopped frame, never as didStopWithError
    names = {SC.SCFrameStatusComplete: "complete", SC.SCFrameStatusStopped: "stopped"}

    def status_of(sbuf):
        att = CM.CMSampleBufferGetSampleAttachmentsArray(sbuf, False)
        return names.get(att[0].get(SC.SCStreamFrameInfoStatus)) if att else None

    def wall_of(sbuf):
        pts = CM.CMTimeGetSeconds(CM.CMSampleBufferGetPresentationTimeStamp(sbuf))  # host clock
        return time.time() - (CM.CMTimeGetSeconds(CM.CMClockGetTime(host)) - pts)

    cls = _sc_classes()
    delegate = cls["delegate"].alloc().init()
    delegate.cb = on_stopped
    out = cls["out"].alloc().init()
    out.cb = _frame_handler(on_frame, on_stopped, status_of, wall_of)
    stream = SC.SCStream.alloc().initWithFilter_configuration_delegate_(flt, cfg, delegate)
    ok, err = stream.addStreamOutput_type_sampleHandlerQueue_error_(out, SC.SCStreamOutputTypeScreen, None, None)
    if not ok:
        raise RuntimeError(f"video output: {err}")
    started: list = []
    ev = threading.Event()

    def on_start(e):
        started.append(e)
        ev.set()

    stream.startCaptureWithCompletionHandler_(on_start)
    if not ev.wait(10) or started[0] is not None:
        raise RuntimeError(f"video start failed: {started and started[0]}")
    return _Stream(stream, (out, delegate))


class _AVWriter:
    """One .mp4 piece. The sample buffer goes straight to AVAssetWriterInput: no pixel work in Python."""

    def __init__(self, path: Path):
        self.path, self.writer, self.input = path, None, None

    def _open(self, sbuf):
        import AVFoundation as AV
        import CoreMedia as CM
        import Quartz
        from Foundation import NSURL

        img = CM.CMSampleBufferGetImageBuffer(sbuf)
        w, h = Quartz.CVPixelBufferGetWidth(img), Quartz.CVPixelBufferGetHeight(img)
        writer, err = AV.AVAssetWriter.assetWriterWithURL_fileType_error_(
            NSURL.fileURLWithPath_(str(self.path)), AV.AVFileTypeMPEG4, None)
        if writer is None:
            raise RuntimeError(f"video writer: {err}")
        inp = AV.AVAssetWriterInput.assetWriterInputWithMediaType_outputSettings_(AV.AVMediaTypeVideo, {
            AV.AVVideoCodecKey: AV.AVVideoCodecTypeH264, AV.AVVideoWidthKey: w, AV.AVVideoHeightKey: h,
            AV.AVVideoCompressionPropertiesKey: {AV.AVVideoAverageBitRateKey: BITRATE}})
        inp.setExpectsMediaDataInRealTime_(True)
        writer.addInput_(inp)
        if not writer.startWriting():
            raise RuntimeError(f"video writer start: {writer.error()}")
        writer.startSessionAtSourceTime_(CM.CMSampleBufferGetPresentationTimeStamp(sbuf))
        self.writer, self.input = writer, inp

    def append(self, sbuf, wall):
        if self.writer is None:
            self._open(sbuf)
        if self.input.isReadyForMoreMediaData():  # else drop the frame: realtime, never queue in Python
            if not self.input.appendSampleBuffer_(sbuf):
                raise RuntimeError(f"video append: {self.writer.error()}")

    def finish(self, end_wall: float):
        import AVFoundation as AV
        import CoreMedia as CM

        # end at end_wall, not the last frame: a static window sends no frames, its tail must not be cut
        now = CM.CMTimeGetSeconds(CM.CMClockGetTime(CM.CMClockGetHostTimeClock()))
        self.input.markAsFinished()
        self.writer.endSessionAtSourceTime_(CM.CMTimeMakeWithSeconds(now - (time.time() - end_wall), 1_000_000_000))
        ev = threading.Event()
        self.writer.finishWritingWithCompletionHandler_(ev.set)
        if not ev.wait(3) or self.writer.status() != AV.AVAssetWriterStatusCompleted:
            raise RuntimeError(f"video finish: {self.writer.error()}")

    def cancel(self):
        if self.writer is not None:
            self.writer.cancelWriting()


class VideoRecorder:
    STOP_BUDGET_S = 4.5  # stop() returns within 5 s; a piece still finishing then arrives late

    def __init__(self, out_dir: Path, t0_wall: Callable[[], float | None], on_piece: Callable[[int, Path], None],
                 on_status: Callable[[str], None], list_windows: Callable[[], list[Win]] = sck_windows,
                 stream_factory=sck_window_stream, poll_s: float = 2.0, piece_s: float = 60.0,
                 writer_factory=_AVWriter):
        self.out_dir, self.t0_wall, self.on_piece, self.on_status = Path(out_dir), t0_wall, on_piece, on_status
        self.list_windows, self.stream_factory, self.writer_factory = list_windows, stream_factory, writer_factory
        self.poll_s, self.piece_s = poll_s, piece_s
        self._halt = threading.Event()  # stop() asked or video failed: no more frames
        self._lock = threading.Lock()
        self._stream = self._writer = self._thread = None
        self._piece_wall = self._piece_ms = None
        self._failed = False
        self._finisher = None  # the last piece-finishing thread; each joins the one before

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="fleck-video", daemon=True)
        self._thread.start()

    def _run(self):
        try:
            self.on_status("Video: waiting for call window")
            while not self._halt.is_set():
                win = pick_window(self.list_windows())
                if win:  # lock on the first match; never re-choose
                    self.on_status(f"Video: {win.title}")
                    stream = self.stream_factory(win.window_id, self._on_frame, self._on_stopped)
                    with self._lock:
                        self._stream = stream
                    if self._halt.is_set():  # stop() or a failure landed while the stream was starting
                        stream.stop()
                    return
                self._halt.wait(self.poll_s)
        except Exception:
            self._fail()

    def _on_frame(self, sbuf, wall: float) -> None:
        try:
            if self._piece_wall is None:  # first frame: audio sets meeting time 0 within the first second
                while self.t0_wall() is None and not self._halt.wait(0.05):
                    pass
            with self._lock:
                if self._halt.is_set():
                    return
                if self._writer is not None and round((wall - self._piece_wall) * 1000) >= round(self.piece_s * 1000):
                    self._hand_off(wall)
                if self._writer is None:
                    self._piece_wall, self._piece_ms = wall, max(0, round((wall - self.t0_wall()) * 1000))
                    self._writer = self.writer_factory(self.out_dir / f"piece-{self._piece_ms:09d}.mp4")
                self._writer.append(sbuf, wall)
        except Exception:
            self._fail()

    def _hand_off(self, end_wall: float):
        """Caller holds the lock. Finish the open piece off the capture thread; pieces are delivered in order."""
        w, ms, prev, self._writer = self._writer, self._piece_ms, self._finisher, None
        if w is None:
            return

        def run():
            if prev is not None:
                prev.join()
            try:
                w.finish(end_wall)
                self.on_piece(ms, w.path)
            except Exception:  # one lost piece; video goes on
                log.warning("video piece %s lost", w.path.name, exc_info=True)

        self._finisher = threading.Thread(target=run, name="fleck-video-piece", daemon=True)
        self._finisher.start()

    def _on_stopped(self):  # the stream ended on its own (window closed, permission pulled, callback error)
        try:
            with self._lock:
                if self._halt.is_set():  # our own stop(), or a failure already reported
                    return
                self._halt.set()
                self._failed = True
                self._hand_off(time.time())
                stream, self._stream = self._stream, None
            log.warning("video stream stopped; recording audio only")
            if stream is not None:
                stream.stop()
        except Exception:
            log.warning("video stream stop failed", exc_info=True)
        self.on_status(NO_VIDEO)

    def _fail(self):
        with self._lock:
            first, self._failed = not self._failed, True
            self._halt.set()
            w, self._writer = self._writer, None
            stream, self._stream = self._stream, None
        if not first:
            return
        log.exception("video failed; recording audio only")
        for end in (w and w.cancel, stream and stream.stop):
            try:
                end and end()
            except Exception:
                pass
        self.on_status(NO_VIDEO)

    def stop(self) -> None:
        deadline = time.monotonic() + self.STOP_BUDGET_S
        self._halt.set()
        with self._lock:
            self._hand_off(time.time())
            stream, self._stream = self._stream, None
            fin = self._finisher
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                log.warning("video stream stop failed", exc_info=True)
        for t in (fin, self._thread):
            if t is not None:
                t.join(max(0.0, deadline - time.monotonic()))
