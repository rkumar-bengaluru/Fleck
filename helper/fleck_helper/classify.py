"""Question classifier (spec 7.2 contract, decisions 94 and 95): hard vetoes, then a learned score.

The score is a logistic regression over the L2-normalised qwen3-embedding:4b vector of the transcript under a
classification instruction (`CLASSIFY_TASK`). On the live path that call runs in parallel with the 0.6b
`Embedder.embed_query` call whose vector search uses; that vector travels on in the verdict and the Trigger.
The model is `question_model.json`, retrained with scripts/train_classifier.py. If either embed fails or the pair
takes over 300 ms, the CPU rules in question_rules decide (logged once)."""
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Callable, Literal

import httpx
import numpy as np

from fleck_helper import question_rules as rules

MODEL_PATH = Path(__file__).with_name("question_model.json")
CLASSIFY_MODEL = "qwen3-embedding:4b"  # the classifier's own embedder; search stays on 0.6b (decision 95)
CLASSIFY_OPTIONS = {"num_ctx": 512}
EMBED_TIMEOUT_S = 0.30  # the live budget for both embeds; over it, the rules decide
CLASSIFY_TASK = ("Decide whether this line, spoken by a client on a sales call, is a substantive question about the vendor, "
                 "the engagement or the solution that the vendor should answer, rather than small talk, call logistics, "
                 "scheduling or a request to send something")
# text -> (classification vector, search vector or None)
Embed = Callable[[str], tuple[list[float], list[float] | None]]
log = logging.getLogger(__name__)
_URL = os.environ.get("FLECK_OLLAMA_URL", "http://localhost:11434")

_OPENING = re.compile(rules._FILLER + rf"({rules._WH}|{rules._AUX}|whats|hows|whos|wheres|whens|any)\b")
# Hard policy: asking the vendor to send a document is a to-do, not a question for the library. "Can you share how
# you…" or "…share some of the challenges…" are questions, so the veto needs a document-like object or none at all.
_REQUEST = re.compile(rules._FILLER + r"((can|could|will) (you|someone|somebody)( please)? (send|email|forward|cc|share)"
                      r"|would you (please )?(send|email|forward)|would you mind (sending|sharing|emailing|forwarding)"
                      r"|(would|will) you be able to (send|share|email|forward)|please (send|share|email|forward))\b(?P<rest>.*)")
_DOCUMENT = re.compile(r"^(( (me|us|over|across|it|them|that|this|those|these|please|again|the|a|an|your|our|its|their"
                       r"|copy|of|soft|full|latest|final|updated|signed|draft|whole|same|one|some|any))*( \w+){0,3} "
                       r"(deck|decks|slides?|invite|minutes|link|agenda|recording|notes|documents?|docs?|files?|sow|proposal"
                       r"|contracts?|template|quote|rate card|whitepaper|certificates?|reports?|diagram|policy|presentation"
                       r"|pdf|case stud(y|ies)|numbers|details|requirements|draft|summary|email|mail|form|brochure"
                       r"|checklist|spreadsheet|sheet|screenshot|demo link|credentials|nda|msa|invoice)\b"
                       r"|( (me|us|over|across|it|them|that|this|please|again|later|afterwards|after the call))*$)")
# Hard policy: "Arjun, can you check…" is the client talking to a colleague. Words that open a clause but are not names:
_NOT_NAMES = frozenset("""sir madam maam ma'am hey hi hello right sure fine great alright hmm brilliant okay ok so and but
also now then sorry yes yeah no well actually ballpark honestly perfect thanks good cool understood interesting quickly
basically lastly finally firstly secondly again otherwise besides anyway still plus roughly approximately just oh ah
look listen see one""".split())


@dataclass(frozen=True)
class QuestionVerdict:
    is_question: bool
    confidence: float
    reason: str
    question: str = ""  # the scored text: the whole transcript
    embedding: list[float] | None = field(default=None, repr=False, compare=False)


def _vocative(s: str) -> bool:
    m = re.match(r"([a-z']+), (.*)", s)
    if not m or m[1] in _NOT_NAMES or m[1] in rules.LEXICON or m[1].rstrip("s") in rules.LEXICON:
        return False
    return bool(_OPENING.match(m[2]) or m[2].startswith("please "))


def shape(s: str) -> str | None:
    """Why a normalised sentence is hard-vetoed, or None: everything else, statements included, goes to the model."""
    # a trailing particle is fine here ("…support single sign on"); the model judges the rest
    if (why := rules.veto(s)) and why != "fragment" or rules.is_fragment(s, prepositions=False):
        return why or "fragment"
    if (m := _REQUEST.match(s)) and _DOCUMENT.match(m["rest"].rstrip("?.!, ")):
        return "request to send or share a document"
    if _vocative(s):
        return "addressed to a colleague"
    return None


def _sentences(transcript: str) -> list[str]:
    return [s for s in re.findall(r"[^.?!…]+[.?!…]*", transcript) if s.strip(" .?!,…")]


def question_text(transcript: str) -> str | None:
    """The whole transcript, or None when every sentence is hard-vetoed (then nothing is embedded)."""
    sents = _sentences(transcript)
    return " ".join(s.strip() for s in sents) if any(shape(rules._norm(s)) is None for s in sents) else None


@cache
def model() -> dict:
    m = json.loads(MODEL_PATH.read_text())
    assert m["classify_instruction"] == CLASSIFY_TASK and m["classify_model"] == CLASSIFY_MODEL, \
        "question_model.json was trained with another instruction or embedding model"
    m["weights"] = np.asarray(m["weights"], np.float64)
    return m


def trigger_threshold() -> float:
    """`FLECK_TRIGGER_MIN_CONFIDENCE`, defaulting to the threshold the training script chose."""
    return float(os.environ.get("FLECK_TRIGGER_MIN_CONFIDENCE") or model()["threshold"])


def score(vector) -> float:
    x = np.asarray(vector, np.float64)
    x = x / (np.linalg.norm(x) or 1.0)
    return float(1 / (1 + np.exp(-(x @ model()["weights"] + model()["bias"]))))


_fell_back = False


def embed_classify(texts: list[str], timeout: float = EMBED_TIMEOUT_S) -> list[list[float]]:
    """qwen3-embedding:4b vectors of `Instruct: {CLASSIFY_TASK}\\nQuery: {text}`, batched (training uses this too)."""
    out = []
    for i in range(0, len(texts), 32):
        r = httpx.post(f"{_URL}/api/embed", timeout=timeout, json={
            "model": CLASSIFY_MODEL, "options": CLASSIFY_OPTIONS, "keep_alive": -1,
            "input": [f"Instruct: {CLASSIFY_TASK}\nQuery: {t}" for t in texts[i:i + 32]]})
        r.raise_for_status()
        out += r.json()["embeddings"]
    return out


_pool = ThreadPoolExecutor(2, thread_name_prefix="embed")


def live_embed(text: str, timeout: float = EMBED_TIMEOUT_S) -> tuple[list[float], list[float]]:
    """The 4b classification vector and exactly `Embedder.embed_query`'s 0.6b search vector, fetched in parallel."""
    from fleck_core.retrieval import Embedder
    search = _pool.submit(Embedder(_URL, timeout=timeout).embed_query, text)
    cls = _pool.submit(embed_classify, [text], timeout)
    return cls.result(timeout=timeout + 0.05)[0], search.result(timeout=timeout + 0.05)


def classify(transcript: str, speaker: Literal["client", "you", "overlap"], embed: Embed | None = None) -> QuestionVerdict:
    """`speaker` is part of the contract and is applied by `triggers`. `embed` defaults to the live embedder."""
    global _fell_back
    q = question_text(transcript)
    if q is None:
        return QuestionVerdict(False, 0.0, "no question-shaped sentence")
    try:
        vec, search = (embed or live_embed)(q)
    except Exception as e:
        if not _fell_back:
            log.warning("question classifier fell back to rules (embedder: %r); logged once", e)
            _fell_back = True
        v = rules.rule_classify(transcript)
        ok = v.is_question and v.confidence >= rules.MIN_CONFIDENCE
        return QuestionVerdict(ok, 1.0 if ok else 0.0, f"rules fallback: {v.reason}", q)
    return QuestionVerdict(True, score(vec), "question shape, model score", q, search)


def triggers(transcript: str, speaker: str, min_confidence: float | None = None, embed: Embed | None = None) -> bool:
    """The spec 7.2 trigger rule."""
    if speaker != "client":
        return False
    v = classify(transcript, speaker, embed)
    return v.is_question and v.confidence >= (trigger_threshold() if min_confidence is None else min_confidence)
