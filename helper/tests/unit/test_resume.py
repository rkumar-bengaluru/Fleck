"""Recording control behind the menu bar: start, stop, resume, and the api's start/stop messages (all fakes)."""
import json
import threading
import time

import numpy as np
import pytest

from fleck_core.events import AssistEvent, SegmentEvent
from fleck_helper.audio import FRAME, Frame, Gap
from fleck_helper.menubar import MODELS, Recorder
from fleck_helper.pipeline import Trigger


class FakeApi:
    def __init__(self, meetings=()):
        self.meetings = {str(m["id"]): m for m in meetings}
        self.posts, self.next_id = [], 100

    def get(self, path):
        if path == "/api/me":
            return {"id": 5, "email": "rupak@northbeam.test"}
        mid = path.rsplit("/", 1)[1]
        m = self.meetings.get(mid, {"status": "recording", "title": f"Meeting {mid}"})
        return {"meeting": m | {"id": int(mid), "people": [], "client_name": None, "library_clients": []}}

    def post(self, path, json=None):
        self.posts.append((path, json))
        if path == "/api/meetings":
            self.next_id += 1
            return {"id": self.next_id}
        return None


class FakePanel:
    def __init__(self):
        self.calls, self.events = [], []

    def show(self, meeting_id=None):
        self.calls.append("show")
        self.meeting_id = meeting_id

    def hide(self):
        self.calls.append("hide")

    def apply(self, e):
        self.events.append(e)


class FakePower:
    def __init__(self):
        self.held = 0

    def acquire(self):
        self.held += 1

    def release(self):
        self.held -= 1


class FakeOllama:
    def __init__(self):
        self.warmed, self.unloaded = [], []

    def warm(self, model):
        self.warmed.append(model)

    def unload(self, model):
        self.unloaded.append(model)


class FakeTransport:
    def __init__(self):
        self.sent, self.chunks = [], []

    def send(self, e):
        self.sent.append(e)

    def upload_chunk(self, meeting_id, seq, path):
        self.chunks.append((meeting_id, seq))


class Silence:
    def frames(self):
        for i in range(100_000):
            time.sleep(0.002)
            yield Frame(i * 20, np.zeros(FRAME, np.float32), np.zeros(FRAME, np.float32))


class FakePipeline:
    created: list = []
    first_t: dict = {}

    def __init__(self, meeting_id):
        self.meeting_id, self.degraded = meeting_id, False
        FakePipeline.created.append(meeting_id)

    def run(self, source):
        seg = SegmentEvent(type="segment", meeting_id=self.meeting_id, segment_id="client-0", stream="client", start_ms=0,
                           end_ms=900, text="Can you start in November?", final=True, gate="client")
        yield seg
        yield Trigger("client-0", seg.text, 900)
        for f in source.frames():
            FakePipeline.first_t.setdefault(self.meeting_id, f.t_ms)


class FakeAssistant:
    def start(self, trigger, meeting_id, post, stream_start_wall=None):
        th = threading.Thread(target=post, args=(AssistEvent(type="assist", meeting_id=meeting_id, assist_id="a1",
                                                             segment_id=trigger.segment_id, phase="thinking",
                                                             question=trigger.question),))
        th.start()
        return th


@pytest.fixture
def make(tmp_path):
    FakePipeline.created, FakePipeline.first_t = [], {}

    def make(api=None, ask=lambda text: True):
        r = Recorder(api or FakeApi(), FakeTransport(), FakePanel(), power=FakePower(), ollama=FakeOllama(), ask=ask,
                     pipeline_factory=FakePipeline, source_factory=Silence, assistant_factory=lambda m: FakeAssistant(),
                     chunks=tmp_path, check_parallel=lambda: None, rewarm=lambda: make.rewarmed.append(1))
        return r
    make.rewarmed = []
    return make


def _until(pred, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_panel_shows_on_start_hides_on_stop(make):
    r = make()
    r.start(title="Ad hoc call")
    assert r.panel.calls == ["show"] and r.power.held == 1 and r.recording == "101"
    assert _until(lambda: r.panel.events)  # the answer reaches the panel and the api
    assert _until(lambda: any(isinstance(e, AssistEvent) for e in r.transport.sent))
    assert _until(lambda: r.ollama.warmed == [MODELS[0]])  # the answer model; the pipeline loads the embedders
    r.stop()
    assert r.panel.calls == ["show", "hide"] and r.power.held == 0 and r.recording is None
    r.wait(5)
    assert sorted(r.ollama.unloaded) == sorted(MODELS)
    assert ("/api/meetings/101/stop", None) in r.api.posts
    assert r.transport.chunks and r.transport.chunks[-1][0] == "101"  # audio went up in chunks, the last one too
    r.stop()  # twice: harmless
    assert r.panel.calls == ["show", "hide"] and r.power.held == 0


def _crashed_mid_meeting(tmp_path, mid="41", next_seq=0):
    """What a crash leaves: the state file naming the meeting being recorded."""
    (tmp_path / "current.json").write_text(json.dumps({"meeting_id": mid, "next_seq": next_seq}))


def test_resume_recording_meeting(make, tmp_path):
    api = FakeApi([{"id": 41, "status": "recording", "recorded_by": 5, "title": "Presales: Harbourline Legal"}])
    _crashed_mid_meeting(tmp_path)
    asked = []
    r = make(api, ask=lambda text: asked.append(text) or True)
    r.start(title="Ad hoc call")
    assert r.recording == "41" and _until(lambda: FakePipeline.created == ["41"])
    assert "Presales: Harbourline Legal" in asked[0]
    assert not [p for p in api.posts if p[0] == "/api/meetings"]  # reused, not created
    r.stop()
    r.wait(5)
    assert not (tmp_path / "current.json").exists()  # a stopped meeting is never offered again


def test_no_resume_without_this_macs_state_or_once_the_meeting_ended(make, tmp_path):
    # another Mac of the same user is recording meeting 42: never offered here (no state file names it)
    r = make(FakeApi([{"id": 42, "status": "recording", "recorded_by": 5, "title": "Other Mac"}]),
             ask=lambda text: pytest.fail("not this Mac's meeting"))
    r.start(title="Ad hoc call")
    assert r.recording == "101"
    r.stop()
    r.wait(5)
    _crashed_mid_meeting(tmp_path)  # ...stopped from the web meanwhile
    r = make(FakeApi([{"id": 41, "status": "processing", "recorded_by": 5, "title": "Presales"}]),
             ask=lambda text: pytest.fail("not recording any more"))
    r.start()
    assert r.recording == "101"  # a new meeting (a fresh api)
    r.stop()
    r.wait(5)


def test_declined_resume_stops_the_old_meeting_and_starts_a_new_one(make, tmp_path):
    api = FakeApi([{"id": 41, "status": "recording", "recorded_by": 5, "title": "Presales"}])
    _crashed_mid_meeting(tmp_path)
    r = make(api, ask=lambda text: False)
    r.start(title="Ad hoc call")
    assert r.recording == "101" and ("/api/meetings/41/stop", None) in api.posts
    r.stop()
    r.wait(5)


def test_resumed_meeting_continues_the_chunk_numbering(make, tmp_path):
    api = FakeApi([{"id": 41, "status": "recording", "recorded_by": 5, "title": "Presales"}])
    _crashed_mid_meeting(tmp_path, next_seq=3)  # chunks 0-2 went up (and were deleted locally)
    r = make(api)
    r.start()
    assert _until(lambda: r.transport.sent)
    r.stop()
    r.wait(5)
    assert r.transport.chunks[0] == ("41", 3)
    assert FakePipeline.first_t["41"] == 30_000  # the timeline goes on after the 3 chunks (30 s) already recorded


def test_a_crashed_or_ended_pipeline_stops_the_recording(make):
    class Crashing(FakePipeline):
        def run(self, source):
            raise RuntimeError("mlx failed")
            yield
    r = make()
    r.pipeline_factory = Crashing
    r.start()
    assert _until(lambda: r.recording is None)
    r.wait(5)
    assert r.power.held == 0 and r.panel.calls == ["show", "hide"] and sorted(r.ollama.unloaded) == sorted(MODELS)
    assert ("/api/meetings/101/stop", None) in r.api.posts  # the api processes what it has

    class Ending(FakePipeline):
        def run(self, source):
            return iter(())  # the source ran out
    r = make()
    r.pipeline_factory = Ending
    r.start()
    assert _until(lambda: r.recording is None)
    r.wait(5)
    assert r.power.held == 0 and ("/api/meetings/101/stop", None) in r.api.posts


def test_back_to_back_meetings_keep_the_models_loaded(make):
    r = make()
    r.start()
    r.stop()
    r.start()  # the next call, at once
    r.wait(5)
    assert r.recording == "102" and r.ollama.unloaded == []  # the old meeting's unload is skipped
    assert ("/api/meetings/101/stop", None) in r.api.posts
    r.stop()
    r.wait(5)
    assert sorted(r.ollama.unloaded) == sorted(MODELS)


def test_a_start_for_another_meeting_while_busy_is_stopped(make):
    r = make()
    r.start()
    r.on_message({"type": "start", "meeting_id": "7"})  # Record in the web app during this call
    assert r.recording == "101"
    assert _until(lambda: ("/api/meetings/7/stop", None) in r.api.posts)  # not left `recording` forever
    r.on_message({"type": "start", "meeting_id": "101"})  # a duplicate after a reconnect: ignored
    time.sleep(0.05)
    assert ("/api/meetings/101/stop", None) not in r.api.posts
    r.stop()
    r.wait(5)


def test_the_panel_starts_each_meeting_empty(make):
    r = make()
    r.start()
    assert r.panel.meeting_id == "101"
    r.stop()
    r.wait(5)


def test_start_message_starts_recording(make):
    r = make(ask=lambda text: pytest.fail("a meeting started from the web is never a resume"))
    r.on_message({"type": "start", "meeting_id": "7", "event_id": 3, "client_name": None, "people": []})
    assert r.recording == "7" and _until(lambda: FakePipeline.created == ["7"])
    assert not [p for p in r.api.posts if p[0] == "/api/meetings"]
    r.on_message({"type": "stop", "meeting_id": "8"})  # not this meeting
    assert r.recording == "7"
    r.on_message({"type": "stop", "meeting_id": "7"})
    assert r.recording is None and r.panel.calls == ["show", "hide"]
    r.wait(5)
    assert not [p for p in r.api.posts if p[0].endswith("/stop")]  # the api already knows


def test_degraded_detection_reaches_the_menu(make):
    class Degrading(FakePipeline):
        def run(self, source):
            for i, e in enumerate(super().run(source)):
                self.degraded = i >= 1  # the rules decided from the trigger on
                yield e
    changes = []
    r = make()
    r.pipeline_factory, r.on_change = Degrading, lambda: changes.append(r.degraded)
    r.start()
    assert _until(lambda: r.degraded)
    assert True in changes and make.rewarmed  # the embedders get a long-timeout load to come back
    r.stop()
    r.wait(5)
    assert r.degraded is False  # cleared with the recording


def test_resume_skips_the_chunk_cut_short_by_the_crash(make, tmp_path):
    api = FakeApi([{"id": 41, "status": "recording", "recorded_by": 5, "title": "Presales"}])
    _crashed_mid_meeting(tmp_path, next_seq=3)
    (tmp_path / "41").mkdir()
    (tmp_path / "41" / "chunk-00003.wav").write_bytes(b"RIFF")  # being written at the crash, never uploaded
    r = make(api)
    r.start()
    assert _until(lambda: r.transport.sent)
    r.stop()
    r.wait(5)
    assert r.transport.chunks[0] == ("41", 4)
    assert FakePipeline.first_t["41"] == 40_000  # after the partial chunk, never back over segments already sent


def test_a_finished_meeting_thread_never_stops_the_next_meeting(make):
    r = make()
    r.start()
    r.stop(meeting_id="999")  # what a late crash path of an earlier meeting does
    assert r.recording == "101" and r.power.held == 1
    r.stop(meeting_id="101")
    assert r.recording is None
    r.wait(5)


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")  # the alert's error, on purpose
def test_a_failing_resume_prompt_answers_start_new(monkeypatch):
    import sys

    import rumps
    from PyObjCTools import AppHelper

    from fleck_helper import menubar
    if sys.platform != "darwin":
        pytest.skip("AppKit")

    def boom(**kw):
        raise RuntimeError("alert failed")
    monkeypatch.setattr(rumps, "alert", boom)
    # no run loop in a test: run it on another thread, as the main thread would (its error never reaches the waiter)
    monkeypatch.setattr(AppHelper, "callAfter", lambda fn, *a: threading.Thread(target=fn, args=a, daemon=True).start())
    out = []
    th = threading.Thread(target=lambda: out.append(menubar._ask("Resume?")), daemon=True)  # off the main thread
    th.start()
    th.join(5)
    assert out == [False]  # answered, not hung


def test_a_capture_gap_becomes_a_transcript_marker(make):
    class Gapped(FakePipeline):
        def run(self, source):
            yield Gap(62_000, 75_500, "client")
            yield Gap(600_000, 601_000, "you")
            yield Gap(3_599_000, 3_605_000, "both")
            yield from super().run(source)
    r = make()
    r.pipeline_factory = Gapped
    r.start()
    assert _until(lambda: len(r.transport.sent) >= 4)
    r.stop()
    r.wait(5)
    marks = [e for e in r.transport.sent if isinstance(e, SegmentEvent) and e.segment_id.startswith("gap-")]
    assert [(m.segment_id, m.stream, m.start_ms, m.end_ms, m.text, m.final, m.gate) for m in marks] == [
        ("gap-62000", "client", 62_000, 75_500, "Audio from the call was interrupted (1:02–1:15)", True, None),
        ("gap-600000", "client", 600_000, 601_000, "Your microphone was interrupted (10:00–10:01)", True, None),
        ("gap-3599000", "client", 3_599_000, 3_605_000, "Audio was interrupted (59:59–60:05)", True, None)]


def test_a_menu_stop_the_api_missed_is_retried_and_offered_again_until_it_lands(make, tmp_path):
    import httpx

    class FlakyApi(FakeApi):
        fails = 2

        def post(self, path, json=None):
            if path.endswith("/stop") and self.fails:
                self.fails -= 1
                raise httpx.ConnectError("api down")
            return super().post(path, json)
    r = make(FlakyApi())
    r.stop_retries = (0.01, 0.01, 0.01)
    r.start()
    assert _until(lambda: (tmp_path / "current.json").exists())
    r.stop()
    r.wait(5)
    assert r.api.posts[-1] == ("/api/meetings/101/stop", None) and not (tmp_path / "current.json").exists()
    r = make(FlakyApi())
    r.stop_retries = ()  # Quit: one try, and the state file stays so the next start offers the meeting again
    r.start()
    assert _until(lambda: (tmp_path / "current.json").exists())
    r.stop()
    r.wait(5)
    assert json.loads((tmp_path / "current.json").read_text())["meeting_id"] == "101"
