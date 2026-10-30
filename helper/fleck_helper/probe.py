"""Capability probe: proves menu bar, mic, system audio and notification action work from the signed bundle.

Run as `open -W -n "dist/Fleck Helper.app" --args --probe --out /path/probe.json`.
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
    wav = tempfile.mktemp(suffix=".wav")
    _write_tone(wav)
    player = subprocess.Popen(["afplay", wav])
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


def run_probe(out_path: str) -> None:
    import rumps
    from AppKit import NSApplication

    result: dict = {"menubar": False, "mic_rms": None, "system_rms": None,
                    "notification_authorized": False, "action_received": False}
    app = rumps.App("Fleck", title="Fleck", quit_button=None)

    def work():
        for step in (probe_mic, probe_system_audio, probe_notification):
            try:
                step(result)
            except Exception as e:  # record, keep probing the other edges
                result[f"{step.__name__}_exception"] = repr(e)
        Path(out_path).write_text(json.dumps(result, indent=2))
        rumps.quit_application()

    def start(_):
        t.stop()
        result["menubar"] = app._nsapp.nsstatusitem is not None
        threading.Thread(target=work, daemon=True).start()

    t = rumps.Timer(start, 0.5)
    t.start()
    app.run()


def main(argv=None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--probe" in argv:
        out = argv[argv.index("--out") + 1] if "--out" in argv else "probe.json"
        run_probe(out)


if __name__ == "__main__":
    main()
