"""Question classifier (spec 7.2 contract, decision 94): a permissive question-shape gate, then a learned score.

The score is a logistic regression over the L2-normalised qwen3-embedding vector of the question text under a
classification instruction (`CLASSIFY_TASK`). The live path fetches it in the same /api/embed request as the
`Embedder.embed_query` vector that search uses, which travels on in the verdict and the Trigger. The model is `question_model.json`, retrained
with scripts/train_classifier.py. If the embedder fails or takes over 150 ms, the CPU rules in question_rules decide
(logged once)."""
import json
import logging
import os
import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Callable, Literal

import numpy as np

from fleck_helper import question_rules as rules

MODEL_PATH = Path(__file__).with_name("question_model.json")
EMBED_TIMEOUT_S = 0.15  # the live budget; over it, the rules decide
CLASSIFY_TASK = ("Decide whether this line, spoken by a client on a sales call, is a substantive question about the vendor, "
                 "the engagement or the solution that the vendor should answer, rather than small talk, call logistics, "
                 "scheduling or a request to send something")
# text -> (classification vector, search vector or None)
Embed = Callable[[str], tuple[list[float], list[float] | None]]
log = logging.getLogger(__name__)

# Shape gate: permissive on purpose, the model decides relevance.
_INDIRECT = re.compile(r"\b((like|love|want|wants|need|needs|interested|hoping|keen|curious|great|good|helpful|useful) to "
                       r"(know|understand|hear|learn)|tell (me|us)|walk (me|us) through|wondering|curious"
                       r"|kindly (explain|tell|share)|(ask|asks|asking) (what|how|whether|if|about|why|when|where|who|which))\b")
_OPENING = re.compile(rules._FILLER + rf"({rules._WH}|{rules._AUX}|whats|hows|whos|wheres|whens|any)\b")
_ENDS_WH = re.compile(r"\b(what|how|when|where|who|which|why)$")  # "and the timeline would be what"
# Hard policy: asking the vendor to send something is a to-do, not a question for the library.
_REQUEST = re.compile(rules._FILLER + r"((can|could|will) (you|someone|somebody)( please)? (send|email|forward|cc|share)"
                      r"|would you (please )?(send|email|forward)|would you mind (sending|sharing|emailing|forwarding)|(would|will) you be able to (send|share|email|forward)"
                      r"|please (send|share|email|forward))\b")
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
    question: str = ""  # the scored text: the transcript from its first question-shaped sentence on
    embedding: list[float] | None = field(default=None, repr=False, compare=False)


def _vocative(s: str) -> bool:
    m = re.match(r"([a-z']+), (.*)", s)
    if not m or m[1] in _NOT_NAMES or m[1] in rules.LEXICON or m[1].rstrip("s") in rules.LEXICON:
        return False
    return bool(_OPENING.match(m[2]) or m[2].startswith("please "))


def shape(s: str) -> str | None:
    """Why a normalised sentence is not a candidate question, or None if it is one."""
    body = s.rstrip("?.!, ")
    # a trailing particle is fine here ("…support single sign on"); the model judges the rest
    if (why := rules.veto(s)) and why != "fragment" or rules.is_fragment(s, prepositions=False):
        return why or "fragment"
    if _REQUEST.match(s):
        return "request to send or share"
    if _vocative(s):
        return "addressed to a colleague"
    if (s.endswith("?") or any(_OPENING.match(c.strip()) for c in body.split(",")) or _ENDS_WH.search(body)
            or _INDIRECT.search(body) or rules._ASK.search(body)):
        return None
    return "not shaped like a question"


def _sentences(transcript: str) -> list[str]:
    return [s for s in re.findall(r"[^.?!…]+[.?!…]*", transcript) if s.strip(" .?!,…")]


def question_text(transcript: str) -> str | None:
    """The transcript from its first question-shaped sentence on, or None when no sentence has question shape."""
    sents = _sentences(transcript)
    first = next((i for i, s in enumerate(sents) if shape(rules._norm(s)) is None), None)
    return None if first is None else " ".join(s.strip() for s in sents[first:])


@cache
def model() -> dict:
    m = json.loads(MODEL_PATH.read_text())
    assert m["classify_instruction"] == CLASSIFY_TASK, "question_model.json was trained with another instruction"
    m["weights"] = np.asarray(m["weights"], np.float64)
    return m


def trigger_threshold() -> float:
    """`FLECK_TRIGGER_MIN_CONFIDENCE`, defaulting to the threshold the training script chose."""
    return float(os.environ.get("FLECK_TRIGGER_MIN_CONFIDENCE") or model()["threshold"])


def score(vector) -> float:
    x = np.asarray(vector, np.float64)
    x = x / (np.linalg.norm(x) or 1.0)
    return float(1 / (1 + np.exp(-(x @ model()["weights"] + model()["bias"]))))


_embedder = None
_fell_back = False


def live_embed(text: str, timeout: float = EMBED_TIMEOUT_S) -> tuple[list[float], list[float]]:
    """Both vectors in one /api/embed request: the classification input and exactly `Embedder.embed_query`'s input."""
    global _embedder
    from fleck_core.retrieval import QUERY_TASK, Embedder
    if _embedder is None or _embedder.timeout != timeout:
        _embedder = Embedder(os.environ.get("FLECK_OLLAMA_URL", "http://localhost:11434"), timeout=timeout)
    cls, search = _embedder.embed([f"Instruct: {CLASSIFY_TASK}\nQuery: {text}", f"Instruct: {QUERY_TASK}\nQuery: {text}"])
    return cls, search


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
