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
    "Can you send the pricing deck after the call?",  # send/share requests are to-dos
    "Could you please share the deck?",
    "Please send the security whitepaper to my email.",
    "Would you mind sharing the architecture diagram over email?",
    "Can you send it over?",
    "How quickly could you",  # cut off mid-question
])
def test_hard_policy_never_reaches_the_model(text):
    def no_model(q):
        raise AssertionError(f"embedded {q!r}")
    v = classify(text, "client", no_model)
    assert not v.is_question and v.confidence == 0.0, v


@pytest.mark.parametrize("text", [
    "Rupak, what would the onsite rate be?",  # the vendor's own name: names are the model's call (re-review 2)
    "Rupak, can you walk us through the support model?",
    "Bengaluru, is that where the whole team sits?",
    "Could you share some numbers on how much review time it saved?",  # share + information, not a document
    "Could you share details on your security certifications?",
    "Can you share your experience with Indian contract law?",
    "Could you share a case study where the pilot failed?",
])
def test_names_and_information_requests_reach_the_model_and_trigger(text, cached_embed):
    assert question_text(text) == text and triggers(text, "client", embed=cached_embed)


@pytest.mark.parametrize("text", [
    "Arjun, can you check with the infra team?",  # the client talking to a colleague: the model, not a veto
    "Meera, what did the vendor team say about the integration?",
    "Dana, can you tell them about our volumes?",
    "Marcus, did you want to ask about the iManage integration?",
])
def test_own_colleague_lines_reach_the_model_and_do_not_trigger(text, cached_embed):
    assert question_text(text) == text and not triggers(text, "client", embed=cached_embed)


@pytest.mark.parametrize("text", [
    "Can you share how you handle it when the requirement changes?",  # share + how/some-of: a question (decision 95)
    "Can you share some of the challenges you faced in earlier projects?",
    "um so what does it actually cost",  # disfluent, no "?": the model decides
    "we were thinking about the timeline",  # statements reach the model too
])
def test_only_hard_vetoes_stop_a_line(text):
    assert question_text(text) == text


@pytest.mark.ollama
def test_committed_model_fits_train():
    """Needs Ollama: 2560-d vectors for all of train are too big for the CI cache."""
    texts = TRAIN["trigger"] + TRAIN["no_trigger"]
    y = np.array([1] * len(TRAIN["trigger"]) + [0] * len(TRAIN["no_trigger"]))
    qs = [question_text(x) for x in texts]
    keep = [i for i, q in enumerate(qs) if q is not None]
    vecs = clf.embed_classify([qs[i] for i in keep], timeout=600)
    p = np.zeros(len(texts))
    p[keep] = [clf.score(v) for v in vecs]
    pred = p >= clf.trigger_threshold()
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
    assert v.question == "Okay, that is about what we expected. How quickly could you have a team working on this?"
    assert v.embedding == search and v.confidence >= clf.trigger_threshold()


def test_rules_decide_when_the_embedder_fails(caplog, monkeypatch):
    monkeypatch.setattr(clf, "_fell_back", False)

    def down(q):
        raise TimeoutError("over 300 ms")
    with caplog.at_level(logging.WARNING):
        assert triggers("What does a senior engineer cost per month?", "client", embed=down)
        assert not triggers("Does Thursday work for the follow up?", "client", embed=down)
        v = classify("Who owns the code you write for us?", "client", down)
    assert v.reason.startswith("rules fallback") and v.embedding is None
    assert sum("fell back to rules" in r.message for r in caplog.records) == 1  # logged once


@pytest.mark.parametrize("text, fires", [
    ("What does a senior engineer cost per month?", True),
    ("Arjun, what's our current SLA with the other vendor?", False),  # a colleague, by name
    ("Do we have the NDA signed by both sides?", False),  # first person: the client's own business
    ("Can you email me the case studies you mentioned?", False),  # a request
    ("Tell me about your security certifications.", False),  # an indirect question scores 0.85, under 0.95
])
def test_rules_fallback_is_strict(text, fires):
    assert triggers(text, "client", embed=clf.rules_only) is fires


def test_slow_embed_falls_back_within_one_shared_deadline(monkeypatch):
    import fleck_core.retrieval

    class SlowSearch:  # the 0.6b search embed stalls while the 4b classify embed is quick
        def __init__(self, *a, **k):
            pass

        def embed_query(self, text):
            time.sleep(0.6)
            return [0.0]
    monkeypatch.setattr(fleck_core.retrieval, "Embedder", SlowSearch)
    monkeypatch.setattr(clf, "embed_classify", lambda texts, timeout: (time.sleep(0.25), [[1.0]])[1])
    t = time.perf_counter()
    v = classify("Who owns the code you write for us?", "client")
    assert time.perf_counter() - t <= 0.35 and v.reason.startswith("rules fallback"), v


class _Tags:
    def __init__(self, digests):
        self.digests = digests

    def json(self):
        return {"models": [{"name": n, "digest": d} for n, d in self.digests.items()]}


def test_digest_mismatch_hands_the_session_to_the_rules(monkeypatch, caplog):
    from fleck_helper import pipeline
    m = clf.model()
    good = {m["classify_model"]: m["classify_digest"], m["search_model"]: m["search_digest"]}
    monkeypatch.setattr(clf.httpx, "get", lambda *a, **k: _Tags(good))
    assert clf.digests_ok()
    monkeypatch.setattr(clf.httpx, "get", lambda *a, **k: _Tags({**good, m["classify_model"]: "re-pulled"}))
    with caplog.at_level(logging.ERROR):
        assert not clf.digests_ok()
    assert any("digest" in r.message for r in caplog.records)

    def no_warm_up(*a, **k):
        raise AssertionError("warmed up a model with the wrong weights")
    monkeypatch.setattr(pipeline, "live_embed", no_warm_up)
    p = pipeline.Pipeline("m1", transcriber=object(), min_confidence=0.5)

    class Empty:
        def frames(self):
            return iter(())
    assert list(p.run(Empty())) == [] and p.embed is clf.rules_only


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
    search = clf.live_embed(q, timeout=30)[1]
    assert search == Embedder("http://localhost:11434", timeout=30).embed_query(q)  # a separate call: bit-identical


@pytest.mark.ollama
def test_cache_matches_live_embedder(embedding_cache):
    keys = sorted(embedding_cache)[:: max(1, len(embedding_cache) // 20)]
    for k in keys:
        live = np.asarray(clf.live_embed(k, timeout=30)[0])
        cached = embedding_cache[k].astype(np.float64)
        cos = live @ cached / np.linalg.norm(live) / np.linalg.norm(cached)
        assert cos > 0.999, (k, cos)
