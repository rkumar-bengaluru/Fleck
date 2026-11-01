import json
import threading
import time

from websockets.sync.server import serve

from fleck_helper.transport import Transport


class FakeApi:
    """A websocket server that acks every message and drops the connection after `drop_after` of them."""
    def __init__(self, drop_after=3):
        self.got, self.drop_after = [], drop_after
        self.server = serve(self._handle, "127.0.0.1", 0)
        self.url = f"http://127.0.0.1:{self.server.socket.getsockname()[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def _handle(self, ws):
        for n, raw in enumerate(ws, 1):
            self.got.append(json.loads(raw))
            ws.send('{"type": "ack"}')
            if n == self.drop_after:
                return  # closes the connection

    def close(self):
        self.server.shutdown()


def test_transport_resends_after_disconnect(tmp_path):
    api = FakeApi()
    t = Transport(api.url, "tok", tmp_path)
    for i in range(10):
        t.send({"type": "segment", "meeting_id": "1", "segment_id": f"c-{i}"})
    deadline = time.time() + 10
    while t.pending() and time.time() < deadline:
        time.sleep(0.05)
    t.close()
    api.close()
    assert [e["segment_id"] for e in api.got] == [f"c-{i}" for i in range(10)]
    assert t.pending() == 0


def test_unsent_items_survive_a_restart(tmp_path):
    t = Transport("http://127.0.0.1:1", "tok", tmp_path)  # nothing listens
    t.send({"type": "segment", "segment_id": "c-0"})
    t.close()
    api = FakeApi(drop_after=99)
    t2 = Transport(api.url, "tok", tmp_path)
    deadline = time.time() + 5
    while t2.pending() and time.time() < deadline:
        time.sleep(0.05)
    t2.close()
    api.close()
    assert [e["segment_id"] for e in api.got] == ["c-0"]


def test_a_failing_on_message_does_not_end_delivery(tmp_path):
    class Api(FakeApi):
        first = True

        def _handle(self, ws):
            if self.first:  # only the first connection greets with a message
                self.first = False
                ws.send('{"type": "stop"}')
            super()._handle(ws)

    api = Api(drop_after=99)
    calls = []

    def boom(msg):
        calls.append(msg)
        raise RuntimeError("bug in the handler")

    t = Transport(api.url, "tok", tmp_path, on_message=boom)
    deadline = time.time() + 5
    while not calls and time.time() < deadline:
        time.sleep(0.05)
    t.send({"type": "segment", "segment_id": "c-0"})  # after the failure, over the reconnected link
    while t.pending() and time.time() < deadline + 5:
        time.sleep(0.05)
    t.close()
    api.close()
    assert t._thread.is_alive() is False and [e["segment_id"] for e in api.got] == ["c-0"]


def test_a_missing_chunk_is_logged_and_dropped(tmp_path, caplog):
    t = Transport("http://127.0.0.1:1", "tok", tmp_path / "box")
    t.close()
    t.upload_chunk("7", 0, tmp_path / "gone.wav")
    t._upload(t._items()[0])
    assert t.pending() == 0 and "gone.wav" in caplog.text


def test_an_uploaded_chunk_is_deleted_locally(tmp_path, monkeypatch):
    import httpx

    from fleck_helper import transport
    codes = iter([503, 204])
    monkeypatch.setattr(transport.httpx, "put", lambda *a, **k: httpx.Response(next(codes)))
    t = Transport("http://127.0.0.1:1", "tok", tmp_path / "box")
    t.close()
    wav = tmp_path / "chunk-00000.wav"
    wav.write_bytes(b"RIFF")
    t.upload_chunk("7", 0, wav)
    try:
        t._upload(t._items()[0])  # a 503: kept for the retry
    except httpx.HTTPError:
        pass
    assert wav.exists() and t.pending() == 1
    t._upload(t._items()[0])
    assert not wav.exists() and t.pending() == 0  # the api has it: about 230 MB an hour no longer piles up


def test_video_upload_does_not_block_events(tmp_path, monkeypatch):
    import httpx

    from fleck_helper import transport

    def slow_put(url, **k):
        time.sleep(2)
        return httpx.Response(204)
    monkeypatch.setattr(transport.httpx, "put", slow_put)
    api = FakeApi(drop_after=99)
    t = Transport(api.url, "tok", tmp_path / "box")
    mp4 = tmp_path / "piece.mp4"
    mp4.write_bytes(b"mp4")
    t.upload_video("7", 0, mp4)
    time.sleep(0.1)  # the video PUT is under way
    t.send({"type": "segment", "segment_id": "c-0"})
    deadline = time.time() + 0.5
    while not api.got and time.time() < deadline:
        time.sleep(0.01)
    got = list(api.got)
    t.close()
    api.close()
    assert [e["segment_id"] for e in got] == ["c-0"]


def test_video_upload_retries_then_deletes(tmp_path, monkeypatch):
    import httpx

    from fleck_helper import transport
    codes, calls = iter([503, 204]), []

    def put(url, **k):
        calls.append((url, k["headers"]["Content-Type"], k["timeout"]))
        return httpx.Response(next(codes))
    monkeypatch.setattr(transport.httpx, "put", put)
    t = Transport("http://127.0.0.1:1", "tok", tmp_path / "box")
    mp4 = tmp_path / "piece-000061000.mp4"
    mp4.write_bytes(b"mp4")
    t.upload_video("7", 61000, mp4)
    deadline = time.time() + 3
    while (mp4.exists() or list((tmp_path / "box").glob("*.vchunk"))) and time.time() < deadline:
        time.sleep(0.02)
    t.close()
    assert calls == [("http://127.0.0.1:1/api/meetings/7/video/61000", "video/mp4", 120)] * 2
    assert not mp4.exists() and not list((tmp_path / "box").glob("*.vchunk"))


def test_non_ascii_text_survives_an_ascii_locale(tmp_path):
    # the app bundle ran with an ASCII locale encoding: a Whisper line with "ú" ended meeting 11's recording
    import subprocess
    import sys
    code = ("import sys; from pathlib import Path; from fleck_helper.transport import Transport\n"
            "t = Transport('http://127.0.0.1:1', 'tok', Path(sys.argv[1]))\n"
            "class E:\n    def model_dump_json(self): return '{\"text\": \"Qu\u00e9 ser\u00e1\"}'\n"
            "t.send(E()); t.close()\n"
            "print(Path(t._items()[0]).read_text(encoding='utf-8'))")
    r = subprocess.run([sys.executable, "-c", code, str(tmp_path)], capture_output=True, text=True, encoding="utf-8",
                       env={"LC_ALL": "en_US.US-ASCII", "PYTHONUTF8": "0", "PYTHONIOENCODING": "utf-8", "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["text"] == "Qué será"
