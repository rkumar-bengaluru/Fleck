"""The helper's line to the api (spec 7.6): events over a websocket, audio chunks by HTTP PUT.

Every item is written to the outbox first and removed only when the api has taken it (an `ack` per websocket message,
a 2xx for a chunk), so a drop, a crash or a restart resends what is left, in order, with exponential backoff. The api
ignores events it already has, so an item the api stored but whose ack was lost is harmless."""
import json
import logging
import threading
import time
from collections import deque
from pathlib import Path

import httpx
from websockets.exceptions import ConnectionClosed, WebSocketException
from websockets.sync.client import connect

OUTBOX = Path.home() / "Library/Application Support/Fleck/outbox"
MIN_BACKOFF_S, MAX_BACKOFF_S = 0.1, 10.0
WINDOW = 64  # events in flight before the first ack is awaited
log = logging.getLogger(__name__)


class Transport:
    def __init__(self, api_url: str, token: str, outbox: Path | None = None, on_message=None):
        """`on_message(dict)` gets the api's `start` and `stop` messages, on the transport's thread."""
        self.api_url, self.token, self.on_message = api_url.rstrip("/"), token, on_message
        self.outbox = Path(outbox or OUTBOX)
        self.outbox.mkdir(parents=True, exist_ok=True)
        self._last = time.time_ns()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def send(self, event) -> None:
        """Queue a SegmentEvent / AssistEvent (or its dict); returns at once."""
        self._put("evt", event.model_dump_json() if hasattr(event, "model_dump_json") else json.dumps(event))

    def upload_chunk(self, meeting_id: str, seq: int, path: Path) -> None:
        """Queue a WAV file for `PUT /api/meetings/{id}/audio/{seq}`. The file must stay until it is uploaded."""
        self._put("chunk", json.dumps({"meeting_id": meeting_id, "seq": seq, "path": str(path)}))

    def pending(self) -> int:
        return len(self._items())

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _put(self, kind: str, body: str) -> None:
        with self._lock:
            self._last = max(self._last + 1, time.time_ns())  # names sort in send order, across restarts too
            name = f"{self._last:020d}.{kind}"
        tmp = self.outbox / (name + ".tmp")
        tmp.write_text(body)
        tmp.rename(self.outbox / name)

    def _items(self) -> list[Path]:
        return sorted(p for p in self.outbox.iterdir() if p.suffix in (".evt", ".chunk"))

    def _run(self) -> None:
        delay = MIN_BACKOFF_S
        while not self._stop.is_set():
            try:
                with connect(self.api_url.replace("http", "ws", 1) + "/api/ws/helper",
                             additional_headers={"Authorization": f"Bearer {self.token}"}, open_timeout=5) as ws:
                    delay = MIN_BACKOFF_S
                    self._session(ws)
            except (OSError, WebSocketException, httpx.HTTPError, TimeoutError) as e:
                log.info("api link down (%s); retrying in %.1f s", type(e).__name__, delay)
            except Exception:  # a bug (say in on_message) must not end delivery for the rest of the meeting
                log.exception("transport error; retrying in %.1f s", delay)
            self._stop.wait(delay)
            delay = min(delay * 2, MAX_BACKOFF_S)

    def _session(self, ws) -> None:
        inflight: deque[Path] = deque()
        try:
            self._pump(ws, inflight)
        except ConnectionClosed:
            try:
                while True:  # acks that arrived before the close are still readable: don't resend what the api has
                    self._take(ws.recv(timeout=0), inflight)
            except (ConnectionClosed, TimeoutError):
                pass
            raise

    def _take(self, raw, inflight: deque) -> None:
        msg = json.loads(raw)
        if msg.get("type") == "ack":
            if inflight:
                inflight.popleft().unlink(missing_ok=True)
        elif self.on_message:
            self.on_message(msg)

    def _pump(self, ws, inflight: deque) -> None:
        while not self._stop.is_set():
            nxt = next((p for p in self._items() if p not in inflight), None)
            wait = 0.05
            if nxt is not None and nxt.suffix == ".evt" and len(inflight) < WINDOW:
                ws.send(nxt.read_text())
                inflight.append(nxt)
                wait = 0
            elif nxt is not None and nxt.suffix == ".chunk" and not inflight:  # keep order: events before it are acked
                self._upload(nxt)
                continue
            try:
                raw = ws.recv(timeout=wait)
            except TimeoutError:
                continue
            self._take(raw, inflight)

    def _upload(self, item: Path) -> None:
        job = json.loads(item.read_text())
        path = Path(job["path"])
        if not path.exists():
            log.warning("chunk %s/%s: %s is gone; dropped", job["meeting_id"], job["seq"], path)
        else:
            r = httpx.put(f"{self.api_url}/api/meetings/{job['meeting_id']}/audio/{job['seq']}", content=path.read_bytes(),
                          headers={"Authorization": f"Bearer {self.token}", "Content-Type": "audio/wav"}, timeout=30)
            if r.status_code >= 500 or r.status_code in (401, 429):
                raise httpx.HTTPError(f"upload {r.status_code}")  # try again after the backoff
            if r.status_code >= 400:
                log.warning("api refused chunk %s/%s (%s); dropped", job["meeting_id"], job["seq"], r.status_code)
        item.unlink(missing_ok=True)
