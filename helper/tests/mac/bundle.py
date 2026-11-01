"""Runs the signed bundle's test modes: only the bundle holds the Screen Recording permission (a terminal doesn't)."""
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[3]
APP = ROOT / "dist/Fleck Helper.app"
needs_bundle = [pytest.mark.skipif(sys.platform != "darwin", reason="macOS only"),
                pytest.mark.skipif(not APP.exists(), reason="run `make helper-app` first")]


def run_in_bundle(*args: str, timeout: float) -> None:
    subprocess.run(["open", "-W", "-n", str(APP), "--args", *args], check=True, timeout=timeout)
