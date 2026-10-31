"""fleck-helper pair | replay | status | run."""
import argparse
import os
import socket
import sys
import time
from pathlib import Path

import httpx
import keyring

SERVICE, ACCOUNT = "ai.fleck.helper", "device_token"


def api_url() -> str:
    return os.environ.get("FLECK_API_URL", "http://localhost:8000").rstrip("/")


def load_token() -> str | None:
    return keyring.get_password(SERVICE, ACCOUNT)


def pair(code: str) -> None:
    r = httpx.post(f"{api_url()}/api/devices/pair", json={"code": code, "name": socket.gethostname()}, timeout=10)
    if r.status_code != 200:
        sys.exit(r.json().get("detail", f"pairing failed ({r.status_code})"))
    keyring.set_password(SERVICE, ACCOUNT, r.json()["device_token"])
    print("Paired.")


def status() -> None:
    from fleck_helper.transport import OUTBOX
    token = load_token()
    try:
        up = httpx.get(f"{api_url()}/api/health", timeout=3).status_code in (200, 503)
    except httpx.HTTPError:
        up = False
    print(f"api {api_url()}: {'reachable' if up else 'not reachable'}")
    print(f"paired: {'yes' if token else 'no (run: fleck-helper pair <code>)'}")
    print(f"unsent: {len(list(OUTBOX.glob('*.evt'))) + len(list(OUTBOX.glob('*.chunk'))) if OUTBOX.exists() else 0}")


def replay(wav: Path, meeting: str, fallback: str) -> None:
    """A WAV through the live path (pipeline, assistant) to the api, as if it were a call."""
    from fleck_core.answer import OllamaClient
    from fleck_core.events import SegmentEvent
    from fleck_core.retrieval import Embedder, Retriever
    from fleck_helper.assist import Assistant
    from fleck_helper.audio import ReplaySource
    from fleck_helper.pipeline import Candidate, Pipeline, Trigger
    from fleck_helper.transport import Transport

    token = load_token() or sys.exit("Not paired: run fleck-helper pair <code>")
    m = httpx.get(f"{api_url()}/api/meetings/{meeting}", headers={"Authorization": f"Bearer {token}"}, timeout=10).raise_for_status().json()["meeting"]
    # the meeting's client is redacted like a library client; attendees on either side are "someone" (decision 109a)
    names = m["library_clients"] + ([m["client_name"]] if m["client_name"] else [])
    assistant = Assistant(Retriever("http://localhost:8080", Embedder("http://localhost:11434")), OllamaClient(), fallback, names, [],
                          people=m["people"])
    transport = Transport(api_url(), token)
    threads = []
    for e in Pipeline(meeting).run(ReplaySource(wav, realtime=True)):
        if isinstance(e, SegmentEvent):
            transport.send(e)
        elif isinstance(e, Candidate):
            assistant.prepare(e.text, e.embedding)
        elif isinstance(e, Trigger):
            threads.append(assistant.start(e, meeting, transport.send))
    for th in threads:
        th.join(timeout=30)
    while transport.pending():  # let the outbox drain before exiting
        time.sleep(0.1)
    transport.close()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="fleck-helper")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pair").add_argument("code")
    r = sub.add_parser("replay")
    r.add_argument("wav", type=Path)
    r.add_argument("--meeting", required=True)
    r.add_argument("--fallback", choices=("claude", "off"), default="claude")
    sub.add_parser("status")
    sub.add_parser("run")
    a = ap.parse_args(argv)
    if a.cmd == "pair":
        pair(a.code)
    elif a.cmd == "status":
        status()
    elif a.cmd == "replay":
        replay(a.wav, a.meeting, a.fallback)
    else:
        sys.exit("The menu-bar app arrives with Task 18.")


if __name__ == "__main__":
    main()
