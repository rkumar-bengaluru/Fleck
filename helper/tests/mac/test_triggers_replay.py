"""The four trigger tests (spec 7.2) on full Whisper replays of the fixture WAVs; each prints its own count."""
import json
import sys
from functools import cache
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="mlx-whisper needs Apple silicon")
ROOT = Path(__file__).parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import measure  # noqa: E402

CONV, NEG = ROOT / "fixtures/conversation", ROOT / "fixtures/negative"


@cache
def _transcriber():
    pytest.importorskip("mlx_whisper")
    from fleck_helper.transcribe import StreamTranscriber
    return StreamTranscriber()


@cache
def _counts(wav: Path) -> dict:
    from fleck_helper.classify import live_embed
    # a 2 s embed budget: Whisper keeps the GPU busy in a non-realtime replay, and this test measures the model,
    # not the 150 ms fallback (measure.py reports how often the live budget falls back)
    events = measure.replay(wav, _transcriber(), embed=lambda q: live_embed(q, timeout=2))
    return measure.counts(events, json.loads((wav.parent / "reference.json").read_text()))


def test_negative_set_short_replay():
    n = _counts(NEG / "negative.wav")["negative_set_short"]
    print(f"negative_set_short_replay: {n}")
    assert n == 0


def test_negative_set_medium_replay():
    c = _counts(NEG / "negative.wav")
    print(f"negative_set_medium_replay: {c['negative_set_medium']}; all triggers {c['conversation_false_triggers']}")
    assert c["negative_set_medium"] == 0 and not c["conversation_false_triggers"]


@pytest.mark.parametrize("wav", ["conversation.wav", "conversation-bleed.wav"])
def test_conversation_false_triggers_replay(wav):
    false = _counts(CONV / wav)["conversation_false_triggers"]
    allowed = json.loads((CONV / "reference.json").read_text())["duration_ms"] / 600_000
    print(f"conversation_false_triggers_replay[{wav}]: {len(false)} (allowed {allowed:.2f}) {false}")
    assert len(false) <= allowed


@pytest.mark.parametrize("wav", ["conversation.wav", "conversation-bleed.wav"])
def test_conversation_recall_replay(wav):
    c = _counts(CONV / wav)
    hit, total = c["conversation_recall"]
    print(f"conversation_recall_replay[{wav}]: {hit}/{total} missed {[q for q, e in c['triggered'].items() if not e]}")
    assert total == 8 and hit >= 0.9 * total
