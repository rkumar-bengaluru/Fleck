import json
import wave
from pathlib import Path

import pytest

FIX = Path(__file__).parents[2] / "fixtures"
EXPECT = {"library", "general", "not_in_material", "reference"}


def load(name):
    return json.loads((FIX / name / "reference.json").read_text())


@pytest.mark.parametrize("name,wavs", [("conversation", ["conversation.wav", "conversation-bleed.wav"]), ("negative", ["negative.wav"])])
def test_reference_schema(name, wavs):
    ref = load(name)
    assert set(ref) == {"duration_ms", "turns", "questions"}
    for t in ref["turns"]:
        assert {"speaker", "start_ms", "end_ms", "text", "question_id"} <= set(t)
        assert t["speaker"] in ("you", "client") and 0 <= t["start_ms"] < t["end_ms"] <= ref["duration_ms"]
        assert isinstance(t["text"], str) and (t["question_id"] is None or isinstance(t["question_id"], str))
    for q in ref["questions"]:
        assert set(q) == {"id", "text", "end_ms", "expect", "doc", "used"}
        assert q["expect"] in EXPECT and isinstance(q["used"], bool)
        assert (q["doc"] is None) == (q["expect"] in ("general", "not_in_material"))
    for w in wavs:
        with wave.open(str(FIX / name / w)) as f:
            assert (f.getnchannels(), f.getframerate()) == (2, 16000)
            assert abs(f.getnframes() * 1000 / 16000 - ref["duration_ms"]) < 2


def test_question_ends_on_client_turn_end():
    ref = load("conversation")
    ends = {t["question_id"]: t for t in ref["turns"] if t["question_id"]}
    assert set(ends) == {q["id"] for q in ref["questions"]}
    for q in ref["questions"]:
        t = ends[q["id"]]
        assert t["speaker"] == "client" and t["end_ms"] == q["end_ms"] and t["text"] == q["text"]
    assert 360_000 <= ref["duration_ms"] <= 480_000


def test_negative_set_has_no_questions_and_right_mix():
    ref = load("negative")
    assert ref["questions"] == [] and 600_000 <= ref["duration_ms"] <= 900_000
    from collections import Counter
    assert Counter(t["category"] for t in ref["turns"]) == {"short": 120, "medium": 80, "logistics": 30}
    gaps = [b["start_ms"] - a["end_ms"] for a, b in zip(ref["turns"], ref["turns"][1:])]
    assert min(gaps) >= 1500
