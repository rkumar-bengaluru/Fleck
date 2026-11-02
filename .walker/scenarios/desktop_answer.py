"""Off camera check, inside the live-desktop take: waits until the meeting holds its Nth answer, finished, so the
panel on screen is showing it, and prints it (question, kind, answer, source) for the narration to cite.
Usage: uv run --all-packages python <walker>/scenarios/desktop_answer.py <state.json> <n>"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "scripts"))
from stack_replay import sign_in  # noqa: E402

mid = json.loads(Path(sys.argv[1]).read_text())["meeting"]
n = int(sys.argv[2])
c = sign_in()
end = time.time() + 420
last, steady = None, 0
while time.time() < end:
    assists = c.get(f"/api/meetings/{mid}").json()["assists"]  # in the order they were asked
    if len(assists) >= n:
        a = assists[n - 1]
        key = (a.get("kind"), a.get("answer"))
        steady = steady + 1 if key == last and (a.get("answer") or a.get("kind") == "not_in_material") else 0
        last = key
        if steady >= 2:  # unchanged for two polls: the answer has finished streaming
            source = "; ".join(x.get("title") or "" for x in a.get("citations") or [])
            print(f"ANSWER n={n} kind={a['kind']} source={source or '-'}")
            print(f"Q: {a.get('question') or ''}")
            print(f"A: {' '.join((a.get('answer') or '').split())}")
            raise SystemExit(0)
    time.sleep(1)
raise SystemExit(f"no finished answer {n} in 7 minutes")
