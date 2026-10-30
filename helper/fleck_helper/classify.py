"""Question classifier (spec 7.2): CPU rules, no model. Precision first: a false trigger costs more than a miss.

Each sentence is judged on its own and the most confident one decides, so "Okay. How quickly could you start?"
triggers and "Can you see my screen? No, sorry… Can you see yours" does not (the reject list beats "?").
Tag questions ("…, right?") are confirmations, not questions for the library (decision 93)."""
import os
import re
from dataclasses import dataclass
from typing import Literal

FLECK_TRIGGER_MIN_CONFIDENCE = float(os.environ.get("FLECK_TRIGGER_MIN_CONFIDENCE", "0.7"))

_DAY = (r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|today|tonight|next week|this week"
        r"|that time|the same time|\d+ ?(am|pm|o'clock))")
# Whole phrases about running the call, scheduling and small talk; never bare nouns ("screen recording" is a product).
_REJECT = re.compile(r"\b(" + "|".join([
    # call mechanics
    r"(can|could|are|do|is) (you|everyone|everybody|we)( guys| all)? (able to )?(see|hear) (me|us|my|this|it|that|yours"
    r"|everything|the (screen|slide|document|deck|chat|link))",
    r"hear me", r"is (the|my) (recording|voice|audio|screen|mic|microphone|video) (on|coming|clear|audible|visible|okay|ok|working)",
    r"(share|sharing|show|showing) (my|your|the) screen", r"screen ?shar(e|ing)", r"next (slide|topic)",
    r"turn (on |off )?(your|my|the) (camera|video|mic)", r"(on|off) mute", r"(un)?mute (yourself|your|my)",
    r"speak up", r"repeat that", r"say that again", r"hold on", r"give me a (second|sec|minute|moment)", r"go first",
    r"in the chat", r"are you (still )?there", r"is everyone here", r"what did you say", r"sorry,? (what|can you repeat)",
    r"does that make sense", r"any questions", r"(is|would) (that|this|it) (be )?(okay|ok|fine|clear|alright|all right|better)",
    r"is it (okay|ok|all right|alright) if", r"do you mind", r"(do you want|would you like) to",
    r"send (me |us )?(the|an?) (invite|deck|minutes|link|agenda|recording|notes)",
    r"(did|have) you (get|got|receive|received|seen) (my|the|our) (email|mail|message|invite|nda|note)",
    r"received the (nda|invite|email)", r"introduce (my|our)", r"take (this|it) offline", r"do you know what",
    # running the meeting and scheduling
    r"(can|shall|should) (we|i) (start|begin|get started|move on|move to|wrap up|wait)",
    r"take (a (quick |short )?break|\w+ minutes)", r"(a good time|ready) to (stop|start|begin|wrap|talk|break)",
    r"pick (this|that|it) up", rf"(does|would|will|is) {_DAY} (\w+ )?(work|suit)",
    r"are you (free|available) (on|at|next|this|tomorrow|today|later)", r"next steps", r"follow[ -]?up (call|meeting)",
    # small talk
    r"(your|the|this|last|good) weekend", r"had (your )?lunch", r"(calling|joining|dialling|dialing) (in )?from",
    r"(the|your) weather", r"how are you", r"how do you do", r"what time is it",
]) + r")\b")
_AUX = (r"(is|are|was|were|am|do|does|did|will|would|can|could|should|shall|has|have|had|may|might|isn't|aren't|wasn't"
        r"|weren't|don't|doesn't|didn't|won't|wouldn't|can't|couldn't|shouldn't|hasn't|haven't)")
_AUX_PRONOUN = r"(can you|could you|would you|do you|does your|have you|is there|are you)"
# "which was/is/means…" opens a relative clause that Whisper sometimes punctuates as a question
_WH = r"(what|how|who|when|where|why|which(?! (is|was|were|means|meant|makes|made|sounds)\b))"
# Without a "?", a wh-word must be followed by an auxiliary: "When we did this…" and "What we need is…" are statements.
_WH_AUX = (r"(what|how( much \w+| many \w+| much| many| quickly| fast| long| soon| often)?|who|when|where|why) "
           r"(is|are|was|were|do|does|did|can|could|would|will|should|has|have)\b")
_WH_VERB = r"(who \w+s|which (of|\w+ (would|will|can|could|do|does|is|are|should)))\b"  # "Who owns the IP", "Which team would"
_FILLER = r"((and|so|but|okay|ok|right|then|also|now|hello|sorry|please) )?"
_ASK = re.compile(r"\b(tell (me|us) (more )?(about|how)|walk (me|us) through|i'd (like|love) to (know|understand|hear)"
                  r"|i was wondering (whether|if|how|what)|i'm curious (how|what|whether|about)|(can|could) you explain"
                  r"|explain how|(kindly|please) (tell|share|explain)|any idea (how|what|when|whether|if)|(is|would) it (be )?possible to)\b")
_TAG = re.compile(r", (right|correct|yes|no|ok|okay|yeah|isn't it|is it|aren't you|don't you|didn't you)\?$")
# A sentence ending on these is cut off ("How quickly could you"); prepositions only count without a "?" ("working on?")
_PRONOUNS = {"i", "you", "we", "they", "he", "she", "it"}
_CUT = {"can", "could", "would", "will", "should", "shall", "may", "might", "must", "do", "does", "did", "is", "are",
        "was", "were", "have", "has", "and", "or", "but", "because", "so", "if", "than", "the", "a", "an"}
_CUT_PREP = {"to", "of", "for", "with", "at", "in", "on", "from", "by", "about", "into", "through", "us", "me", "them"}


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
    words = re.findall(r"[a-z0-9']+", body)
    if len(words) < 4:
        return QuestionVerdict(False, 0.9, "under 4 words")
    if _REJECT.search(body):
        return QuestionVerdict(False, 0.95, "logistics or small talk")
    if q and _TAG.search(s.rstrip("?") + "?"):
        return QuestionVerdict(False, 0.8, "tag question")
    last, prev = words[-1], words[-2]
    # "could you" is cut off, "for you" is not; "…the project you did" is complete
    cut = last in _CUT and prev not in _PRONOUNS or last in _PRONOUNS and prev in _CUT
    if cut or not q and last in _CUT_PREP:
        return QuestionVerdict(False, 0.6, "fragment")
    clauses = [c.strip() for c in body.split(",")]
    if q and (re.match(_FILLER + rf"({_AUX}|{_WH})\b", clauses[0])
              or any(re.match(_FILLER + rf"({_WH}|{_AUX_PRONOUN})\b", c) for c in clauses[1:])):
        return QuestionVerdict(True, 0.95, "interrogative and '?'")
    if _ASK.search(body):
        return QuestionVerdict(True, 0.85, "indirect question")
    if re.match(_FILLER + rf"({_AUX_PRONOUN}\b|{_WH_AUX})", clauses[0]):
        return QuestionVerdict(True, 0.85, "interrogative opening")
    if any(re.match(_FILLER + _WH_VERB, c) and len(c.split()) >= 4 for c in clauses):
        return QuestionVerdict(True, 0.8, "wh-word and verb")
    if q and re.match(r"(and|what about|how about) (the|your|our) ", body):
        return QuestionVerdict(True, 0.8, "follow-up ('and the …?')")
    if q and re.match(r"(so )?you (can|could|will|would|do|have|are)\b", body):
        return QuestionVerdict(True, 0.75, "declarative question to the vendor")
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
