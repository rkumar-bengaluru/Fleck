import os
import re
import statistics
import threading
import time

import pytest

from fleck_core import answer
from fleck_core.answer import Delta, Done, OllamaClient, Sentence, prefill, stream_library_answer
from fleck_core.prompts import build_messages
from fleck_core.types import Passage
from fleck_core.validate import NOT_IN_MATERIAL

RATE = Passage(n=1, doc_id="d1", chunk_id="d1-0", title="Rate card FY2026", client=None, kind="firm",
               text="Rate card FY2026 · Time and materials (USD per person per month)\nSenior engineer · Offshore (Bengaluru): 5,200")
FAQ = Passage(n=2, doc_id="d2", chunk_id="d2-0", title="Delivery FAQ", client=None, kind="firm",
              text="Delivery FAQ · Can you work with our in-house team?\nYes. Mixed teams are common.")


class FakeOllama:
    def __init__(self, tokens, first_delay=0.0, delay=0.0, forever=False):
        self.tokens, self.first_delay, self.delay, self.forever = tokens, first_delay, delay, forever
        self.calls, self.closed = [], threading.Event()

    def stream(self, messages, model, options=None, **kw):
        self.calls.append((messages, options))
        try:
            time.sleep(self.first_delay)
            while True:
                for t in self.tokens:
                    yield t
                    time.sleep(self.delay)
                if not self.forever:
                    return
        finally:
            self.closed.set()


def run(tokens, passages=(RATE, FAQ), question="q?", **kw):
    now = time.time()
    return list(stream_library_answer(question, list(passages), [], FakeOllama(tokens, **kw), now, now))


def sents(ev):
    return [(e.index, e.text, e.ok, e.reason) for e in ev if isinstance(e, Sentence)]


def shown(ev):
    return "".join(e.text for e in ev if isinstance(e, Delta))


def test_streams_figureless_sentence_token_by_token():
    ev = run(["Mixed", " teams", " are", " common", " [2]", "."])
    deltas = [e for e in ev if isinstance(e, Delta)]
    assert len(deltas) >= 4 and all(d.index == 0 for d in deltas)
    assert "".join(d.text for d in deltas).strip() == "Mixed teams are common."
    assert sents(ev) == [(0, "Mixed teams are common.", True, None)]
    assert [c.title for c in ev[-2].citations] == ["Delivery FAQ"]
    done = ev[-1]
    assert isinstance(done, Done) and done.check.ok and done.check.kind == "library"
    assert done.first_words_ms is not None and done.full_ms >= done.first_words_ms


def test_holds_and_replaces_sentence_with_wrong_figure():
    ev = run(["A", " senior", " engineer", " costs", " USD", " 9", ",999", " a", " month", " [1]", ".",
              " Senior", " engineer", " offshore", " is", " USD", " 5,200", " a", " month", " [1]", "."])
    shown = "".join(e.text for e in ev if isinstance(e, Delta))
    assert "9" not in shown and "5,200" not in shown  # figures are held until the sentence is checked
    assert sents(ev) == [(0, NOT_IN_MATERIAL, False, "figure_not_in_cited_passage"),
                         (1, "Senior engineer offshore is USD 5,200 a month.", True, None)]
    assert [c.title for c in [e for e in ev if isinstance(e, Sentence)][0].citations] == ["Rate card FY2026"]  # the link
    assert ev[-1].check.ok  # the good sentence carries the answer


def test_none_reply_returns_not_in_material():
    ev = run(["NO", "NE"])
    assert len(ev) == 1 and ev[0].check.kind == "not_in_material" and ev[0].check.reasons == ["none"]
    assert ev[0].first_words_ms is None


def test_n_start_that_is_not_none_still_streams():
    ev = run(["No", " outside", " storage", " is", " used", " [2]", "."])
    assert [e.text for e in ev if isinstance(e, Sentence)] == ["No outside storage is used."]


def test_markers_stripped_from_display():
    ev = run(["Mixed", " teams", " are", " common", ".", " [", "2", "]", " We", " name", " a", " lead", " [2", "]", "."])
    shown = "".join(e.text for e in ev if isinstance(e, Delta))
    assert "[" not in shown and "]" not in shown and not re.search(r"\d", shown)
    assert [e.text for e in ev if isinstance(e, Sentence)] == ["Mixed teams are common.", "We name a lead."]
    assert ev[-1].check.ok and [c.title for c in ev[-1].check.citations] == ["Delivery FAQ"]


def test_answer_timeout_degrades(monkeypatch):
    assert (answer.FIRST_TOKEN_S, answer.DONE_S) == (4.0, 8.0)
    monkeypatch.setattr(answer, "FIRST_TOKEN_S", 0.2)  # first token never arrives in time
    ev = run(["x"], passages=[RATE], first_delay=1.0)
    assert len(ev) == 1 and ev[0].check.kind == "not_in_material" and ev[0].check.reasons == ["timeout"]
    assert [c.title for c in ev[0].check.citations] == ["Rate card FY2026"]  # the closest document
    monkeypatch.setattr(answer, "DONE_S", 0.5)  # tokens flow, but the answer is not done in time
    ev = run(["Mixed", " teams"], passages=[FAQ], delay=0.05, forever=True)
    assert ev[-1].check.reasons == ["timeout"] and any(isinstance(e, Delta) for e in ev)


def test_consumer_stopping_closes_the_stream():
    fake = FakeOllama(["Mixed", " teams"], delay=0.01, forever=True)
    now = time.time()
    gen = stream_library_answer("q?", [FAQ], [], fake, now, now)
    assert isinstance(next(gen), Delta)
    gen.close()  # Task 14 abandons a speculative answer (Superseded)
    assert fake.closed.wait(1.0)


def test_uncited_sentence_inherits_next_citation():
    # the q_ip shape: the claim is uncited, the marker comes only on the next sentence
    ev = run(["Yes", ",", " mixed", " teams", ".", " We", " name", " a", " lead", " [2]", "."])
    s = [e for e in ev if isinstance(e, Sentence)]
    assert sents(ev) == [(0, "Yes, mixed teams.", True, None), (1, "We name a lead.", True, None)]
    assert [c.title for c in s[0].citations] == ["Delivery FAQ"] and ev[-1].check.ok


def test_uncited_figure_sentence_is_removed_not_inherited():
    ev = run(["Teams", " cost", " USD", " 9,999", ".", " Mixed", " teams", " are", " common", " [2]", "."])
    assert sents(ev) == [(0, "", False, "uncited"), (1, "Mixed teams are common.", True, None)]


def test_no_inheritance_from_a_failed_sentence():
    # filler must not turn a failed figure sentence into an ok library answer (re-review 2, R1)
    ev = run(["It", " works", " well", ".", " A", " senior", " engineer", " costs", " USD", " 9,999", " [1]", "."])
    s = sents(ev)
    assert s[0] == (0, "", False, "uncited") and s[1][0] == 1 and s[1][2] is False
    assert not ev[-1].check.ok


def test_two_uncited_then_cited_removes_the_first():
    ev = run(["Yes", ".", " Sure", ".", " Mixed", " teams", " are", " common", " [2]", "."])
    assert sents(ev) == [(0, "", False, "uncited"), (1, "Sure.", True, None), (2, "Mixed teams are common.", True, None)]


def test_uncited_sentence_at_the_end_is_removed():
    ev = run(["Mixed", " teams", " are", " common", " [2]", ".", " Ask", " us", "."])
    assert sents(ev) == [(0, "Mixed teams are common.", True, None), (1, "", False, "uncited")]


def test_stops_after_three_cited_sentences():
    fake = FakeOllama([" Mixed", " teams", " are", " common", " [2]", "."], forever=True)
    now = time.time()
    ev = list(stream_library_answer("q?", [FAQ], [], fake, now, now))
    assert [s[0] for s in sents(ev)] == [0, 1, 2] and isinstance(ev[-1], Done) and ev[-1].check.ok
    assert fake.closed.wait(1.0)


@pytest.mark.parametrize("tokens", [["You", " need", " one", " engineer", " [1]", "."],
                                    ["Teams", " start", " within", " two", " weeks", " [1]", "."],
                                    ["Teams", " start", " within", " a", " week", " [1]", "."],
                                    ["We", " have", " a", " dozen", " people", " [1]", "."],
                                    ["It", " costs", " 1", "2,000", " a", " month", " [1]", "."],
                                    ["Response", " is", " thirty", " minutes", " [1]", "."],
                                    ["A", " discount", " of", " five", " percent", " [1]", "."],
                                    ["It", " is", " $", "5,200", " [1]", "."],
                                    ["Yes", " [1]", ".", " It", " is", " 5", ".", "200", " USD", " [1]", "."]])
def test_number_words_and_split_figures_never_stream(tokens):
    ev = run(tokens, passages=[RATE])
    assert not re.search(r"\d|\b(?:one|two|thirty|five|dozen)\b|\ban?\s*$", shown(ev), re.I), shown(ev)


@pytest.mark.parametrize("tokens", [["None", " of", " the", " passages", " say", "."],
                                    ["The", " passages", " do", " not", " cover", " this", "."],
                                    ["No", " passage", " answers", " it", "."], ["None", "."]])
def test_none_forms_hand_over(tokens):
    ev = run(tokens)
    assert len(ev) == 1 and ev[0].check.reasons == ["none"]


@pytest.mark.parametrize("mark", [" [1, 2]", " [1][2]", " (1)", " 【1】"])
def test_marker_forms(mark):
    ev = run(["Senior", " engineer", " offshore", " is", " USD", " 5,200", " a", " month", mark, "."])
    assert sents(ev) == [(0, "Senior engineer offshore is USD 5,200 a month.", True, None)] and ev[-1].check.ok


def test_client_name_in_uncited_sentence_dropped():
    now = time.time()
    ev = list(stream_library_answer("q?", [FAQ], ["Larkspur Retail Group"], FakeOllama(["Larkspur", " liked", " it", "."]), now, now))
    assert sents(ev) == [(0, "", False, "uncited")] and not ev[-1].check.ok


class Boom:
    def __init__(self, after):
        self.after = after

    def stream(self, messages, model, options=None, **kw):
        yield from self.after
        raise RuntimeError("ollama: boom")


@pytest.mark.parametrize("before", [[], ["Mixed", " teams"]])
def test_ollama_error_degrades(before):
    now = time.time()
    ev = list(stream_library_answer("q?", [FAQ], [], Boom(before), now, now))
    assert ev[-1].check.kind == "not_in_material" and ev[-1].check.reasons == ["ollama_error"]
    assert any(isinstance(e, Delta) for e in ev) == bool(before)


def test_prefill_sends_the_answer_prefix():
    fake = FakeOllama(["x"])
    prefill([RATE, FAQ], "What does a senior", fake)
    now = time.time()
    list(stream_library_answer("What does a senior engineer cost?", [RATE, FAQ], [], fake, now, now))
    (pre, opts), (real, _) = fake.calls
    assert opts["num_predict"] == 1 and pre[0] == real[0]
    assert real[1]["content"].startswith(pre[1]["content"])  # passages first, then the question
    assert real[1]["content"].index(RATE.text) < real[1]["content"].index("What does a senior engineer cost?")


# ---------- live models (Ollama on 11434, the seeded library in Weaviate on 8080) ----------

PREFILL = os.environ.get("FLECK_TEST_NO_PREFILL") is None  # set it once to see the test fail (control)
CLIENTS = ["Larkspur Retail Group", "Calloway & Marsh", "Brightwater Insurance", "Meridian Intelligence"]


def _live(question):
    from fleck_core.retrieval import Embedder, Retriever
    passages = Retriever("http://localhost:8080", Embedder("http://localhost:11434", timeout=30), timeout=10).search(question)
    now = time.time()
    ev = list(stream_library_answer(question, passages, CLIENTS, OllamaClient(), now, now))
    return passages, ev


@pytest.mark.ollama
def test_real_rate_question_cites_rate_card():
    _, ev = _live("What does a senior engineer cost per month, offshore?")
    done = ev[-1]
    assert done.check.ok and [c.title for c in done.check.citations] == ["Rate card FY2026"]
    assert "5,200" in " ".join(e.text for e in ev if isinstance(e, Sentence) and e.ok)


@pytest.mark.ollama
def test_price_question_never_quotes_past_client():
    passages, ev = _live("Ballpark, what would the whole project cost us?")
    assert any(p.client == "Larkspur Retail Group" for p in passages)  # the trap is in the prompt
    done = ev[-1]
    shown = " ".join(e.text for e in ev if isinstance(e, Sentence) and e.ok)
    assert not re.search(r"1\.42|1,420|38,000|Larkspur", shown)
    if done.check.ok:
        assert all(c.kind == "firm" for c in done.check.citations)
    else:
        assert done.check.kind == "not_in_material"


def _ttft(ollama, passages, question):
    t = time.perf_counter()
    for _ in ollama.stream(build_messages(question, passages), answer.MODEL, options=answer.OPTIONS):
        return time.perf_counter() - t


@pytest.mark.ollama
def test_prefill_halves_first_token_time():
    ol = OllamaClient()
    base = [RATE, FAQ]
    big = Passage(n=3, doc_id="x", chunk_id="x-0", title="Capabilities", client=None, kind="firm",
                  text="Capabilities\n" + "We build data platforms. " * 60)
    cold, warm = [], []
    for _ in range(3):
        # a nonce at the start of passage 1, new for the warm run too: only prefill can have cached the prompt
        def fresh():
            return [base[0].model_copy(update={"text": f"{os.urandom(4).hex()} {base[0].text}"}), base[1], big]
        cold.append(_ttft(ol, fresh(), "What does a senior engineer cost per month, offshore?"))
        ps = fresh()
        if PREFILL:
            prefill(ps, "What does a senior", ol)
        warm.append(_ttft(ol, ps, "What does a senior engineer cost per month, offshore?"))
    print(f"ttft cold {cold} prefilled {warm}")
    assert statistics.median(warm) <= 0.5 * statistics.median(cold)


# ---------- re-review 1 probes (N1, N2, N4) ----------

PROBE_RATE = Passage(n=1, doc_id="r", chunk_id="r1", title="Rate card FY2026", client=None, kind="firm",
                     text="Senior engineer offshore 5,200 USD per month. Teams start within 2 weeks. Hypercare 8 weeks.")
PROBE_FAQ = Passage(n=2, doc_id="f", chunk_id="f1", title="Delivery FAQ", client=None, kind="firm", text="Mixed teams are common. We name a lead.")
PROBE_PROP = Passage(n=3, doc_id="p", chunk_id="p1", title="Proposal Larkspur", client="Larkspur Retail Group", kind="client",
                     text="Discovery fixed at USD 38,000. Hypercare: 8 weeks included.")


def words(s):
    return [w if i == 0 else " " + w for i, w in enumerate(s.split())]


def probe(s, passages=(PROBE_RATE, PROBE_FAQ, PROBE_PROP)):
    now = time.time()
    return list(stream_library_answer("How fast can you start?", list(passages), ["Larkspur Retail Group"], FakeOllama(words(s)), now, now))


@pytest.mark.parametrize("answer", ["Larkspur got hypercare for 8 weeks. Hypercare is 8 weeks [1].",
                                    "Larkspur Retail Group loved working with us. Mixed teams are common [2].",
                                    "We have done this for many retailers before. Mixed teams are common [2].",
                                    "Our discovery takes 2 weeks. Teams start within 2 weeks [1]."])
def test_figures_and_claims_never_inherit_a_citation(answer):
    ev = probe(answer)
    assert sents(ev)[0] == (0, "", False, "uncited") and sents(ev)[1][2] is True
    done = ev[-1]
    assert done.check.ok and done.check.reasons == ["uncited"]  # the trace matches the screen


def test_cap_after_an_inherited_sentence_removes_the_current_one():
    ev = probe("Mixed teams are common [2]. We name a lead [2]. It works well. Teams start fast [2].")
    assert [(i, ok, r) for i, _, ok, r in sents(ev)] == [(0, True, None), (1, True, None), (2, True, None), (3, False, "cap")]
    assert {e.index for e in ev if isinstance(e, Delta)} <= {s[0] for s in sents(ev)}  # no orphaned Deltas
    assert ev[-1].check.ok and ev[-1].check.reasons == ["cap"]


def test_parenthesised_count_is_not_a_citation():
    ev = probe("Mixed teams have leads (3). We name a lead [2].", passages=(PROBE_RATE, PROBE_FAQ))
    assert sents(ev) == [(0, "", False, "uncited"), (1, "We name a lead.", True, None)]
    assert "citation_out_of_range" not in ev[-1].check.reasons


def test_bare_number_ending_the_reply_is_its_citation():
    ev = run(["A senior engineer costs 5,200 per month when offshore.", " 1"])
    assert ev[-1].check.ok and sents(ev)[0][1] == "A senior engineer costs 5,200 per month when offshore."
    assert not run(["Mixed teams are common.", " 7"])[-1].check.ok  # not a passage number: still uncited
