"""Question classifier (spec 7.2): CPU rules, no model. Precision first: a false trigger costs more than a miss.

Each sentence is judged on its own and the most confident one decides, so "Okay. How quickly could you start?"
triggers and "Can you see my screen? No, sorry… Can you see yours" does not (logistics beats "?")."""
import os
import re
from dataclasses import dataclass
from typing import Literal

FLECK_TRIGGER_MIN_CONFIDENCE = float(os.environ.get("FLECK_TRIGGER_MIN_CONFIDENCE", "0.7"))

# Meeting logistics: questions, but never for the library.
_LOGISTICS = re.compile(
    r"\b(can|could|are) (you|everyone|everybody|we) (able to )?(see|hear)\b|\b(shall|should) we (move on|start|begin)\b"
    r"|\bis (that|this) (okay|ok|clear)\b|\bare you (still )?there\b|\bdoes that make sense\b|\bany questions\b"
    r"|\bsorry,? (what|can you repeat)\b|\bis it (okay|ok|all right|alright) if\b|\bdo you mind if\b"
    r"|\bspeak up\b|\b(un)?mute\b|\b(screen|camera|slide|microphone|audio|recording)\b"
)
_AUX_PRONOUN = r"(can you|could you|would you|do you|does your|have you|is there|are you)"
# "which was/is/means…" opens a relative clause that Whisper sometimes punctuates as a question
_WH = r"(what|how|who|when|where|why|which(?! (is|was|were|means|meant|makes|made|sounds)\b))"
# Without a "?", a wh-word must be followed by an auxiliary: "When we did this…" and "Which is why…" are statements.
_WH_AUX = (r"(what|how( much| many| quickly| fast| long| soon| often)?|who|when|where|why) "
           r"(is|are|was|were|do|does|did|can|could|would|will|should|has|have)\b")
_FILLER = r"((and|so|but|okay|ok|right|then|also|now) )?"
_ASK = re.compile(r"\b(tell me (about|how)|walk me through|i'd like to (know|understand))\b")


@dataclass(frozen=True)
class QuestionVerdict:
    is_question: bool
    confidence: float
    reason: str


def _norm(text: str) -> str:
    text = text.lower().replace("’", "'").replace("…", ". ")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9'?.!, ]", " ", text)).strip()


def _sentence(s: str) -> QuestionVerdict:
    q = s.endswith("?")
    body = s.rstrip("?.!, ")
    if len(re.findall(r"[a-z0-9']+", body)) < 4:
        return QuestionVerdict(False, 0.9, "under 4 words")
    if _LOGISTICS.search(body):
        return QuestionVerdict(False, 0.95, "meeting logistics")
    clauses = [c.strip() for c in body.split(",")]
    if q and any(re.match(_FILLER + rf"({_WH}|{_AUX_PRONOUN})\b", c) for c in clauses):
        return QuestionVerdict(True, 0.95, "interrogative and '?'")
    if re.match(_FILLER + rf"({_AUX_PRONOUN}\b|{_WH_AUX})", clauses[0]):
        return QuestionVerdict(True, 0.85, "interrogative opening")
    if _ASK.search(body):
        return QuestionVerdict(True, 0.9, "explicit request")
    if q:
        return QuestionVerdict(True, 0.6, "'?' alone")
    return QuestionVerdict(False, 0.7, "statement")


def classify(transcript: str, speaker: Literal["client", "you", "overlap"]) -> QuestionVerdict:
    """Text rules only; `speaker` is part of the contract and is applied by `triggers`."""
    sentences = [s.strip() for s in re.findall(r"[^.?!]+[.?!]*", _norm(transcript)) if s.strip(" .?!,")]
    if not sentences:
        return QuestionVerdict(False, 0.9, "empty")
    verdicts = [_sentence(s) for s in sentences]
    questions = [v for v in verdicts if v.is_question]
    return max(questions, key=lambda v: v.confidence) if questions else max(verdicts, key=lambda v: v.confidence)


def triggers(transcript: str, speaker: str, min_confidence: float = FLECK_TRIGGER_MIN_CONFIDENCE) -> bool:
    """The spec 7.2 trigger rule."""
    v = classify(transcript, speaker)
    return speaker == "client" and v.is_question and v.confidence >= min_confidence
