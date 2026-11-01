"""Spec 5 targets on the full stack (Task 22): conversation.wav replayed through `fleck-helper replay` into the running
stack (`make up`), FLECK_TARGET_RUNS times (default 3), one after another. Every run must trigger 8/8 with the expected
kind and document; decision 112's gates are pooled over all runs; late answers are listed apart.
FLECK_TARGET_BRIEF=1 builds the demo event's brief first and records run 1 into that event's meeting.
Each run's helper log is kept in web/test-results/targets/. ~7 min per run.
Run: FLECK_TARGETS=1 uv run --all-packages pytest helper/tests/mac/test_targets.py -s"""
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
pytestmark = pytest.mark.skipif(sys.platform != "darwin" or not os.environ.get("FLECK_TARGETS"),
                                reason="full-stack replays (~7 min each): set FLECK_TARGETS=1 with the stack up")


def build_demo_brief(c, event_title: str = "Presales: demo prospect") -> int:
    from stack_replay import build_brief
    eid = int(os.environ.get("FLECK_DEMO_EVENT_ID", "1"))
    assert c.get(f"/api/events/{eid}").json()["title"] == event_title
    b = build_brief(c, eid)
    print("demo brief:", [(i["question"], i["kind"]) for i in b["items"]])
    return eid


def test_targets():
    from stack_replay import LOOP_MS, create_meeting, gate_lines, helper_env, log_counts, print_rows, score, sign_in, start_replay, stop, \
        used_scores, wait_ready
    runs = int(os.environ.get("FLECK_TARGET_RUNS", "3"))
    tag = os.environ.get("FLECK_TARGET_TAG", time.strftime("%H%M"))
    c = sign_in()
    env = helper_env(c)
    eid = build_demo_brief(c) if os.environ.get("FLECK_TARGET_BRIEF") else None
    pooled, wrong = [], []
    for i in range(runs):
        print(os.popen("pmset -g therm").read().strip())
        mid = create_meeting(c, f"Task 22 targets {tag} run {i + 1}", eid if i == 0 else None)
        log = ROOT / f"web/test-results/targets/helper-replay-{tag}-run{i + 1}.log"
        p = start_replay(env, mid, log)
        assert p.wait() == 0, log.read_text()[-3000:]
        stop(c, mid)
        m = wait_ready(c, mid)
        rows, extra = score(m)
        print(f"--- run {i + 1} meeting {mid}: {log_counts(log.read_text())}")
        print_rows(rows, extra, used_scores(mid))
        minutes = LOOP_MS // 60_000
        assert len(extra) <= max(1, minutes // 10), [x["question"] for x in extra]  # false triggers: spec 5, 1 per 10 min
        pooled += rows
        wrong += [f"run {i + 1} {r['id']}" for r in rows if not (r["triggered"] and r["ok"])]
    lines, ok = gate_lines(pooled)
    print("\n".join(lines))
    assert not wrong, wrong
    assert ok
