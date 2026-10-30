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
from concurrent.futures import ThreadPoolExecutor, wait
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

# Hard policy: asking the vendor to send a document is a to-do, not a question for the library. Only when the
# document-like object ends the line or is followed by a delivery phrase: "share some numbers on how much time it
# saved" or "share your experience with contract law" ask for information and go to the model (re-review 2).
_REQUEST = re.compile(rules._FILLER + r"((can|could|will) (you|someone|somebody)( please)? (send|email|forward|cc|share)"
                      r"|would you (please )?(send|email|forward)|would you mind (sending|sharing|emailing|forwarding)"
                      r"|(would|will) you be able to (send|share|email|forward)|please (send|share|email|forward))\b(?P<rest>.*)")
_FILL = r"( (me|us|over|across|it|them|that|this|those|these|please|again|the|a|an|your|our|its|their|my" \
        r"|copy|of|soft|full|latest|final|updated|signed|draft|whole|same|one|some|any))*"
_NOUN = (r"(deck|decks|slides?|invite|minutes|link|agenda|recording|notes|documents?|docs?|files?|sow|proposal"
         r"|contracts?|template|quote|rate card|whitepaper|certificates?|reports?|diagram|policy|presentation"
         r"|pdf|case stud(y|ies)|numbers|details|requirements|draft|summary|email|mail|form|brochure"
         r"|checklist|spreadsheet|sheet|screenshot|demo link|credentials|nda|msa|invoice)")
_DELIVERY = (r"( (to|with) (me|us|everyone|the team|all of us|(my|our) (e-?mail|inbox|email id|email address)))?"
             r"( (over|across|again|please|afterwards|later|today|tomorrow|(later|sometime) today"
             r"|(after|before|post) (the|this|our) (call|meeting)|after the session"
             r"|(by|over|via|on|through|in an?) (e-?mail|mail|whatsapp|slack|teams)|in the chat|on the chat"
             r"|by (eod|end of day|tomorrow|monday|tuesday|wednesday|thursday|friday)))*")
# The rules fallback (embedder down or slow) runs stricter than the rules alone: a false trigger is worse than a miss.
# Chosen on fixtures/classifier/train.json only (decision 106): "interrogative and '?'" (0.95) and none of a vocative
# opening ("Arjun, …" asks a colleague), a first-person subject ("do we…", "can I…") or a send/add/invite request.
# Train: recall 296/1259, false triggers 26/1392 (1.9%); at the old 0.7 with no filters 574/1259 and 137/1392 (9.8%).
FALLBACK_MIN_CONFIDENCE = 0.95
_VOCATIVE = re.compile(r"^(\w+ )?[A-Z][a-z]+, ")  # case-sensitive; "Okay, …" goes too: the fallback stays strict
_FALLBACK_VETO = re.compile(r"\b(we|i|we've|we're|our team|us"
                            r"|send|email|e-mail|cc|resend|forward|add|invite|share|put|bring|schedule|book|loop)\b", re.I)
_DOCUMENT = re.compile(rf"^({_FILL}( (?!(on|about|with|where|how|what|why|which|who|for|regarding|from|in|that)\b)\w+){{0,3}}"
                       rf" {_NOUN}|( (me|us|over|across|it|them|that|this|please|again))*){_DELIVERY}$")


@dataclass(frozen=True)
class QuestionVerdict:
    is_question: bool
    confidence: float
    reason: str
    question: str = ""  # the scored text: the whole transcript
    embedding: list[float] | None = field(default=None, repr=False, compare=False)


def shape(s: str) -> str | None:
    """Why a normalised sentence is hard-vetoed, or None: everything else, statements included, goes to the model."""
    # a trailing particle is fine here ("…support single sign on"); the model judges the rest
    if (why := rules.veto(s)) and why != "fragment" or rules.is_fragment(s, prepositions=False):
        return why or "fragment"
    if (m := _REQUEST.match(s)) and _DOCUMENT.match(m["rest"].replace(",", "").rstrip("?.! ")):
        return "request to send or share a document"
    return None  # "Arjun, can you check…" (a colleague) and "Rupak, what would…" (the vendor) are the model's call


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
    if len(wait([cls, search], timeout)[0]) < 2:  # one deadline for the pair, not one per call
        raise TimeoutError(f"embeds over {timeout * 1000:.0f} ms")
    return cls.result()[0], search.result()


def digests_ok() -> bool:
    """Ollama serves the exact embedding weights question_model.json was trained on; a re-pull scores other vectors."""
    m = model()
    served = {x["name"]: x["digest"] for x in httpx.get(f"{_URL}/api/tags", timeout=5).json()["models"]}
    bad = [n for n, d in ((m["classify_model"], m["classify_digest"]), (m["search_model"], m["search_digest"]))
           if served.get(n) != d]
    if bad:
        log.error("embedding model digest differs from question_model.json for %s: the rules decide this session "
                  "(run scripts/ensure_ollama.sh or retrain)", bad)
    return not bad


def rules_only(text: str):
    """An `embed` that always hands the decision to the CPU rules."""
    raise RuntimeError("rules only")


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
        t = transcript.strip()
        ok = (v.is_question and v.confidence >= FALLBACK_MIN_CONFIDENCE
              and not _VOCATIVE.match(t) and not _FALLBACK_VETO.search(t))
        return QuestionVerdict(ok, 1.0 if ok else 0.0, f"rules fallback: {v.reason}", q)
    return QuestionVerdict(True, score(vec), "question shape, model score", q, search)


def triggers(transcript: str, speaker: str, min_confidence: float | None = None, embed: Embed | None = None) -> bool:
    """The spec 7.2 trigger rule."""
    if speaker != "client":
        return False
    v = classify(transcript, speaker, embed)
    return v.is_question and v.confidence >= (trigger_threshold() if min_confidence is None else min_confidence)
