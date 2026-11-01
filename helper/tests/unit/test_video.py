import threading
import time

from fleck_helper.video import VideoRecorder, Win, _frame_handler, pick_window

ZOOM, CHROME = "us.zoom.xos", "com.google.Chrome"
T = 1_700_000_000.0


def test_pick_window_prefers_zoom_then_meet():
    inbox, meet, zoom = Win(1, CHROME, "Inbox", True), Win(2, CHROME, "Meet - abc-defg-hij", True), Win(3, ZOOM, "Zoom Meeting", True)
    assert pick_window([inbox, meet, zoom]) == zoom
    assert pick_window([inbox, meet]) == meet
    assert pick_window([Win(4, ZOOM, "Meeting", True)]).window_id == 4
    assert pick_window([Win(5, ZOOM, "Zoom Workplace", True)]) is None  # the Zoom home window is not a call


def test_pick_window_never_fleck_or_fallback():
    assert pick_window([Win(1, CHROME, "Fleck: Meeting", True), Win(2, CHROME, "Gmail", True)]) is None
    assert pick_window([Win(3, CHROME, "Meet - abc-defg-hij", False)]) is None
    assert pick_window([Win(4, ZOOM, "Fleck Meeting", True)]) is None


class FakeWriter:
    def __init__(self, path, log, fail=False, slow=0.0):
        self.path, self.log, self.fail, self.slow, self.frames = path, log, fail, slow, []

    def append(self, sbuf, wall):
        if self.fail:
            raise RuntimeError("encoder exploded")
        self.frames.append(sbuf)

    def finish(self, end_wall):
        time.sleep(self.slow)
        self.end_wall = end_wall
        self.log.append(("finish", self.path.name, len(self.frames)))

    def cancel(self):
        self.log.append(("cancel", self.path.name))


class FakeStream:
    def __init__(self, frames):
        self.frames, self.stopped, self.on_frame, self.done = frames, False, None, False

    def factory(self, window_id, on_frame, on_stopped):
        self.window_id, self.on_frame, self.on_stopped = window_id, on_frame, on_stopped
        for i, wall in enumerate(self.frames):
            on_frame(f"buf{i}", wall)
        self.done = True
        return self

    def stop(self):
        self.stopped = True


def _rec(tmp_path, stream, wins=lambda: [Win(7, CHROME, "Meet - abc-defg-hij", True)], t0=lambda: T, fail=False,
         slow=0.0, **kw):
    pieces, status, log, writers = [], [], [], []

    def writer(p):
        writers.append(FakeWriter(p, log, fail, slow))
        return writers[-1]

    r = VideoRecorder(tmp_path, t0, lambda ms, p: pieces.append((ms, p)), status.append,
                      list_windows=wins, stream_factory=stream.factory, poll_s=0.01, writer_factory=writer, **kw)
    r.writers = writers
    return r, pieces, status, log


def _wait(cond, s=2.0):
    end = time.monotonic() + s
    while not cond() and time.monotonic() < end:
        time.sleep(0.005)
    assert cond()


def test_late_lock_offsets_start_ms(tmp_path):
    polls = []

    def wins():
        polls.append(1)
        return [Win(1, CHROME, "Inbox", True)] if len(polls) <= 10 else [Win(7, CHROME, "Meet - abc-defg-hij", True)]

    stream = FakeStream([T + 20.0, T + 20.1])
    r, pieces, status, _ = _rec(tmp_path, stream, wins=wins)
    r.start()
    _wait(lambda: stream.done)
    r.stop()
    assert len(polls) == 11 and stream.window_id == 7
    assert [ms for ms, _ in pieces] == [20000]
    assert pieces[0][1] == tmp_path / "piece-000020000.mp4"
    assert status == ["Video: waiting for call window", "Video: Meet - abc-defg-hij"]


def test_waits_for_t0_at_first_frame(tmp_path):
    t0 = [None]
    threading.Timer(0.1, lambda: t0.__setitem__(0, T)).start()
    stream = FakeStream([T + 3.0])
    r, pieces, _, _ = _rec(tmp_path, stream, t0=lambda: t0[0])
    r.start()
    _wait(lambda: stream.done)
    r.stop()
    assert [ms for ms, _ in pieces] == [3000]


def test_pieces_roll_every_piece_s(tmp_path):
    stream = FakeStream([T + i / 10 for i in range(25)])
    r, pieces, _, log = _rec(tmp_path, stream, piece_s=1.0)
    r.start()
    _wait(lambda: stream.done)
    r.stop()
    assert [ms for ms, _ in pieces] == [0, 1000, 2000]
    assert [n for _, _, n in log] == [10, 10, 5]
    assert [w.end_wall for w in r.writers[:2]] == [T + 1.0, T + 2.0]  # a piece ends where the next begins


def test_stop_finishes_partial_piece(tmp_path):
    stream = FakeStream([T + i / 10 for i in range(5)])
    r, pieces, status, log = _rec(tmp_path, stream)
    r.start()
    _wait(lambda: stream.done)
    t = time.monotonic()
    r.stop()
    assert time.monotonic() - t < 5
    assert len(pieces) == 1 and log == [("finish", "piece-000000000.mp4", 5)]
    assert stream.stopped and "Video: none (audio only)" not in status
    stream.on_frame("late", T + 9)  # frames after stop are dropped
    assert len(pieces) == 1


def test_stop_while_waiting_for_window(tmp_path):
    stream = FakeStream([])
    r, pieces, status, _ = _rec(tmp_path, stream, wins=lambda: [])
    r.start()
    _wait(lambda: status)
    t = time.monotonic()
    r.stop()
    assert time.monotonic() - t < 1 and pieces == [] and stream.on_frame is None


def test_capture_error_reports_audio_only(tmp_path):
    stream = FakeStream([T, T + 0.1, T + 0.2])
    r, pieces, status, log = _rec(tmp_path, stream, fail=True)
    r.start()
    _wait(lambda: "Video: none (audio only)" in status)
    r.stop()
    assert status.count("Video: none (audio only)") == 1
    assert pieces == [] and stream.stopped and log == [("cancel", "piece-000000000.mp4")]


def test_list_windows_error_reports_audio_only(tmp_path):
    def boom():
        raise RuntimeError("no screen recording permission")

    r, pieces, status, _ = _rec(tmp_path, FakeStream([]), wins=boom)
    r.start()
    _wait(lambda: "Video: none (audio only)" in status)
    r.stop()
    assert status == ["Video: waiting for call window", "Video: none (audio only)"]


def test_window_closed_delivers_piece_and_reports_audio_only(tmp_path):
    stream = FakeStream([T, T + 0.1])
    r, pieces, status, _ = _rec(tmp_path, stream)
    r.start()
    _wait(lambda: stream.done)
    stream.on_stopped()
    _wait(lambda: pieces)
    stream.on_stopped()  # a second stop signal changes nothing
    r.stop()
    assert [ms for ms, _ in pieces] == [0]
    assert status == ["Video: waiting for call window", "Video: Meet - abc-defg-hij", "Video: none (audio only)"]
    assert stream.stopped


def test_frame_handler_forwards_complete_and_stops_on_stopped_or_error():
    got, stops = [], []
    status = {"a": "complete", "idle": None, "end": "stopped", "after": "complete"}
    cb = _frame_handler(lambda b, w: got.append((b, w)), lambda: stops.append(1), status.__getitem__, lambda b: 5.0)
    for b in ("a", "idle", "end", "after"):
        cb(b)
    assert got == [("a", 5.0)] and stops == [1]

    def boom(b):
        raise ValueError("bad attachment")

    stops.clear()
    cb = _frame_handler(lambda b, w: got.append(b), lambda: stops.append(1), boom, lambda b: 5.0)
    cb("x")  # must not raise on the SCK queue
    cb("y")
    assert stops == [1]


def test_rollover_does_not_block_capture(tmp_path):
    stream = FakeStream([T + i / 10 for i in range(25)])
    r, pieces, _, _ = _rec(tmp_path, stream, piece_s=1.0, slow=0.2)
    r.start()
    t = time.monotonic()
    _wait(lambda: stream.done)
    assert time.monotonic() - t < 0.3  # two slow finishes ran off the capture thread
    r.stop()
    assert [ms for ms, _ in pieces] == [0, 1000, 2000]  # still in order


def test_stop_keeps_its_budget_with_slow_finishes(tmp_path):
    stream = FakeStream([T + i / 10 for i in range(15)])
    r, pieces, _, _ = _rec(tmp_path, stream, piece_s=1.0, slow=0.4)
    r.STOP_BUDGET_S = 0.5
    r.start()
    _wait(lambda: stream.done)
    t = time.monotonic()
    r.stop()
    assert time.monotonic() - t < 0.7 and stream.stopped
    _wait(lambda: len(pieces) == 2)  # the late piece still arrives, in order
    assert [ms for ms, _ in pieces] == [0, 1000]
