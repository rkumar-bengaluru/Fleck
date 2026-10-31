"""The answer panel never appears in a screen capture (spec 7.5, NSWindowSharingNone)."""
import json

from bundle import needs_bundle, run_in_bundle

pytestmark = needs_bundle


def test_panel_not_in_share(tmp_path):
    import Quartz
    # a locked screen captures the lock screen, so the control shot finds no panel (control_hits 0)
    assert not (Quartz.CGSessionCopyCurrentDictionary() or {}).get("CGSSessionScreenIsLocked"), "unlock the screen first"
    out = tmp_path / "panel.json"
    run_in_bundle("--panel-test", "--out", str(out), timeout=60)
    r = json.loads(out.read_text())
    print(f"panel_not_in_share: {r}")
    assert "error" not in r, r
    assert r["control_hits"] > 100  # with sharing allowed the stripe and chip colour are found where the panel is
    assert r["hidden_hits"] == 0
