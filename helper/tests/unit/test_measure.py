import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[3] / "scripts"))
import measure  # noqa: E402

from fleck_helper.pipeline import Trigger  # noqa: E402

REF = {"turns": [{"speaker": "client", "end_ms": 1000, "text": "Who owns the code you write for us?", "question_id": "q_ip"},
                 {"speaker": "client", "end_ms": 5000, "text": "Fine.", "question_id": None}],
       "questions": [{"id": "q_ip"}]}


def test_recall_needs_the_question_text_not_just_the_time():
    hit = measure.counts([Trigger("a", "the fifteenth would suit us. Who owns the code you write for us?", 1100)], REF)
    assert hit["conversation_recall"] == (1, 1) and not hit["conversation_false_triggers"]
    miss = measure.counts([Trigger("a", "How was your trip back?", 1100)], REF)
    assert miss["conversation_recall"] == (0, 1) and miss["conversation_false_triggers"] == ["How was your trip back?"]
