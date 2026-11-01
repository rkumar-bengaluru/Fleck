"""seed_demo never leaves the demo meeting `recording` (decision 147): the replay is killed and the meeting stopped
when the replay fails or is interrupted."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[3] / "scripts"))
import seed_demo  # noqa: E402


class Replay:
    def __init__(self, wait):
        self.pid, self._wait, self.running = 4242, wait, True

    def wait(self):
        return self._wait()

    def poll(self):
        return None if self.running else 0


@pytest.fixture
def seen(monkeypatch):
    seen = []
    monkeypatch.setattr(seed_demo, "stop", lambda c, mid: seen.append(("stop", mid)))
    monkeypatch.setattr(seed_demo.os, "killpg", lambda pid, sig: seen.append(("kill", pid)))
    return seen


def test_an_interrupted_replay_is_killed_and_the_meeting_stopped(monkeypatch, seen, tmp_path):
    def ctrl_c():
        raise KeyboardInterrupt
    monkeypatch.setattr(seed_demo, "start_replay", lambda env, mid, log: Replay(ctrl_c))
    with pytest.raises(KeyboardInterrupt):
        seed_demo.replay_and_stop(None, {}, 7, tmp_path / "log")
    assert seen == [("kill", 4242), ("stop", 7)]


def test_a_failed_replay_still_stops_the_meeting(monkeypatch, seen, tmp_path):
    (tmp_path / "log").write_text("boom")
    r = Replay(lambda: 1)
    r.running = False
    monkeypatch.setattr(seed_demo, "start_replay", lambda env, mid, log: r)
    with pytest.raises(SystemExit, match="boom"):
        seed_demo.replay_and_stop(None, {}, 7, tmp_path / "log")
    assert seen == [("stop", 7)]
