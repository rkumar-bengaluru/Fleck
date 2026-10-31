"""The menu-bar app (spec 7.5; mockup MenuBar.dc.html): recording control, the Today list, reminders, status.

`Recorder` holds the recording logic and takes its edges as arguments, so it is tested with fakes; `MenuBarApp` is
the rumps shell around it. macOS-only imports are lazy so Linux CI can import this module."""
import dataclasses
import json
import logging
import os
import re
import subprocess
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path

import httpx

from fleck_core.answer import MODEL as ANSWER_MODEL
from fleck_core.events import SegmentEvent
from fleck_core.retrieval import EMBED_MODEL
from fleck_helper.audio import CHUNK_FRAMES, AudioChunkWriter, Frame, Gap, Unavailable
from fleck_helper.classify import CLASSIFY_MODEL
from fleck_helper.pipeline import Candidate, Trigger

STOP_RETRIES = (2, 5, 10, 20, 30, 60)  # seconds between tries of a Stop the api didn't take (~2 min, decision 153)
MODELS = (ANSWER_MODEL, EMBED_MODEL, CLASSIFY_MODEL)  # loaded while recording, unloaded after (decision 123)
CHUNKS = Path.home() / "Library/Application Support/Fleck/recordings"
LOG = Path.home() / "Library/Logs/Fleck/helper.log"
log = logging.getLogger("fleck.app")


def fallback_line(fallback_mode: str) -> str:
    return f"General knowledge answers: {'on' if fallback_mode == 'claude' else 'off'}"


def status_lines(fallback_mode: str, has_key: bool, degraded: bool) -> list[str]:
    """Warnings the menu shows (and the menu-bar title flags)."""
    return ((["No Claude key: general answers are off"] if fallback_mode == "claude" and not has_key else [])
            + (["Question detection degraded"] if degraded else []))


def _ps() -> str:
    return subprocess.run(["ps", "-axo", "pid=,args="], capture_output=True, text=True, timeout=5).stdout


def parallel_slots(ps_text: str) -> list[int]:
    """`-np N` of each running Ollama runner: OLLAMA_NUM_PARALLEL as the server applied it."""
    return [int(m.group(1)) for line in ps_text.splitlines() if "llama-server" in line
            for m in [re.search(r" -np (\d+)", line)] if m]


def check_parallel() -> None:
    """Decision 98's prefill reuses Ollama's one slot; with more slots the answer may land on a cold one. Warn only:
    the user's Ollama settings are theirs."""
    slots = parallel_slots(_ps())
    if not slots:
        log.info("OLLAMA_NUM_PARALLEL not checked: no Ollama runner found")
    elif set(slots) == {1}:
        log.info("Ollama runners use one slot each (OLLAMA_NUM_PARALLEL=1)")
    elif set(slots) != {1}:
        log.warning("Ollama runs with %s parallel slots; prefill (decision 98) assumes OLLAMA_NUM_PARALLEL=1, so "
                    "answers may start slower. Set it to 1 and restart Ollama.", slots)


class Api:
    def __init__(self, url: str, token: str | None):
        self.url, self.headers = url.rstrip("/"), {"Authorization": f"Bearer {token}"}

    def get(self, path: str):
        return httpx.get(self.url + path, headers=self.headers, timeout=10).raise_for_status().json()

    def post(self, path: str, json=None):
        r = httpx.post(self.url + path, json=json, headers=self.headers, timeout=10).raise_for_status()
        return r.json() if r.content else None


class PowerAssertion:
    """Keeps the Mac from idle-sleeping while recording (IOPMAssertionCreateWithName)."""

    def __init__(self):
        import ctypes
        self._c = ctypes
        self._io = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/IOKit.framework/IOKit")
        self._cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self._cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        self._cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        self._io.IOPMAssertionCreateWithName.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
                                                         ctypes.POINTER(ctypes.c_uint32)]
        self._io.IOPMAssertionRelease.argtypes = [ctypes.c_uint32]
        self._id = None

    def _cfstr(self, s: str):
        return self._cf.CFStringCreateWithCString(None, s.encode(), 0x08000100)  # kCFStringEncodingUTF8

    def acquire(self) -> None:
        if self._id is None:
            aid = self._c.c_uint32(0)
            # kIOPMAssertionTypePreventUserIdleSystemSleep, kIOPMAssertionLevelOn
            if self._io.IOPMAssertionCreateWithName(self._cfstr("PreventUserIdleSystemSleep"), 255,
                                                    self._cfstr("Fleck is recording a meeting"), self._c.byref(aid)) == 0:
                self._id = aid.value
            else:
                log.warning("could not hold the no-sleep assertion")

    def release(self) -> None:
        if self._id is not None:
            self._io.IOPMAssertionRelease(self._id)
            self._id = None


class _Recording:
    """A source that ends when `stop` is set, writes its audio to chunks (uploaded as each completes), and moves its
    timeline past what a resumed meeting already recorded."""

    def __init__(self, source, stop: threading.Event, offset_ms: int, writer: AudioChunkWriter, upload):
        self.source, self.stop, self.offset, self.writer, self.upload = source, stop, offset_ms, writer, upload
        self.t0_wall = None  # wall time at meeting time 0

    def frames(self):
        gen = self.source.frames()
        try:
            for item in gen:
                if self.stop.is_set():
                    break
                if isinstance(item, Frame):
                    if self.t0_wall is None:
                        self.t0_wall = time.time() - (item.t_ms + self.offset) / 1000
                    if done := self.writer.push(item):
                        self.upload(*done)
                yield dataclasses.replace(item, **{k: getattr(item, k) + self.offset
                                                   for k in ("t_ms", "start_ms", "end_ms") if hasattr(item, k)})
        finally:
            gen.close()  # stops the capture
            if done := self.writer.close():
                self.upload(*done)


def gap_marker(mid: str, gap: Gap) -> SegmentEvent:
    """Spec 7.7: a capture outage (stream restart, failure or dropped audio) as a transcript line. It travels as a final
    client segment with no gate (the api stores it like any segment; the web shows `gap-` ids as a marker, not speech)."""
    def stamp(ms):
        return f"{ms // 60_000}:{ms // 1000 % 60:02d}"
    what = {"you": "Your microphone", "both": "Audio"}.get(gap.stream, "Audio from the call")
    return SegmentEvent(type="segment", meeting_id=mid, segment_id=f"gap-{gap.start_ms}", stream="client", start_ms=gap.start_ms,
                        end_ms=gap.end_ms, text=f"{what} was interrupted ({stamp(gap.start_ms)}–{stamp(gap.end_ms)})", final=True, gate=None)


def rewarm_embedders() -> None:
    """Loads both embedders with a long timeout: a live classify call gives up after 1 s, which can cancel a model
    load that would take longer, so an evicted embedder never comes back by itself."""
    from fleck_helper.classify import live_embed
    try:
        live_embed("warm up", timeout=30, warm=True)
    except Exception as e:
        log.warning("embedder re-warm failed: %r", e)


def default_source():
    """FLECK_SOURCE=replay:<wav> replays a stereo WAV in real time (demos, screenshots); else live capture."""
    from fleck_helper.audio import LiveSource, ReplaySource
    src = os.environ.get("FLECK_SOURCE", "")
    return ReplaySource(Path(src.removeprefix("replay:"))) if src.startswith("replay:") else LiveSource()


def fallback_mode(api_mode: str | None = None) -> str:
    """Off when the helper's own environment or the api (`/api/me`, .env's FLECK_LIVE_FALLBACK) says off."""
    return "off" if "off" in (os.environ.get("FLECK_LIVE_FALLBACK"), api_mode) else "claude"


class Recorder:
    """One recording at a time. `start` and `stop` return at once; the meeting runs on its own thread."""

    def __init__(self, api, transport, panel, *, power, ollama, ask, pipeline_factory=None, source_factory=default_source,
                 assistant_factory=None, chunks: Path = CHUNKS, check_parallel=check_parallel, rewarm=rewarm_embedders,
                 on_change=lambda: None):
        if pipeline_factory is None:
            from fleck_helper.pipeline import Pipeline as pipeline_factory
        if assistant_factory is None:
            from fleck_helper.cli import load_brief, make_assistant
            assistant_factory = lambda m: make_assistant(m, fallback_mode(), load_brief(self.api.get, m["id"]))  # noqa: E731
        self.api, self.transport, self.panel, self.power, self.ollama, self.ask = api, transport, panel, power, ollama, ask
        self.pipeline_factory, self.source_factory, self.assistant_factory = pipeline_factory, source_factory, assistant_factory
        self.chunks, self.check_parallel, self.rewarm, self.on_change = Path(chunks), check_parallel, rewarm, on_change
        self.recording: str | None = None  # the meeting id
        self.title: str | None = None
        self.started_at: float | None = None
        self.degraded = False
        self.stop_retries = STOP_RETRIES  # Quit sets () so it doesn't wait: the kept state file offers the Stop again
        self._lock = threading.RLock()
        self._thread = self._after = None

    def start(self, event_id: int | None = None, title: str | None = None, meeting_id: str | None = None) -> None:
        """`meeting_id` records an existing meeting (the api's `start`); otherwise the meeting this Mac was recording
        when Fleck stopped (the state file) is offered for resume, else a new one is created. Makes HTTP calls: call
        it off the main thread (`ask` is then run on the main thread)."""
        with self._lock:
            if self.recording:
                if meeting_id and meeting_id != self.recording:  # Record in the web app during this call
                    log.warning("already recording %s; meeting %s stopped", self.recording, meeting_id)
                    threading.Thread(target=self._post_stop, args=(meeting_id,), daemon=True).start()
                return
            mid = meeting_id or self._resumable() or str(self.api.post("/api/meetings", {"event_id": event_id, "title": title})["id"])
            self.recording, self.started_at, stop = mid, time.time(), threading.Event()
            self._stop = stop
            self.power.acquire()
            self.panel.show(mid)
            threading.Thread(target=self._warm, daemon=True).start()
            self._thread = threading.Thread(target=self._record, args=(mid, stop), daemon=True, name=f"meeting-{mid}")
            self._thread.start()
        self.on_change()

    # the meeting this Mac is recording and its next chunk number; removed at Stop, so after a crash it names the
    # meeting to resume (chunks are deleted once uploaded, so the number can't come from the folder)
    def _state(self) -> dict:
        try:
            return json.loads((self.chunks / "current.json").read_text())
        except (OSError, ValueError):
            return {}

    def _save_state(self, mid: str, next_seq: int) -> None:
        with self._lock:
            if self.recording != mid:  # after Stop the state file is gone for good
                return
            self.chunks.mkdir(parents=True, exist_ok=True)
            (self.chunks / "current.json.tmp").write_text(json.dumps({"meeting_id": mid, "next_seq": next_seq}))
            (self.chunks / "current.json.tmp").replace(self.chunks / "current.json")

    def _resumable(self) -> str | None:
        mid = self._state().get("meeting_id")
        if not mid:
            return None
        try:
            m = self.api.get(f"/api/meetings/{mid}")["meeting"]
        except Exception as e:
            log.warning("meeting %s not checked for resume: %r", mid, e)
            return None
        if m["status"] == "recording":
            if self.ask(f"“{m['title']}” is still recording (Fleck stopped before it ended). Resume it?"):
                return mid
            self._post_stop(mid)  # declined: process what it has
        (self.chunks / "current.json").unlink(missing_ok=True)
        return None

    def _post_stop(self, mid: str, retries=()) -> bool:
        """True once the api has the Stop; a refusal (4xx) is not retried."""
        for wait in (*retries, None):
            try:
                self.api.post(f"/api/meetings/{mid}/stop")
                return True
            except Exception as e:
                log.warning("stop for meeting %s not sent: %r", mid, e)
                if wait is None or (isinstance(e, httpx.HTTPStatusError) and e.response.status_code < 500):
                    return False
                time.sleep(wait)
        return False

    def _forget(self, mid: str) -> None:
        with self._lock:
            if self.recording != mid and self._state().get("meeting_id") == mid:
                (self.chunks / "current.json").unlink(missing_ok=True)

    def stop(self, notify_api: bool = True, meeting_id: str | None = None) -> None:
        """`meeting_id`: stop only if that meeting is the one recording (a finished meeting thread never stops the next)."""
        with self._lock:
            if not self.recording or (meeting_id is not None and meeting_id != self.recording):
                return
            mid, th = self.recording, self._thread
            self.recording = self.started_at = None
            self._stop.set()
            if not notify_api:  # the api already has this Stop; otherwise the file goes once it does (a failed Stop is offered again)
                (self.chunks / "current.json").unlink(missing_ok=True)
            self.panel.hide()
            self.power.release()

            def after():  # the pipeline flushes its last segments and chunks first
                if th is not threading.current_thread():
                    th.join(120)
                with self._lock:
                    busy = self.recording is not None  # the next meeting already started: keep its models
                for m in () if busy else MODELS:
                    try:
                        self.ollama.unload(m)
                    except Exception as e:
                        log.warning("unload %s failed: %r", m, e)
                if notify_api and self._post_stop(mid, self.stop_retries):
                    self._forget(mid)
            self._after = threading.Thread(target=after, daemon=True)
            self._after.start()
        self.on_change()

    def wait(self, timeout: float | None) -> None:
        if self._after:
            self._after.join(timeout)

    def on_message(self, msg: dict) -> None:
        """The api's `start` (Record in the web app) and `stop` (Stop in the web app), on the transport's thread."""
        if msg.get("type") == "start":
            self.start(meeting_id=str(msg["meeting_id"]))
        elif msg.get("type") == "stop" and str(msg.get("meeting_id")) == self.recording:
            self.stop(notify_api=False)

    def _warm(self) -> None:
        # the answer model only: the pipeline's warm-up loads both embedders with the options their calls use (a load
        # with other options would make Ollama reload them at the first question)
        try:
            self.ollama.warm(ANSWER_MODEL)
        except Exception as e:
            log.warning("warm %s failed: %r", ANSWER_MODEL, e)
        self.check_parallel()

    def _set_degraded(self, value: bool) -> None:
        if value != self.degraded:
            self.degraded = value
            if value and self.recording:
                threading.Thread(target=self.rewarm, daemon=True).start()
            self.on_change()

    def _record(self, mid: str, stop: threading.Event) -> None:
        try:
            m = self.api.get(f"/api/meetings/{mid}")["meeting"]
            self.title = m.get("title")
            self.on_change()
            assistant = self.assistant_factory(m)
            writer = AudioChunkWriter(self.chunks / mid)
            state = self._state()
            seq = state.get("next_seq", 0) if state.get("meeting_id") == mid else 0  # a resumed meeting goes on
            # the chunk being written when Fleck crashed is never uploaded but stays on disk: skip its number, so the
            # timeline never steps back over segments already sent.
            # shortcut: that cut-short chunk counts as a full 10 s (the api fills a missing seq the same way)
            writer.seq = seq + 1 if (writer.dir / f"chunk-{seq:05d}.wav").exists() else seq
            self._save_state(mid, writer.seq)

            def upload(seq, path):
                self.transport.upload_chunk(mid, seq, path)
                self._save_state(mid, seq + 1)
            src = _Recording(self.source_factory(), stop, writer.seq * CHUNK_FRAMES * 20, writer, upload)

            def post(e):
                self.transport.send(e)
                self.panel.apply(e)
            pipe = self.pipeline_factory(mid)
            for e in pipe.run(src):
                if isinstance(e, SegmentEvent):
                    self.transport.send(e)
                elif isinstance(e, Candidate):
                    assistant.prepare(e.text, e.embedding)
                elif isinstance(e, Trigger):
                    assistant.start(e, mid, post, src.t0_wall)
                elif isinstance(e, (Gap, Unavailable)):
                    log.warning("meeting %s: %s", mid, e)
                    if isinstance(e, Gap):
                        self.transport.send(gap_marker(mid, e))
                self._set_degraded(getattr(pipe, "degraded", False))
        except Exception:
            log.exception("recording meeting %s failed", mid)
        finally:
            self._set_degraded(False)
            if not stop.is_set():  # a crash or a source that ended: release everything as Stop does
                self.stop(meeting_id=mid)


def _ask(text: str) -> bool:
    """The resume question, on the main thread (`Recorder.start` runs off it), waiting for the answer."""
    import rumps
    from AppKit import NSApp
    from Foundation import NSThread
    from PyObjCTools import AppHelper
    box, done = [], threading.Event()

    def ask():
        try:
            NSApp.activateIgnoringOtherApps_(True)  # a menu-bar app's alert would open behind the call
            box.append(rumps.alert(title="Fleck", message=text, ok="Resume", cancel="Start new") == 1)
        finally:  # a failing alert must not leave `start` waiting forever with the recorder's lock held
            done.set()
    if NSThread.isMainThread():
        ask()
    else:
        AppHelper.callAfter(ask)
        done.wait()
    return bool(box and box[0])  # no answer counts as "Start new"


def _bg(fn, *args, **kw) -> None:
    """Menu and notification actions that make HTTP calls run off the main thread, so the panel never freezes."""
    threading.Thread(target=fn, args=args, kwargs=kw, daemon=True).start()


def _clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"


def _app_class():
    import rumps
    from PyObjCTools import AppHelper

    class MenuBarApp(rumps.App):
        """The menu from MenuBar.dc.html; rebuilt on the main thread whenever the recorder's state changes."""

        def __init__(self, api, recorder, panel, scheduler, has_key: bool, web_url: str):
            super().__init__("Fleck", title="Fleck", quit_button=None)
            self.api, self.recorder, self.panel, self.scheduler = api, recorder, panel, scheduler
            self.has_key, self.web_url = has_key, web_url.rstrip("/")
            self.events, self.who, self.api_fallback = [], "Not connected", None
            if recorder:
                recorder.on_change = lambda: AppHelper.callAfter(self.rebuild)
            panel.on_visibility = lambda visible: AppHelper.callAfter(self.rebuild)
            self.rebuild()
            rumps.Timer(self._tick, 1).start()
            rumps.Timer(lambda _: _bg(self._refresh), 300).start()
            threading.Thread(target=self._refresh, daemon=True).start()

        def _refresh(self, _=None):
            """The Today list, reminders, the Claude key and "Connected as"; call it off the main thread."""
            from fleck_helper.cli import load_claude_key
            self.has_key = load_claude_key()  # `set-claude-key` while the app runs clears the warning
            if self.recorder is None:
                return
            try:
                me = self.api.get('/api/me')
                self.who, self.api_fallback = f"Connected as {me['email']}", me.get("live_fallback")
                self.events = [e for e in self.api.get("/api/events?range=today") if not e.get("cancelled")]
                if self.scheduler:
                    self.scheduler.schedule(self.events)
            except Exception as e:
                self.who = "Not connected to Fleck"
                log.info("refresh failed: %r", e)
            AppHelper.callAfter(self.rebuild)

        def _warnings(self):
            return status_lines(fallback_mode(self.api_fallback), self.has_key, bool(self.recorder and self.recorder.degraded))

        def _tick(self, _):
            r = self.recorder
            flag = " ⚠" if self._warnings() else ""
            self.title = f"● Fleck {_clock(time.time() - r.started_at)}{flag}" if r and r.started_at else f"Fleck{flag}"
            if r and r.started_at and self._rec_item is not None:
                self._rec_item.title = f"Recording {_clock(time.time() - r.started_at)}"

        def rebuild(self):
            from fleck_helper.cli import load_claude_key
            r = self.recorder
            if r and r.recording:
                self.has_key = load_claude_key()  # re-read at each recording start (a local keychain read)
            items, self._rec_item = [], None
            if r and r.recording:
                self._rec_item = rumps.MenuItem(f"Recording {_clock(time.time() - (r.started_at or time.time()))}")
                items += [self._rec_item] + ([rumps.MenuItem(r.title)] if r.title else [])
                items += [rumps.MenuItem("Stop recording", callback=lambda _: _bg(r.stop)), None]
            hide = rumps.MenuItem("Hide answer panel" if not self.panel.user_hidden else "Show answer panel",
                                  callback=lambda _: self.panel.toggle())
            from AppKit import NSEventModifierFlagCommand, NSEventModifierFlagOption
            hide._menuitem.setKeyEquivalent_("f")
            hide._menuitem.setKeyEquivalentModifierMask_(NSEventModifierFlagCommand | NSEventModifierFlagOption)
            items.append(hide)
            if r and r.recording:
                mid = r.recording
                items.append(rumps.MenuItem("Open meeting in browser", callback=lambda _: webbrowser.open(f"{self.web_url}/meetings/{mid}")))
            items.append(None)
            if self.events:
                items.append(rumps.MenuItem("Today"))
                for e in self.events:
                    at = datetime.fromisoformat(e["start_at"]).astimezone().strftime("%H:%M")
                    items.append(rumps.MenuItem(f"{at}   {e['title']}", callback=self._record_event(e) if r and not r.recording else None))
                items.append(None)
            items.append(rumps.MenuItem("Start recording now", callback=(lambda _: _bg(r.start)) if r and not r.recording else None))
            items.append(rumps.MenuItem("Open Fleck", callback=lambda _: webbrowser.open(self.web_url)))
            items.append(None)
            items.append(rumps.MenuItem(fallback_line(fallback_mode(self.api_fallback))))
            items += [rumps.MenuItem(f"⚠ {w}") for w in self._warnings()]
            items.append(rumps.MenuItem(self.who if r else "Not paired: run fleck-helper pair <code>"))
            items.append(rumps.MenuItem("Quit Fleck", callback=self._quit))
            self.menu.clear()
            self.menu = items
            self._tick(None)

        def _record_event(self, e):
            return lambda _: _bg(self.recorder.start, event_id=e["id"], title=e["title"])

        def _quit(self, _):
            if self.recorder and self.recorder.recording:
                self.recorder.stop_retries = ()
                self.recorder.stop()
                self.recorder.wait(None)  # its thread waits up to 120 s for the pipeline, then unloads the models
            rumps.quit_application()

    return MenuBarApp


def main() -> None:
    from fleck_core.answer import OllamaClient
    from fleck_helper.cli import api_url, load_claude_key, load_token
    from fleck_helper.notify import Scheduler, UNNotifier
    from fleck_helper.panel import AnswerPanel
    from fleck_helper.transport import Transport

    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(LOG)])
    has_key = load_claude_key()  # never logged
    token = load_token()
    panel = AnswerPanel()
    api, recorder, scheduler = Api(api_url(), token), None, None
    if token:
        recorder = Recorder(api, None, panel, power=PowerAssertion(), ollama=OllamaClient(), ask=_ask)
        recorder.transport = Transport(api_url(), token, on_message=recorder.on_message)
        from Foundation import NSBundle
        if NSBundle.mainBundle().bundleIdentifier():
            scheduler = Scheduler(UNNotifier(lambda event_id: _bg(recorder.start, event_id)))
        else:  # `fleck-helper run` from a terminal: UNUserNotificationCenter aborts without a bundle
            log.warning("meeting reminders off: not running from the app bundle")
    app = _app_class()(api, recorder, panel, scheduler, has_key, os.environ.get("FLECK_WEB_URL", "http://localhost:5173"))
    panel.install_shortcut()
    app.run()
