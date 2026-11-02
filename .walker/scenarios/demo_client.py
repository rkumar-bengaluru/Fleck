"""Off camera: the demo client exists in the library (as `make seed-demo` makes it) and today's calendar is synced,
so Today shows "Presales: demo prospect" with Harbourline Legal in its Client dropdown. Safe to re-run.
Usage (in the repo): uv run --all-packages python <walker>/scenarios/demo_client.py"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "scripts"))
from stack_replay import ORIGIN, sign_in  # noqa: E402

c = sign_in()
end = time.time() + 120
while c.get("/api/calendar/status").json()["syncing"]:
    if time.time() > end:
        raise SystemExit("calendar sync still running after 120 s")
    time.sleep(2)
if not any(x["name"] == "Harbourline Legal" for x in c.get("/api/clients").json()):
    c.post("/api/clients", json={"name": "Harbourline Legal"}, headers=ORIGIN).raise_for_status()
assert any(e["title"] == "Presales: demo prospect" for e in c.get("/api/events", params={"range": "today"}).json())
print("demo client ready: Harbourline Legal")
