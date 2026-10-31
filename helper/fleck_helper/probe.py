"""The signed bundle's entry point: the menu-bar app, or with a flag one of the checks that need its permissions.

Capability probe (menu bar, mic, system audio, notification action):
`open -W -n "dist/Fleck Helper.app" --args --probe --out /path/probe.json`. `--capture-test` and `--panel-test` are
the Mac tests' in-bundle halves (tests/mac).
macOS-only imports are lazy so Linux CI can import this module.
"""
import json
import math
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
from array import array
from pathlib import Path

PROMPT_WAIT = 45  # seconds a person gets to answer each permission prompt
ACTION_WAIT = 20  # seconds a person gets to click Record on the notification


def rms(samples) -> float:
    n = len(samples)
    return math.sqrt(sum(x * x for x in samples) / n) if n else 0.0


def tone_samples(amp: float, hz: float, rate: int, secs: float) -> list[float]:
    return [amp * math.sin(2 * math.pi * hz * i / rate) for i in range(int(rate * secs))]


def _write_tone(path: str) -> None:
    pcm = [int(x * 32767) for x in tone_samples(0.5, 440, 16000, 6.0)]
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack(f"<{len(pcm)}h", *pcm))


def _wait(pred, timeout) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.2)
    return False


def probe_mic(out: dict) -> None:
    import AVFoundation as AV

    granted = []
    AV.AVCaptureDevice.requestAccessForMediaType_completionHandler_(AV.AVMediaTypeAudio, lambda ok: granted.append(ok))
    _wait(lambda: granted, PROMPT_WAIT)
    out["mic_authorized"] = bool(granted and granted[0])
    if not out["mic_authorized"]:
        return
    engine = AV.AVAudioEngine.alloc().init()
    inp = engine.inputNode()
    ok, err = inp.setVoiceProcessingEnabled_error_(True, None)
    out["voice_processing"] = bool(ok)
    acc: list[float] = []

    def tap(buf, when):
        n = buf.frameLength()
        ch = buf.floatChannelData()
        if ch and n:
            acc.extend(ch[0][i] for i in range(n))

    inp.installTapOnBus_bufferSize_format_block_(0, 4096, None, tap)
    ok, err = engine.startAndReturnError_(None)
    if not ok:
        out["mic_error"] = str(err)
        return
    time.sleep(3)
    engine.stop()
    inp.removeTapOnBus_(0)
    out["mic_rms"] = rms(acc)


def probe_system_audio(out: dict) -> None:
    import CoreMedia as CM
    import ScreenCaptureKit as SC
    from Foundation import NSObject

    acc: list[float] = []

    class Out(NSObject):
        def stream_didOutputSampleBuffer_ofType_(self, stream, sbuf, typ):
            if typ != SC.SCStreamOutputTypeAudio:
                return
            blk = CM.CMSampleBufferGetDataBuffer(sbuf)
            n = CM.CMBlockBufferGetDataLength(blk)
            st, data = CM.CMBlockBufferCopyDataBytes(blk, 0, n, None)
            if st == 0 and data:
                acc.extend(array("f", bytes(data)[: n // 4 * 4]))

    shareable = []
    SC.SCShareableContent.getShareableContentWithCompletionHandler_(lambda c, e: shareable.append((c, e)))
    if not _wait(lambda: shareable, PROMPT_WAIT) or shareable[0][0] is None:
        out["system_audio_error"] = f"no shareable content (screen recording not granted?): {shareable and shareable[0][1]}"
        return
    display = shareable[0][0].displays()[0]
    flt = SC.SCContentFilter.alloc().initWithDisplay_excludingWindows_(display, [])
    cfg = SC.SCStreamConfiguration.alloc().init()
    cfg.setCapturesAudio_(True)
    cfg.setExcludesCurrentProcessAudio_(True)
    cfg.setSampleRate_(16000)
    cfg.setChannelCount_(1)
    stream = SC.SCStream.alloc().initWithFilter_configuration_delegate_(flt, cfg, None)
    handler = Out.alloc().init()
    ok, err = stream.addStreamOutput_type_sampleHandlerQueue_error_(handler, SC.SCStreamOutputTypeAudio, None, None)
    if not ok:
        out["system_audio_error"] = str(err)
        return
    started = []
    stream.startCaptureWithCompletionHandler_(lambda e: started.append(e))
    if not _wait(lambda: started, 10) or started[0] is not None:
        out["system_audio_error"] = f"start failed: {started and started[0]}"
        return
    with tempfile.NamedTemporaryFile(suffix=".wav") as f:
        _write_tone(f.name)
        player = subprocess.Popen(["afplay", f.name])
        time.sleep(3)
        player.terminate()
    done = []
    stream.stopCaptureWithCompletionHandler_(lambda e: done.append(e))
    _wait(lambda: done, 5)
    out["system_rms"] = rms(acc)


def probe_notification(out: dict) -> None:
    import UserNotifications as UN
    from Foundation import NSObject

    received = []

    class Delegate(NSObject):
        def userNotificationCenter_didReceiveNotificationResponse_withCompletionHandler_(self, c, resp, done):
            received.append(str(resp.actionIdentifier()))
            done()

        def userNotificationCenter_willPresentNotification_withCompletionHandler_(self, c, n, done):
            done(UN.UNNotificationPresentationOptionBanner | UN.UNNotificationPresentationOptionList)

    center = UN.UNUserNotificationCenter.currentNotificationCenter()
    delegate = Delegate.alloc().init()
    center.setDelegate_(delegate)
    action = UN.UNNotificationAction.actionWithIdentifier_title_options_("record", "Record", UN.UNNotificationActionOptionForeground)
    cat = UN.UNNotificationCategory.categoryWithIdentifier_actions_intentIdentifiers_options_("meeting", [action], [], 0)
    center.setNotificationCategories_({cat})
    res = []
    center.requestAuthorizationWithOptions_completionHandler_(
        UN.UNAuthorizationOptionAlert | UN.UNAuthorizationOptionSound, lambda ok, e: res.append((ok, e)))
    _wait(lambda: res, PROMPT_WAIT)
    out["notification_authorized"] = bool(res and res[0][0])
    out["action_received"] = False
    if not out["notification_authorized"]:
        if res and res[0][1] is not None:
            out["notification_error"] = str(res[0][1])
        return
    content = UN.UNMutableNotificationContent.alloc().init()
    content.setTitle_("Fleck probe")
    content.setBody_("Meeting detected. Click Record to finish the probe.")
    content.setCategoryIdentifier_("meeting")
    req = UN.UNNotificationRequest.requestWithIdentifier_content_trigger_("fleck-probe", content, None)
    posted = []
    center.addNotificationRequest_withCompletionHandler_(req, lambda e: posted.append(e))
    _wait(lambda: posted, 5)
    out["notification_posted"] = bool(posted and posted[0] is None)
    out["action_received"] = _wait(lambda: "record" in received, ACTION_WAIT)


def _run_in_app(work) -> None:
    """Runs `work(app)` on a thread inside a menu-bar app (AppKit, SCK and AVFoundation want a running main loop)."""
    import rumps

    app = rumps.App("Fleck", title="Fleck", quit_button=None)

    def start(_):
        t.stop()
        threading.Thread(target=lambda: _finish(work, app), daemon=True).start()

    t = rumps.Timer(start, 0.5)
    t.start()
    app.run()


def _finish(work, app) -> None:
    import rumps
    try:
        work(app)
    finally:
        rumps.quit_application()


def run_probe(out_path: str) -> None:
    result: dict = {"menubar": False, "mic_rms": None, "system_rms": None,
                    "notification_authorized": False, "action_received": False}

    def work(app):
        result["menubar"] = app._nsapp.nsstatusitem is not None
        for step in (probe_mic, probe_system_audio, probe_notification):
            try:
                step(result)
            except Exception as e:  # record, keep probing the other edges
                result[f"{step.__name__}_exception"] = repr(e)
        Path(out_path).write_text(json.dumps(result, indent=2))
    _run_in_app(work)


def capture_test(play: str, out_dir: str) -> None:
    """Mac test helper (tests/mac/test_live_capture.py): LiveSource while `afplay` plays `play` through the speakers;
    writes capture.wav (left You, right Client, 16 kHz) and capture.json. Runs in the signed bundle, which holds
    the Screen Recording permission."""
    import numpy as np
    from fleck_helper.audio import Frame, Gap, LiveSource, Unavailable

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    def work(app):
        you, client, info = [], [], {"gaps": [], "unavailable": [], "play_start_ms": None, "play_end_ms": None}
        player, t_ms = None, 0
        try:
            for item in LiveSource().frames():
                if isinstance(item, Frame):
                    t_ms = item.t_ms
                    you.append(item.you)
                    client.append(item.client)
                    if player is None and t_ms >= 1500:  # capture is up: play
                        player, info["play_start_ms"] = subprocess.Popen(["afplay", play]), t_ms
                    if player is not None and info["play_end_ms"] is None and player.poll() is not None:
                        info["play_end_ms"] = t_ms
                    if info["play_end_ms"] is not None and t_ms >= info["play_end_ms"] + 2000:
                        break
                    if t_ms > 60_000:
                        break
                elif isinstance(item, Gap):
                    info["gaps"].append([item.start_ms, item.end_ms, item.stream])
                elif isinstance(item, Unavailable):
                    info["unavailable"].append(item.reason)
        except Exception as e:
            info["error"] = repr(e)
        pcm = np.clip(np.column_stack([np.concatenate(you or [np.zeros(0)]), np.concatenate(client or [np.zeros(0)])]) * 32768,
                      -32768, 32767).astype("<i2")
        with wave.open(str(out / "capture.wav"), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(pcm.tobytes())
        (out / "capture.json").write_text(json.dumps(info, indent=2))
    _run_in_app(work)


STRIPE_RGB = (0x2F, 0xB8, 0xA8)


def _screenshot_rgba(display):
    """A full-display ScreenCaptureKit screenshot as an (h, w, 4) sRGB uint8 array, top row first."""
    import numpy as np
    import Quartz as Q
    import ScreenCaptureKit as SC

    flt = SC.SCContentFilter.alloc().initWithDisplay_excludingWindows_(display, [])
    cfg = SC.SCStreamConfiguration.alloc().init()
    cfg.setWidth_(display.width() * 2)
    cfg.setHeight_(display.height() * 2)
    got = []
    SC.SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_(flt, cfg, lambda img, e: got.append((img, e)))
    if not _wait(lambda: got, 10) or got[0][0] is None:
        raise RuntimeError(f"screenshot failed: {got and got[0][1]}")
    img = got[0][0]
    w, h = Q.CGImageGetWidth(img), Q.CGImageGetHeight(img)
    buf = bytearray(w * h * 4)
    ctx = Q.CGBitmapContextCreate(buf, w, h, 8, w * 4, Q.CGColorSpaceCreateWithName(Q.kCGColorSpaceSRGB),
                                  Q.kCGImageAlphaPremultipliedLast)
    Q.CGContextDrawImage(ctx, ((0, 0), (w, h)), img)
    del ctx
    return np.frombuffer(bytes(buf), np.uint8).reshape(h, w, 4)


def stripe_hits(rgba, box, tol: int = 24) -> int:
    """Pixels in box (x0, y0, x1, y1, image pixels) within `tol` of the library stripe colour on every channel."""
    import numpy as np
    x0, y0, x1, y1 = box
    region = rgba[y0:y1, x0:x1, :3].astype(int)
    return int((np.abs(region - np.array(STRIPE_RGB)).max(axis=2) <= tol).sum())


def panel_test(out_path: str) -> None:
    """Mac test helper (tests/mac/test_panel_not_in_share.py): shows the panel with an "Our material" answer, takes a
    full-screen ScreenCaptureKit screenshot and counts stripe-coloured pixels where the panel is. A control shot
    with sharing allowed proves the colour would be found."""
    import ScreenCaptureKit as SC
    from AppKit import NSScreen, NSWindowSharingNone, NSWindowSharingReadOnly
    from PyObjCTools import AppHelper

    from fleck_core.events import AssistEvent
    from fleck_core.types import Citation
    from fleck_helper.panel import AnswerPanel

    def work(app):
        result = {}
        try:
            panel = AnswerPanel()
            cit = Citation(doc_id="d1", chunk_id="d1-0", title="Rate card FY2026", client=None, kind="firm")
            ev = dict(type="assist", meeting_id="t", assist_id="a1", segment_id="client-61000",
                      question="What does a senior engineer cost per month, offshore?")
            panel.show("t")
            panel.apply(AssistEvent(phase="thinking", **ev))
            panel.apply(AssistEvent(phase="done", kind="library", citations=[cit],
                                    text="$5,200 a month offshore in Bengaluru, $13,500 onsite.", **ev))
            time.sleep(1.5)
            shareable = []
            SC.SCShareableContent.getShareableContentWithCompletionHandler_(lambda c, e: shareable.append((c, e)))
            if not _wait(lambda: shareable, 10) or shareable[0][0] is None:
                raise RuntimeError(f"no shareable content: {shareable and shareable[0][1]}")
            display = shareable[0][0].displays()[0]
            f, screen = panel._panel.frame(), NSScreen.screens()[0].frame()
            k = display.width() * 2 / screen.size.width
            box = (int(f.origin.x * k), int((screen.size.height - f.origin.y - f.size.height) * k),
                   int((f.origin.x + f.size.width) * k), int((screen.size.height - f.origin.y) * k))
            result.update(panel_frame=[f.origin.x, f.origin.y, f.size.width, f.size.height], box=box)
            result["hidden_hits"] = stripe_hits(_screenshot_rgba(display), box)
            AppHelper.callAfter(panel._panel.setSharingType_, NSWindowSharingReadOnly)
            time.sleep(0.5)
            result["control_hits"] = stripe_hits(_screenshot_rgba(display), box)
            AppHelper.callAfter(panel._panel.setSharingType_, NSWindowSharingNone)
        except Exception as e:
            result["error"] = repr(e)
        Path(out_path).write_text(json.dumps(result, indent=2))
    _run_in_app(work)


def main(argv=None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    arg = lambda name, default=None: argv[argv.index(name) + 1] if name in argv else default  # noqa: E731
    if "--probe" in argv:
        run_probe(arg("--out", "probe.json"))
    elif "--capture-test" in argv:
        capture_test(arg("--play"), arg("--out"))
    elif "--panel-test" in argv:
        panel_test(arg("--out"))
    else:  # the app itself
        from fleck_helper.menubar import main as run
        run()


if __name__ == "__main__":
    main()
