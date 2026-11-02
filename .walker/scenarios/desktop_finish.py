"""Off camera, end of the live-desktop take: the recorded conversation ends and the app stops the meeting itself;
waits until the api has processed it and assembled the call-window video the app recorded; then quits the app and
removes its temporary HOME.
Usage: uv run --all-packages python <walker>/scenarios/desktop_finish.py <state.json>"""
import json
import os
import shutil
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "scripts"))
from stack_replay import sign_in, wait_ready  # noqa: E402

st = json.loads(Path(sys.argv[1]).read_text())
mid, c = st["meeting"], sign_in()
end = time.time() + 900
while c.get(f"/api/meetings/{mid}").json()["meeting"]["status"] == "recording":
    if time.time() > end:
        raise SystemExit("still recording after 15 min")
    time.sleep(5)
m = wait_ready(c, mid)["meeting"]
assert m["status"] == "ready", m
end = time.time() + 600
while not c.get(f"/api/meetings/{mid}").json()["meeting"].get("has_video"):
    if time.time() > end:
        raise SystemExit("video not assembled in 10 min")
    time.sleep(5)
try:
    os.killpg(st["pid"], signal.SIGTERM)
except ProcessLookupError:
    pass
shutil.rmtree(st["home"], ignore_errors=True)
print(f"meeting {mid} ready with video")
