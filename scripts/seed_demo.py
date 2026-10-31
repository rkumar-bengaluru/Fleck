"""The demo (plan Task 23): run after `make seed-library` on a running stack (`make up`); `make seed-demo` does both.
Signs in rupak through gsim (which syncs the calendar), links the event "Presales: demo prospect" to the client
Harbourline Legal, builds its brief, replays fixtures/conversation/conversation.wav into a meeting of that event through
`fleck-helper replay`, stops it and waits until it is ready. A ready demo meeting already there: nothing to do.
Usage: uv run --all-packages python scripts/seed_demo.py"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from stack_replay import ORIGIN, ROOT, build_brief, create_meeting, helper_env, sign_in, start_replay, stop, wait_ready  # noqa: E402

TITLE, CLIENT = "Presales: demo prospect", "Harbourline Legal"

if __name__ == "__main__":
    c = sign_in()
    if done := [m for m in c.get("/api/meetings").json() if m["title"] == TITLE and m["status"] == "ready"]:
        raise SystemExit(f"seed-demo: meeting {done[0]['id']} ({TITLE}) is already ready")
    end = time.time() + 120
    while c.get("/api/calendar/status").json()["syncing"]:  # sign-in queues the sync when the inline one is slow
        if time.time() > end:
            raise SystemExit("seed-demo: calendar sync still running after 120 s; is the worker up? (make up)")
        time.sleep(2)
    event = next((e for e in c.get("/api/events", params={"range": "today"}).json() if e["title"] == TITLE), None)
    if event is None:
        raise SystemExit(f"seed-demo: no event {TITLE!r} today; is gsim up?")
    client = c.post("/api/clients", json={"name": CLIENT}, headers=ORIGIN).raise_for_status().json()
    c.put(f"/api/events/{event['id']}/client", json={"client_id": client["id"]}, headers=ORIGIN).raise_for_status()
    brief = build_brief(c, event["id"])
    print(f"event {event['id']} linked to {CLIENT}; brief: {len(brief['items'])} questions")
    env = helper_env(c)
    mid = create_meeting(c, TITLE, event["id"])
    log = ROOT / "web/test-results/seed-demo/helper-replay.log"
    print(f"replaying conversation.wav into meeting {mid} (about 7 min; helper log {log.relative_to(ROOT)})")
    if start_replay(env, mid, log).wait() != 0:
        raise SystemExit(f"seed-demo: replay failed:\n{log.read_text()[-3000:]}")
    stop(c, mid)
    m = wait_ready(c, mid)
    if m["meeting"]["status"] != "ready":
        raise SystemExit(f"seed-demo: meeting {mid} {m['meeting']['status']}: {m['meeting']['failure_reason']}")
    print(f"meeting {mid} ready: {len(m['segments'])} transcript lines, {len(m['assists'])} answers, http://localhost:5173/meetings/{mid}")
