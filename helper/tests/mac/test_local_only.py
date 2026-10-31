"""Local-only operation (Task 22, decision 110): with FLECK_LIVE_FALLBACK=off the helper replays the whole conversation
into the running stack while a socket guard (helper/tests/mac/socket_guard/sitecustomize.py, loaded in the helper
process) refuses and records every connection or name lookup that is not localhost: no Claude call and no warm HEAD
to api.anthropic.com, no model download check. Every question must still trigger; library answers as expected.
Run: FLECK_TARGETS=1 uv run --all-packages pytest helper/tests/mac/test_local_only.py -s   (~7 min, stack up)"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
pytestmark = pytest.mark.skipif(sys.platform != "darwin" or not os.environ.get("FLECK_TARGETS"),
                                reason="a full-stack replay (~7 min): set FLECK_TARGETS=1 with the stack up")


def test_local_only(tmp_path):
    from stack_replay import create_meeting, helper_env, log_counts, print_rows, score, sign_in, start_replay, stop, wait_ready
    guard = tmp_path / "violations.txt"
    c = sign_in()
    env = helper_env(c, (Path(__file__).parent / "socket_guard",), FLECK_LIVE_FALLBACK="off", FLECK_SOCKET_GUARD=str(guard))
    env.pop("ANTHROPIC_API_KEY", None)
    probe = subprocess.run(["uv", "run", "--package", "fleck-helper", "python", "-c",
                            "import httpx; httpx.head('https://api.anthropic.com/', timeout=5)"], cwd=ROOT, env=env, capture_output=True, text=True)
    assert probe.returncode != 0 and "socket guard" in probe.stderr and guard.read_text(), "the guard is not loaded"
    guard.unlink()
    mid = create_meeting(c, f"Task 22 local only {time.strftime('%H%M')}")
    log = ROOT / f"web/test-results/targets/helper-replay-local-only-{time.strftime('%H%M')}.log"
    assert start_replay(env, mid, log).wait() == 0, log.read_text()[-3000:]
    stop(c, mid)
    rows, extra = score(wait_ready(c, mid))
    print(log_counts(log.read_text()))
    print_rows(rows, extra)
    leaks = guard.read_text() if guard.exists() else ""
    print(f"connections refused by the guard: {leaks or 'none'}")
    assert "socket guard" not in log.read_text() and not leaks
    assert all(r["triggered"] for r in rows), [r["id"] for r in rows if not r["triggered"]]
    # without Claude a general question ends as Not in our material (decision 101 covers q_liability)
    assert all(r["ok"] or r["id"] in ("q_general", "q_liability") and r["kind"] == "not_in_material" for r in rows)
