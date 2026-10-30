"""The four trigger tests (spec 7.2) at classifier level, on the reference transcripts, plus the hard policies.
The learned score uses the committed CI embedding cache, so no Ollama is needed (decision 94)."""
import json
import logging
import time
from pathlib import Path

import numpy as np
import pytest

from fleck_helper import classify as clf
from fleck_helper.classify import classify, question_text, triggers
from fleck_helper.question_rules import rule_classify

FIX = Path(__file__).parents[3] / "fixtures"
CONV = json.loads((FIX / "conversation/reference.json").read_text())
NEG = json.loads((FIX / "negative/reference.json").read_text())
TRAIN = json.loads((FIX / "classifier/train.json").read_text())


def _neg_triggers(categories, embed):
    # every turn as if the client said it: the text alone must not trigger
    return [t["text"] for t in NEG["turns"] if t["category"] in categories and triggers(t["text"], "client", embed=embed)]


def test_negative_set_short(cached_embed):
    hits = _neg_triggers({"short"}, cached_embed)
    print(f"negative_set_short: {len(hits)} triggers {hits}")
    assert not hits


def test_negative_set_medium(cached_embed):
    hits = _neg_triggers({"medium", "logistics"}, cached_embed)
    print(f"negative_set_medium: {len(hits)} triggers {hits}")
    assert not hits


def test_conversation_false_triggers(cached_embed):
    hits = [t["text"] for t in CONV["turns"] if not t["question_id"] and triggers(t["text"], t["speaker"], embed=cached_embed)]
    allowed = CONV["duration_ms"] / 600_000
    print(f"conversation_false_triggers: {len(hits)} (allowed {allowed:.2f}) {hits}")
    assert len(hits) <= allowed


def test_conversation_recall(cached_embed):
    asked = [t for t in CONV["turns"] if t["question_id"]]
    hit = [t["question_id"] for t in asked if triggers(t["text"], t["speaker"], embed=cached_embed)]
    print(f"conversation_recall: {len(hit)}/{len(asked)} {hit}")
    assert len(asked) == 8 and len(hit) >= 0.9 * len(asked)


@pytest.mark.parametrize("text", [
    "Can you see my screen? No, sorry… Can you see yours",  # logistics beats "?"
    "So the IP would belong to us, right?",  # tag questions confirm, they don't ask (decision 93)
    "And you'd sign an NDA before we share data, correct?",
    "Arjun, can you check with the infra team?",  # the client talking to a colleague
    "Meera, what did the vendor team say about the integration?",
    "Can you send the pricing deck after the call?",  # send/share requests are to-dos
    "Could you please share the deck?",
    "Please send the security whitepaper to my email.",
    "How quickly could you",  # cut off mid-question
])
def test_hard_policy_never_reaches_the_model(text):
    def no_model(q):
        raise AssertionError(f"embedded {q!r}")
    v = classify(text, "client", no_model)
    assert not v.is_question and v.confidence == 0.0, v


def test_honorifics_and_topics_are_not_vocatives(cached_embed):
    assert question_text("Sir, what is the expected timeline for phase one?") is not None
    assert question_text("Ballpark, what would the whole project cost us?") is not None


def test_committed_model_fits_train(embedding_cache):
    texts = TRAIN["trigger"] + TRAIN["no_trigger"]
    y = np.array([1] * len(TRAIN["trigger"]) + [0] * len(TRAIN["no_trigger"]))
    t = clf.trigger_threshold()
    pred = np.array([(q := question_text(x)) is not None and clf.score(embedding_cache[q].astype(np.float64)) >= t
                     for x in texts])
    acc = (pred == (y == 1)).mean()
    print(f"train accuracy {acc:.3f}, recall {pred[y == 1].mean():.3f}, false positives {pred[y == 0].mean():.3f}")
    assert acc >= 0.95


def test_only_client_triggers(cached_embed):
    q = "What does a senior engineer cost per month?"
    assert triggers(q, "client", embed=cached_embed)
    assert not triggers(q, "you", embed=cached_embed) and not triggers(q, "overlap", embed=cached_embed)


def test_search_vector_and_question_come_back(cached_embed):
    search = [0.25] * 1024  # stands in for the embed_query vector fetched in the same request
    v = classify("Okay, that is about what we expected. How quickly could you have a team working on this?", "client",
                 lambda q: (cached_embed(q)[0], search))
    assert v.question == "How quickly could you have a team working on this?"
    assert v.embedding == search and v.confidence >= clf.trigger_threshold()


def test_rules_decide_when_the_embedder_fails(caplog, monkeypatch):
    monkeypatch.setattr(clf, "_fell_back", False)

    def down(q):
        raise TimeoutError("over 150 ms")
    with caplog.at_level(logging.WARNING):
        assert triggers("What does a senior engineer cost per month?", "client", embed=down)
        assert not triggers("Does Thursday work for the follow up?", "client", embed=down)
        v = classify("Who owns the code you write for us?", "client", down)
    assert v.reason.startswith("rules fallback") and v.embedding is None
    assert sum("fell back to rules" in r.message for r in caplog.records) == 1  # logged once


def test_rules_still_precision_first():
    assert rule_classify("What does a senior engineer cost per month?").confidence >= 0.85
    assert rule_classify("How was your weekend?").confidence < 0.7 or not rule_classify("How was your weekend?").is_question


def test_shape_gate_is_fast():
    t = time.perf_counter()
    for turn in NEG["turns"]:
        question_text(turn["text"])
    assert (time.perf_counter() - t) / len(NEG["turns"]) < 0.005


@pytest.mark.ollama
def test_live_search_vector_is_embed_query():
    from fleck_core.retrieval import Embedder
    q = "Who owns the code you write for us?"
    search = np.asarray(clf.live_embed(q, timeout=30)[1])
    ref = np.asarray(Embedder("http://localhost:11434", timeout=30).embed_query(q))
    assert search @ ref / np.linalg.norm(search) / np.linalg.norm(ref) > 0.999


@pytest.mark.ollama
def test_cache_matches_live_embedder(embedding_cache):
    keys = sorted(embedding_cache)[:: max(1, len(embedding_cache) // 20)]
    for k in keys:
        live = np.asarray(clf.live_embed(k, timeout=30)[0])
        cached = embedding_cache[k].astype(np.float64)
        cos = live @ cached / np.linalg.norm(live) / np.linalg.norm(cached)
        assert cos > 0.999, (k, cos)
