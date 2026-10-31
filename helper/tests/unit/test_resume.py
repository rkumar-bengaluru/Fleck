"""Recording control behind the menu bar: start, stop, resume, and the api's start/stop messages (all fakes)."""
import threading
import time

import numpy as np
import pytest

from fleck_core.events import AssistEvent, SegmentEvent
from fleck_helper.audio import FRAME, Frame
from fleck_helper.menubar import MODELS, Recorder
from fleck_helper.pipeline import Trigger


class FakeApi:
    def __init__(self, meetings=()):
        self.meetings, self.posts, self.next_id = list(meetings), [], 100

    def get(self, path):
        if path == "/api/me":
            return {"id": 5, "email": "rupak@northbeam.test"}
        if path == "/api/meetings":
            return self.meetings
        mid = path.rsplit("/", 1)[1]
        return {"meeting": {"id": int(mid), "people": [], "client_name": None, "library_clients": []}}

    def post(self, path, json=None):
        self.posts.append((path, json))
        if path == "/api/meetings":
            self.next_id += 1
            return {"id": self.next_id}
        return None


class FakePanel:
    def __init__(self):
        self.calls, self.events = [], []

    def show(self):
        self.calls.append("show")

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


def test_resume_recording_meeting(make):
    api = FakeApi([{"id": 40, "status": "ready", "recorded_by": 5, "title": "Old"},
                   {"id": 41, "status": "recording", "recorded_by": 5, "title": "Presales: Harbourline Legal"},
                   {"id": 42, "status": "recording", "recorded_by": 9, "title": "Someone else's"}])
    asked = []
    r = make(api, ask=lambda text: asked.append(text) or True)
    r.start(title="Ad hoc call")
    assert r.recording == "41" and _until(lambda: FakePipeline.created == ["41"])
    assert "Presales: Harbourline Legal" in asked[0]
    assert not [p for p in api.posts if p[0] == "/api/meetings"]  # reused, not created
    r.stop()
    r.wait(5)


def test_declined_resume_stops_the_old_meeting_and_starts_a_new_one(make):
    api = FakeApi([{"id": 41, "status": "recording", "recorded_by": 5, "title": "Presales"}])
    r = make(api, ask=lambda text: False)
    r.start(title="Ad hoc call")
    assert r.recording == "101" and ("/api/meetings/41/stop", None) in api.posts
    r.stop()
    r.wait(5)


def test_resumed_meeting_continues_the_chunk_numbering(make, tmp_path):
    (tmp_path / "41").mkdir()
    for i in range(3):
        (tmp_path / "41" / f"chunk-{i:05d}.wav").write_bytes(b"x")
    api = FakeApi([{"id": 41, "status": "recording", "recorded_by": 5, "title": "Presales"}])
    r = make(api)
    r.start()
    assert _until(lambda: r.transport.sent)
    r.stop()
    r.wait(5)
    assert r.transport.chunks[0] == ("41", 3)
    assert FakePipeline.first_t["41"] == 30_000  # the timeline goes on after the 3 chunks (30 s) already recorded


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
