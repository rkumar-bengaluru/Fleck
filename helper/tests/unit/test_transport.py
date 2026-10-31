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
