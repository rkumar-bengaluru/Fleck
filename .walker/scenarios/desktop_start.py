"""Off camera, before the live-desktop take films: the real Fleck menu-bar app (`fleck-helper run`), paired with a
throwaway keyring, fed the recorded conversation (FLECK_SOURCE=replay:...), its answer panel allowed on camera
(FLECK_PANEL_SHAREABLE=1). It runs with its own temporary HOME, so it never reads or writes the owner's real
recordings, outbox or log under ~/Library; HF_HOME points at the real model cache so Whisper is not downloaded again.
Writes the app's state for desktop_record.py and desktop_finish.py.
Usage: uv run --all-packages python <walker>/scenarios/desktop_start.py <state.json>"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "scripts"))
from stack_replay import ROOT, WAV, helper_env, sign_in  # noqa: E402

state = Path(sys.argv[1])
state.parent.mkdir(parents=True, exist_ok=True)
home = Path(tempfile.mkdtemp(prefix="fleck-demo-home-"))
env = helper_env(sign_in(), FLECK_SOURCE=f"replay:{WAV}", FLECK_PANEL_SHAREABLE="1",
                 HF_HOME=str(Path.home() / ".cache/huggingface"))
env["HOME"] = str(home)  # after pairing: the keyring is a file named in FLECK_TEST_KEYRING, not under HOME
log = state.with_name("helper-app.log")
p = subprocess.Popen([str(ROOT / ".venv/bin/python"), "-m", "fleck_helper.cli", "run"], cwd=ROOT, env=env,
                     stdout=subprocess.DEVNULL, stderr=log.open("w"), start_new_session=True)
time.sleep(8)  # the menu-bar app connects its transport
if p.poll() is not None:
    raise SystemExit(f"the helper app exited ({p.returncode}); see {log}")
state.write_text(json.dumps({"pid": p.pid, "home": str(home), "log": str(log)}))
print(f"HELPER_UP pid={p.pid}")
