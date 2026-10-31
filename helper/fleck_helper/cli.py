"""fleck-helper pair | replay | status | set-claude-key | run."""
import argparse
import logging
import os
import socket
import sys
import tempfile
import time
from pathlib import Path

import httpx
import keyring

log = logging.getLogger("fleck.cli")

SERVICE, ACCOUNT = "ai.fleck.helper", "device_token"
CLAUDE_ACCOUNT = "anthropic_api_key"  # the live fallback's key, for an app started from Finder with no .env (decision 123)


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


def _dotenv_key(path: Path) -> str:
    """ANTHROPIC_API_KEY as dotenv reads it: the last assignment wins, `export ` is allowed, and an unquoted value
    ends at a ` #` comment."""
    key = ""
    for line in path.read_text().splitlines():
        k, eq, v = line.strip().removeprefix("export ").partition("=")
        if eq and k.strip() == "ANTHROPIC_API_KEY":
            v = v.strip()
            if v[:1] in ("'", '"') and v[0] in v[1:]:
                key = v[1:v.index(v[0], 1)]
            else:
                key = v.split(" #", 1)[0].strip()
    return key


def set_claude_key(env_file: Path | None) -> None:
    """Stores the Claude key in the login keychain, from a .env file or typed in; never echoes it."""
    import getpass
    key = _dotenv_key(env_file) if env_file else getpass.getpass("Claude API key: ").strip()
    if not key:
        sys.exit(f"No ANTHROPIC_API_KEY in {env_file}" if env_file else "No key given.")
    keyring.set_password(SERVICE, CLAUDE_ACCOUNT, key)
    print("Saved the Claude key to your login keychain.")


def load_claude_key() -> bool:
    """Puts the keychain's Claude key in the environment unless one is set there. True if a key is now set."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = keyring.get_password(SERVICE, CLAUDE_ACCOUNT) or ""
    return bool(os.environ["ANTHROPIC_API_KEY"])


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


def load_brief(get, meeting_id: str) -> list:
    """The meeting's brief items with their question vectors (`GET /api/meetings/{id}/brief`); `get(path)` returns the
    JSON. A missing or broken brief means no brief, never a failed recording."""
    from fleck_core.types import BriefItem
    try:
        return [BriefItem(**i) for i in get(f"/api/meetings/{meeting_id}/brief")["items"]]
    except Exception as e:
        log.warning("no brief for meeting %s: %r", meeting_id, e)
        return []


def make_assistant(m: dict, fallback: str, brief_items: list = ()):
    """The live Assistant for a meeting (the api's `GET /api/meetings/{id}` "meeting")."""
    from fleck_core.answer import OllamaClient
    from fleck_core.retrieval import Embedder, Retriever
    from fleck_helper.assist import Assistant
    # the meeting's client is redacted like a library client; attendees on either side are "someone" (decision 109a)
    names = m["library_clients"] + ([m["client_name"]] if m["client_name"] else [])
    return Assistant(Retriever("http://localhost:8080", Embedder("http://localhost:11434")), OllamaClient(), fallback, names, list(brief_items),
                     people=m["people"])


class UploadingSource:
    """Passes a source's frames through and uploads them as 10 s chunks, as a live recording does."""

    def __init__(self, source, writer, upload):
        self.source, self.writer, self.upload = source, writer, upload

    def frames(self):
        from fleck_helper.audio import Frame
        try:
            for item in self.source.frames():
                if isinstance(item, Frame) and (done := self.writer.push(item)):
                    self.upload(*done)
                yield item
        finally:
            if done := self.writer.close():
                self.upload(*done)


def replay(wav: Path, meeting: str, fallback: str) -> None:
    """A WAV through the live path (pipeline, assistant) to the api, as if it were a call."""
    from fleck_core.events import SegmentEvent
    from fleck_helper.audio import AudioChunkWriter, ReplaySource
    from fleck_helper.pipeline import Candidate, Pipeline, Trigger
    from fleck_helper.transport import Transport

    token = load_token() or sys.exit("Not paired: run fleck-helper pair <code>")
    m = httpx.get(f"{api_url()}/api/meetings/{meeting}", headers={"Authorization": f"Bearer {token}"}, timeout=10).raise_for_status().json()["meeting"]
    load_claude_key()  # the keychain's key, as the app does (never logged)
    brief = load_brief(lambda path: httpx.get(f"{api_url()}{path}", headers={"Authorization": f"Bearer {token}"}, timeout=10).raise_for_status().json(), meeting)
    assistant = make_assistant(m, fallback, brief)
    transport = Transport(api_url(), token)
    threads = []
    chunks = tempfile.TemporaryDirectory()  # the transport deletes each chunk once the api has it
    source = UploadingSource(ReplaySource(wav, realtime=True), AudioChunkWriter(Path(chunks.name)),
                             lambda seq, path: transport.upload_chunk(meeting, seq, path))
    for e in Pipeline(meeting).run(source):
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
    chunks.cleanup()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="fleck-helper")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pair").add_argument("code")
    r = sub.add_parser("replay")
    r.add_argument("wav", type=Path)
    r.add_argument("--meeting", required=True)
    r.add_argument("--fallback", choices=("claude", "off"), default="claude")
    sub.add_parser("status")
    sub.add_parser("set-claude-key").add_argument("--from-env", type=Path, help="read ANTHROPIC_API_KEY from this .env file")
    sub.add_parser("run")
    a = ap.parse_args(argv)
    if a.cmd == "pair":
        pair(a.code)
    elif a.cmd == "status":
        status()
    elif a.cmd == "replay":
        replay(a.wav, a.meeting, a.fallback)
    elif a.cmd == "set-claude-key":
        set_claude_key(a.from_env)
    else:
        from fleck_helper.menubar import main as run
        run()


if __name__ == "__main__":
    main()
