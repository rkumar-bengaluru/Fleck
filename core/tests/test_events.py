import pytest
from pydantic import ValidationError

from fleck_core.events import AssistEvent, MeetingStateEvent, SegmentEvent, parse_event
from fleck_core.types import Citation

EVENTS = [
    SegmentEvent(type="segment", meeting_id="m", segment_id="s", stream="client", start_ms=0, end_ms=10,
                 text="hi", final=True, gate=None),
    AssistEvent(type="assist", meeting_id="m", assist_id="a", segment_id="s", phase="done", question="q",
                kind="library", citations=[Citation(doc_id="d", chunk_id="c", title="t", client=None, kind="firm")],
                first_words_ms=5, rejected=["x"]),
    MeetingStateEvent(type="meeting_state", meeting_id="m", status="live"),
]


@pytest.mark.parametrize("ev", EVENTS, ids=lambda e: e.type)
def test_round_trip_each_event(ev):
    assert parse_event(ev.model_dump(mode="json")) == ev


def test_unknown_type_rejected():
    with pytest.raises(ValidationError):  # pydantic's, a ValueError subclass
        parse_event({"type": "nope", "meeting_id": "m"})


def test_replacement_sequence_parses_in_order():
    base = dict(type="assist", meeting_id="m", assist_id="a", segment_id="s", question="q")
    seq = [dict(base, phase="delta", text="It is five"), dict(base, phase="sentence", sentence_index=0, text="Not in our material"),
           dict(base, phase="done", kind="not_in_material")]
    evs = [parse_event(d) for d in seq]
    assert [e.phase for e in evs] == ["delta", "sentence", "done"] and evs[1].text == "Not in our material" and evs[2].kind == "not_in_material"
