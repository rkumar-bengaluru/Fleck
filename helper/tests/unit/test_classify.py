"""The four trigger tests (spec 7.2) at classifier level, on the reference transcripts; each prints its own count."""
import json
import time
from pathlib import Path

from fleck_helper.classify import classify, triggers

FIX = Path(__file__).parents[3] / "fixtures"
CONV = json.loads((FIX / "conversation/reference.json").read_text())
NEG = json.loads((FIX / "negative/reference.json").read_text())


def _neg_triggers(categories):
    # every turn as if the client said it: the text alone must not trigger
    return [t["text"] for t in NEG["turns"] if t["category"] in categories and triggers(t["text"], "client")]


def test_negative_set_short():
    hits = _neg_triggers({"short"})
    print(f"negative_set_short: {len(hits)} triggers {hits}")
    assert not hits


def test_negative_set_medium():
    hits = _neg_triggers({"medium", "logistics"})
    print(f"negative_set_medium: {len(hits)} triggers {hits}")
    assert not hits


def test_conversation_false_triggers():
    hits = [t["text"] for t in CONV["turns"] if not t["question_id"] and triggers(t["text"], t["speaker"])]
    allowed = CONV["duration_ms"] / 600_000
    print(f"conversation_false_triggers: {len(hits)} (allowed {allowed:.2f}) {hits}")
    assert len(hits) <= allowed


def test_conversation_recall():
    asked = [t for t in CONV["turns"] if t["question_id"]]
    hit = [t["question_id"] for t in asked if triggers(t["text"], t["speaker"])]
    print(f"conversation_recall: {len(hit)}/{len(asked)} {hit}")
    assert len(asked) == 8 and len(hit) >= 0.9 * len(asked)


def test_logistics_beats_question_marks():
    v = classify("Can you see my screen? No, sorry… Can you see yours", "client")
    assert not v.is_question and v.confidence >= 0.9


def test_strong_and_weak_questions():
    assert classify("Ballpark, what would the whole project cost us?", "client").confidence >= 0.85
    assert classify("how quickly could you have a team working on this", "client").confidence >= 0.85  # no "?"
    assert classify("Walk me through your onboarding process.", "client").is_question
    weak = classify("Does Thursday work for the follow up?", "client")
    assert weak.is_question and weak.confidence == 0.6 and not triggers("Does Thursday work for the follow up?", "client")
    assert not classify("It did, which was the plan.", "client").is_question
    assert not triggers("I did absolutely nothing. Which was the plan?", "client")  # seen from Whisper
    assert triggers("Which team would you put on this?", "client")
    assert not classify("Why?", "client").is_question  # under 4 words


def test_only_client_triggers():
    q = "What does a senior engineer cost per month?"
    assert triggers(q, "client") and not triggers(q, "you") and not triggers(q, "overlap")


def test_fast():
    t = time.perf_counter()
    for turn in NEG["turns"]:
        classify(turn["text"], "client")
    assert (time.perf_counter() - t) / len(NEG["turns"]) < 0.05
