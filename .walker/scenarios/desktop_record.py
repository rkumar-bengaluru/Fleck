"""Off camera, inside the live-desktop take: Record for today's "Presales: demo prospect", as the web app's Record
does (POST /api/meetings with start_helper), then checks the helper found the staged call window (its log says
"Video: Meet ..."; "audio only" fails the take). Prints MEETING=<id>.
Usage: uv run --all-packages python <walker>/scenarios/desktop_record.py <state.json>"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "scripts"))
from stack_replay import ORIGIN, sign_in  # noqa: E402

st_path = Path(sys.argv[1])
st = json.loads(st_path.read_text())
c = sign_in()
event = next(e for e in c.get("/api/events", params={"range": "today"}).json() if e["title"] == "Presales: demo prospect")
end = time.time() + 90
while True:  # 409 until the app's transport is connected
    r = c.post("/api/meetings", json={"title": "Presales: demo prospect", "event_id": event["id"], "start_helper": True},
               headers=ORIGIN)
    if r.status_code != 409 or time.time() > end:
        break
    time.sleep(2)
r.raise_for_status()
mid = r.json()["id"]
st_path.write_text(json.dumps(st | {"meeting": mid}))
log = Path(st["log"])
end = time.time() + 45
while time.time() < end:
    text = log.read_text(errors="replace")
    if f"meeting {mid}: Video: Meet" in text:
        break
    if f"meeting {mid}: Video: none" in text or "recording audio only" in text:
        raise SystemExit("the helper is recording audio only: it did not find the staged call window")
    time.sleep(1)
else:
    raise SystemExit("the helper never reported a call window")
print(f"MEETING={mid}")
