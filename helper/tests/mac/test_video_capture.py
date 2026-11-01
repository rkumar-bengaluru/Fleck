"""Real call-window video (spec 2026-11-01, Task 6): a Chrome window titled "Meet - test" (meet_test.html, a counter
that changes every 100 ms) is recorded by the real VideoRecorder for 70 s inside the signed bundle (only it holds
Screen Recording). Two pieces, each decodable, 10 +/- 1 fps, longer side <= 1280, the first start_ms on time.
Run: FLECK_TARGETS=1 uv run --all-packages pytest helper/tests/mac/test_video_capture.py -s   (~80 s)"""
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from bundle import needs_bundle, run_in_bundle

pytestmark = needs_bundle + [pytest.mark.skipif(not os.environ.get("FLECK_TARGETS"), reason="~80 s on screen: set FLECK_TARGETS=1")]
PAGE = Path(__file__).with_name("meet_test.html")


def _probe(path: str) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries",
                        "stream=width,height,nb_read_frames:format=duration", "-of", "json", path],
                       capture_output=True, text=True, check=True)
    j = json.loads(r.stdout)
    s = j["streams"][0]
    return dict(w=s["width"], h=s["height"], frames=int(s["nb_read_frames"]), secs=float(j["format"]["duration"]))


def test_records_chrome_meet_window(tmp_path):
    t0 = time.time()
    profile = tmp_path / "chrome"
    # its own Chrome instance: a fresh profile, sound without a click, a known window size
    subprocess.run(["open", "-na", "Google Chrome", "--args", f"--user-data-dir={profile}", "--no-first-run",
                    "--no-default-browser-check", "--autoplay-policy=no-user-gesture-required",
                    "--window-size=1280,800", "--new-window", PAGE.as_uri()], check=True)
    try:
        time.sleep(5)
        run_in_bundle("--video-test", "--out", str(tmp_path / "v"), "--secs", "70", "--t0", str(t0), timeout=120)
    finally:
        subprocess.run(["pkill", "-f", f"user-data-dir={profile}"])  # no leading "--": pkill would read it as an option
    info = json.loads((tmp_path / "v/video.json").read_text())
    print("video_test:", info)
    assert info["status"][:2] == ["Video: waiting for call window", "Video: Meet - test"], info["status"]
    assert len(info["pieces"]) == 2
    total_mb = total_s = 0.0
    for ms, path in info["pieces"]:
        p = _probe(path)
        mb = Path(path).stat().st_size / 1e6
        total_mb, total_s = total_mb + mb, total_s + p["secs"]
        print(f"piece {ms} ms: {p}, {p['frames'] / p['secs']:.2f} fps, {mb:.2f} MB")
        assert 9 <= p["frames"] / p["secs"] <= 11
        assert max(p["w"], p["h"]) <= 1280
    print(f"MB per minute: {total_mb / total_s * 60:.2f}  (per hour: {total_mb / total_s * 3600:.0f} MB)")
    # the window is already open, so the first frame comes as soon as the stream starts (one window poll at most)
    expected_ms = (info["start_wall"] - t0) * 1000
    assert abs(info["pieces"][0][0] - expected_ms) <= 500, (info["pieces"][0][0], expected_ms)
