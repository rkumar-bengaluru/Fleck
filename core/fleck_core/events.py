from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .types import Citation


class _Event(BaseModel):
    # defaults are always present on the wire, so TS fields are non-optional
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


class SegmentEvent(_Event):
    type: Literal["segment"]
    meeting_id: str
    segment_id: str
    stream: Literal["you", "client"]
    start_ms: int
    end_ms: int
    text: str
    final: bool
    gate: Literal["client", "you", "overlap"] | None


class AssistEvent(_Event):
    """Replacement rule: a later `sentence` event with the same `sentence_index`, or a `done` whose kind is
    `not_in_material`, replaces text already shown (a streamed sentence that failed its checks)."""

    type: Literal["assist"]
    meeting_id: str
    assist_id: str
    segment_id: str
    phase: Literal["thinking", "delta", "sentence", "done"]
    question: str
    text: str = ""
    sentence_index: int | None = None
    kind: Literal["library", "reference", "general", "not_in_material"] | None = None
    from_brief: bool = False
    citations: list[Citation] = []
    first_words_ms: int | None = None
    full_ms: int | None = None
    rejected: list[str] = []


class MeetingStateEvent(_Event):
    type: Literal["meeting_state"]
    meeting_id: str
    status: str


Event = Annotated[SegmentEvent | AssistEvent | MeetingStateEvent, Field(discriminator="type")]
_adapter = TypeAdapter(Event)


def parse_event(data: dict) -> SegmentEvent | AssistEvent | MeetingStateEvent:
    """Raises pydantic.ValidationError (a ValueError) on unknown type or bad fields."""
    return _adapter.validate_python(data)
